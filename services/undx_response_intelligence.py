"""Expert response intelligence: the stage that turns verified evidence into prose.

Before this module existed, :func:`services.undx_tool_gateway._explain` answered every
successful read with the same five words. That was not a cosmetic defect. A system whose
sentence is chosen by outcome code rather than by evidence cannot say anything the
evidence does not already contain, which sounds safe and is the opposite: it means the
person is told "Here is what I found" whether the answer is four rows, one row, zero
rows, or zero rows because a table could not be reached. The distinctions the runtime
works hardest to preserve — verified against accepted-unverified, honest zero against
confident zero — died at the last inch, in the only place the user actually reads.

So the design commitment here is narrow and total: **language varies because meaning
varies.** There is no bag of openings to draw from. Every sentence is composed from
facts that are present in the evidence, and two answers read differently exactly when
the evidence differs — in count, in kind, in recency, in metric, in provenance, in
completeness. When two turns really do have the same shape, the repetition guard does
not reach for a synonym; it re-leads with a *different true fact* about the same
evidence (the provenance instead of the count, the exemplar instead of the total). If
no further true framing exists, the answer repeats, because inventing variety is worse
than being boring.

The second commitment is that this layer may never outrank the verified facts. It
receives the same ``ToolResult`` and ``VerificationResult`` the gateway used to decide
the outcome, it derives a :class:`ResponsePlan` from them deterministically, and then
:func:`validate_consistency` re-reads its own output and refuses to emit prose that
claims more than the plan supports. That last check is the one that matters: it is not
enough to build the sentence from evidence, because a template can still smuggle a
completion claim, a completeness claim, or a number that appears nowhere in the data.
Every rendered string is audited against the plan before it is returned, and a string
that fails is discarded rather than shipped with a warning.

What is deliberately absent: no model call, no randomness, no persistence, no second
runtime. :func:`compose` is a pure function of its arguments. That is what makes it
testable, and it is why the repetition guard takes conversation history as a parameter
instead of reading it from anywhere.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from services.undx_agent_contracts import (
    AgentOutcome,
    ToolResult,
    VerificationResult,
    VerificationState,
    clean,
)
from services.undx_cross_domain import build_cross_reading
from services.undx_domain_reasoning import DomainReading, build_reading

logger = logging.getLogger("undx.response_intelligence")

#: The old cap was 400 characters, which is shorter than a single worked example in the
#: detailed-answer standard. A detailed answer carries a direct answer, the finding, the
#: supporting evidence, what it means, the limitation and a next step; 400 characters
#: truncates it mid-clause, and a truncated honest answer reads as a careless one.
MAX_EXPLANATION_CHARS = 1200

#: Bounded so that a genuinely repetitive turn terminates instead of searching forever
#: for a phrasing that does not exist.
MAX_RENDER_ATTEMPTS = 6

#: How many previous assistant replies the repetition guard considers. Two is not enough
#: (a three-turn A/B/A alternation reads as a loop) and ten is noise: a resemblance to
#: something said twenty minutes ago is not what makes a conversation feel canned.
HISTORY_WINDOW = 5

#: The sentinel that means "do not narrow the search". Above any reachable draft count —
#: the widest lead branch is the read branch at five framings and there are four clause
#: orderings, so the search space is twenty — and therefore a deployment that never sets
#: ``UNDX_RESPONSE_MAX_REGENERATIONS`` behaves exactly as it
#: did before the flag had a reader. Wiring a dead flag in with a default *below* current
#: behaviour is not wiring it in; it is a silent narrowing wearing a fix's clothes.
_MAX_REGENERATIONS_DEFAULT = 64


def _max_regenerations() -> int:
    """How many rejected drafts :func:`render` may discard before it stops searching.

    Reads ``UNDX_RESPONSE_MAX_REGENERATIONS``, which was declared in
    :mod:`services.undx_brain.config` and — until this function existed — read by
    nothing. Its own description said it governed "how many times a response failing the
    factuality check may be regenerated before UNDX answers with the honest boundary
    instead", and the honest boundary was reached on exactly the same schedule whatever
    the variable said, because no code anywhere consulted it.

    Its declared default was ``1`` against a maximum of ``3``. The render loop can build
    up to twenty drafts, so every value the flag was permitted to hold described a
    behaviour narrower than the one that shipped — a control whose entire range is below
    the thing it claims to govern is not a control, it is a misleading label, and wiring
    it in as declared would have quietly cut UNDX off after its first rejected draft in
    every deployment. The declaration now defaults above the whole search space, so
    switching this on changes nothing until somebody deliberately lowers it, and lowering
    it can only make UNDX give up earlier and say less. It cannot make UNDX say more.

    Never raises. A configuration lookup that fails must not be the reason a person gets
    no answer, so the fall-back is the widest value — which is also the value that
    preserves the behaviour this function replaced.
    """
    try:
        from services.undx_brain import config as brain_config
        raw = brain_config.resolve(None).values.get(
            "UNDX_RESPONSE_MAX_REGENERATIONS", _MAX_REGENERATIONS_DEFAULT)
        return max(0, min(int(raw), _MAX_REGENERATIONS_DEFAULT))
    except Exception:  # pragma: no cover - config is written not to raise
        logger.warning("undx_response_regeneration_budget_unreadable; using the default")
        return _MAX_REGENERATIONS_DEFAULT


class DetailLevel:
    """How much of the plan the renderer is allowed to spend."""

    BRIEF = "brief"
    STANDARD = "standard"
    DETAILED = "detailed"
    EXPERT = "expert"

    ORDER = (BRIEF, STANDARD, DETAILED, EXPERT)
    ALL = frozenset(ORDER)

    @classmethod
    def rank(cls, level: str) -> int:
        try:
            return cls.ORDER.index(level)
        except ValueError:
            return cls.ORDER.index(cls.STANDARD)

    @classmethod
    def at_least(cls, level: str, floor: str) -> str:
        """Raise ``level`` to ``floor`` if it sits below it, never lower it.

        Used where the *evidence* demands room the question did not ask for: a partial
        answer has to explain itself even when the person said "quickly".
        """
        return level if cls.rank(level) >= cls.rank(floor) else floor


#: Outcomes in which the turn produced no usable answer. Named once because three
#: separate decisions depend on the same membership test, and a set that is retyped at
#: each call site is a set that eventually disagrees with itself.
_FAILURE_STATUSES = frozenset({
    AgentOutcome.TERMINAL_FAILURE,
    AgentOutcome.RECOVERABLE_FAILURE,
    AgentOutcome.PERMISSION_DENIED,
    AgentOutcome.UNSUPPORTED_CAPABILITY,
})


class ResponseType:
    """The shape of answer being given, as declared by the plan."""

    ANSWER = "answer"
    EXPLANATION = "explanation"
    COMPARISON = "comparison"
    SUMMARY = "summary"
    RECOMMENDATION = "recommendation"
    DRAFT = "draft"
    CLARIFICATION = "clarification"
    ACTION_RECEIPT = "action_receipt"
    FAILURE_REPORT = "failure_report"

    ALL = frozenset({
        ANSWER, EXPLANATION, COMPARISON, SUMMARY, RECOMMENDATION,
        DRAFT, CLARIFICATION, ACTION_RECEIPT, FAILURE_REPORT,
    })


class GoalShape:
    """What the person was trying to accomplish, as the Brain read it.

    Deliberately *not* an enum and deliberately not imported from
    :mod:`services.undx_brain.goals`. This module sits below the Brain and must keep
    working with the Brain switched off, so it accepts the vocabulary as strings and
    treats an unrecognised one — including the empty string a disabled Brain
    produces — as :attr:`UNKNOWN`, falling back to the behaviour it had before any of
    this existed.

    Two members of the directive's vocabulary are absent, and their absence is the
    design rather than an oversight:

    * **find** is here, but it is not a shape the Brain reads. "Show my alerts" and
      "find my Bitcoin alert" are the same shape — retrieval — and differ in whether
      the sentence narrowed to one named resource. That is a fact the runtime already
      computes when it resolves arguments, so ``find`` is derived from it by
      :func:`services.undx_agent_runtime.goal_shape_for` rather than by a second list
      of phrasings competing with :data:`services.undx_brain.goals.EXPLAIN_FRAMES`.
      A second reader of intent is the failure this whole layer exists to avoid.
    * **compare** is not here at all. The Brain has no comparison shape, and adding
      one to satisfy a vocabulary list would be inventing a distinction nothing reads.
      Comparison is a *response mode*, already detected from the question by
      :data:`_COMPARISON_MARKERS`, and it stays there.
    """

    SHOW = "show"
    FIND = "find"
    EXPLAIN = "explain"
    REPAIR = "repair"
    ACT = "act"
    MANAGE = "manage"
    UNKNOWN = ""

    ALL = frozenset({SHOW, FIND, EXPLAIN, REPAIR, ACT, MANAGE, UNKNOWN})

    @classmethod
    def read(cls, value: Any) -> str:
        """Coerce anything to a known shape. Unknown input degrades, never raises."""
        text = clean(str(value or ""), 24).strip().lower()
        return text if text in cls.ALL else cls.UNKNOWN


class ResponseMode:
    """What the answer is *for*, which is not the same as what kind of answer it is.

    :class:`ResponseType` classifies the material — a failure report is not a summary.
    This classifies the debt: a list owes items, an explanation owes an account of
    them, a diagnosis owes evidence for a cause. Two turns over identical evidence can
    share a response type and differ here, which is precisely the collapse the goal
    layer was built to stop: "show my alerts" and "explain my alerts" both return
    ``answer`` and must not both return a bare list.
    """

    LIST = "list"
    RESOURCE = "resource"
    EXPLANATION = "explanation"
    DIAGNOSIS = "diagnosis"
    COMPARISON = "comparison"
    RECEIPT = "receipt"

    ALL = frozenset({LIST, RESOURCE, EXPLANATION, DIAGNOSIS, COMPARISON, RECEIPT})

    #: Modes that owe the reader an account rather than a recital. Named once because
    #: the detail floor, the clause set and the validator all test the same membership,
    #: and three copies of a set eventually disagree.
    ACCOUNTING = frozenset({EXPLANATION, DIAGNOSIS})


#: What each mode is required to spend, named in plan-field terms so the requirement is
#: checkable against the plan rather than against the prose. ``unmet_evidence`` reports
#: what a plan could not supply; it does not quietly drop the requirement, because a
#: contract that shrinks to fit the evidence is not a contract.
_REQUIRED_EVIDENCE: dict[str, tuple[str, ...]] = {
    ResponseMode.LIST: ("evidence",),
    ResponseMode.RESOURCE: ("evidence",),
    ResponseMode.EXPLANATION: ("evidence", "interpretations", "limitations"),
    ResponseMode.DIAGNOSIS: ("evidence", "interpretations", "recommended_next_steps"),
    ResponseMode.COMPARISON: ("evidence", "interpretations"),
    ResponseMode.RECEIPT: ("evidence",),
}

#: Prohibitions attached to a mode, over and above the ones the evidence attaches.
#: Every entry names a check that actually runs — see :func:`validate_consistency` and
#: the runtime's explain-over-write guard. A ``must_not_do`` entry with no enforcer is
#: a promise, and this file does not publish promises.
_MODE_PROHIBITIONS: dict[str, tuple[str, ...]] = {
    ResponseMode.EXPLANATION: ("perform_a_write", "answer_with_a_bare_list"),
    ResponseMode.DIAGNOSIS: ("perform_a_write", "assert_a_cause_without_evidence"),
}


class ActionState:
    """What, if anything, the turn did to the user's data."""

    NONE = "none"
    PROPOSED = "proposed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    DEGRADED = "degraded"

    ALL = frozenset({
        NONE, PROPOSED, CONFIRMATION_REQUIRED,
        VERIFIED_SUCCESS, VERIFIED_FAILURE, DEGRADED,
    })


class EvidenceShape:
    """The coarse shape of a result set. The renderer branches on this, not on strings."""

    EMPTY = "empty"
    SINGLE = "single"
    FEW = "few"
    MANY = "many"
    STATE = "state"       # a settings-style result: no rows, a dict of current values
    MUTATION = "mutation"  # a write's read-back


# ---------------------------------------------------------------------------
# Vocabulary
#
# These tables are data about PulseSoc, not phrasing. They exist so a sentence can
# name what it counted ("four saved posts") instead of naming its own plumbing
# ("four records"). Nothing here chooses between alternative wordings; each entry is
# the single correct noun for one domain.
# ---------------------------------------------------------------------------

#: Capability-id prefix -> (singular, plural). Longest prefix wins.
_NOUNS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("account.health", ("account health signal", "account health signals")),
    ("activity", ("activity event", "activity events")),
    ("ads", ("ad campaign", "ad campaigns")),
    ("comments", ("comment", "comments")),
    ("conversations", ("conversation", "conversations")),
    ("creator", ("creator metric", "creator metrics")),
    ("crypto.alerts", ("price alert", "price alerts")),
    ("events", ("event", "events")),
    ("feed.comments", ("comment", "comments")),
    ("feed.post.performance", ("performance figure", "performance figures")),
    ("feed.posts", ("post", "posts")),
    ("groups", ("group", "groups")),
    ("learning", ("learning item", "learning items")),
    ("live", ("Live session", "Live sessions")),
    ("localization", ("language and region preference", "language and region preferences")),
    ("marketplace.listing", ("listing detail", "listing details")),
    ("marketplace.order", ("order detail", "order details")),
    ("marketplace", ("listing", "listings")),
    ("memory", ("remembered item", "remembered items")),
    ("messages", ("message", "messages")),
    ("music", ("track", "tracks")),
    ("notifications.preference", ("notification setting", "notification settings")),
    ("notifications", ("notification", "notifications")),
    ("premium", ("entitlement", "entitlements")),
    ("presence", ("presence privacy setting", "presence privacy settings")),
    ("profile.relationship", ("relationship figure", "relationship figures")),
    ("profile", ("profile detail", "profile details")),
    ("reels.comments", ("comment", "comments")),
    ("reels.performance", ("performance figure", "performance figures")),
    ("reels", ("Reel", "Reels")),
    ("saved", ("saved item", "saved items")),
    ("search", ("result", "results")),
    ("security.device", ("device", "devices")),
    ("security.sessions", ("session", "sessions")),
    ("security", ("security event", "security events")),
    ("settings", ("setting", "settings")),
    ("social.followers", ("follower", "followers")),
    ("social", ("relationship", "relationships")),
    ("status", ("Status", "Statuses")),
    ("support.tickets", ("support ticket", "support tickets")),
    ("verification", ("verification request", "verification requests")),
)

#: Record ``kind`` -> plain noun, for results that mix several domains in one list.
_KIND_NOUNS: dict[str, tuple[str, str]] = {
    "creator_analytics": ("creator metric", "creator metrics"),
    "crypto_alert": ("price alert", "price alerts"),
    "event": ("event", "events"),
    "group": ("group", "groups"),
    "live_performance": ("Live performance figure", "Live performance figures"),
    "live_session": ("Live session", "Live sessions"),
    "marketplace_listing": ("listing", "listings"),
    "marketplace_order": ("order", "orders"),
    "message_received": ("received message", "received messages"),
    "music_track": ("track", "tracks"),
    "new_follower": ("new follower", "new followers"),
    "notification": ("notification", "notifications"),
    "notification_explanation": ("notification explanation", "notification explanations"),
    # The activity and search kinds were missing, and the fallback below pluralises by
    # appending an "s" to the raw kind — so the breakdown clause, which is the one place
    # these kinds are named aloud, read "three post createds" and "two status activitys".
    # They appear only in the mixed-kind results, which is exactly where the breakdown
    # fires, so the defect was invisible to every single-domain test.
    "post_created": ("post", "posts"),
    "reel_activity": ("reel", "reels"),
    "status_activity": ("status update", "status updates"),
    "post": ("post", "posts"),
    "message": ("message", "messages"),
    "profile": ("person", "people"),
    "presence_privacy": ("presence privacy setting", "presence privacy settings"),
    "region_preference": ("region preference", "region preferences"),
    "security_event": ("security event", "security events"),
    "security_session": ("session", "sessions"),
    "support_ticket": ("support ticket", "support tickets"),
    "translation_preference": ("translation preference", "translation preferences"),
    "verification_request": ("verification request", "verification requests"),
}

#: Verified boolean field -> (state when true, state when false). Used to describe a
#: completed write from what the read-back *observed*, rather than from what the caller
#: asked for. The distinction is the entire point of read-back verification, and it
#: would be lost if the sentence were built from the arguments.
_FIELD_STATE: dict[str, tuple[str, str]] = {
    "saved": ("that post is in your Saved library",
              "that post is no longer in your Saved library"),
    "liked": ("your like is on that post", "your like has been taken off that post"),
    "following": ("you are following that account", "you are not following that account"),
    "push": ("push is on for that category", "push is off for that category"),
    "active": ("that alert is running", "that alert is paused"),
}

#: Metric words PulseSoc does not expose through any capability in this registry.
#: A prose string containing one of these, for a capability whose evidence does not
#: carry it, is an invented analytic — the specific failure the detailed-answer
#: standard names.
_UNAVAILABLE_METRIC_TERMS: tuple[str, ...] = (
    "reach", "impression", "revenue", "watch time", "watch-time", "watchtime",
    "click-through", "clickthrough", "ctr", "conversion", "monetis", "monetiz",
    "earnings", "payout", "demographic", "audience growth", "follower growth",
    "retention curve", "bounce rate", "session length",
)

#: Matched at a word boundary and as a stem, so "impression" also catches
#: "impressions" and "monetis" catches "monetisation". A plain substring test is not
#: good enough in either direction: it misses the plural and it fires on "outreach".
_METRIC_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    term: re.compile(r"\b" + re.escape(term), re.IGNORECASE)
    for term in _UNAVAILABLE_METRIC_TERMS
}

#: Words that turn a following metric term back into ordinary English. "I could not
#: reach one of your sources" is a statement about a failed read, not a claim about
#: audience reach, and the first version of this check rejected the runtime's own
#: honest degradation sentence for containing it. A guard list is the narrow fix; the
#: broad one — dropping "reach" from the vocabulary — would stop catching the thing
#: the vocabulary exists to catch.
_METRIC_VERB_GUARD = re.compile(
    r"(?:could not|cannot|can't|couldn't|unable to|did not|didn't|failed to|to|not)\s+$",
    re.IGNORECASE,
)

#: Phrases a draft must never contain about itself. A draft that says it was sent is
#: not a wording problem; it is a false statement about whether a message left the
#: account, and the whole capability exists to keep that from happening.
_SENT_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:i|undx)\s+(?:have\s+)?sent\b",
    r"\b(?:has|have|was|were)\s+been?\s+sent\b",
    r"\bhas\s+been\s+sent\b",
    r"\bmessage\s+(?:is\s+)?(?:sent|delivered|on its way|went out)\b",
    r"\bi\s+replied\b",
    r"\bsent\s+(?:it|that|the\s+reply)\b",
))

#: Claims of a completed change. Checked whenever the plan's action state is not
#: ``verified_success`` — the only state in which the system has independently
#: observed the new value.
_COMPLETION_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    # The first-person completion claim. Three things were missing from the original,
    # and each was one surface form of a concept the pattern already covered:
    #
    # * the contraction — "I've completed that" (the space after ``i`` never came),
    # * an adverb between subject and verb — "I just changed it", "I successfully
    #   liked the post",
    # * the verbs a *model* reaches for rather than the ones this module writes —
    #   "completed", "liked", "turned it off".
    #
    # They were missing because the list was grown against the runtime's own prose,
    # which is generated from a fixed vocabulary and never contracts or hedges. Model
    # prose does both, and the model is now the thing being checked: see
    # :func:`services.pulse_ai_service._strip_unsupported_execution_claims`, which runs
    # this over a provider reply on the path where no receipt can exist.
    #
    # The adverb slot is a closed set rather than ``\w+``, which is what keeps a
    # negation from being swallowed: "I have not sent it" and "I never deleted
    # anything" claim nothing and must go on claiming nothing. ``followed(?!\s+up)``
    # is there for the same reason — "I followed up on that" is not a claim to have
    # followed an account.
    r"\b(?:i|undx)(?:'ve|\s+have|\s+has)?\s+(?:(?:just|already|successfully|now|then)\s+){0,2}"
    r"(?:sent|posted|published|deleted|removed|created|updated|changed|cancell?ed"
    r"|paused|resumed|completed|finished|liked|unliked|saved|unsaved|scheduled"
    r"|enabled|disabled|blocked|unblocked|muted|unmuted|unfollowed|reported"
    r"|followed(?!\s+up)|turned\s+(?:it\s+)?(?:on|off))\b",
    r"\b(?:i|undx)(?:'ve|\s+have)\s+(?:just\s+|already\s+)?done\s+(?:that|it|this)\b",
    r"\b(?:has|have)\s+been\s+(?:sent|posted|published|deleted|removed|created|updated|changed|cancell?ed|paused|resumed|completed|liked|unliked|saved|enabled|disabled|blocked|muted|reported)\b",
    # ``are`` as well as ``is``. The plural was missing and the omission is the same
    # shape as every other near-miss in a hand-written list: the concept was obviously
    # covered, and exactly one of its surface forms was. "Push notifications are now
    # off" is a completion claim by any reading, and it went straight past the guard.
    r"\b(?:is|are)\s+now\s+(?:saved|liked|unliked|followed|unfollowed|deleted|paused|resumed|active|inactive|off|on)\b",
    r"\ball\s+set\b",
    r"\ball\s+done\b",
    r"\bthat'?s\s+done\b",
    r"\bi\s+(?:did|made)\s+(?:that|it)\b",
    # The runtime's own confirmed-write vocabulary. Added after a degraded write was
    # found rendering "Done — ..., and I read it back from PulseSoc to confirm it"
    # above a receipt that said ``degraded``: the sentence was built by this module,
    # so the validator was the last thing standing between it and the reader, and it
    # matched none of the patterns above. Each is written affirmatively so that the
    # honest negative — "I could not read it back to confirm it", which is what a
    # non-verified write is *supposed* to say — is not caught by its own guard.
    r"^done\b",
    r"\bi\s+read\s+it\s+back\b",
    r"\bi\s+confirmed\s+this\b",
    r"\bthe\s+change\s+went\s+through\b",
    r"\bthe\s+follow-?up\s+read\s+agrees\b",
    # The gateway's idempotent-replay sentence. It is a completion claim in the plainest
    # possible words and it was written in a module that never consults this list, which
    # is exactly why it needs to be here: the replay path reports a *prior* operation,
    # and when that operation's recorded status was ``ok`` rather than ``verified``
    # nobody ever read the new value back. "I had already done that" is then a claim
    # about something no part of the system observed.
    r"\bi\s+had\s+already\s+done\s+that\b",
))


def completion_claim(text: str, limit: int = MAX_EXPLANATION_CHARS) -> str:
    """The completion claim this sentence makes, or ``""``.

    ``limit`` bounds how much of ``text`` is read, and defaults to the cap that suits
    the original caller: :func:`validate_consistency` is handed a *composed*
    explanation, which this module built and which cannot exceed
    :data:`MAX_EXPLANATION_CHARS`. The conversational guard in
    :mod:`services.pulse_ai_service` is handed free-form provider output instead, which
    the transport allows up to 6000 characters, and raises the limit accordingly — a
    claim in a fourth paragraph is exactly as false as one in the first, and a default
    that quietly stopped reading at 1200 would have been a hole rather than a bound.

    The single reader of :data:`_COMPLETION_CLAIM_PATTERNS`, extracted so that the two
    places which need this question — :func:`validate_consistency` here, and the
    runtime's metacognitive self-check in
    :func:`~services.undx_agent_runtime.build_card` — ask it of the same list.

    They did not. The runtime carried its own four-entry tuple, ``(" completed",
    " is paused", " is active", " was deleted")``, and every one of the sentences this
    module actually produces on a write went straight past it: "Done — …", "I confirmed
    this against your account after the change", "The change went through and the
    follow-up read agrees", "I had already done that". A guard whose job is to catch
    unsupported completion claims, which does not recognise the vocabulary its own
    system uses to claim completion, is a guard that only fires on sentences nobody
    writes.

    The four-entry tuple was also *wider* than this list in the one direction that
    hurts. ``" is paused"`` matches "your BTC alert is paused" — a true statement about
    a thing a read just looked at, which the guard would have rewritten into "The
    request returned without enough independent evidence to claim completion." These
    patterns are deliberately shaped around completion *verbs* ("is now paused", "I
    paused"), so a read describing a state it observed is not mistaken for a write
    claiming it caused one.

    Returns the matching pattern source, truncated, so callers can log *which* claim was
    found rather than only that one was. Empty string means the sentence claims nothing.
    """
    body = clean(text, limit)
    if not body:
        return ""
    for pattern in _COMPLETION_CLAIM_PATTERNS:
        if pattern.search(body):
            return pattern.pattern[:40]
    return ""


#: How much of a reply :func:`execution_narration` reads. Deliberately not
#: :data:`MAX_EXPLANATION_CHARS`: that cap bounds a *composed* explanation, and this
#: function is pointed at free-form provider output, which the transport allows up to
#: 6000 characters. Scanning the first 1200 would have meant a model could put the
#: narration in a fourth paragraph and walk straight past the guard.
MAX_SCANNED_REPLY_CHARS = 8000

#: Narration of an execution *in progress*. A separate list from
#: :data:`_COMPLETION_CLAIM_PATTERNS` because it is a separate claim, and the
#: distinction matters: "[Executing action...]" asserts that nothing has finished yet,
#: so the completion list is right to ignore it. It is nonetheless exactly as
#: forbidden, because saying an execution is under way is a claim that an execution
#: *started*, and in this system only the tool gateway starts one. A model that has
#: called no gateway and writes "one moment while I like that" has already told the
#: reader something untrue, and the reader will read whatever follows in that light.
#:
#: Every pattern is anchored on first-person agency or on bracketed machine narration.
#: Second-person instruction must not be caught — "you can like a post by tapping the
#: heart" is help, and "PulseSoc is processing your upload" is a statement about the
#: platform rather than a claim about what UNDX just did. That is why there is no bare
#: ``\bexecuting\b`` here despite it being the single most obvious word to match.
_EXECUTION_NARRATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    # The literal string from the failure this guard was written for, and the shape of
    # it. Models emit bracketed stage directions because their training data is full of
    # transcripts that contain them; the model is imitating a tool-calling trace it
    # never produced. Bracket content is bounded so an ordinary long aside in square
    # brackets cannot drift into a match.
    r"\[\s*(?:executing|running|calling|performing|processing|invoking|action|tool)[^\]]{0,60}\]",
    r"\bexecuting\s+(?:the\s+|this\s+|your\s+)?(?:action|request|command|tool|capability|change)\b",
    r"\b(?:i'?m|i\s+am|undx\s+is)\s+(?:now\s+)?(?:executing|running|performing|invoking|submitting|applying)\b",
    r"\b(?:i'?m|i\s+am)\s+(?:now\s+)?(?:going\s+ahead\s+and\s+)?"
    r"(?:liking|posting|deleting|updating|sending|saving|scheduling|following|unfollowing"
    r"|blocking|muting|reporting|cancell?ing|pausing|resuming)\b",
    r"\blet\s+me\s+(?:go\s+ahead\s+and\s+)?(?:execute|run|perform|do)\s+(?:that|it|this)\b",
    r"\bone\s+moment\s+while\s+i\b",
    r"\bhold\s+on\s+while\s+i\b",
    r"\bi'?ll\s+(?:go\s+ahead\s+and\s+)?(?:do|execute|run|perform|handle)\s+(?:that|it|this)\s+(?:right\s+)?now\b",
    r"\bworking\s+on\s+(?:that|it)\s+(?:right\s+)?now\b",
    r"\baction\s+in\s+progress\b",
    r"\bcalling\s+the\s+(?:api|tool|gateway|backend|endpoint)\b",
))


def execution_narration(text: str) -> str:
    """The in-progress execution claim this text makes, or ``""``.

    The counterpart to :func:`completion_claim`, and deliberately not folded into it.
    A completion claim is checked against whether the action *verified*; an execution
    narration is checked against whether an action was *attempted at all*. Those are
    different questions with different answers, and a caller that has neither — the
    conversational path, where by construction there is no receipt — needs to ask both.

    Returns the matching pattern source, truncated, so a caller can log which shape was
    found rather than only that something was. Empty string means the text narrates no
    execution.
    """
    body = clean(text, MAX_SCANNED_REPLY_CHARS)
    if not body:
        return ""
    for pattern in _EXECUTION_NARRATION_PATTERNS:
        if pattern.search(body):
            return pattern.pattern[:40]
    return ""


#: Claims that the answer is the whole answer. Forbidden whenever a source degraded,
#: because a partial view asserted as total is precisely the confident-zero failure.
_COMPLETENESS_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bthat(?:'s| is)\s+everything\b",
    r"\bthe\s+(?:full|complete|whole|entire)\s+(?:picture|list|set|history|record)\b",
    r"\ball\s+of\s+(?:your|the)\b",
    r"\bnothing\s+else\b",
    r"\bcomplete\s+list\b",
    r"\bevery\s+single\b",
))

#: Filler openers that make a written reply read as generated rather than written.
#: Used by :func:`draft_quality_issues` against message drafts, where the person is
#: about to send the words as their own.
_DRAFT_FILLER_PHRASES: tuple[str, ...] = (
    "thank you for reaching out",
    "thanks for reaching out",
    "i hope this message finds you well",
    "i hope this finds you well",
    "i hope you are doing well",
    "please let me know if you have any questions",
    "let me know if you have any questions",
    "i appreciate your understanding",
    "thank you for your understanding",
    "thank you for your patience",
    "as per my last message",
    "i wanted to reach out",
    "please do not hesitate to",
    "at your earliest convenience",
    "i trust this email finds you",
)

#: Question markers. These select detail level from what was asked, which is the only
#: honest input for that decision — the evidence cannot say how much the person wants.
_BREVITY_MARKERS: tuple[str, ...] = (
    "quick", "quickly", "briefly", "in short", "short answer", "tl;dr", "tldr",
    "one line", "one word", "just tell me", "just the", "yes or no", "summarise in a",
    "summarize in a",
)
_DEPTH_MARKERS: tuple[str, ...] = (
    "why", "explain", "how come", "walk me through", "break down", "break it down",
    "in detail", "detailed", "what does that mean", "what does this mean",
    "help me understand", "how does", "what happened",
)
_EXPERT_MARKERS: tuple[str, ...] = (
    "everything", "deep dive", "full picture", "exhaustive", "expert",
    "as much detail", "all the detail", "thorough",
)
_COMPARISON_MARKERS: tuple[str, ...] = (
    "compare", "versus", " vs ", "difference between", "better than", "worse than",
    "more than", "less than", "against last", "compared to",
)
_RECOMMENDATION_MARKERS: tuple[str, ...] = (
    "should i", "what should", "recommend", "suggest", "advice", "best way",
    "what would you", "worth it", "help me decide",
)
#: A follow-up is short, and leans on something already said rather than naming it.
_CONTINUITY_MARKERS: tuple[str, ...] = (
    "what about", "and then", "why is that", "why though", "how about", "what else",
    "the same for", "and that", "go on", "tell me more", "more detail", "why not",
)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceView:
    """A normalised, read-only view of one ``ToolResult``.

    The renderer never touches ``ToolResult`` directly. Everything it is allowed to
    say has to survive the trip through this object first, which is what keeps an
    untrusted string inside a record from reaching prose by accident: only declared
    fields are lifted, and each is bounded on the way.
    """

    capability_id: str
    is_write: bool
    shape: str
    total: int
    kinds: tuple[tuple[str, int], ...]
    titles: tuple[str, ...]
    details: tuple[str, ...]
    sources: tuple[str, ...]
    newest: str
    oldest: str
    metrics: tuple[tuple[str, float], ...]
    flags: tuple[tuple[str, bool], ...]
    labels: tuple[tuple[str, str], ...]
    degraded: tuple[str, ...]
    low_confidence: int

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)

    @property
    def kind_count(self) -> int:
        return len(self.kinds)

    @property
    def dominant_kind(self) -> str:
        return self.kinds[0][0] if self.kinds else ""


#: Keys in a result's ``data`` that describe the envelope rather than the answer.
_STRUCTURAL_KEYS = frozenset({
    "items", "count", "complete", "truncated", "degraded_sources", "source", "sources",
    "timestamp", "generated_at", "authorization_scope", "native_route", "confidence",
    "id", "user_id", "owner_user_id", "conversation_id", "post_id", "reel_id",
    "status_id", "alert_id", "notification_id", "listing_id", "order_id", "live_id",
})


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _count_field(value: Any) -> int:
    """A row count a tool reported in ``data`` rather than as rows, or zero.

    Separate from :func:`_numeric`, which must stay strict about strings so that a
    version label like ``"2.1"`` is never mistaken for a metric. ``count`` is a declared
    field with one meaning, so a JSON-shaped ``"7"`` is the number seven — but only
    where the whole string is digits, and only where the result is a plausible count.
    """
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        # ``int(nan)`` and ``int(inf)`` both raise; the comparison rules them out first,
        # since NaN is not greater than zero and infinity fails the upper bound.
        return int(value) if 0 < value < 1_000_000_000 else 0
    if isinstance(value, str) and value.strip().isdigit():
        counted = int(value.strip())
        return counted if counted < 1_000_000_000 else 0
    return 0


def _collect_scalars(source: dict[str, Any], metrics: dict[str, float],
                     flags: dict[str, bool], labels: dict[str, str]) -> None:
    """Split one mapping into numbers, booleans and short strings.

    Split rather than merged because the three are said differently: a number is
    quoted, a boolean becomes a state clause, and a label is named. Collapsing them
    into "fields" is what produces sentences that read like a database dump.
    """
    for key, value in (source or {}).items():
        # Cleaned and capped like every value, because a key is not a safer input than a
        # value — it is the same untrusted mapping, read from the other side. Keys reach
        # the prose through ``_humanise`` and reach the receipt verbatim, so an
        # uncleaned one could carry newlines into a rendered sentence or an unbounded
        # string into the audit record, which is precisely what :class:`EvidenceView`
        # promises does not happen ("only declared fields are lifted, and each is
        # bounded"). A key that cleans away to nothing had no name to begin with.
        name = clean(str(key), 80)
        if not name:
            continue
        if name in _STRUCTURAL_KEYS or name.endswith("_id"):
            continue
        number = _numeric(value)
        if number is not None:
            metrics.setdefault(name, number)
        elif isinstance(value, bool):
            flags.setdefault(name, bool(value))
        elif isinstance(value, str) and value and len(value) <= 80:
            labels.setdefault(name, clean(value, 80))


def build_view(spec: Any, result: ToolResult) -> EvidenceView:
    """Normalise a tool result into the only evidence the renderer may read."""
    records = [r for r in (result.records or []) if isinstance(r, dict)]
    data = dict(result.data or {})

    kind_counts: dict[str, int] = {}
    titles: list[str] = []
    details: list[str] = []
    sources: list[str] = []
    stamps: list[str] = []
    metrics: dict[str, float] = {}
    flags: dict[str, bool] = {}
    labels: dict[str, str] = {}
    low_confidence = 0

    for record in records:
        kind = clean(record.get("kind") or "", 60)
        if kind:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        title = clean(record.get("title") or "", 120)
        if title:
            titles.append(title)
        detail = clean(record.get("detail") or "", 200)
        if detail:
            details.append(detail)
        source = clean(record.get("source") or "", 80)
        if source:
            sources.append(source)
        stamp = clean(record.get("timestamp") or "", 40)
        if stamp:
            stamps.append(stamp)
        try:
            if float(record.get("confidence", 1.0)) < 1.0:
                low_confidence += 1
        except (TypeError, ValueError):
            low_confidence += 1
        inner = record.get("data")
        if isinstance(inner, dict):
            _collect_scalars(inner, metrics, flags, labels)

    _collect_scalars(data, metrics, flags, labels)

    total = len(records)
    if total == 0:
        # A settings-shaped result carries its answer in ``data`` rather than in rows.
        # Calling that "empty" would report a configured account as an unconfigured
        # one, so the two are distinguished by whether anything was actually read.
        #
        # Only ever *upwards* from zero, and only from a count the tool itself reported.
        # ``total`` drives the counting prose — "You have twelve posts" — so this number
        # is one the renderer will state aloud while the evidence list has nothing to
        # show behind it. Coerced defensively rather than with ``int()``: a NaN, an
        # infinity or a non-numeric string each raise from ``int``, and this line sits
        # inside ``build_view``, below the gateway's only safety net, where an exception
        # costs the whole turn its answer. A malformed count is no count.
        total = _count_field(data.get("count"))

    degraded = tuple(clean(s, 80) for s in (result.degraded_sources or []) if s)

    if getattr(spec, "is_write", False):
        shape = EvidenceShape.MUTATION
    elif len(records) == 0 and (metrics or flags or labels):
        shape = EvidenceShape.STATE
    elif total == 0:
        shape = EvidenceShape.EMPTY
    elif total == 1:
        shape = EvidenceShape.SINGLE
    elif total <= 5:
        shape = EvidenceShape.FEW
    else:
        shape = EvidenceShape.MANY

    ordered_kinds = tuple(sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    ordered_stamps = sorted(s for s in stamps if s)

    return EvidenceView(
        capability_id=clean(getattr(spec, "capability_id", "") or result.capability_id, 120),
        is_write=bool(getattr(spec, "is_write", False)),
        shape=shape,
        total=int(total),
        kinds=ordered_kinds,
        titles=tuple(titles[:6]),
        details=tuple(details[:4]),
        sources=tuple(dict.fromkeys(sources))[:6],
        newest=ordered_stamps[-1] if ordered_stamps else "",
        oldest=ordered_stamps[0] if ordered_stamps else "",
        metrics=tuple(sorted(metrics.items())[:8]),
        flags=tuple(sorted(flags.items())[:8]),
        labels=tuple(sorted(labels.items())[:8]),
        degraded=degraded,
        low_confidence=int(low_confidence),
    )


def nouns_for(view: EvidenceView) -> tuple[str, str]:
    """The singular and plural noun this evidence is about.

    Resolved from the capability first and the record kind second. A mixed-kind result
    keeps the capability's own noun, because "four results" is true of a global search
    in a way that "four notifications" is not.
    """
    capability = view.capability_id
    best: tuple[str, str] | None = None
    best_len = -1
    for prefix, pair in _NOUNS:
        if capability.startswith(prefix) and len(prefix) > best_len:
            best, best_len = pair, len(prefix)
    if best:
        return best
    if view.kind_count == 1 and view.dominant_kind in _KIND_NOUNS:
        return _KIND_NOUNS[view.dominant_kind]
    return ("record", "records")


def _count_phrase(count: int, singular: str, plural: str) -> str:
    """Small counts as words, larger ones as digits — how people write them."""
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine"}
    noun = singular if count == 1 else plural
    return f"{words.get(count, str(count))} {noun}"


def _humanise(key: str) -> str:
    return str(key).replace("_", " ").strip()


def _sentence_case(text: str) -> str:
    """Uppercase the first letter and leave every other one alone.

    ``str.capitalize`` lowercases the rest of the string, which turned "open Crypto in
    PulseSoc" into "Open crypto in pulsesoc" — the product's own name, spelled wrong,
    in the sentence recommending it.
    """
    body = str(text or "").strip()
    return body[:1].upper() + body[1:] if body else ""


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _section_label(native_route: str) -> str:
    """A human name for the app section a route points at.

    Derived rather than tabulated, and deliberately dropped when the route carries a
    parameter placeholder: telling someone to open ``/pulse/reels/:reel_id`` is worse
    than telling them nothing.
    """
    route = str(native_route or "")
    parts = [p for p in route.split("/") if p and not p.startswith(":") and "?" not in p]
    if len(parts) < 2:
        return ""
    label = parts[1].replace("-", " ").replace("_", " ")
    return label.title() if label else ""


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class ResponsePlan:
    """What the answer is allowed to say, decided before any of it is written.

    The field set is fixed by the expert-response contract and :meth:`to_dict` emits
    exactly those keys. The trailing attributes are working state for the renderer and
    the validator; they are not part of the plan's published shape and never travel to
    a client.

    Four fields were added when the goal layer got its first real consumer, and they
    are published rather than working state on purpose: ``goal_shape`` and
    ``response_mode`` are what a native client would need in order to lay the answer
    out differently, and ``must_not_do`` is what an auditor needs in order to check
    afterwards that the turn kept its own rules. A constraint nobody can read is not
    a constraint.
    """

    response_type: str = ResponseType.ANSWER
    detail_level: str = DetailLevel.STANDARD
    #: What the person was trying to accomplish, from the Brain, or "" when the goal
    #: layer is switched off. Never derived from the question by this module: a second
    #: reader of intent is exactly the drift the goal layer was built to prevent.
    goal_shape: str = GoalShape.UNKNOWN
    #: What the answer owes. Derived from :attr:`goal_shape` where there is one and
    #: from the evidence where there is not, so the module degrades to its previous
    #: behaviour rather than to nothing.
    response_mode: str = ResponseMode.LIST
    #: Plan fields this mode is required to spend. Not filtered to what the evidence
    #: happens to supply — see :func:`unmet_evidence` for the honest difference.
    required_evidence: list[str] = field(default_factory=list)
    #: Named prohibitions in force for this turn. Every entry corresponds to a check
    #: that actually runs, either here in :func:`validate_consistency` or upstream in
    #: the runtime's explain-over-write guard.
    must_not_do: list[str] = field(default_factory=list)
    user_goal: str = ""
    direct_answer: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    cross_domain_links: list[dict[str, Any]] = field(default_factory=list)
    interpretations: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    action_state: str = ActionState.NONE
    native_cards: list[str] = field(default_factory=list)
    prohibited_claims: list[str] = field(default_factory=list)

    # --- working state, not part of the published plan -----------------------
    capability_id: str = ""
    allowed_numbers: frozenset[str] = field(default_factory=frozenset)
    view: EvidenceView | None = None
    is_follow_up: bool = False
    #: The domain's own one-line reading of this result, or "". Working state rather
    #: than a published field because :meth:`to_dict` emits the expert-response contract
    #: exactly, and this is a rendering input rather than a contract member — what the
    #: domain concluded reaches a client through ``interpretations`` and the rendered
    #: explanation, both of which already exist for the purpose.
    domain_assessment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_type": self.response_type,
            "detail_level": self.detail_level,
            "goal_shape": self.goal_shape,
            "response_mode": self.response_mode,
            "required_evidence": list(self.required_evidence),
            "must_not_do": list(self.must_not_do),
            "user_goal": self.user_goal,
            "direct_answer": self.direct_answer,
            "evidence": [dict(item) for item in self.evidence],
            "cross_domain_links": [dict(item) for item in self.cross_domain_links],
            "interpretations": list(self.interpretations),
            "uncertainties": list(self.uncertainties),
            "limitations": list(self.limitations),
            "recommended_next_steps": list(self.recommended_next_steps),
            "action_state": self.action_state,
            "native_cards": list(self.native_cards),
            "prohibited_claims": list(self.prohibited_claims),
        }


def is_follow_up(question: str, history: Sequence[str] = ()) -> bool:
    """Whether this turn leans on the previous one instead of standing alone.

    Requires both a prior assistant turn and a question that does not name its own
    subject. "Why?" after an answer is a follow-up; "Why did my Reel underperform?"
    is a new question that happens to start with the same word.
    """
    text = clean(question, 400).lower()
    if not text or not [h for h in history if clean(h, 10)]:
        return False
    if any(marker in text for marker in _CONTINUITY_MARKERS):
        return True
    words = re.findall(r"[a-z']+", text)
    if len(words) <= 4 and words and words[0] in {"why", "how", "when", "where", "and", "so"}:
        return True
    return False


def select_detail_level(question: str, view: EvidenceView, status: str,
                        *, follow_up: bool = False) -> str:
    """Choose how much room the answer gets.

    Two inputs, in this order. The question decides first, because the person asking
    is the authority on how much they want. The evidence can then only *raise* the
    floor, and only for the reasons that make a short answer misleading rather than
    merely terse: a partial result and a failure both have to explain themselves.
    """
    text = clean(question, 400).lower()

    if any(marker in text for marker in _EXPERT_MARKERS):
        level = DetailLevel.EXPERT
    elif any(marker in text for marker in _DEPTH_MARKERS) or any(
            marker in text for marker in _COMPARISON_MARKERS):
        level = DetailLevel.DETAILED
    elif any(marker in text for marker in _BREVITY_MARKERS):
        level = DetailLevel.BRIEF
    else:
        level = DetailLevel.STANDARD

    if follow_up:
        # A follow-up asked for more than the previous answer gave, or it would not
        # have been asked. Raising by one step is the cheapest honest reading of that.
        level = DetailLevel.at_least(level, DetailLevel.DETAILED)

    if view.is_degraded:
        level = DetailLevel.at_least(level, DetailLevel.STANDARD)
    if status in _FAILURE_STATUSES:
        level = DetailLevel.at_least(level, DetailLevel.STANDARD)
    return level


def select_response_type(spec: Any, status: str, question: str, view: EvidenceView) -> str:
    """Classify the answer. Outcome first, because a failure is never a summary."""
    capability = getattr(spec, "capability_id", "")
    if status in _FAILURE_STATUSES:
        return ResponseType.FAILURE_REPORT
    if status == AgentOutcome.CONFIRMATION_REQUIRED:
        return ResponseType.CLARIFICATION
    if capability.endswith(".draft") or capability.endswith(".suggest"):
        return ResponseType.DRAFT
    if getattr(spec, "is_write", False):
        return ResponseType.ACTION_RECEIPT

    text = clean(question, 400).lower()
    if any(marker in text for marker in _COMPARISON_MARKERS):
        return ResponseType.COMPARISON
    if any(marker in text for marker in _RECOMMENDATION_MARKERS) or capability.endswith(".recommend"):
        return ResponseType.RECOMMENDATION
    if any(marker in text for marker in _DEPTH_MARKERS) or capability.endswith(".explain"):
        return ResponseType.EXPLANATION
    if view.shape in {EvidenceShape.MANY, EvidenceShape.FEW} and view.kind_count > 1:
        return ResponseType.SUMMARY
    if capability.endswith(".summary") or capability.endswith("_summary"):
        return ResponseType.SUMMARY
    return ResponseType.ANSWER


def select_response_mode(goal_shape: str, response_type: str, view: EvidenceView) -> str:
    """Decide what the answer owes.

    The goal shape wins wherever there is one, because it is the only input that knows
    what the *person* was after; the evidence knows only what came back. Where there is
    no goal shape — the Brain switched off, or a turn that reached this module without
    passing the goal layer — the answer falls back to the classification this module
    was already making, so the mode is never invented out of nothing.

    The one place the two are combined is retrieval: ``show`` over a single record or a
    settings-style result is a resource, not a list of one. That is not a second opinion
    about intent, it is the same intent laid out for what actually came back.
    """
    shape = GoalShape.read(goal_shape)

    if shape == GoalShape.EXPLAIN:
        return ResponseMode.EXPLANATION
    if shape in (GoalShape.REPAIR, GoalShape.MANAGE):
        return ResponseMode.DIAGNOSIS
    if shape == GoalShape.ACT:
        return ResponseMode.RECEIPT
    if shape == GoalShape.FIND:
        return ResponseMode.RESOURCE
    if shape == GoalShape.SHOW:
        return (ResponseMode.RESOURCE
                if view.shape in {EvidenceShape.SINGLE, EvidenceShape.STATE}
                else ResponseMode.LIST)

    # No goal shape. Read the classification this module already made.
    if response_type in (ResponseType.ACTION_RECEIPT, ResponseType.CLARIFICATION):
        return ResponseMode.RECEIPT
    if response_type == ResponseType.COMPARISON:
        return ResponseMode.COMPARISON
    if response_type == ResponseType.FAILURE_REPORT:
        return ResponseMode.DIAGNOSIS
    if response_type in (ResponseType.EXPLANATION, ResponseType.RECOMMENDATION):
        return ResponseMode.EXPLANATION
    if view.shape in {EvidenceShape.SINGLE, EvidenceShape.STATE, EvidenceShape.MUTATION}:
        return ResponseMode.RESOURCE
    return ResponseMode.LIST


def required_evidence_for(mode: str) -> list[str]:
    """The plan fields this mode must spend. Fixed by the mode, never by the result."""
    return list(_REQUIRED_EVIDENCE.get(mode, ("evidence",)))


def must_not_do_for(mode: str, view: EvidenceView, status: str) -> list[str]:
    """The named prohibitions in force this turn, from the mode and from the evidence.

    Order is mode-first then evidence, and the list is de-duplicated while keeping that
    order, so a reader can tell at a glance which constraints came from what the person
    asked for and which came from what came back.
    """
    out: list[str] = list(_MODE_PROHIBITIONS.get(mode, ()))
    if status != AgentOutcome.VERIFIED_SUCCESS or not view.is_write:
        # The completion-claim guard runs on exactly this condition; naming it here
        # keeps the published constraint and the enforced one from drifting apart.
        out.append("claim_a_completed_change")
    if view.is_degraded:
        out.append("claim_the_answer_is_complete")
    out.append("state_a_metric_pulsesoc_does_not_expose")
    seen: set[str] = set()
    ordered: list[str] = []
    for name in out:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def unmet_evidence(plan: ResponsePlan) -> list[str]:
    """Required plan fields this plan could not fill.

    Reported rather than repaired, and reported rather than quietly removed from
    ``required_evidence``. An explanation with nothing to interpret is a real event —
    it means the capability returned material the domain layer has no reading for —
    and the honest response is to say so in the log and let the answer be shorter,
    not to redefine the requirement as whatever was available.
    """
    missing: list[str] = []
    for name in plan.required_evidence:
        if not getattr(plan, name, None):
            missing.append(name)
    return missing


def _action_state_for(status: str, view: EvidenceView,
                      verification: VerificationResult) -> str:
    if status == AgentOutcome.CONFIRMATION_REQUIRED:
        return ActionState.CONFIRMATION_REQUIRED
    if view.is_degraded:
        # Checked before the status, deliberately. The gateway already refuses to call a
        # degraded result a verified success, but this layer must not depend on that:
        # if the two ever disagree, the answer the person reads should be the cautious
        # one, and a partial view described as verified is the confident-zero failure
        # wearing the runtime's own seal of approval.
        return ActionState.DEGRADED
    if status == AgentOutcome.VERIFIED_SUCCESS:
        # A read changes nothing, so it has no action state to report. This is not a
        # technicality: ``validate_consistency`` treats any state other than
        # ``verified_success`` as grounds to reject a completion claim, so returning
        # NONE here is what stops a successful *lookup* from being allowed to say
        # "I updated that".
        if not view.is_write:
            return ActionState.NONE
        # Both halves of the canonical rule, not one of them. ``may_claim_completed``
        # requires a completed status *and* an independent read-back that verified, and
        # this line used to require only the first — which made it a second, weaker
        # definition of success sitting directly upstream of the sentence the person
        # reads. The gateway's idempotent-replay path is where the gap was reachable: it
        # returns ``verified_success`` describing an *earlier* operation while this turn
        # verified nothing, so a write whose recorded status was ``ok`` rather than
        # ``verified`` arrived here and was rendered "Done — …, and I read it back from
        # PulseSoc to confirm it". Nothing had been read back. Requiring the verification
        # state as well means this layer can only ever agree with the receipt or be
        # more cautious than it, never less.
        return (ActionState.VERIFIED_SUCCESS
                if verification.state == VerificationState.VERIFIED
                else ActionState.DEGRADED)
    if verification.state == VerificationState.FAILED:
        return ActionState.VERIFIED_FAILURE
    if status == AgentOutcome.ACCEPTED_UNVERIFIED:
        return ActionState.DEGRADED
    if status in _FAILURE_STATUSES:
        return ActionState.VERIFIED_FAILURE if view.is_write else ActionState.NONE
    return ActionState.NONE


_DIGITS = re.compile(r"\d+(?:\.\d+)?")

#: How many record titles the findings clause names before it summarises the rest.
_LISTED_TITLES = 3


def _overflow_count(view: EvidenceView) -> int:
    """How many records the findings clause will describe as "and N more".

    Named, rather than inlined at the one place it is written, because a *second* place
    has to agree with it: :func:`_allowed_numbers` must permit this number or the
    validator rejects the very clause the renderer just built. The two derivations
    living apart is what made that happen — see the regression this guards.
    """
    return max(0, view.total - min(_LISTED_TITLES, len(view.titles)))


def _allowed_numbers(view: EvidenceView, result: ToolResult,
                     verification: VerificationResult) -> frozenset[str]:
    """Every numeric token the prose is permitted to contain.

    Built by scraping digits out of the serialised evidence and adding the counts the
    renderer itself derives. It is a coarse net on purpose: the guarantee worth having
    is not "the number is meaningful" — no cheap check can establish that — but "no
    number appears in the answer that does not appear in the data", which is exactly
    the shape of an invented statistic.
    """
    tokens: set[str] = set()

    def scrape(value: Any) -> None:
        if isinstance(value, dict):
            for inner in value.values():
                scrape(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                scrape(inner)
        elif value is not None and not isinstance(value, bool):
            tokens.update(_DIGITS.findall(str(value)))

    scrape(result.data)
    scrape(result.records)
    scrape(verification.expected)
    scrape(verification.observed)
    scrape(verification.evidence)
    # Source names are quoted verbatim by the provenance and uncertainty clauses, so
    # any digits inside them are evidence the prose is entitled to contain. Omitting
    # this made the check fire on identifiers rather than on quantities: a table named
    # ``pulse_ai_v2`` put a "2" into an answer that the validator then rejected, and
    # because the rejection is silent and total, a capability whose source name
    # happened to carry a digit could not render *any* degraded answer at all.
    scrape(list(result.degraded_sources or []))
    scrape(list(view.sources))

    tokens.add(str(view.total))
    # The findings clause subtracts the titles it named from the total and prints the
    # remainder. That number is derived by the renderer and appears nowhere in the
    # evidence, so without this line the validator rejected the clause as an invented
    # statistic — and because rejection is silent and total, any read of five or more
    # records with no incidental digit anywhere in its fields lost its findings,
    # provenance, limitations and next-step clauses and answered with the bare lead.
    tokens.add(str(_overflow_count(view)))
    tokens.add(str(len(view.degraded)))
    tokens.add(str(view.kind_count))
    tokens.add(str(view.low_confidence))
    for _kind, count in view.kinds:
        tokens.add(str(count))
    for _name, value in view.metrics:
        tokens.add(_format_number(value))
        tokens.update(_DIGITS.findall(str(value)))
    return frozenset(tokens)


def _prohibited_claims(view: EvidenceView) -> list[str]:
    """Metric words this evidence cannot support, so the validator can enforce them."""
    present = " ".join(name.lower() for name, _ in view.metrics)
    present += " " + " ".join(name.lower() for name, _ in view.labels)
    return [term for term in _UNAVAILABLE_METRIC_TERMS if term not in present]


def _evidence_items(view: EvidenceView, result: ToolResult) -> list[dict[str, Any]]:
    """The supporting-evidence list, capped and stripped to declared fields."""
    items: list[dict[str, Any]] = []
    for record in (result.records or [])[:6]:
        if not isinstance(record, dict):
            continue
        items.append({
            "kind": clean(record.get("kind") or "", 60),
            "title": clean(record.get("title") or "", 120),
            "detail": clean(record.get("detail") or "", 200),
            "source": clean(record.get("source") or "", 80),
            "timestamp": clean(record.get("timestamp") or "", 40),
        })
    for name, value in view.metrics[:6]:
        items.append({"kind": "metric", "title": _humanise(name),
                      "detail": _format_number(value), "source": "", "timestamp": ""})
    return items


def _cross_domain_links(view: EvidenceView) -> list[dict[str, Any]]:
    """Relationships that are visible *in this result*, and no others.

    A link here means two kinds of record arrived together from one authorised read.
    That is a weak claim, and it is stated as one: co-occurrence, with counts. Anything
    stronger would need evidence this stage does not have.
    """
    if view.kind_count < 2:
        return []
    return [{
        "relationship": "co_occurring_in_one_authorised_read",
        "kinds": [{"kind": kind, "count": count} for kind, count in view.kinds[:4]],
        "capability_id": view.capability_id,
    }]


def _interpretations(view: EvidenceView, status: str,
                     verification: VerificationResult) -> list[str]:
    """What the evidence means, stated only where the meaning is defensible."""
    out: list[str] = []
    singular, plural = nouns_for(view)

    if view.shape == EvidenceShape.EMPTY and not view.is_degraded:
        out.append(
            f"this is an empty result from a lookup that ran, so it means you have no "
            f"{plural} rather than that the check failed"
        )
    if view.is_degraded:
        out.append(
            "a count taken from a partial read is a floor, not a total, so the real "
            "number can only be higher than what is shown"
        )
    if view.kind_count > 1:
        listed = ", ".join(
            _count_phrase(count, *_KIND_NOUNS.get(kind, (kind.replace("_", " "),
                                                         kind.replace("_", " ") + "s")))
            for kind, count in view.kinds[:3]
        )
        out.append(f"the total is not one thing: it breaks down as {listed}")
    if view.low_confidence:
        out.append(
            f"{view.low_confidence} of these came back with reduced confidence, which "
            "usually means the underlying record was incomplete rather than wrong"
        )
    if view.is_write and status == AgentOutcome.VERIFIED_SUCCESS:
        out.append(
            "this is confirmed against a fresh read of your account, not against what "
            "the change itself reported"
        )
    if verification.state == VerificationState.FAILED:
        out.append(
            "the call reported success and the follow-up read disagreed, so the "
            "safe reading is that the change did not take effect"
        )
    return out


def _uncertainties(view: EvidenceView, status: str,
                   verification: VerificationResult) -> list[str]:
    out: list[str] = []
    if view.is_degraded:
        named = ", ".join(view.degraded[:3])
        out.append(f"one or more sources could not be read for this answer ({named})")
    if view.is_write and verification.state in {VerificationState.PENDING,
                                                VerificationState.IMPOSSIBLE}:
        out.append("the change could not be independently read back")
    if view.low_confidence:
        out.append(f"{view.low_confidence} records carry reduced confidence")
    if status == AgentOutcome.RECOVERABLE_FAILURE:
        out.append("this failure looks temporary, but that is a guess about the cause")
    return out


def _limitations(view: EvidenceView, status: str) -> list[str]:
    """What this answer cannot tell the person, said plainly.

    The degraded case is first and uses the word "incomplete" deliberately: it is the
    one limitation that changes whether an empty answer should be believed at all.
    """
    out: list[str] = []
    if view.is_degraded:
        missing = len(view.degraded)
        part = "one part of" if missing == 1 else f"{missing} parts of"
        out.append(
            f"I could not reach {part} your data, so treat this as incomplete rather "
            "than as the whole answer"
        )
    # Only where a trend was a *plausible* thing to expect: one record is too few to
    # compare against anything, which is worth saying. Nothing at all is not a thin
    # sample, it is an absent one, and "there is not enough here to show a trend" after
    # "there are no notifications" adds a second sentence that says less than the
    # first. Attached to a failure it is worse than redundant — it invites the reader
    # to think a partial answer arrived when none did.
    if (not view.is_write and view.shape == EvidenceShape.SINGLE and not view.metrics
            and status not in _FAILURE_STATUSES):
        out.append("there is not enough here to show a trend")
    if status == AgentOutcome.PERMISSION_DENIED:
        out.append("this is a permissions boundary, not a temporary problem")
    return out


def _account(view: EvidenceView, spec: Any, status: str) -> tuple[list[str], list[str]]:
    """The extra interpretations and limitations an *account* owes over a recital.

    Returns ``(interpretations, limitations)``, and is spent only when
    :attr:`ResponsePlan.response_mode` is one of :attr:`ResponseMode.ACCOUNTING`. Both
    lists are frequently empty, which is the intended behaviour: an explanation of
    evidence that carries no configuration says less than one of evidence that does,
    and padding the difference with sentences that are true of everything is how a
    system starts sounding like it is explaining while explaining nothing.

    Every clause here is entailed by a declared field of :class:`EvidenceView` or by the
    capability's own registered description. Nothing is inferred about the world, and in
    particular nothing is inferred about *what the person should conclude* — that is the
    domain analysers' job, and where one exists its reading is folded in alongside this
    and reaches the reader first.

    **The generalisation trap, stated because the code silently invites it.**
    :func:`_collect_scalars` merges scalars with ``setdefault``, so for a multi-record
    result ``view.flags``, ``view.labels`` and ``view.metrics`` hold *whichever record
    was first*, not a property of the set. Saying "these are active" from that would be
    a fabrication dressed as a summary — the single most plausible way this function
    could start lying. So configuration is stated only where there is exactly one thing
    it could be about (:attr:`EvidenceShape.SINGLE` and :attr:`~EvidenceShape.STATE`),
    and where there is more than one the honest limitation is stated instead.
    """
    interpretations: list[str] = []
    limitations: list[str] = []

    if status in _FAILURE_STATUSES:
        # An account of a failure is the failure report, which the plan already builds
        # from the status and the error. Describing what the capability *would* have
        # returned reads as though something was read when nothing was.
        return interpretations, limitations

    # 1. What it does. The registry's own sentence about the capability, which is the
    #    only authoritative answer to "what is this" available at this layer and is
    #    currently spent nowhere in the prose.
    described = clean(getattr(spec, "description", "") or "", 160).rstrip(".")
    if described:
        interpretations.append(
            f"what produced this is {described[0].lower()}{described[1:]}, so it shows "
            f"what that covers and nothing outside it"
        )

    single = view.shape in {EvidenceShape.SINGLE, EvidenceShape.STATE}

    # 2. Configuration and state — the settings, not the rows. Safe only in the singular
    #    case; see the docstring.
    if single:
        on = [_humanise(name) for name, value in view.flags if value]
        off = [_humanise(name) for name, value in view.flags if not value]
        if on or off:
            parts = []
            if on:
                parts.append(f"{', '.join(on[:4])} {'is' if len(on) == 1 else 'are'} on")
            if off:
                parts.append(f"{', '.join(off[:4])} {'is' if len(off) == 1 else 'are'} off")
            interpretations.append(f"as it is configured now, {' and '.join(parts)}")
        settings = [f"{_humanise(name)} {_format_number(value)}"
                    for name, value in view.metrics[:3]]
        settings += [f"{_humanise(name)} {value}" for name, value in view.labels[:3]]
        if settings:
            interpretations.append(
                f"the values it is set to are {', '.join(settings[:4])}"
            )
    elif view.flags or view.labels or view.metrics:
        limitations.append(
            "the settings behind these are not summarised here, because they differ "
            "per record and this read returns them one record at a time"
        )

    # 3. Why results may or may not arrive. A read of stored configuration can say how
    #    something is set up and cannot say whether it has ever fired — that distinction
    #    is the substance of "explain why my alerts aren't going off", and stating it is
    #    the difference between an account and a list.
    #
    #    An earlier draft withheld this whenever ``view.metrics`` was populated, on the
    #    reasoning that activity numbers answer the question directly. That guard was
    #    wrong, and instructively so: ``metrics`` holds every number lifted off the
    #    record, and a *threshold* is a setting rather than an activity. Nothing at this
    #    layer can tell the two apart — the distinction lives in the domain, not in the
    #    type — so the guard silently suppressed the one sentence a "why isn't this
    #    firing" question needs, on exactly the capabilities most likely to be asked it.
    #    A claim that is true of every read is stated on every read.
    if not view.is_write and view.shape != EvidenceShape.EMPTY:
        limitations.append(
            "this is a read of how things are stored, so it shows how they are set up "
            "rather than whether they have fired"
        )

    # 4. Currency. The newest thing the read can see, which bounds every claim above.
    if view.newest and view.shape != EvidenceShape.EMPTY:
        interpretations.append(
            f"the most recent of these is dated {view.newest}, which is the furthest "
            f"forward this read can see"
        )

    return interpretations, limitations


def _causes(view: EvidenceView, status: str,
            verification: VerificationResult) -> list[str]:
    """Candidate causes a diagnosis may name, each one entailed by the evidence.

    Spent only for :attr:`ResponseMode.DIAGNOSIS`. The last branch is the important one
    and is not a fallback: when nothing in the reachable evidence points at a cause, the
    diagnosis says exactly that. A repair request is the strongest pull towards
    inventing an explanation — the person has already told the system something is
    broken, and agreeing with a plausible-sounding reason costs nothing and reads as
    competence. It is also the failure mode ``assert_a_cause_without_evidence`` in
    :data:`_MODE_PROHIBITIONS` is named for, so the prohibition and the honest sentence
    are written here together rather than one being declared and the other assumed.
    """
    out: list[str] = []

    if view.is_degraded:
        named = ", ".join(view.degraded[:3])
        out.append(
            f"part of the data could not be read for this answer ({named}), and that "
            f"alone is enough to explain a result that looks incomplete"
        )
    if view.shape == EvidenceShape.EMPTY and not view.is_degraded:
        out.append(
            "the lookup ran and found nothing configured, so there is nothing here that "
            "could have produced a result"
        )
    if view.shape in {EvidenceShape.SINGLE, EvidenceShape.STATE}:
        off = [_humanise(name) for name, value in view.flags if not value]
        if off:
            out.append(
                f"{', '.join(off[:3])} {'is' if len(off) == 1 else 'are'} switched off, "
                f"which would stop it doing anything"
            )
    if view.low_confidence:
        out.append(
            f"{view.low_confidence} of these records came back incomplete, so what they "
            f"describe may not be the whole setup"
        )
    if verification.state == VerificationState.FAILED:
        out.append(
            "a change was reported as applied and the follow-up read disagreed, so the "
            "safe reading is that it never took effect"
        )
    if status == AgentOutcome.PERMISSION_DENIED:
        out.append("this stopped at a permissions boundary rather than at a fault")

    if not out and status not in _FAILURE_STATUSES:
        out.append(
            "nothing in what I can read here points to a cause: what was returned looks "
            "intact, so the problem is either outside this read or in something I have "
            "not been asked to look at"
        )
    return out


def _next_steps(spec: Any, view: EvidenceView, status: str) -> list[str]:
    out: list[str] = []
    label = _section_label(getattr(spec, "native_route", "") or "")
    if status == AgentOutcome.ACCEPTED_UNVERIFIED and view.is_write:
        out.append("check the screen before relying on this")
    elif view.is_degraded:
        out.append("ask again once the missing source is available, and the count may change")
    elif status == AgentOutcome.RECOVERABLE_FAILURE:
        out.append("this is worth trying again")
    elif view.total and label and not view.is_write:
        out.append(f"open {label} in PulseSoc to work with these directly")
    return out


#: Problems :func:`validate_consistency` reports about an *answer* rather than about the
#: sentence it was handed. Degradation disclosure is a property of the finished reply —
#: one clause somewhere has to say the view is partial, and it is not this one's job —
#: so screening an individual clause against it would reject every domain clause on
#: every degraded read, which is the failure mode in reverse.
_WHOLE_ANSWER_PROBLEMS = frozenset({"degradation_not_disclosed"})


def _sayable(plan: ResponsePlan, text: str) -> bool:
    """Whether one candidate clause can stand on its own against the plan.

    The reason this exists rather than letting the final validator catch things: the
    final validator's verdict is on the *whole answer*, and its remedy is to discard the
    whole answer. Fold in one domain clause the guard dislikes and the reader loses the
    lead, the findings, the provenance and the limitations along with it — a worse
    outcome than never having had a domain reading at all.

    Screening each clause before it joins the answer makes a rejected clause cost only
    itself. It is the same rule set, applied one clause earlier, which is why it calls
    the real validator instead of restating any of it.
    """
    problems = [p for p in validate_consistency(plan, text)
                if p.split(":", 1)[0] not in _WHOLE_ANSWER_PROBLEMS]
    if problems:
        logger.warning("undx_domain_clause_rejected capability=%s problems=%s",
                       plan.capability_id, ";".join(problems)[:120])
    return not problems


def _fold_reading(plan: ResponsePlan, reading: DomainReading) -> None:
    """Merge a domain reading into the plan, in place.

    Numbers first, and that ordering is load-bearing: every screening call below runs
    ``validate_consistency``, which rejects any digit outside ``allowed_numbers``, so a
    reading whose numbers had not yet been admitted would screen out every clause it
    just derived from the evidence.

    Domain material goes *ahead* of the shape-based material in each list because it is
    strictly more specific — "one restriction is limiting your account" and "you have
    four account health items" are both true, and only one of them is an answer. It goes
    ahead of it and never instead of it: nothing here removes a shape-based clause, so
    the domain layer can only ever add to what the evidence already supported.
    """
    if not reading:
        return
    plan.allowed_numbers = frozenset(plan.allowed_numbers) | frozenset(reading.numbers)

    # First writer wins. Two readings can reach this — the single-domain one and the
    # cross-domain one — and the single-domain reading is folded first because it is the
    # more specific of the two: "one restriction is limiting your account" says more than
    # "this is mostly one thing". Letting the second overwrite the first would make the
    # broader statement displace the narrower one, which is the wrong direction, and the
    # displaced clause would vanish rather than move, because the assessment slot holds
    # exactly one sentence.
    if reading.assessment and not plan.domain_assessment and _sayable(plan, reading.assessment):
        plan.domain_assessment = reading.assessment

    interpretations = [t for t in reading.interpretations if _sayable(plan, t)]
    if reading.attention:
        # Rendered as one clause rather than as several, because these are record titles
        # and a list of bare titles is not a sentence. Named at all — rather than
        # counted — because "Marketplace paused" tells someone what to do next and "two
        # items need attention" does not.
        named = "; ".join(reading.attention[:3])
        clause = f"the ones that need you are {named}"
        if _sayable(plan, clause):
            interpretations.append(clause)
    plan.interpretations = interpretations + list(plan.interpretations)

    plan.uncertainties = list(plan.uncertainties) + [
        t for t in reading.uncertainties if _sayable(plan, t)]
    plan.recommended_next_steps = [
        t for t in reading.next_steps if _sayable(plan, t)
    ] + list(plan.recommended_next_steps)


def build_plan(spec: Any, status: str, result: ToolResult,
               verification: VerificationResult, *, question: str = "",
               history: Sequence[str] = (),
               goal_shape: str = GoalShape.UNKNOWN) -> ResponsePlan:
    """Derive the plan from the evidence. Deterministic, and never from message text.

    ``question`` influences only *how much* is said and *what kind* of answer it is.
    It has no route to the facts: every claim in the plan is lifted from ``result`` or
    ``verification``, so a hostile question can make an answer longer but cannot make
    it say something untrue.

    ``goal_shape`` carries the same restriction and one addition. It can raise the
    detail floor and change which clauses are spent, because an explanation that omits
    what the result means is not an explanation. It cannot add a fact, reach the
    evidence, or lower a floor the evidence has raised — an answer over a degraded read
    still says so, whatever the person was trying to accomplish.
    """
    view = build_view(spec, result)
    follow_up = is_follow_up(question, history)
    shape = GoalShape.read(goal_shape)
    response_type = select_response_type(spec, status, question, view)
    mode = select_response_mode(shape, response_type, view)
    detail = select_detail_level(question, view, status, follow_up=follow_up)
    if mode in ResponseMode.ACCOUNTING:
        # An account of something is longer than a recital of it, and the person asking
        # for one has said so. This is a floor, not an override: ``at_least`` never
        # lowers a level the question or the evidence already raised.
        detail = DetailLevel.at_least(detail, DetailLevel.DETAILED)
    plan = ResponsePlan(
        response_type=response_type,
        goal_shape=shape,
        response_mode=mode,
        required_evidence=required_evidence_for(mode),
        must_not_do=must_not_do_for(mode, view, status),
        detail_level=detail,
        user_goal=clean(question, 200) or clean(getattr(spec, "description", ""), 200),
        evidence=_evidence_items(view, result),
        cross_domain_links=_cross_domain_links(view),
        interpretations=_interpretations(view, status, verification),
        uncertainties=_uncertainties(view, status, verification),
        limitations=_limitations(view, status),
        recommended_next_steps=_next_steps(spec, view, status),
        action_state=_action_state_for(status, view, verification),
        native_cards=[clean(getattr(spec, "result_card", "") or "", 60)] if getattr(
            spec, "result_card", "") else [],
        prohibited_claims=_prohibited_claims(view),
        capability_id=view.capability_id,
        allowed_numbers=_allowed_numbers(view, result, verification),
        view=view,
        is_follow_up=follow_up,
    )
    # Folded before the lead is chosen, so that a domain that has something to say is
    # already visible to :func:`_lead_forms` and to the validator screening it. Folding
    # afterwards would let the plan's own published answer be settled against evidence
    # the plan had not finished assembling.
    #
    # A read only. Every analyser registered today reads, and that is not a coincidence
    # to be relied on later: a write's answer is settled by the read-back, and a domain
    # narrating a mutation would be describing state it did not confirm.
    if not view.is_write:
        _fold_reading(plan, build_reading(view.capability_id, result))
        # Second, and never instead. The two readings answer different questions — one
        # about a domain, one about the relationship between domains — and a result can
        # legitimately have both. Order matters only for the assessment slot, which
        # holds one sentence and is claimed by whichever reading gets there first; see
        # :func:`_fold_reading`.
        _fold_reading(plan, build_cross_reading(view.capability_id, result))

    # The account, added last and only for the modes that owe one. Appended rather than
    # prepended: where a domain analyser has a reading, its sentence is the better
    # answer and must stay first — this is the floor beneath it, not a replacement for
    # it. Without this, an explanation of a capability no analyser covers came back as
    # the list plus a provenance line, which is a recital with a citation attached.
    if plan.response_mode in ResponseMode.ACCOUNTING:
        extra_meaning, extra_limits = _account(view, spec, status)
        plan.interpretations = plan.interpretations + [
            note for note in extra_meaning if _sayable(plan, note)]
        plan.limitations = plan.limitations + [
            note for note in extra_limits if _sayable(plan, note)]

    # A diagnosis owes candidate causes before it owes anything else. Prepended, because
    # unlike the account these *are* the answer to what was asked, and an answer that
    # opens with what the capability does and reaches the possible cause fourth has
    # ordered itself by what was easiest to say.
    if plan.response_mode == ResponseMode.DIAGNOSIS:
        causes = [note for note in _causes(view, status, verification)
                  if _sayable(plan, note)]
        plan.interpretations = causes + plan.interpretations

    # Asking for one thing and being handed several is the ambiguity the resource mode
    # exists to notice. Said as a limitation rather than a finding because it is a fact
    # about the *answer* — that it did not narrow to what was asked for — and the reader
    # needs it whether or not the list that follows is interesting.
    if plan.response_mode == ResponseMode.RESOURCE and view.shape in {
            EvidenceShape.FEW, EvidenceShape.MANY}:
        note = (f"this did not narrow to a single one — {_count_phrase(view.total, *nouns_for(view))} "
                f"match, so say which you mean if you want just one")
        if _sayable(plan, note):
            plan.limitations = plan.limitations + [note]

    # Checked after the fold and the account, because both are where an explanation's
    # interpretations actually come from. Logged rather than raised: a shortfall means
    # the evidence genuinely could not support what the mode owes, and the person asking
    # still gets the truthful shorter answer rather than an error.
    shortfall = unmet_evidence(plan)
    if shortfall:
        logger.info("undx_response_unmet_evidence capability=%s mode=%s missing=%s",
                    plan.capability_id, plan.response_mode, ",".join(shortfall))

    # Validated before it is stored, and not only because :func:`render` uses it as a
    # fallback. ``to_dict`` publishes ``direct_answer`` into the receipt the gateway
    # writes, so it leaves this module on a second path that never passes through
    # :func:`render` at all — any client that displays the plan's answer instead of the
    # rendered explanation was reading prose the consistency guard had never seen. The
    # guard exists to be unavoidable; a field that skips it is a hole in it.
    for candidate in _lead_forms(plan, spec, status, result, verification):
        lead = _sentence_case(clean(candidate, MAX_EXPLANATION_CHARS).rstrip("."))
        if not lead:
            continue
        sentence = f"{lead}."
        if not validate_consistency(plan, sentence):
            plan.direct_answer = sentence
            return plan
    plan.direct_answer = _last_resort(plan)
    return plan


# ---------------------------------------------------------------------------
# Rendering
#
# Every clause below is a statement about the evidence. The variants are not
# rewordings of one another: each leads with a different verified fact, which is why
# the repetition guard is allowed to swap between them without changing what is true.
# ---------------------------------------------------------------------------


def _subject_of(verification: VerificationResult) -> str:
    """What the read-back says it read, named the way the card named it, or "".

    Published by the verifier — see ``crypto_alert_status`` — and never composed here.
    The distinction is the whole point: a subject built in the prose layer could only
    draw on the request, and the sentence exists to report what *moved*, not what was
    asked for. Those are the same string until the turn where they are not, and that
    is the turn a person needs the sentence to be right.
    """
    evidence = getattr(verification, "evidence", None)
    if not isinstance(evidence, dict):
        return ""
    return clean(evidence.get("subject") or "", 160)


def _write_state_sentence(spec: Any, result: ToolResult,
                          verification: VerificationResult) -> str:
    """Describe a completed write from what the read-back observed.

    The subject clause is not decoration. Every branch below used to end in a state
    with no thing attached to it — "the current value is paused" — and that sentence
    was demonstrated on an iPhone 17 Pro Max against a real backend one screen after
    a confirmation card that had correctly said "BTC alert · above · 999,999". A
    person holding several alerts could read the receipt and still not know which one
    UNDX had touched. Naming the subject is what makes the receipt checkable, and a
    receipt that cannot be checked is not a receipt.

    It degrades to the old wording rather than inventing one. A verifier that
    publishes no subject has not withheld a name it knew; it did not read a record
    that carries one, and guessing at that point would be the prose layer asserting
    something no read-back supports.
    """
    observed = verification.observed
    fields = tuple(getattr(spec, "verified_fields", ()) or ())
    subject = _subject_of(verification)

    def value_for(name: str) -> Any:
        if isinstance(observed, dict) and name in observed:
            return observed[name]
        data = result.data or {}
        return data.get(name)

    for name in fields:
        value = value_for(name)
        if name in _FIELD_STATE and isinstance(value, bool):
            true_phrase, false_phrase = _FIELD_STATE[name]
            return true_phrase if value else false_phrase
        if isinstance(value, bool):
            return f"{_humanise(name)} is {'on' if value else 'off'}"
        if value not in (None, ""):
            return f"{_humanise(name)} is now {clean(value, 60)}"
    if isinstance(observed, bool):
        if subject:
            return f"{subject} is {'on' if observed else 'off'}"
        return "that setting is on" if observed else "that setting is off"
    if observed not in (None, "", {}):
        if subject:
            return f"{subject} is now {clean(observed, 60)}"
        return f"the current value is {clean(observed, 60)}"
    if subject:
        return f"{subject} matches what you asked for"
    return "the new state matches what you asked for"


def _lead_forms(plan: ResponsePlan, spec: Any, status: str, result: ToolResult,
                verification: VerificationResult) -> list[str]:
    """Distinct factual framings of the same evidence, best first.

    Each entry leads with a different verified fact — the result, its provenance, its
    most recent member, its breakdown, its measured value. The list is short when the
    evidence is thin, which is correct: there is only so much that is true about an
    empty table, and the alternative to repeating oneself is not to invent a second
    thing to say.
    """
    view = plan.view or build_view(spec, result)
    singular, plural = nouns_for(view)
    forms: list[str] = []

    # --- failures ---------------------------------------------------------
    if status in {AgentOutcome.TERMINAL_FAILURE, AgentOutcome.RECOVERABLE_FAILURE}:
        if verification.state == VerificationState.FAILED:
            return [
                "The action reported success but the verified state does not match, "
                "so I have not marked it as done",
                "I checked your account after the change and it does not show the new "
                "state, so I am treating this as not done",
            ]
        message = clean(result.error_message or "", 220)
        default = ("That did not go through" if status == AgentOutcome.RECOVERABLE_FAILURE
                   else "That did not work")
        # Stripped once, and the *stripped* value is what everything downstream tests.
        # A service that reports an error of ``"..."`` is not hypothetical — punctuation
        # survives message templating far more often than words do — and stripping it
        # leaves the empty string, which the previous form indexed at ``[0]`` after
        # checking the length of the *unstripped* text. That raised ``IndexError`` from
        # inside the failure path, the one path that exists to explain a failure calmly.
        base = clean(message, 220).rstrip(".").strip() or default
        forms = [base]
        if len(base) > 1:
            forms.append(f"This did not complete: {base[0].lower()}{base[1:]}")
        return forms

    # Both of these forward a service's message, and both must survive one arriving as
    # punctuation. ``"...".rstrip(".")`` is the empty string, an empty lead assembles to
    # an empty sentence, no candidate survives, and the turn falls through to the
    # last-resort line — so a refusal that had a perfectly good reason to give was
    # replaced by "I could not put this into words I can stand behind". The ``or`` has
    # to sit *after* the stripping, not before it, for the default to do its job.
    if status == AgentOutcome.PERMISSION_DENIED:
        message = clean(result.error_message or "", 220).rstrip(".").strip()
        return [message or "You do not have access to that"]
    if status == AgentOutcome.UNSUPPORTED_CAPABILITY:
        message = clean(result.error_message or "", 220).rstrip(".").strip()
        return [message or "UNDX cannot do that"]

    # --- writes -----------------------------------------------------------
    if view.is_write:
        state = _write_state_sentence(spec, result, verification)
        # Keyed off the plan's own action state, not off ``status``. The two can differ,
        # and where they differ it is always in the same direction: the gateway does not
        # consider ``degraded_sources`` when settling the status of a *write*, so a
        # mutation whose confirming read was partial arrives here labelled
        # ``verified_success`` while :func:`_action_state_for` has already — correctly —
        # written ``degraded`` into the receipt. Reading ``status`` here produced the
        # worst of both: a receipt saying degraded above a sentence saying "I read it
        # back from PulseSoc to confirm it". The plan is the single source of truth for
        # what this turn is allowed to claim, and this is one of the claims that matters.
        if plan.action_state == ActionState.VERIFIED_SUCCESS:
            forms.append(f"Done — {state}, and I read it back from PulseSoc to confirm it")
            forms.append(f"I confirmed this against your account after the change: {state}")
            if verification.detail:
                forms.append(
                    f"The change went through and the follow-up read agrees: {state}")
            return forms
        forms.append(
            "PulseSoc accepted the change, but I could not read it back to confirm it")
        forms.append(
            "The change was accepted and then my confirming read did not come back, so "
            "I cannot tell you it is done")
        return forms

    # --- reads ------------------------------------------------------------
    if view.shape == EvidenceShape.EMPTY:
        forms.append(f"There are no {plural} on your account right now")
        if view.sources:
            forms.append(
                f"I checked {view.sources[0]} for your account and found no {plural}")
        forms.append(f"Nothing is on record under {plural} for your account")
        return forms

    if view.shape == EvidenceShape.STATE:
        if view.labels:
            named = ", ".join(f"{_humanise(k)} is {v}" for k, v in view.labels[:3])
            forms.append(f"Your current settings: {named}")
            forms.append(f"{_sentence_case(named)} — that is what your account is set to now")
        if view.flags:
            on = [_humanise(k) for k, v in view.flags if v]
            off = [_humanise(k) for k, v in view.flags if not v]
            parts = []
            if on:
                parts.append(f"{', '.join(on[:3])} {'is' if len(on) == 1 else 'are'} on")
            if off:
                parts.append(f"{', '.join(off[:3])} {'is' if len(off) == 1 else 'are'} off")
            if parts:
                forms.append(_sentence_case(" and ".join(parts)))
        if view.metrics:
            named = ", ".join(f"{_humanise(k)} is {_format_number(v)}"
                              for k, v in view.metrics[:3])
            forms.append(f"On the figures your account holds, {named}")
        if not forms:
            forms.append("Your account holds current values for this, and nothing more")
        return forms

    count = _count_phrase(view.total, singular, plural)
    forms.append(f"You have {count}")

    if view.titles:
        newest_clause = f" from {view.newest}" if view.newest else ""
        if view.total == 1:
            forms.append(f"There is one: {view.titles[0]}{newest_clause}")
        else:
            forms.append(
                f"The most recent is {view.titles[0]}{newest_clause}, and there are "
                f"{count} in total")

    if view.kind_count > 1:
        breakdown = ", ".join(
            _count_phrase(c, *_KIND_NOUNS.get(k, (k.replace('_', ' '),
                                                  k.replace('_', ' ') + 's')))
            for k, c in view.kinds[:3]
        )
        forms.append(f"That is {count}, made up of {breakdown}")

    if view.metrics:
        named = ", ".join(f"{_humanise(k)} at {_format_number(v)}"
                          for k, v in view.metrics[:2])
        forms.append(f"Across {count} the figures are {named}")

    if view.oldest and view.newest and view.oldest != view.newest:
        forms.append(f"Between {view.oldest} and {view.newest} there are {count}")

    return forms


def _clauses(plan: ResponsePlan, lead: str) -> list[tuple[str, str]]:
    """The full clause set for this plan, tagged so variants can reorder them."""
    view = plan.view
    level = plan.detail_level
    rank = DetailLevel.rank(level)
    # Cased here rather than in each branch of :func:`_lead_forms`, because one branch
    # does not compose its lead at all — it forwards a service's error message, which
    # arrives in whatever case the service wrote it and opened the answer in lowercase.
    out: list[tuple[str, str]] = [("lead", _sentence_case(lead.rstrip(".")))]

    # Placed at STANDARD rather than at DETAILED, because this is not colour on top of
    # the answer — for the domains that have an analyser it usually *is* the answer.
    # "You have three account health items" at standard detail, with "one restriction is
    # limiting your account" held back for a longer reply, would be a system that knows
    # the answer and gives it only when asked at length.
    if rank >= DetailLevel.rank(DetailLevel.STANDARD) and plan.domain_assessment:
        out.append(("domain", _sentence_case(plan.domain_assessment.rstrip("."))))

    if rank >= DetailLevel.rank(DetailLevel.STANDARD) and view is not None:
        if view.titles and view.total > 1 and plan.response_type != ResponseType.ACTION_RECEIPT:
            listed = "; ".join(view.titles[:_LISTED_TITLES])
            more = _overflow_count(view)
            tail = f", and {more} more" if more > 0 else ""
            out.append(("findings", f"The most recent are {listed}{tail}"))
        elif view.details and plan.response_type != ResponseType.ACTION_RECEIPT:
            out.append(("findings", view.details[0].rstrip(".")))

    if rank >= DetailLevel.rank(DetailLevel.DETAILED):
        if plan.evidence:
            sourced = sorted({item["source"] for item in plan.evidence if item.get("source")})
            if sourced:
                out.append(("evidence",
                            f"This comes from {', '.join(sourced[:3])} in your own account"))
        if plan.interpretations:
            out.append(("meaning", _sentence_case(plan.interpretations[0].rstrip("."))))
        if plan.cross_domain_links and view is not None and view.kind_count > 1:
            kinds = ", ".join(k for k, _ in view.kinds[:3]).replace("_", " ")
            out.append(("links",
                        f"These arrived together from one authorised read, covering {kinds}"))

    # What an account owes over and above a recital, spent at whatever detail level the
    # turn ended up at. These are not extra colour: they are the difference between
    # "here are your four alerts" and an answer to the question that was actually asked.
    # Guarded on the mode rather than on the level so that "explain this quickly" still
    # explains — brevity may shorten an account, it may not turn it back into a list.
    if plan.response_mode in ResponseMode.ACCOUNTING and view is not None:
        # State and configuration. ``details`` is where a settings-shaped result puts
        # its current values, and it is skipped by the findings clause above whenever
        # there are titles to list — which is exactly the case where an explanation
        # needs it and a list does not.
        if view.details and view.titles:
            out.append(("state", view.details[0].rstrip(".")))
        for note in plan.uncertainties[:1]:
            out.append(("uncertainty", f"Worth knowing: {note.rstrip('.')}"))

    if plan.response_mode == ResponseMode.DIAGNOSIS:
        # A diagnosis that names no next step has not diagnosed anything the person can
        # use. Two rather than the usual one, and spent at any detail level.
        for note in plan.recommended_next_steps[:2]:
            out.append(("next", _sentence_case(note.rstrip("."))))

    if rank >= DetailLevel.rank(DetailLevel.EXPERT):
        for note in plan.interpretations[1:3]:
            out.append(("meaning", _sentence_case(note.rstrip("."))))
        for note in plan.uncertainties[:2]:
            out.append(("uncertainty", f"Worth knowing: {note.rstrip('.')}"))

    # Limitations are never dropped, at any detail level. A brief answer that omits
    # the reason it might be wrong is not brief, it is misleading.
    for note in plan.limitations[:2]:
        out.append(("limits", _sentence_case(note.rstrip("."))))

    # Skipped for a diagnosis, which has already spent two of these above. Without the
    # guard the first step is emitted twice and the answer repeats itself, which is a
    # peculiarly bad look for the mode whose whole job is to say what to do next.
    if rank >= DetailLevel.rank(DetailLevel.STANDARD) \
            and plan.response_mode != ResponseMode.DIAGNOSIS:
        for note in plan.recommended_next_steps[:1]:
            out.append(("next", _sentence_case(note.rstrip("."))))

    return out


#: Orderings used when the first rendering collides with something recently said.
#: These change which fact the reader meets first, which is a real difference in
#: emphasis rather than a change of costume.
#:
#: Every tag :func:`_clauses` can emit appears in every ordering. ``_assemble`` does
#: append unlisted tags rather than dropping them, but it appends them last and in
#: arbitrary order, so a tag that is missing here still reaches the reader — just at
#: the end of every sentence, whichever ordering was chosen. That is a silent
#: degradation rather than a visible one, which is why "state" is listed rather than
#: left to the fallback.
_ORDERS: tuple[tuple[str, ...], ...] = (
    ("lead", "domain", "findings", "state", "evidence", "meaning", "links", "uncertainty", "limits", "next"),
    ("limits", "lead", "domain", "findings", "state", "meaning", "evidence", "links", "uncertainty", "next"),
    ("domain", "lead", "limits", "meaning", "findings", "state", "evidence", "links", "uncertainty", "next"),
    ("findings", "state", "domain", "lead", "links", "meaning", "evidence", "uncertainty", "limits", "next"),
)


def _assemble(clauses: list[tuple[str, str]], order: tuple[str, ...]) -> str:
    buckets: dict[str, list[str]] = {}
    for tag, text in clauses:
        buckets.setdefault(tag, []).append(text)
    ordered: list[str] = []
    for tag in order:
        ordered.extend(buckets.get(tag, []))
    for tag, texts in buckets.items():
        if tag not in order:
            ordered.extend(texts)
    sentences = [s.strip() for s in ordered if s and s.strip()]
    if not sentences:
        return ""
    body = ". ".join(s.rstrip(".") for s in sentences)
    return clean(f"{body}.", MAX_EXPLANATION_CHARS)


# ---------------------------------------------------------------------------
# Repetition
# ---------------------------------------------------------------------------


def _normalise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", clean(text, MAX_EXPLANATION_CHARS).lower())


def _ngrams(words: Sequence[str], size: int = 3) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


#: Threshold for the *word-set* detector, which catches the case n-grams miss entirely:
#: the same sentences delivered in a different order. Every 3-gram changes when clauses
#: are permuted, so a reordering scores near zero on overlap while reading as a verbatim
#: repeat. Set high on purpose — two honest answers about the same four records share
#: most of their vocabulary and must not be flagged for it, so only a near-total match
#: of the words themselves counts.
_WORD_SET_THRESHOLD = 0.82


def _structure(text: str) -> tuple[tuple[str, int], ...]:
    """A sentence-shape fingerprint: first word and length bucket of each sentence.

    Two answers with the same opening word in every sentence and the same rhythm read
    as one answer even when the nouns differ, which is the failure the naive
    "did we send this exact string" check misses entirely.
    """
    out: list[tuple[str, int]] = []
    for sentence in re.split(r"[.!?]+", clean(text, MAX_EXPLANATION_CHARS)):
        words = _normalise(sentence)
        if not words:
            continue
        out.append((words[0], min(len(words) // 5, 6)))
    return tuple(out)


def detect_repetition(candidate: str, history: Sequence[str] = ()) -> str:
    """Name the way ``candidate`` repeats something recent, or return "".

    Several detectors rather than one, because repetition is several different
    things: the same opening, the same landing, the same words in a different order,
    and the same rhythm. A conversation can feel canned through any of them alone.
    """
    text = clean(candidate, MAX_EXPLANATION_CHARS)
    if not text:
        return ""
    recent = [clean(h, MAX_EXPLANATION_CHARS) for h in list(history)[-HISTORY_WINDOW:]]
    recent = [h for h in recent if h]
    if not recent:
        return ""

    words = _normalise(text)
    opening = tuple(words[:6])
    closing = tuple(words[-6:])
    grams = _ngrams(words)
    shape = _structure(text)

    for previous in recent:
        if text.lower() == previous.lower():
            return "identical_response"
        prior_words = _normalise(previous)
        if opening and len(opening) >= 4 and opening == tuple(prior_words[:6]):
            return "repeated_opening"
        if closing and len(closing) >= 4 and closing == tuple(prior_words[-6:]):
            return "repeated_closing"
        if _jaccard(grams, _ngrams(prior_words)) >= 0.55:
            return "ngram_overlap"
        if len(words) >= 8 and _jaccard(set(words), set(prior_words)) >= _WORD_SET_THRESHOLD:
            return "reordered_repeat"
        prior_shape = _structure(previous)
        if len(shape) >= 3 and shape == prior_shape:
            return "repeated_structure"
    return ""


def _repetition_score(candidate: str, history: Sequence[str]) -> float:
    """How much ``candidate`` resembles the most similar thing recently said, 0 to 1.

    :func:`detect_repetition` answers "is this a repeat", which is the right question
    when deciding whether to look for another framing and the wrong one when every
    framing has already been rejected. At that point the choice is between several
    true sentences that all resemble something, and picking the *first* means picking
    the one the previous turn was built from — the worst available option, chosen by
    accident of iteration order.

    Blended rather than maximised, and that detail carries the whole weight of the
    function. Word-set overlap is 1.0 for any reordering — the words are the same
    words — so taking the maximum scores a verbatim repeat and a genuine
    restructuring identically, and the tie falls back to iteration order, which puts
    the verbatim repeat first. Averaging the two measures lets the n-gram term, which
    *does* fall when clauses move, break that tie in favour of the answer that reads
    differently.
    """
    words = _normalise(candidate)
    if not words:
        return 1.0
    grams = _ngrams(words)
    worst = 0.0
    for previous in list(history)[-HISTORY_WINDOW:]:
        prior = _normalise(previous)
        if not prior:
            continue
        if candidate.strip().lower() == previous.strip().lower():
            return 1.0
        blended = (_jaccard(grams, _ngrams(prior)) + _jaccard(set(words), set(prior))) / 2
        worst = max(worst, blended)
    return worst


# ---------------------------------------------------------------------------
# Factual consistency
# ---------------------------------------------------------------------------


def draft_quality_issues(text: str) -> list[str]:
    """Filler that makes a drafted message read as machine-written.

    Applied to message drafts, where the words go out under the person's name. None of
    these phrases are wrong; they are simply what a reply says when it has nothing to
    say, and a draft that opens with one has already told the recipient it was not
    written for them.
    """
    lowered = clean(text, 4000).lower()
    return [phrase for phrase in _DRAFT_FILLER_PHRASES if phrase in lowered]


def _quoted_spans(plan: ResponsePlan, body: str) -> list[tuple[int, int]]:
    """Where in ``body`` the answer is repeating a name that came out of the evidence.

    The metric guard exists to stop UNDX asserting a figure it was never given. Naming a
    record is not that assertion: a support ticket called "Payout missing" is the user's
    own title, read out of PulseSoc and handed straight back, and the answer that
    contains it has claimed nothing about payouts at all.

    Without this, the guard fired on the record's *name*, and because a failed
    validation discards the whole string rather than the offending clause, one such
    title collapsed the entire reply to its opening sentence — every finding, every
    source line, every limitation and every next step gone, on a read where nothing was
    wrong. The vocabulary is twenty ordinary English words, so the titles that trigger
    it are ordinary too: "Payout missing", "Revenue Team", "Conversion Workshop", "How
    to grow your reach".

    Only exact, verbatim occurrences of an evidence string count. A span is a licence to
    ignore a guard, so it is granted solely where the answer is demonstrably echoing
    text the evidence supplied, never where it merely resembles it.
    """
    view = plan.view
    if view is None:
        return []
    spans: list[tuple[int, int]] = []
    for source in (view.titles, view.details):
        for value in source:
            name = (value or "").strip()
            if len(name) < 3:
                continue
            start = body.find(name)
            while start != -1:
                spans.append((start, start + len(name)))
                start = body.find(name, start + 1)
    return spans


def validate_consistency(plan: ResponsePlan, text: str) -> list[str]:
    """Re-read the rendered answer and list every way it outruns the plan.

    This runs on output, not on inputs, and that ordering is the point. Building a
    sentence from evidence makes an unsupported claim unlikely; checking the finished
    sentence is what makes it detectable. A non-empty return means the string is
    discarded, never softened.
    """
    problems: list[str] = []
    body = clean(text, MAX_EXPLANATION_CHARS)
    if not body:
        return ["empty_response"]

    if plan.action_state != ActionState.VERIFIED_SUCCESS:
        claim = completion_claim(body)
        if claim:
            problems.append(f"unverified_completion_claim:{claim}")

    if plan.response_type == ResponseType.DRAFT or plan.capability_id.endswith(".draft"):
        for pattern in _SENT_CLAIM_PATTERNS:
            if pattern.search(body):
                problems.append("draft_claimed_as_sent")
                break

    view = plan.view
    if view is not None and view.is_degraded:
        for pattern in _COMPLETENESS_CLAIM_PATTERNS:
            if pattern.search(body):
                problems.append("completeness_claim_while_degraded")
                break
        if "incomplete" not in body.lower():
            problems.append("degradation_not_disclosed")

    quoted = _quoted_spans(plan, body)
    for term in plan.prohibited_claims:
        pattern = _METRIC_TERM_PATTERNS.get(term)
        if pattern is None:
            continue
        for match in pattern.finditer(body):
            if _METRIC_VERB_GUARD.search(body[:match.start()]):
                continue  # the verb, not the metric
            if any(start <= match.start() and match.end() <= end
                   for start, end in quoted):
                continue  # the record's name, not a metric UNDX is claiming
            problems.append(f"unavailable_metric:{term}")
            break

    unknown = sorted(set(_DIGITS.findall(body)) - set(plan.allowed_numbers))
    if unknown:
        problems.append(f"unsupported_numbers:{','.join(unknown[:5])}")

    return problems


def _carries(body: str, fragment: str) -> bool:
    """Whether ``body`` actually says ``fragment``, ignoring case and punctuation.

    A word-run test rather than a substring one, because the clause builders sentence-
    case their input and strip its trailing stop, so the fragment as the plan holds it
    is never byte-identical to the fragment as the reader meets it.
    """
    words = _normalise(fragment)
    if not words:
        return False
    run = words[:6]
    hay = _normalise(body)
    span = len(run)
    return any(hay[i:i + span] == run for i in range(max(0, len(hay) - span + 1)))


def accounting_shortfall(plan: ResponsePlan, text: str) -> str:
    """Why this rendering is a recital where an account was asked for, or "".

    Deliberately not part of :func:`validate_consistency`. That function is a veto on
    *untruth* and it also screens the plan's own one-sentence ``direct_answer``, which
    is a lead by construction and would fail a completeness test every single time. A
    lead that is only a lead is not a lie; it is a summary. So this is a separate,
    weaker judgement: :func:`render` uses it to *prefer* a fuller candidate, and falls
    back to a thin one when the plan genuinely had nothing more to say.
    """
    if plan.response_mode not in ResponseMode.ACCOUNTING:
        return ""
    owed = [note for note in (
        plan.domain_assessment,
        plan.interpretations[0] if plan.interpretations else "",
        plan.limitations[0] if plan.limitations else "",
        plan.recommended_next_steps[0] if (
            plan.response_mode == ResponseMode.DIAGNOSIS and plan.recommended_next_steps
        ) else "",
    ) if note]
    if not owed:
        # Nothing to withhold. The shortfall is in the analysers, not in this sentence,
        # and :func:`unmet_evidence` has already logged it.
        return ""
    if any(_carries(text, note) for note in owed):
        return ""
    return f"{plan.response_mode}_without_an_account"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _last_resort(plan: ResponsePlan) -> str:
    """The sentence used when every framing, including the plan's own, was rejected.

    Assembled from the plan rather than written out as a constant, because a constant
    cannot be true of every plan and the previous one was not true of two large classes
    of them. It offered "here are the records themselves" on an empty result, where
    there are no records to offer; and it carried no degradation disclosure, so on a
    degraded read — the one case where a person most needs to be told the view is
    partial — the single string that :func:`render` could reach without passing the
    validator was also the single string that never said so. The guard cannot veto its
    own escape hatch, which is exactly why the escape hatch has to be built to pass it.

    Claim-free by construction: no digits, no completion verb, no metric vocabulary,
    and the degradation clause appears precisely when the view reports one.
    """
    view = plan.view
    parts = ["I could not put this into words I can stand behind"]
    if view is not None and not view.is_write and view.shape != EvidenceShape.EMPTY:
        parts.append("the underlying records are below exactly as they came back")
    if view is not None and view.is_degraded:
        parts.append("part of your data could not be read, so treat this as incomplete")
    return _sentence_case(", and ".join(parts).rstrip(".")) + "."


def render(plan: ResponsePlan, spec: Any, status: str, result: ToolResult,
           verification: VerificationResult, *,
           history: Sequence[str] = (),
           attempts: int = MAX_RENDER_ATTEMPTS) -> str:
    """Write the answer described by ``plan``.

    The search order is deliberate: try every *factual framing* first, then every
    *ordering* of the clauses. Both change what the reader meets first; neither
    changes what is claimed. A candidate that fails :func:`validate_consistency` is
    dropped outright — the repetition guard is a preference, the consistency guard is
    a veto, and they are never allowed to trade against each other.

    ``attempts`` may lower :data:`MAX_RENDER_ATTEMPTS` and may not raise it. It was
    previously read as ``max(1, int(attempts))``, which clamps the floor and leaves the
    ceiling open, so the constant documented as the thing that makes a repetitive turn
    terminate was in fact only a default value a caller could step over. The blast
    radius was small — the search space is twenty drafts and every extra candidate has
    already passed the validator — but a ceiling a caller can raise is not a ceiling,
    and this one is named in its own docstring as one.
    """
    leads = _lead_forms(plan, spec, status, result, verification) or [plan.direct_answer]
    candidates: list[str] = []
    seen: set[str] = set()
    rejected = 0
    attempt_budget = max(1, min(int(attempts), MAX_RENDER_ATTEMPTS))
    regeneration_budget = _max_regenerations()
    for lead in leads:
        if rejected >= regeneration_budget:
            break
        for order in _ORDERS:
            if rejected >= regeneration_budget:
                # ``UNDX_RESPONSE_MAX_REGENERATIONS`` spent. Stop rendering and let the
                # fallback below answer with the honest boundary, which is what the flag
                # says it is for. The budget counts *rejected* drafts rather than drafts
                # built: a permutation that collapsed onto a string already seen cost
                # nothing and claimed nothing, so charging it here would exhaust the
                # budget on bookkeeping and silence answers that were never unsafe.
                #
                # ``>=`` rather than ``>``. The first spelling permitted N+1 rejected
                # drafts for a budget of N, and permitted one for a budget of zero —
                # which is the single value an operator would set precisely to mean
                # "do not regenerate at all". A ceiling that is exceeded by exactly one
                # is still exceeded, and the off-by-one lived in the value most likely
                # to have been chosen deliberately.
                logger.info("undx_response_regeneration_budget_spent capability=%s "
                            "rejected=%s budget=%s",
                            plan.capability_id, rejected, regeneration_budget)
                break
            text = _assemble(_clauses(plan, lead), order)
            if not text:
                continue
            if text in seen:
                # A permutation that produced the same string is not another attempt.
                # Most plans carry only two or three clause tags, so several orderings
                # collapse onto one sentence; counting those against the attempt budget
                # exhausted it before the later — and more different — factual framings
                # were ever built.
                continue
            problems = validate_consistency(plan, text)
            if problems:
                rejected += 1
                logger.warning(
                    "undx_response_rejected capability=%s problems=%s",
                    plan.capability_id, ";".join(problems)[:200],
                )
                continue
            seen.add(text)
            candidates.append(text)
            if len(candidates) >= attempt_budget:
                break
        if len(candidates) >= attempt_budget:
            break

    if not candidates:
        # Nothing survived validation. Fall back to the plan's own direct answer, and
        # if even that is unsupportable, to a sentence that claims nothing at all.
        fallback = clean(plan.direct_answer, MAX_EXPLANATION_CHARS)
        fallback = f"{fallback.rstrip('.')}." if fallback else ""
        if fallback and not validate_consistency(plan, fallback):
            return fallback
        last = _last_resort(plan)
        logger.error("undx_response_fallback capability=%s residual=%s",
                     plan.capability_id, ";".join(validate_consistency(plan, last))[:120])
        return last

    # Ordered before the repetition guard runs, so that an explanation which actually
    # explains is preferred over a shorter phrasing of the same evidence. A stable sort
    # on a boolean, so within each group the search order above is preserved and the
    # ranking of factual framings is untouched — this decides between a full account and
    # a thin one, never between two accounts.
    shortfalls = {text: accounting_shortfall(plan, text) for text in candidates}
    candidates.sort(key=lambda text: bool(shortfalls[text]))
    if shortfalls.get(candidates[0]):
        logger.info("undx_response_thin_account capability=%s mode=%s reason=%s",
                    plan.capability_id, plan.response_mode, shortfalls[candidates[0]])

    for candidate in candidates:
        reason = detect_repetition(candidate, history)
        if not reason:
            return candidate
        logger.info("undx_response_repetition capability=%s reason=%s",
                    plan.capability_id, reason)

    # Every framing collided, which is the expected outcome when the same question is
    # asked twice of unchanged data: two honest answers about four records share most
    # of their vocabulary, and that resemblance is a fact about the evidence rather
    # than a defect in the prose. So the attempt limit is respected — no further
    # rendering, and certainly no invented variety — and the least similar of the
    # already-validated candidates is returned instead of whichever happened to be
    # built first.
    #
    # Restricted to the full accounts when there are any, for the same reason the sort
    # above exists: on a repeated question the least-similar phrasing is worth having,
    # but not at the price of answering "explain my alerts" with a list again.
    full = [text for text in candidates if not shortfalls[text]] or candidates
    return min(full, key=lambda text: _repetition_score(text, history))


def compose(spec: Any, status: str, result: ToolResult,
            verification: VerificationResult, *, question: str = "",
            history: Sequence[str] = (),
            goal_shape: str = GoalShape.UNKNOWN) -> tuple[str, ResponsePlan]:
    """Plan, render, and return both. The gateway's single call into this module."""
    plan = build_plan(spec, status, result, verification,
                      question=question, history=history, goal_shape=goal_shape)
    text = render(plan, spec, status, result, verification, history=history)
    return text, plan


__all__ = [
    "ActionState",
    "DetailLevel",
    "EvidenceShape",
    "EvidenceView",
    "GoalShape",
    "MAX_EXPLANATION_CHARS",
    "MAX_SCANNED_REPLY_CHARS",
    "ResponseMode",
    "ResponsePlan",
    "ResponseType",
    "accounting_shortfall",
    "build_plan",
    "build_view",
    "completion_claim",
    "compose",
    "detect_repetition",
    "draft_quality_issues",
    "execution_narration",
    "is_follow_up",
    "must_not_do_for",
    "nouns_for",
    "render",
    "required_evidence_for",
    "select_detail_level",
    "select_response_mode",
    "select_response_type",
    "unmet_evidence",
    "validate_consistency",
]
