"""The agent runtime: turn one authenticated request into one governed outcome.

This module owns the sequence — understand, resolve, plan, execute, report — but it
owns no authority. Every decision that matters is delegated: the registry decides
what exists, :mod:`services.undx_agent_policy` decides what is permitted, and
:mod:`services.undx_tool_gateway` decides what actually runs. The runtime's job is
to arrange those calls correctly and to tell the truth about what came back.

Two design choices are worth stating plainly, because they are what make an LLM
safe to put in front of this.

**A proposal is just an argument.** :func:`handle` accepts a capability id and a
dictionary. It does not care whether a language model, a deterministic matcher, or
a unit test produced them. Nothing about being "chosen by the model" grants a
proposal any standing, so prompt injection can at worst cause a *different
authorised action* to be proposed — and that proposal still faces the registry, the
policy engine, the ownership filter and the confirmation card. This is why the
built-in matcher below is allowed to be simple: it is a convenience, not a control.

**Reference resolution is a first-class step, and it is allowed to refuse.**
"pause my Bitcoin alert" is only actionable if exactly one alert matches. Two
matches is not a coin flip to be resolved by picking the first row; it is a
question to ask. :func:`resolve_reference` returns the count, and the gateway
refuses ambiguity on the user's behalf.

The runtime never claims an action succeeded on the strength of the tool saying so.
That judgement belongs to the verification engine, arrives through the receipt, and
is reproduced here verbatim.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from services import undx_agent_policy as policy
from services import undx_agent_tools, undx_tool_gateway
from services.undx_agent_contracts import (
    MAX_TEXT_CHARS,
    AgentError,
    AgentOutcome,
    AgentReceipt,
    CardType,
    RiskLevel,
    VerificationState,
    clean,
    describe_alert,
    format_amount,
    new_id,
)
from services.undx_capability_registry import REGISTRY, CapabilitySpec, get, require

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent matching
# ---------------------------------------------------------------------------


#: Phrases that make an instruction *explicit* rather than exploratory. The
#: distinction decides whether a CONTEXTUAL capability may run without a card, so
#: these are matched against the user's own message only — never against retrieved
#: content, tool output, or anything else the user did not type.
_EXPLICIT_MARKERS = (
    "pause ", "resume ", "unpause ", "turn off ", "turn on ", "stop ", "start ",
    "delete ", "remove ", "mute ", "unmute ", "disable ", "enable ",
)

_HEDGES = ("should i", "can you", "could you", "what if", "maybe", "how do i",
           "is it possible", "what happens if", "would it")

# Operations intentionally outside the active registry must fail closed before the
# conversational provider can imply that it will perform them.  These expressions are
# anchored to imperative requests so educational questions and explicit non-action
# drafting ("draft this, but do not send it") continue to reach normal conversation.
_BLOCKED_OPERATIONAL_INTENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*(?:please\s+)?send\s+(?:a\s+)?message\b", re.IGNORECASE),
     "Message sending is not enabled for UNDX."),
    (re.compile(r"^\s*(?:please\s+)?(?:buy|purchase|sell|trade|transfer|withdraw)\b", re.IGNORECASE),
     "Financial transactions are not enabled for UNDX."),
    (re.compile(r"^\s*(?:please\s+)?(?:delete|remove)\s+(?:my\s+)?(?:(?:last|latest|recent)\s+)?(?:post|reel|status|message|account)\b", re.IGNORECASE),
     "Destructive content actions are not enabled for UNDX."),
)


def _blocked_operational_response(text: str, started: float) -> AgentResponse | None:
    for pattern, reason in _BLOCKED_OPERATIONAL_INTENTS:
        if pattern.search(text):
            return AgentResponse(
                handled=True,
                reply=f"{reason} I did not make any change.",
                card={
                    "component": CardType.UNSUPPORTED_CAPABILITY,
                    "status": AgentOutcome.UNSUPPORTED_CAPABILITY,
                    "may_claim_done": False,
                },
                latency_ms=int((time.monotonic() - started) * 1000),
            )
    return None


#: Frames in which a person *names* a write without asking for it. Three families,
#: written out separately because they fail for different reasons and a future reader
#: adding a phrase needs to know which family it belongs to.
#:
#: This is deliberately **not** ``_HEDGES``, and the difference is the point. ``_HEDGES``
#: decides whether a CONTEXTUAL write may run without a confirmation card, so it is
#: allowed to be cautious: it lists "can you" and "could you", and treating "can you
#: pause my alert" as non-explicit merely means the person sees a card. Routing cannot
#: borrow that list. "Can you unfollow user 7" is a request — a polite one — and a
#: matcher that refused to route it would have stopped answering ordinary English.
#:
#: So the two predicates have opposite defaults, on purpose. ``is_explicit`` permits
#: only what it recognises as an instruction. ``asks_for_the_action`` refuses only what
#: it recognises as *not* one. Each is conservative in the direction where being wrong
#: is cheap.
#: Negation is handled at token level rather than as a substring, because "do not" in a
#: message is not automatically about the write. "Delete alert 10 and do not ask again"
#: is an instruction to delete; the negation governs *asking*. Scoping it — the write's
#: own verb must follow the negation closely — is the difference between refusing the
#: two messages that mean "don't" and refusing every message that contains the word.
_NEGATION_TOKENS = frozenset({"not", "don't", "dont", "never", "nor", "neither"})

#: How many tokens a negation reaches forward. Four covers "do not ever unfollow" and
#: "i do not want to follow" without spanning a comma into the next clause.
_NEGATION_REACH = 4

_NEGATED = (
    "no need to ", "instead of ", "rather than ", "changed my mind",
    "leave it alone", "leave them alone", "not now",
)

_DELIBERATED = (
    "should i", "should we", "i might ", "i may ", "thinking about", "thinking of",
    "was going to", "were going to", " or not", "if i ", "if you were me",
    "i wonder", "whether to", "whether i", "on the fence", "i almost ",
)

_EXPLANATORY = (
    "what does it mean", "what it means", "what happens", "what would happen",
    "explain what", "explain how", "explain why", "how do i", "how to ",
    "how would i", "how does", "remind me how", "why would i", "why does",
    "why do i", "is it safe", "is it a good idea", "is it worth", "is it possible",
    "is it ok", "what is the point", "i already ", "have i already",
)

_NOT_A_REQUEST = _NEGATED + _DELIBERATED + _EXPLANATORY


def asks_for_the_action(text: str) -> bool:
    """Whether a message that names a write is actually asking for that write.

    "Do not unfollow user 7" contains "unfollow user", and a subsequence matcher has
    no notion of negation, so without this the message routes straight to
    ``social.unfollow``. That capability confirms ``never`` — deliberately, because
    following is cheap to undo — which means there is no card between the match and
    the act. The two mechanisms are individually reasonable and jointly a defect: the
    one place a person expects the word "not" to be load-bearing is the one place
    nothing was reading it.

    Returning False does not mean the turn fails. It means the write is off the table
    and the matcher looks for a read instead, which is usually what was wanted anyway
    — someone asking what deleting an alert would do is asking about the alert.
    """
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    return not any(frame in lowered for frame in _NOT_A_REQUEST)


def _negation_blocks(message_tokens: list[str], phrase_tokens: list[str]) -> bool:
    """Whether a negation in the message scopes over this particular intent phrase.

    Only the phrase's leading token matters: it is the verb, and a negation binds to the
    verb that follows it. "Do not delete alert 3" negates ``delete``; "delete alert 10
    and do not ask again" negates ``ask``, which is not a capability and not this
    function's business.
    """
    if not phrase_tokens:
        return False
    verb = phrase_tokens[0]
    for index, token in enumerate(message_tokens):
        if token not in _NEGATION_TOKENS:
            continue
        window = message_tokens[index + 1:index + 1 + _NEGATION_REACH]
        if verb in window:
            return True
    return False


def is_explicit(text: str) -> bool:
    """Whether the user issued an instruction rather than explored an option.

    A hedge anywhere in the message disqualifies it. "Should I pause my alert?"
    contains "pause " and is not an instruction, and treating it as one would mean
    acting on a question — the single most annoying way for an agent to be wrong.
    """
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    if any(hedge in lowered for hedge in _HEDGES):
        return False
    return any(marker in lowered for marker in _EXPLICIT_MARKERS)


def _words(text: str) -> list[str]:
    """The message as written, lowercased. The surface form, before any stemming."""
    return re.findall(r"[a-z0-9']+", clean(text, MAX_TEXT_CHARS).lower())


def _tokens(text: str) -> list[str]:
    """Words, lowercased, with a naive plural stripped.

    Crude on purpose. "alerts" and "alert" must be the same token or every intent
    phrase would need both spellings, and a registry that has to enumerate English
    morphology is a registry nobody will keep correct.

    The crudeness has one cost worth naming, because it is invisible from here:
    stemming erases the difference between "show alert" and "show alerts", which is
    the *only* thing distinguishing ``crypto.alerts.get`` from ``crypto.alerts.list``.
    Both then score identically and the winner is whichever capability id sorts last —
    a coin flip dressed as determinism. ``_subsequence_score`` restores the signal by
    consulting the surface form as well; see the bonus there.
    """
    return [word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
            for word in _words(text)]


#: Words whose presence between a phrase's tokens says nothing about whether the phrase
#: fits. Skipping these is ordinary English — "pause my Bitcoin alert" has a possessive
#: and a qualifier wedged into "pause alert" and is still exactly that instruction.
#: Skipping a *content* word is different: it usually means the phrase has been found
#: scattered across a sentence that is about something else.
#:
#: Listed in stemmed form, because that is what the matcher compares — hence "thi",
#: "hi", "doe", "wa", which are what ``_tokens`` makes of "this", "his", "does", "was".
_FUNCTION_WORDS = frozenset("""
a an the my mine our your their hi her it thi that these those all any some every each
both of for to in on at from with about and or but please just really me i them him he
she they do doe did is are wa were be been can could would should will shall have ha
had there here so very much many more most other another new own same too then than
when while as by
""".split())

#: What one skipped *content* word costs, in the same units as matched characters.
#:
#: The number is not arbitrary, and neither is the decision to count only content words.
#: Without any gap cost, "find posts from people I follow" matched ``search.people``
#: ("find" … "people", three words apart) exactly as well as ``feed.posts.list`` ("find
#: posts", adjacent), and the tie fell to whichever capability id sorted last. But a
#: flat per-word penalty overcorrected in a way that mattered more: "delete all my
#: alerts" stopped reaching ``crypto.alerts.delete``, because "all my" cost four points
#: and handed the message to ``crypto.alerts.list``. Both gaps are two words wide; only
#: one of them is evidence. Counting content words separates them, and at three the
#: adjacent phrase wins every contested case in the corpus while "delete all my alerts"
#: still deletes. Four and six change nothing further, which is what says three is the
#: knee of the curve rather than a value fitted to one example.
_GAP_PENALTY = 3


def _subsequence_score(phrase_tokens: list[str], message_tokens: list[str],
                       phrase_words: list[str] = (), message_words: list[str] = ()) -> int:
    """How well an intent phrase matches, or 0 for no match.

    The phrase's words must all appear, in order, but need not be adjacent — which
    is what lets "pause alert" match "pause my Bitcoin alert". Requiring adjacency
    was the original mistake: real instructions almost always have a possessive or a
    qualifier wedged in the middle, so a contiguous matcher recognises the phrasing
    found in test fixtures and almost nothing a person actually types.

    Three terms make up the score.

    *Matched characters*, so the more specific phrase wins a tie: "delete alert"
    outranks "alert" for the same message.

    *Minus a penalty per skipped content word*, because "in order but not adjacent"
    with no cost attached lets a phrase claim a message it merely happens to be
    sprinkled across. Proximity is evidence and was previously worth nothing. Function
    words are not counted: skipping "my" is how English works, skipping "posts" is a
    sign the phrase has landed in a sentence about something else.

    *Plus one per word matching in its surface form*, which is how the singular/plural
    distinction survives stemming. "Show alert 5" prefers ``crypto.alerts.get``'s
    "show alert" over ``crypto.alerts.list``'s "show alerts" because the message says
    "alert", and that is exactly the distinction a person is drawing when they say it.

    The word lists are optional so existing callers keep working; without them the
    surface bonus is simply zero, which degrades to the old ranking rather than to a
    wrong one.
    """
    position = first = last = 0
    bonus = 0
    matched: set[int] = set()
    for index, token in enumerate(phrase_tokens):
        try:
            found = message_tokens.index(token, position)
        except ValueError:
            return 0
        if index == 0:
            first = found
        last = found
        position = found + 1
        matched.add(found)
        if (index < len(phrase_words) and found < len(message_words)
                and phrase_words[index] == message_words[found]):
            bonus += 1
    characters = sum(len(token) for token in phrase_tokens)
    skipped = sum(1 for slot in range(first, last + 1)
                  if slot not in matched and message_tokens[slot] not in _FUNCTION_WORDS)
    # Floored at 1: a match that scores itself to nothing is still a match, and
    # returning 0 would make it indistinguishable from no match at all.
    return max(1, characters - _GAP_PENALTY * skipped + bonus)


def match_capability(text: str) -> CapabilitySpec | None:
    """Deterministic best-effort capability match, used when no planner supplied one.

    Returning ``None`` is a perfectly good answer, and the common one: the caller
    then falls back to a conversational reply rather than guessing at an action.
    A matcher that always finds something is a matcher that acts on small talk.

    Writes are held to a second test. Scoring alone cannot tell "unfollow user 7" from
    "do not unfollow user 7" — the tokens that earn the score are the same ones — so
    when the message is framed as a negation, a deliberation or a question about what
    the action does, writes are excluded from consideration entirely and the best
    remaining *read* is returned. Excluded rather than merely demoted: demoting the
    forbidden write would let the second-best write win, and "I was going to save post
    9 but changed my mind" landing on a different write is not an improvement.
    """
    message_tokens = _tokens(text)
    if not message_tokens:
        return None
    message_words = _words(text)
    writes_allowed = asks_for_the_action(text)
    best: tuple[int, str, CapabilitySpec] | None = None
    for spec in REGISTRY.values():
        if spec.is_write and not writes_allowed:
            continue
        for phrase in spec.intents:
            phrase_tokens = _tokens(phrase)
            if spec.is_write and _negation_blocks(message_tokens, phrase_tokens):
                continue
            score = _subsequence_score(phrase_tokens, message_tokens,
                                       _words(phrase), message_words)
            # Ties broken by capability id so the choice is stable across runs rather
            # than dependent on dictionary ordering.
            if score and (best is None or (score, spec.capability_id) > (best[0], best[1])):
                best = (score, spec.capability_id, spec)
    return best[2] if best else None


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def _read_permitted(user_id: int, capability_id: str) -> bool:
    """Whether this module may perform a supporting read on its own.

    Two paths here — reference resolution and the confirmation preview — call
    executors directly rather than through the gateway, because neither is an action
    the user asked for and neither should produce a receipt, an audit row or a ledger
    entry. That shortcut skipped the flags. An operator who disabled
    ``crypto.alerts.get``, or switched reads off during an incident, still had its
    data read and rendered into a confirmation card, which makes those switches mean
    less than their names promise.

    The gate is ``policy.evaluate`` rather than a hand-rolled flag check, so there is
    one definition of "may this capability run" and this path cannot drift from the
    gateway's. For a read-only spec that evaluation depends on nothing but the flags,
    the cohort and the capability id, which is why passing no arguments is sound.

    Failing closed here is cheap: resolution reports that it could not read, and the
    preview renders a confirmation with an unknown current value. Neither invents one.
    """
    spec = get(capability_id)
    if spec is None or spec.is_write:
        return False
    return not policy.evaluate(int(user_id), spec, {}).denied


#: How many alerts reference resolution will compare before declining to. Matches the
#: maximum ``crypto.alerts.list`` accepts, so this reads the largest window the
#: capability permits rather than the executor's conversational default of 20.
_MAX_REFERENCE_SCAN = 50

_SYMBOL_ALIASES = {
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL", "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE", "ripple": "XRP", "xrp": "XRP",
    # Shortened names people actually type. Their absence was invisible while these
    # aliases were only used to *narrow* a list of alerts the account already had —
    # "my ether alert" still found the one alert. Creating an alert has nothing to
    # narrow, so a name not in this map became "Which coin?" asked of someone who had
    # just named the coin.
    "ether": "ETH", "sola": "SOL", "bitcoins": "BTC", "xbt": "BTC",
}


class Reference:
    """The outcome of turning a phrase like "my Bitcoin alert" into an id.

    ``count`` is deliberately reported even when it is zero or greater than one,
    because those are the cases the caller must not paper over.
    """

    __slots__ = ("count", "resource_id", "candidates", "detail")

    def __init__(self, count: int, resource_id: int = 0,
                 candidates: list[dict[str, Any]] | None = None, detail: str = "") -> None:
        self.count = int(count)
        self.resource_id = int(resource_id)
        self.candidates = candidates or []
        self.detail = detail

    @property
    def unique(self) -> bool:
        return self.count == 1 and self.resource_id > 0


#: An id named in the sentence itself. Anchored on the noun, never bare: the digits in
#: "change my alert to 95000" are a price and the digits in "alert me above 90000" are a
#: threshold, and a pattern loose enough to read either as a row id would turn the most
#: common phrasing in the corpus into a write against an arbitrary alert. The noun has
#: to be immediately followed by the number — "alert to 95000" does not match, because
#: "to" sits between them.
_ALERT_ID_IN_TEXT = re.compile(r"\b(?:alerts?|rules?)\s*(?:id\s*)?#?\s*(\d{1,9})\b|#\s*(\d{1,9})\b")

#: The rest of a list, once the noun has introduced the first of them. "Pause alerts 4
#: and 7" carries the noun only once, so the second id has no anchor of its own and
#: would be dropped — which is the failure worth avoiding here, because dropping it
#: silently is what turns "pause both" into "pause one and say it went fine".
#: Only what immediately continues the list: a separator, then a number, repeatedly.
#: Anchoring is done by ``.match(text, position)`` at the call site rather than by an
#: escape in the pattern — Python's ``re`` has no ``\G``.
_ALERT_ID_LIST_TAIL = re.compile(r"\s*(?:,|and|&|\+)\s*#?\s*(\d{1,9})\b")


def _ids_named_in(text: str) -> list[int]:
    """Every alert id the sentence names, in the order it names them.

    A list rather than the first match, because "pause alerts 4 and 7" names two rows
    for a capability that changes one. Taking the first would do half of what was asked
    and report complete success for it, and the person has no way to see the omission.
    """
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    seen: list[int] = []

    def remember(raw: str) -> None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return
        if value > 0 and value not in seen:
            seen.append(value)

    for match in _ALERT_ID_IN_TEXT.finditer(lowered):
        remember(match.group(1) or match.group(2))
        # Walk any continuation of the list from exactly where this match ended, so
        # "alerts 4 and 7" is two ids while "alert 4 and delete 7 posts" is one — the
        # tail has to butt directly against what came before it.
        position = match.end()
        while True:
            tail = _ALERT_ID_LIST_TAIL.match(lowered, position)
            if tail is None:
                break
            remember(tail.group(1))
            position = tail.end()
    return seen


def resolve_alert_reference(user_id: int, text: str, *, explicit_id: Any = None) -> Reference:
    """Find the single alert a message refers to, or report that there isn't one.

    An id supplied directly is still checked for ownership by reading it back
    through the owner-scoped service call, so a caller cannot smuggle in another
    account's row by naming it precisely.

    An id written into the sentence is read here too, and that is not a convenience.
    Until it was, ``explicit_id`` was the only way an id could arrive, and it is empty
    on a first turn because extraction runs after resolution — so "pause alert 4" fell
    through to the listing path and was answered by whatever that path concluded about
    the account as a whole. On an account with two alerts it produced "more than one of
    your alerts matches", which is false about a sentence naming exactly one. On an
    account with one alert it produced a *paused alert*: "pause alert 999" matched the
    only row, wrote to it, and read it back as proof. The person named a row that does
    not exist and was told, truthfully as far as the runtime knew, that it was done.
    """
    if not _read_permitted(user_id, "crypto.alerts.get"):
        return Reference(0, detail="Your alerts could not be read just now.")

    named = [int(explicit_id)] if explicit_id else _ids_named_in(text)
    if len(named) == 1:
        # One id, from either road, and both are checked the same way. Ownership is
        # established by a real owner-scoped read rather than by the id looking
        # plausible, so naming another account's row precisely gets the same answer as
        # naming a row that never existed.
        result = undx_agent_tools.crypto_alerts_get(int(user_id), {"alert_id": named[0]})
        if result.ok:
            return Reference(1, named[0])
        return Reference(0, detail="That alert is not on your account.")
    if len(named) > 1:
        # Several named at once. These capabilities change one alert, so this is a
        # chooser rather than a refusal — the person has said everything except which,
        # and Batch 7's machinery can take the answer. Only the ids they actually own
        # are offered; an unowned id in the list would be a chooser entry that fails
        # the moment it is chosen.
        owned: list[dict[str, Any]] = []
        for value in named:
            result = undx_agent_tools.crypto_alerts_get(int(user_id), {"alert_id": value})
            if result.ok and isinstance(result.data, dict):
                owned.append(dict(result.data))
        if len(owned) == 1:
            return Reference(1, int(owned[0].get("alert_id") or 0), owned)
        if not owned:
            return Reference(0, detail="Those alerts are not on your account.")
        return Reference(len(owned), 0, owned,
                         detail="I can change one alert at a time — which of those?")

    lowered = clean(text, MAX_TEXT_CHARS).lower()
    named_symbols = {code for word, code in _SYMBOL_ALIASES.items()
                     if re.search(rf"\b{word}\b", lowered)}

    # The coin is asked of the store, not of the page. Naming exactly one means the
    # scan can be about *that* coin, and the difference is not a refinement — it is the
    # difference between an answerable question and a refused one. The truncation guard
    # below used to run before any narrowing, so an account holding fifty Ethereum
    # alerts and one Bitcoin alert was told UNDX could not compare its alerts, about a
    # sentence that named exactly one row. Nothing had been compared. The scan stopped
    # one block above the filter that would have found the answer.
    #
    # Only when exactly one coin is named. Two would need an OR the store does not
    # take, and zero is the "my alert" case, which really is a question about the whole
    # account and is right to be scanned as one.
    scan: dict[str, Any] = {"limit": _MAX_REFERENCE_SCAN}
    if len(named_symbols) == 1:
        scan["symbol"] = next(iter(named_symbols))

    # The largest page the capability permits, not the executor's default. Resolution
    # asks "is there exactly one?", which is a question about the whole account; asking
    # it of a 20-row window silently redefines it as "exactly one on page one".
    listing = undx_agent_tools.crypto_alerts_list(int(user_id), scan)
    if not listing.ok:
        return Reference(0, detail="Your alerts could not be read just now.")

    # Only live alerts are candidates. Matching a deleted alert and then refusing to
    # act on it would report "ambiguous" for a set the user considers to have one item.
    alerts = [item for item in listing.records if str(item.get("status") or "") != "deleted"]

    # The narrowing the store could not do. ``scan`` covers a coin named by alias;
    # this covers a symbol the person typed that only the rows themselves reveal, and
    # it is a no-op whenever the store already answered the narrow question.
    wanted = set(named_symbols)
    for item in alerts:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and re.search(rf"\b{re.escape(symbol.lower())}\b", lowered):
            wanted.add(symbol)
    if wanted:
        alerts = [item for item in alerts if str(item.get("symbol") or "").upper() in wanted]

    if (listing.data or {}).get("truncated"):
        # More rows exist than were read, so uniqueness cannot be established — there may
        # be a second match just past the edge of the page. Refusing here costs one
        # clarifying question; guessing costs the user a change to the wrong alert, made
        # under a confirmation card that named a different one as the only candidate.
        #
        # Still an empty refusal rather than a chooser over what was read, and the reason
        # is worth stating because the opposite was tried. The rows that survive to here
        # are a partial view: drawing fifty of them would imply the set is complete when
        # the row the person wants may be among the ones never read, and fifty rows of
        # the same coin cannot be told apart by eye any better than the runtime can tell
        # them apart. A card that asks a question its own list may not be able to answer
        # is the empty-chooser defect with more scrolling.
        #
        # What reaches here is also much rarer than it was. The narrowing above is now a
        # question to the store, so this fires only when the *named coin* has more alerts
        # than the scan permits — not, as before, when the account does.
        return Reference(
            2, 0, [],
            detail="You have more alerts than UNDX can compare at once. Open your alerts and tell me which one.",
        )

    if len(alerts) == 1:
        return Reference(1, int(alerts[0].get("alert_id") or 0), alerts)
    return Reference(
        len(alerts), 0, alerts,
        detail=("You do not have an alert matching that." if not alerts
                else "More than one of your alerts matches that description."),
    )


#: Words a person uses for a thing that the registry knows by one name. Extraction has
#: to accept the synonyms or it refuses sentences that are not ambiguous at all —
#: "summarize chat 5" names conversation 5 as plainly as "summarize conversation 5".
_RESOURCE_NOUNS = {
    "post_id": ("post",),
    "reel_id": ("reel",),
    "status_id": ("status",),
    "conversation_id": ("conversation", "chat", "thread", "dm"),
    "notification_id": ("notification",),
    "listing_id": ("listing",),
    "order_id": ("order",),
    "live_id": ("live", "live session", "stream", "broadcast"),
    "alert_id": ("alert",),
}


def resource_reference(text: str, nouns: tuple[str, ...]) -> int:
    """The id a sentence gives for a thing, or 0.

    Three forms, tried in order, and the order is the point: each is more permissive
    than the last, so the most direct reading always wins.

    *Adjacent* — "post 9", "post #9", "post id 9". Unambiguous and the common case.

    *Linked by a preposition* — "show post performance for 9", "summarize reel comments
    on 4". The noun and the number are separated by words belonging to the capability's
    own name, which is why the earlier extractor missed them: it required adjacency, and
    the more precisely a person named the capability the further they pushed the number
    away from the noun. Bounded to a short span so it cannot reach across a sentence.

    *The only number in a sentence that names the thing* — "explain live session 9".
    Permissive, and safe for a reason worth stating: it declines the moment a second
    number appears, so "change alert 3 to trigger at 95000" never reaches it. Where the
    reading is genuinely uncertain there are two numbers, and two numbers turn this off.
    """
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    for noun in nouns:
        match = re.search(rf"\b{re.escape(noun)}\s*(?:id\s*)?#?\s*(\d+)\b", lowered)
        if match:
            return int(match.group(1))
    for noun in nouns:
        match = re.search(
            rf"\b{re.escape(noun)}\b[^.?!]{{0,40}}?\b(?:for|on|of|number|numbered)\s+#?(\d+)\b",
            lowered)
        if match:
            return int(match.group(1))
    numbers = re.findall(r"\b(\d+)\b", lowered)
    if len(numbers) == 1 and any(re.search(rf"\b{re.escape(noun)}\b", lowered) for noun in nouns):
        return int(numbers[0])
    return 0


#: Written thresholds as people write them. "100k" is a hundred thousand in every
#: context this field appears in, and refusing it would mean refusing the most common
#: way a person states a price target.
_MAGNITUDES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

#: Phrasings for each alert condition. Ordered longest-intent-first within each entry
#: so "goes above" is read as ``above`` rather than matched twice.
_CONDITIONS = (
    ("moves_up_percent", ("moves up", "rises by", "gains", "up by", "% up", "percent up")),
    ("moves_down_percent", ("moves down", "falls by", "drops by", "down by", "% down",
                            "percent down")),
    ("volatility_above", ("volatility", "swings", "gets volatile")),
    ("above", ("above", "over", "exceeds", "hits", "reaches", "goes past", "breaks",
               "more than", "greater than", "at or above")),
    ("below", ("below", "under", "beneath", "drops to", "falls to", "less than",
               "lower than", "dips")),
)


def resolve_threshold(text: str) -> float | None:
    """The price a sentence names, or None.

    Reads the *last* number rather than the first, because the sentence orders them
    that way: "change alert 3 to trigger at 95000" names the alert before the price,
    and every phrasing in the corpus that carries both puts the identifier first. Where
    only one number appears the choice does not arise.

    Suffixed forms are expanded here rather than left to the schema, which validates a
    float and would reject "100k" as a type error — accurate about the type and silent
    about the fact that the number was right there in the sentence.
    """
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    matches = re.findall(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*([kmb])?\b", lowered)
    if not matches:
        return None
    raw, suffix = matches[-1]
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    return value * _MAGNITUDES.get(suffix, 1)


def resolve_alert_write_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Symbol, condition and threshold for creating or retargeting an alert.

    These three fields were required by two capabilities and filled by nothing. A
    message like "alert me when bitcoin goes over 100k" routed correctly, arrived at
    the gateway with every field empty, and came back as a schema error naming three
    fields the person had in fact supplied. The extraction was the missing piece, not
    the sentence.

    Each field is filled only from what the sentence says. Nothing is defaulted: an
    alert with a guessed threshold is worse than no alert, because it will fire.
    """
    resolved = dict(arguments)
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    if not resolved.get("symbol"):
        wanted = [code for word, code in _SYMBOL_ALIASES.items()
                  if re.search(rf"\b{re.escape(word)}\b", lowered)]
        if len(set(wanted)) == 1:
            resolved["symbol"] = wanted[0]
    if not resolved.get("condition"):
        for condition, phrases in _CONDITIONS:
            if any(phrase in lowered for phrase in phrases):
                resolved["condition"] = condition
                break
    if resolved.get("threshold") in (None, ""):
        # The identifier is not the threshold. Stripping it first is what lets the
        # last-number rule stay simple: with "alert 3" removed, one number is left.
        stripped = re.sub(r"\balert\s*(?:id\s*)?#?\s*\d+\b", " ", lowered)
        value = resolve_threshold(stripped)
        if value is not None:
            resolved["threshold"] = value
    return resolved


#: Verbs and nouns that introduce a search rather than belonging to it. "Find people who
#: work in crypto" is a search for "people who work in crypto" only if you keep the noun;
#: it is a search for "who work in crypto" if you do not. The noun is what the capability
#: already knows, so dropping it is what leaves the part the person actually typed.
_SEARCH_PREAMBLE = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|hey\s+|quick one\s*[-–]\s*)*"
    r"(?:find|search|search for|look for|look up|show me|show|get me|get|list)\s+"
    r"(?:me\s+)?(?:my\s+|the\s+|a\s+|an\s+|any\s+|all\s+)?"
    r"(?:people|person|users?|accounts?|content|posts?|reels?|messages?|activity|"
    r"live(?:\s+sessions?)?|streams?|groups?|courses?|classes?|music|songs?|tracks?|"
    r"marketplace|listings?|everything|anything)?\s*",
    re.IGNORECASE)

#: Words that are a scope, not a search term. "Search my messages from ana" is a search
#: for "ana"; "in my messages" is where to look, which the capability already is.
_QUERY_TRAILING_NOISE = re.compile(r"\s*(?:please|for me|thanks|thank you)\s*[.!?]*\s*$",
                                   re.IGNORECASE)


def resolve_query_argument(text: str, *, strip_preamble: bool = True) -> str:
    """The words a person is searching for, or "".

    The previous rule required "about", "for" or "named" and so filled the field only
    when the sentence happened to contain one of three prepositions. "Find people who
    work in crypto" contains none of them and is not in the least ambiguous; it failed
    for want of a preposition, not for want of a query.

    Two passes. The preposition rule still runs first, because when it does apply it is
    the more precise reading — "search marketplace for a bike" means the bike, not
    "marketplace for a bike". Otherwise the introduction is stripped: the search verb,
    the politeness, and the noun naming the thing being searched, all of which the
    capability already encodes. What is left is what the person added.

    Returning "" rather than the whole sentence is deliberate. A query of "search my
    messages" would match on the words "search" and "messages", producing results that
    look like an answer and are an artefact of the parse.

    ``strip_preamble`` is off for capabilities where ``query`` is optional, and the
    distinction is not a tuning knob. A required query means the capability is a search
    and the sentence must say what for; an optional one means empty is a real answer.
    "Find my saved posts" is a complete instruction to ``saved.items.list`` — the whole
    library, narrowed to posts — and stripping "find my" out of it yields the query
    "saved posts", which then filters the library down to nothing and reports an empty
    Saved folder to someone who has one. Learned by breaking it.
    """
    body = clean(text, MAX_TEXT_CHARS).strip()
    match = re.search(
        r"(?:term is|query is|keyword is|about|for|named|mentioning|containing|regarding)"
        r"\s+(.+?)[.!?]*$",
        body, re.IGNORECASE)
    if match:
        return clean(_QUERY_TRAILING_NOISE.sub("", match.group(1)), 120)
    if not strip_preamble:
        return ""
    stripped = _SEARCH_PREAMBLE.sub("", body, count=1)
    stripped = _QUERY_TRAILING_NOISE.sub("", stripped).strip(" ,.!?")
    if not stripped or stripped.lower() == body.lower():
        return ""
    return clean(stripped, 120)


def resolve_notification_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Derive ``category`` and ``push`` from a sentence, or report that they aren't there.

    This delegates to ``undx_architecture.notification_action_from_text`` rather than
    reimplementing the parse. That function is what the shipped V4/V5 path uses, and
    while both paths exist they must read an identical sentence identically — a second
    parser would eventually disagree with the first, and the disagreement would show up
    as the agent changing a different setting than the one the user was shown.

    ``None`` means the direction was not stated. "Change my notifications" names no
    value to change it to, and guessing the opposite of the current setting would be
    inventing an instruction rather than following one.
    """
    if "push" in arguments and arguments.get("category"):
        return dict(arguments)
    from services import undx_architecture

    parsed = undx_architecture.notification_action_from_text(text)
    if not parsed:
        return None
    derived = dict(parsed.get("arguments") or {})
    if "category" not in derived or "push" not in derived:
        return None
    # An explicitly supplied value still wins: a planner that names the category is
    # more specific than a phrase match, and the gateway validates either way.
    derived.update({key: value for key, value in arguments.items() if value is not None})
    return derived


def resolve_saved_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Narrow Saved-library reads from the user's own product wording."""
    resolved = dict(arguments)
    if clean(resolved.get("content_type"), 40) not in {"", "all"}:
        return resolved
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    types = (
        ("reel", ("reel", "reels")),
        ("post", ("post", "posts")),
        ("status", ("status", "statuses")),
        ("marketplace", ("listing", "listings", "marketplace")),
        ("video", ("video", "videos")),
    )
    for content_type, words in types:
        if any(re.search(rf"\b{word}\b", lowered) for word in words):
            resolved["content_type"] = content_type
            break
    resolved.setdefault("content_type", "all")
    return resolved


def resolve_relationship_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Distinguish inbound followers from accounts the caller follows."""
    resolved = dict(arguments)
    if clean(resolved.get("direction"), 20) in {"followers", "following"}:
        return resolved
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    resolved["direction"] = (
        "following"
        if any(phrase in lowered for phrase in ("am i following", "i follow", "my following"))
        else "followers"
    )
    return resolved


def resolve_saved_post_write_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Extract an explicit post id and desired state; never infer a toggle."""
    resolved = dict(arguments)
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    if not resolved.get("post_id"):
        match = re.search(r"\bpost(?:\s+#?|\s+number\s+)(\d+)\b", lowered)
        if match:
            resolved["post_id"] = int(match.group(1))
    if "saved" not in resolved:
        removing = any(
            phrase in lowered
            for phrase in ("unsave", "remove from saved", "remove post from saved")
        )
        resolved["saved"] = not removing
    return resolved


def resolve_user_target_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Extract an explicit numeric QA-safe user target; names require disambiguation."""
    resolved = dict(arguments)
    if not resolved.get("target_user_id"):
        match = re.search(r"\b(?:user|account|member)\s+#?(\d+)\b", clean(text, MAX_TEXT_CHARS).lower())
        if match:
            resolved["target_user_id"] = int(match.group(1))
    return resolved


class Resolution:
    """What a sentence yielded for one capability, and what is still missing.

    ``arguments`` is what the gateway will be given. ``unresolved`` is set only when
    the runtime must stop and ask — a distinct outcome from "the field is absent",
    because an absent field produces a schema error the person cannot act on while an
    unresolved reference produces a question they can answer.

    ``missing`` is neither: it is the diagnostic view, listing required fields that no
    resolver filled. Nothing in the request path reads it. It exists so the benchmark
    can ask a question the request path cannot — "which capabilities are reachable but
    unusable?" — without the answer depending on a live database.

    ``choice_field`` names the argument an unresolved *reference* was trying to fill.
    It is carried explicitly rather than inferred from the shape of ``unresolved``,
    because the two ways a turn can stop and ask look identical from outside and mean
    opposite things: a missing field has no candidates and needs a value invented, an
    ambiguous reference has candidates and needs one of them chosen. Inferring the
    difference from "are there candidates?" would work today, when one resolver exists,
    and would quietly become wrong the first time a second one returned an empty list.
    """

    __slots__ = ("arguments", "resolved_count", "unresolved", "missing", "choice_field")

    def __init__(self, arguments: dict[str, Any], *, resolved_count: int = 1,
                 unresolved: Reference | None = None,
                 missing: tuple[str, ...] = (), choice_field: str = "") -> None:
        self.arguments = arguments
        self.resolved_count = int(resolved_count)
        self.unresolved = unresolved
        self.missing = tuple(missing)
        self.choice_field = str(choice_field or "")


def missing_required(spec: CapabilitySpec, arguments: dict[str, Any]) -> tuple[str, ...]:
    """Required fields with no value and no default. The gateway's rejection, predicted.

    A field with a default is not missing — the schema supplies it, and a message that
    says nothing about ``limit`` is not an incomplete message. Only a required field
    that must come out of the sentence counts.
    """
    return tuple(
        item.name for item in spec.fields
        if item.required and item.default is None
        and arguments.get(item.name) in (None, "")
    )


#: What to ask for when a field cannot be filled from the sentence. Phrased as a
#: request to the person, in the vocabulary of the product rather than of the schema.
#: Fields not listed here fall back to the field name, which is worse but not wrong.
_FIELD_QUESTIONS = {
    "post_id": "Which post? Tell me its number, or open it and ask again.",
    "reel_id": "Which reel? Tell me its number, or open it and ask again.",
    "status_id": "Which status? Tell me its number, or open it and ask again.",
    "conversation_id": "Which conversation? Tell me its number, or open it and ask again.",
    "notification_id": "Which notification? Open it and ask again, or tell me its number.",
    "listing_id": "Which listing? Tell me its number.",
    "order_id": "Which order? Tell me its number.",
    "live_id": "Which live session? Tell me its number.",
    "query": "What should I search for?",
    "body": "What should the message say?",
    "threshold": "What price should it trigger at?",
    "symbol": "Which coin?",
    "target_user_id": "Which account? Tell me its user number.",
    "saved": "Should I save it or remove it from Saved?",
}

#: Enum values that do not say what they mean. Most choices are already the word a
#: person would use — ``above``, ``messages``, ``reel`` — so listing them raw is fine.
#: Language codes are the exception: telling someone who asked for German that the
#: options are "en, es, fr" is accurate and requires them to decode it.
_CHOICE_LABELS = {
    "en": "English", "es": "Spanish", "fr": "French",
    "moves_up_percent": "rises by a percentage",
    "moves_down_percent": "falls by a percentage",
    "volatility_above": "volatility goes above",
}


def _missing_field_question(spec: CapabilitySpec, missing: tuple[str, ...]) -> str:
    """The sentence to put in front of someone whose message left a field empty.

    A required field that the sentence does not contain is not an error. "Update alert
    1 with a new threshold" names no new threshold, and there is nothing wrong with
    that message — it is how a person opens a conversation about changing an alert.
    What was wrong was the reply: the gateway rejected it as a schema violation naming
    ``threshold``, which tells the person that a field they have never heard of failed
    a validation they did not know was running.

    Enum fields answer themselves. "Set my preferred language to German" fails because
    PulseSoc has three languages and German is not one, and the useful reply says so.
    Reading the choices off the registry means that reply stays true when the list
    changes, which a hand-written sentence would not.
    """
    fields = {item.name: item for item in spec.fields}
    parts = []
    for name in missing:
        field = fields.get(name)
        if field is not None and field.choices:
            choices = ", ".join(_CHOICE_LABELS.get(str(choice), str(choice))
                                for choice in field.choices)
            parts.append(f"PulseSoc supports these for "
                         f"{name.replace('_', ' ')}: {choices}.")
        else:
            parts.append(_FIELD_QUESTIONS.get(name, f"I still need {name.replace('_', ' ')}."))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Answering the question
# ---------------------------------------------------------------------------


#: Ways of saying "drop it". Checked only when the outstanding field is free text,
#: where every other reply is a valid answer and so nothing else can distinguish an
#: answer from a withdrawal. For a numeric or enum field the absence of a number or a
#: known choice already does that job, and adding this list there would only create a
#: way to fail to give an answer that contains the word "cancel".
_ABANDONMENT = ("never mind", "nevermind", "forget it", "forget that", "cancel that",
                "cancel", "stop", "no thanks", "no thank you", "leave it", "skip it",
                "don't bother", "dont bother", "actually no")


def answer_for_field(field: Any, text: str) -> Any:
    """Read one outstanding field out of a reply, or return ``None``.

    Extraction over a first message and extraction over a reply are not the same
    problem, which is why this is not ``resolve_arguments`` called a second time. A
    first message has to say what it is about, so the extractors there look for a noun
    — "post 9", "alert 3" — and a bare "9" is correctly ignored, because in an opening
    message a lone number means nothing. A reply has already been given its subject by
    the question that prompted it. "9" is a complete answer to "which post?" and the
    only thing it could be.

    So the noun requirement is dropped and the field's own declared kind takes over as
    the test. That is also where the safety comes from, and it is worth being precise
    about it: this returns ``None`` far more readily than it returns a value. A reply
    with no number cannot answer an ``int`` field; a reply naming no supported choice
    cannot answer an ``enum``. The failure mode of a permissive reader here would be
    the runtime deciding that "good morning" is post 0, and the shape of the guard is
    that there is nothing in "good morning" for any of these branches to return.
    """
    reply = clean(text, MAX_TEXT_CHARS).strip()
    if not reply:
        return None
    lowered = reply.lower()
    kind = str(getattr(field, "kind", "") or "")
    choices = tuple(getattr(field, "choices", ()) or ())

    if choices:
        # Both directions, and the labels are why. The question said "English, Spanish,
        # French" because "en, es, fr" is not a sentence anyone should have to read;
        # having asked in those words, the reply has to be accepted in them too. A
        # question the system cannot understand the answer to is worse than the schema
        # error it replaced.
        for choice in choices:
            label = str(_CHOICE_LABELS.get(str(choice), choice)).lower()
            if re.search(rf"\b{re.escape(str(choice).lower())}\b", lowered) or \
                    re.search(rf"\b{re.escape(label)}\b", lowered):
                return choice
        return None

    if kind == "bool":
        if re.search(r"\b(yes|yeah|yep|save|saved|keep|do it|please do)\b", lowered):
            return True
        if re.search(r"\b(no|nope|remove|unsave|delete|take it out|don't|dont)\b", lowered):
            return False
        return None

    if kind == "identifier":
        wanted = {code for word, code in _SYMBOL_ALIASES.items()
                  if re.search(rf"\b{re.escape(word)}\b", lowered)}
        if len(wanted) == 1:
            return wanted.pop()
        # A bare ticker nobody has aliased yet. Two to five letters, alone in the
        # reply, is the shape of an answer to "which coin?" and of very little else.
        bare = re.fullmatch(r"[a-z]{2,5}", lowered)
        return bare.group(0).upper() if bare else None

    if kind == "float":
        return resolve_threshold(reply)

    if kind == "int":
        numbers = re.findall(r"\b(\d+)\b", lowered)
        # One number is an answer. Several is a sentence that has gone somewhere else,
        # and picking one of them would be a guess dressed as a reading.
        return int(numbers[0]) if len(numbers) == 1 else None

    if kind == "str":
        if any(re.search(rf"\b{re.escape(phrase)}\b", lowered) for phrase in _ABANDONMENT):
            return None
        return clean(reply, 400)

    return None


#: Position words, and the index each names. Ordinals only — a chooser with more than
#: a handful of entries is a list to scroll, not a sentence to answer, and "the
#: seventeenth one" is not something a person says.
#:
#: The table used to stop at "fifth", which was defensible right up until the number of
#: rows a chooser can draw stopped being a handful. ``_MAX_REFERENCE_SCAN`` is 50, and a
#: six-row chooser is the first place the old table showed: "the sixth one" fell out of
#: ``read_choice`` with no reading, no miss and no card, so a person answering a list the
#: runtime itself had drawn was answered with silence. Ten is where a numbered list stops
#: being read as a sentence and starts being scrolled, so ten is where this stops.
_ORDINALS = {
    "first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "sixth": 5, "6th": 5,
    "seventh": 6, "7th": 6, "eighth": 7, "8th": 7, "ninth": 8, "9th": 8,
    "tenth": 9, "10th": 9, "top": 0,
}

#: The far end of the list, by any of the names it goes by. "Final" and "bottom" said
#: exactly what "last" says and were read as nothing at all.
_LAST_WORDS = re.compile(r"\b(?:last|final|bottom)\b")

#: The row *before* the far end, which is why it must be tested first. ``\blast\b``
#: matches inside "the one before last", so that reply resolved to the last row — a
#: person naming row N-1 and being handed row N, on a confirmation card, silently. This
#: is the kind of wrong answer that is worse than no answer, because no answer costs a
#: turn and this one costs a write to a row nobody picked.
#: The phrase is removed from the text before the ordinal and last-word searches run,
#: rather than merely tested, because "second to last" contains "second": tested, it
#: would name row 2 and row N-1 at once and be refused as ambiguous, which is a different
#: wrong answer rather than a fix.
_PENULTIMATE_WORDS = re.compile(
    r"\bpenultimate\b|"
    r"\b(?:one[\s-]+before|before|next[\s-]+to|second[\s-]+to|2nd[\s-]+to)"
    r"[\s-]+(?:the[\s-]+)?(?:last|final|bottom)\b")

#: Words that order the list by *when*, rather than by where it is drawn. Read as a
#: reported miss and never as a position — see ``_recency_asked``.
_RECENCY_WORDS = re.compile(
    r"\b(?:newest|newer|latest|oldest|older|earliest|most\s+recent|"
    r"least\s+recent|recent(?:ly)?)\b")

#: Cues that turn a named row from a choice into an exclusion. ``read_choice`` collects
#: readings by searching for words anywhere in the reply, so before this existed "not the
#: first one" resolved *to* the first one: the rule that reads by presence alone cannot
#: tell naming a row from ruling it out, and a reply that names a row in order to exclude
#: it is the worst possible input to such a rule.
#:
#: A bare "no" is deliberately absent. It is a discourse marker far more often than an
#: operator — "no, the third one" is a correction that names a row and must keep
#: resolving — and the scope rule below is what lets "no, not the third one" still be
#: read as an exclusion without "no" itself having to mean one.
#:
#: The apostrophe is optional, and that single character was carrying a wrong write.
#: Batch 15 closed "not the first one" and left ``n't\b`` matching only the punctuated
#: form, so "dont pause the first one" — the same sentence off a phone keyboard that had
#: not autocorrected — missed the exclusion path entirely and resolved *to* row one, on a
#: confirmation card, from a reply whose whole purpose was to say no to it. "wasnt the
#: first one" and "isnt the second one" did the same. It is not a dialect or a typo class
#: worth being strict about: it is how a large share of people type, and being strict
#: about it costs a write against the row they ruled out.
#:
#: The stems are enumerated rather than matched as ``n'?t\b``, which was the first cut and
#: is unusable: without the apostrophe that pattern matches every word ending in "nt", so
#: "I want the first one" and "the recent one" and "the second component" all become
#: negations. Each entry below is the contraction's actual stem — "ca" for can't, "wo" for
#: won't — so the alternation only ever fires on a contraction that really exists.
_NEGATION = re.compile(
    r"\bnot\b|"
    r"\b(?:do|does|did|is|are|was|were|has|have|had|ca|wo|sha|ai|could|would|should|"
    r"must|might|need|dare)n'?t\b|"
    r"\bnever\b|\bneither\b|\bnor\b|\bexcept\b|\bexcluding\b|"
    r"\bbesides\b|\bother\s+than\b|\b(?:any|anything|everything|all)\s+but\b|"
    r"\bbut\s+not\b|\baside\s+from\b|\bapart\s+from\b")

#: Saying the question cannot be answered. This is an answer — the person read the rows
#: and told the runtime the rows were not enough — and before it existed it was filed
#: with "what is my account health": the turn declined, the question was burned, and the
#: numbered list stayed on screen above a number that would no longer do anything.
#:
#: Every phrase here also trips ``_NEGATION``, which is why the exclusion reader hands
#: back to ``_unread`` when it finds a negation that named no row rather than treating
#: the absence as silence.
_UNDECIDED = re.compile(
    r"\bdunno\b|\bunsure\b|\bno\s+idea\b|\bnot\s+certain\b|\bnot\s+sure\b|"
    r"\b(?:do\s*n't|don'?t|do\s+not|did\s+not|didn'?t)\s+know\b|"
    r"\bcan'?t\s+tell\b|\bcannot\s+tell\b|\bcan'?t\s+remember\b|"
    r"\bno\s+clue\b|\bhard\s+to\s+say\b")

#: Asking for the whole list at once. Also an answer, and a different one: the person is
#: not stuck, they want something the chooser has no way to express.
#:
#: They are told so rather than served, and that is a fact about the registry and not a
#: policy invented here. Every alert write — pause, resume, delete, update — takes one
#: ``alert_id``; there is no bulk capability anywhere in the eighty. Reading this reply
#: as a fan-out would mean synthesising a multiplication of writes from a phrase, with
#: one confirmation card standing in for all of them, which is exactly the shape of
#: consent this whole layer exists to refuse.
#:
#: Bare "all" is admitted because ``_unread`` runs only after every row-naming reading
#: has declined, so by the time this is consulted "all" has already failed to be a row.
#: It is not consulted at all when the reply carries a negation cue, since "all but that
#: one" is an exclusion that happens to start with the word.
_EVERY_ROW = re.compile(
    r"\ball\b|\bboth\b|\bevery\s+(?:one|single\s+one|alert|rule)\b|"
    r"\beach\s+(?:one|of\s+(?:them|these|those))\b|\bthe\s+lot\b|\bthe\s+whole\s+lot\b")

#: Words that carry no meaning of their own around a withdrawal, so a message made only
#: of these plus one withdrawal phrase is still just a withdrawal.
#:
#: Kept small and deliberately excludes anything that could name or modify a thing.
#: "that" and "it" earn their place because "cancel that" is the commonest phrasing
#: there is; "the third one" does not, and that asymmetry is the whole guard.
_WITHDRAWAL_FILLER = (r"(?:please|just|actually|then|now|ok|okay|sorry|wait|"
                      r"hold\s+on|it|that|this|thanks|thank\s+you)")

#: Calling off a staged action in words.
#:
#: Anchored end to end, and that is the single most important thing about it. Every
#: other vocabulary in this file is a ``search`` over a reply that has already failed to
#: be anything else; this one is a ``fullmatch`` in all but name, because it is consulted
#: while a real grant is live and a false positive here does not misread a reply — it
#: destroys permission the person was still deciding about.
#:
#: The overshoot this shape exists to prevent is bare "no". "No" is a withdrawal on its
#: own and a discourse marker in front of a correction, and the two are indistinguishable
#: by presence: Batch 15 established that "no, the third one" must keep resolving, and
#: "no, make it 95000" is a person changing a number rather than abandoning it. Requiring
#: the withdrawal to be the *whole* message separates them without a special case —
#: "the third one" and "make it 95000" are not filler, so neither message matches, while
#: "no", "no thanks" and "actually no" all do.
#:
#: "undo" is absent on purpose. It asks to reverse something that already happened, which
#: is a different request with a different answer, and swallowing it here would tell
#: someone their completed change was cancelled when it was not.
#:
#: "stop" is admitted because the bare word is unambiguous next to a confirmation card,
#: and "stop the bitcoin alert" never reaches this code — it routes to a capability, and
#: :func:`handle` only consults this vocabulary for a message that routed nowhere.
_WITHDRAWN = re.compile(
    r"^\W*(?:" + _WITHDRAWAL_FILLER + r"\W+)*"
    r"(?:cancel|abort|"
    r"never\s*mind|nvm|"
    r"forget\s+(?:it|that|this|about\s+it)|"
    r"leave\s+it|drop\s+it|skip\s+it|"
    r"scrap\s+(?:it|that)|call\s+it\s+off|"
    r"(?:do\s*n't|don'?t|do\s+not)\s+(?:bother|do\s+it|worry\s+about\s+it)|"
    r"no\s+thanks?|no\s+thank\s+you|"
    r"stop|"
    r"no)"
    r"(?:\W+" + _WITHDRAWAL_FILLER + r")*\W*$",
    re.IGNORECASE)

#: Words that carry no distinguishing power, so a reply consisting only of these has
#: not narrowed anything. Filtered before the label match, because "the one" would
#: otherwise match every candidate whose label contains "one" and be read as a choice.
_CHOICE_STOPWORDS = frozenset({
    "the", "one", "that", "this", "it", "a", "an", "my", "please", "ok", "okay",
    "yes", "yeah", "sure", "do", "go", "with", "use", "pick", "choose", "select",
    "and", "for", "of", "to", "alert", "alerts", "number", "no", "id", "option",
})


def _choice_id(choice: dict[str, Any]) -> int:
    """The id a candidate row carries, whatever the resolver called it."""
    for key in ("alert_id", "rule_id", "id", "resource_id"):
        value = choice.get(key)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _choice_words(choice: dict[str, Any]) -> set[str]:
    """The words that could distinguish this candidate from its siblings.

    Drawn from the fields a chooser would actually display. Numbers are excluded on
    purpose: a threshold of 90000 appearing in a label must not let the reply "90000"
    be read as naming this row, because "90000" is far more likely to be an answer to
    a different question entirely.
    """
    words: set[str] = set()
    for key in ("symbol", "display_name", "label", "title", "name", "condition"):
        value = choice.get(key)
        if not value:
            continue
        for word in re.findall(r"[a-z]{2,}", str(value).lower()):
            words.add(word)
    symbol = str(choice.get("symbol") or "").upper()
    for alias, code in _SYMBOL_ALIASES.items():
        if code == symbol and re.fullmatch(r"[a-z]{2,}", alias):
            words.add(alias)
    return words - _CHOICE_STOPWORDS


#: Why a reply to a chooser did not name a row. Empty means the reply was not aimed at
#: the question at all, which is the only case that should reach the person as silence.
#:
#: The two named misses are the ones the runtime has positive evidence about. That
#: matters more than it sounds: without evidence, distinguishing "tried to answer" from
#: "changed the subject" would need a vocabulary of reply-shaped phrases, and a
#: vocabulary is a guess that grows. These two are facts about the *candidate list* — a
#: word that matched several rows, a number that matched none — and neither requires
#: knowing anything about English.
#:
#: The two below join them on the same terms. Neither needs a vocabulary of reply-shaped
#: phrases either: one is the fact that every row the reply named, it named in order to
#: rule out, and the other is the fact that the reply ordered the list by a property the
#: list is not ordered by. Both are things the runtime can prove about the reply and the
#: candidate list together, and both were silence before.
#: The last two are the only members of this set that *are* vocabularies, and the reason
#: is that they are the only two replies to "which one?" that name no row and are still
#: unmistakably about the question. Every other miss is proved from the reply and the
#: candidate list together — a number out of range, a word on two rows, an ordering the
#: list does not have. "I don't know" and "all of them" cannot be proved that way,
#: because what makes them answers is what they mean, not what they point at.
#:
#: Keeping them small is the discipline that makes them safe. Each phrase in the two
#: patterns below is one a person types *at a numbered list*, and neither pattern is
#: consulted until every reading that could name a row has already declined. A wrong
#: match here costs a re-ask with the rows still on screen, never a write.
CHOICE_MISS_AMBIGUOUS = "ambiguous"
CHOICE_MISS_NO_SUCH_ROW = "no_such_row"
CHOICE_MISS_EXCLUDED = "excluded"
CHOICE_MISS_UNORDERED = "unordered"
CHOICE_MISS_UNDECIDED = "undecided"
CHOICE_MISS_EVERY_ROW = "every_row"


class ChoiceReading:
    """What a reply to a chooser turned out to be.

    ``chosen`` is the id, or 0. ``miss`` says why not, and exists because
    ``answer_for_choice`` used to answer that question with ``None`` for three different
    situations — a row named, a row named twice, and a message about something else —
    and the caller then treated all three as the last one. The person who typed "the
    bitcoin one" against three bitcoin alerts got no card, no error and no question left
    to answer again.
    """

    __slots__ = ("chosen", "miss")

    def __init__(self, chosen: int = 0, miss: str = "") -> None:
        self.chosen = int(chosen)
        self.miss = miss


def answer_for_choice(choices: list[dict[str, Any]], text: str) -> int | None:
    """Read "which one?" out of a reply, or return ``None``.

    A thin wrapper over ``read_choice``, kept because the id is what almost every caller
    wants and because ``None`` is the right answer to "which row" when there isn't one.
    Callers that must tell a failed answer from an unrelated message ask ``read_choice``.

    Three readings, and a candidate is chosen only when they do not contradict each
    other. Ordered by how directly each names a row.

    *The id itself* — "alert 3", "3". Accepted only when the number is one of the
    candidates. A number outside the set is not a bolder choice, it is evidence the
    reply is about something else, and the pre-existing behaviour for a message that
    means something else is to decline.

    *A position* — "the first one", "the second". An index into the list the person was
    shown, which is why the shown list is stored rather than rebuilt.

    *A distinguishing word* — "the bitcoin one". Taken only when it matches exactly one
    candidate. A word shared by two of them has not chosen between them, and picking
    the earlier would be a guess wearing the costume of a reading.

    The contradiction rule is the part worth defending, and it is kept below for every
    reply that carries more than a number. A bare "2" inside a sentence against
    candidates with ids ``[7, 2]`` is both "the alert whose id is 2" and "the second one
    shown", and those name different rows; declining costs a sentence, while guessing
    costs a write to an alert the person did not choose.

    A reply that is *only* a number is the exception, and it is an exception the runtime
    owes rather than one it takes. The rows are sent numbered — ``_numbered`` stamps
    ``choice_index`` 1..N in the order shown — and that number is the only handle on a
    row the person can see, since ids are not on screen unless they typed one. Reading a
    lone digit through the contradiction rule therefore refused the answer the chooser
    itself invites, and refused it *unevenly*: against three alerts created in order,
    the shown list runs position 1→id 3, 2→id 2, 3→id 1, so "2" resolved and "1" and "3"
    did not. Which rows are answerable was decided by an arithmetic coincidence between
    a position and an id, and nothing on screen told the person which ones those were.

    Two things make the position reading safe here rather than merely convenient. It is
    only taken when the reply is nothing but a number, so a sentence that happens to
    contain one still goes through the contradiction rule intact. And the chosen id goes
    back through ``resolve_arguments`` and lands on a confirmation card that names the
    row, because a continued turn is never ``is_explicit`` — so a misreading is shown to
    the person and refusable before anything is written, which is not true of the
    ambiguous readings this rule still declines.
    """
    reading = read_choice(choices, text)
    return reading.chosen or None


def _rows_named(choices: list[dict[str, Any]], ids: list[int],
                segment: str) -> tuple[set[int], bool, bool]:
    """Which rows a stretch of reply names, however it names them.

    Carried out of ``read_choice`` because Batch 15 needs to ask the question twice of
    the same reply — once of the parts that assert a row and once of the parts that rule
    one out — and a rule applied to a whole reply cannot tell those apart.

    Returns the ids named, whether a word matched several rows, and whether a position
    word overshot the list. The last two are not readings, they are the two facts about
    *why* there is no reading that the caller is allowed to say out loud.

    ``overshot`` is the position analogue of "alert 8" against three rows. "The fourth
    one" against a three-row chooser is not a message about something else — it names
    the very kind of thing the question is about and gets the number wrong — and it was
    silence until this line existed, which meant the chooser could draw three rows, be
    answered about a fourth, and end the turn with no card at all.
    """
    readings: set[int] = set()
    overshot = False

    named = re.search(r"\b(?:alert|rule|id)\s*#?\s*(\d+)\b", segment)
    if named:
        if int(named.group(1)) in ids:
            readings.add(int(named.group(1)))
    else:
        numbers = {int(value) for value in re.findall(r"\b(\d+)\b", segment)}
        if len(numbers) == 1:
            number = numbers.pop()
            if number in ids:
                readings.add(number)
            # A position may also be written as a digit — "2" after a two-item chooser.
            # Only within the list's own length; "9" against two candidates is not a
            # position, it is a number that belongs to some other sentence.
            if 1 <= number <= len(ids):
                readings.add(ids[number - 1])

    # The penultimate phrase is *removed* before the ordinal and last-word searches, not
    # merely tested alongside them, because it contains both of their triggers: "second
    # to last" carries "second", and every one of these phrases carries "last".
    residue = _PENULTIMATE_WORDS.sub(" ", segment)
    if residue != segment:
        if len(ids) >= 2:
            readings.add(ids[-2])
        else:
            overshot = True

    for word, index in _ORDINALS.items():
        if re.search(rf"\b{re.escape(word)}\b", residue):
            if index < len(ids):
                readings.add(ids[index])
            else:
                overshot = True
    if _LAST_WORDS.search(residue):
        readings.add(ids[-1])

    spoken = set(re.findall(r"[a-z]{2,}", segment)) - _CHOICE_STOPWORDS
    shared = False
    if spoken:
        matched = [ids[position] for position, choice in enumerate(choices)
                   if spoken & _choice_words(choice)]
        if len(matched) == 1:
            readings.add(matched[0])
        elif len(matched) > 1:
            # Named something two candidates share. Not a choice, and not a reply about
            # anything else either — which this line has said since Batch 7 while
            # returning the same ``None`` as a reply about something else, so the
            # distinction it draws reached nobody. It is now carried out of here.
            shared = True

    return {value for value in readings if value > 0}, shared, overshot


def _unread(reply: str, alone: bool, overshot: bool) -> ChoiceReading:
    """No row was named. Decide whether that is owed a sentence or owed silence.

    The order is the order of the evidence's strength, and the default at the bottom is
    silence rather than a reassuring guess: a message the runtime has no evidence about
    is a message about something else, and a question that starts answering those has
    started swallowing unrelated sentences.
    """
    if alone or overshot:
        return ChoiceReading(miss=CHOICE_MISS_NO_SUCH_ROW)
    if _UNDECIDED.search(reply):
        # "I don't know" is the reply that most obviously belongs to the question and
        # was most completely dropped by it: filed as a message about something else,
        # so the turn declined, the question burned, and the next thing the person
        # typed — a number, off the list still on their screen — answered nothing.
        #
        # Placed above the recency reading on purpose. "I'm not sure which is the
        # newest" is a person saying they cannot decide, not a person ordering the
        # list, and telling them the list is not in date order answers a question they
        # did not ask.
        return ChoiceReading(miss=CHOICE_MISS_UNDECIDED)
    if _EVERY_ROW.search(reply) and not _NEGATION.search(reply):
        # Not stuck — asking for something the chooser cannot express. Read here and
        # refused plainly rather than fanned out; see ``_EVERY_ROW`` for why that is
        # the registry's answer and not a preference.
        return ChoiceReading(miss=CHOICE_MISS_EVERY_ROW)
    if _RECENCY_WORDS.search(reply):
        # "The newest one" against a chooser is unmistakably an answer — it is an
        # ordering word about a numbered list, and there is nothing else in the
        # conversation it could be ordering. What it is not is *readable*, and the
        # reason is worth being exact about, because the tempting fix is wrong.
        #
        # The rows are drawn in the order the store returned them, and that order is
        # ``active first, then updated_at DESC, id DESC`` — so row 1 is the most recently
        # *touched* active alert, not the most recently created one, and a paused alert
        # edited a minute ago sorts below an active one untouched for a month. Mapping
        # "newest" onto row 1 would therefore be right most of the time and quietly wrong
        # exactly when the account is interesting.
        #
        # Reading it from the timestamps the rows carry was measured and rejected too.
        # ``created_at`` is stored to the second, and three alerts made in one sitting
        # come back with identical values — so that reading would resolve or fall silent
        # depending on how fast the person had been typing when they set the alerts up.
        # An ordinal has to mean the same thing every time it is typed, and one that is
        # decided by a clock's resolution does not.
        #
        # So it is reported, not read. The person is told the list is not in date order
        # and asked for the number, which is a sentence they can act on and the exact
        # thing they got none of before.
        return ChoiceReading(miss=CHOICE_MISS_UNORDERED)
    return ChoiceReading()


def _read_excluding(choices: list[dict[str, Any]], ids: list[int],
                    reply: str) -> ChoiceReading:
    """Read a reply that rules rows out, rather than one that picks one.

    ``read_choice`` collects readings by searching for words anywhere in the reply. That
    rule cannot tell naming a row from ruling it out, so "not the first one" resolved
    *to* the first one — a wrong row, on a confirmation card, from a reply whose whole
    purpose was to say no to it. Silence would have been better; this was worse than
    silence, because it produced a write the person then had to catch.

    Scope is the comma, and nothing larger. Full negation scope is a vocabulary, and a
    vocabulary is a guess that grows; a comma is punctuation the person typed. It is also
    the boundary that actually matters here, because it is what separates the discourse
    marker from the operator: "no, the third one" is a correction that names a row and
    must keep resolving, while "no, not the third one" rules that same row out. Splitting
    on the comma reads both correctly without "no" having to mean anything at all.

    What survives is set subtraction over the list the person was shown, which is why the
    complement is trusted when one row is left. "Neither the first nor the second" against
    three rows names the third as surely as saying so, and the published chooser is the
    complete set by construction. Two rows left is not a choice and is re-asked.
    """
    asserted: set[int] = set()
    excluded: set[int] = set()
    shared = False
    overshot = False
    for segment in re.split(r"[,;]+", reply):
        if not segment.strip():
            continue
        found, hit, over = _rows_named(choices, ids, segment)
        shared = shared or hit
        overshot = overshot or over
        if _NEGATION.search(segment):
            excluded |= found
        else:
            asserted |= found

    # A row the person ruled out is never chosen, whatever else the reply also said.
    asserted -= excluded

    if len(asserted) == 1:
        return ChoiceReading(chosen=asserted.pop())
    if shared or len(asserted) > 1:
        return ChoiceReading(miss=CHOICE_MISS_AMBIGUOUS)
    if not excluded:
        # A negation that named no row. "I'm not sure" and "do not pause it" reach here,
        # and neither is an answer to "which one" — so this stays exactly as silent as it
        # was before the exclusion path existed.
        return _unread(reply, False, overshot)

    remaining = [value for value in ids if value > 0 and value not in excluded]
    if len(remaining) == 1:
        return ChoiceReading(chosen=remaining[0])
    return ChoiceReading(miss=CHOICE_MISS_EXCLUDED)


def read_choice(choices: list[dict[str, Any]], text: str) -> ChoiceReading:
    """``answer_for_choice``'s reading, with the reason it declined kept.

    Every rule here is the one documented on ``answer_for_choice``; the only thing this
    function adds is refusing to throw away *which* rule declined.

    Two of the refusals are evidence that the reply was an attempt to answer. A word
    matching several rows is one — the person named something, and the thing they named
    is on the card more than once. A reply that is nothing but a number matching no row
    is the other; a bare number against a live chooser cannot be a message about
    anything else, because there is nothing else a bare number says. Both leave the
    person owed a sentence. Every other refusal is a message about something else and is
    owed nothing, which is why ``miss`` stays empty for them rather than defaulting to
    something reassuring.
    """
    reply = clean(text, MAX_TEXT_CHARS).strip().lower()
    if not reply or not choices:
        return ChoiceReading()
    ids = [_choice_id(choice) for choice in choices]
    if not any(ids):
        # No row carries a usable id, so nothing here can be chosen. Reported as a miss
        # rather than as silence: the rows were drawn, so the person could pick one, and
        # a pick that lands on a malformed list is the runtime's fault to explain.
        return ChoiceReading(miss=CHOICE_MISS_NO_SUCH_ROW)

    if _NEGATION.search(reply):
        # Handled before anything else, because every rule below reads by presence and
        # presence is exactly what a negated reply cannot be read by. Nothing else in
        # this function changes shape for a reply without a negation cue in it.
        return _read_excluding(choices, ids, reply)

    # A number introduced by the noun is an id and nothing else. "Alert 1" is the way a
    # person names a row, never the way they name a position — nobody says "alert 1" to
    # mean "the first one shown". Reading it both ways would make the most explicit
    # reply available the one most likely to be refused for ambiguity.
    #
    # A bare ``#`` is deliberately not in this list, though it was briefly. "#3" is the
    # way a numbered list is referred to, not the way a resource is named: the number
    # beside the row is on screen and the id is not, so "#3" means the third row and is
    # left to the position reading below. "Alert #3" keeps its meaning, because there
    # the noun is doing the naming and the sigil is decoration.
    named = re.search(r"\b(?:alert|rule|id)\s*#?\s*(\d+)\b", reply)
    if named and int(named.group(1)) in ids:
        return ChoiceReading(chosen=int(named.group(1)))
    if named:
        # The noun and a number, naming no row on the card. "Alert 8" against three rows
        # is not a message about something else — it names the very kind of thing the
        # question is about, and gets the number wrong. That is stronger evidence of an
        # attempted answer than a bare number, which is why it is reported as a miss
        # rather than left to fall through and be read as a different sentence.
        return ChoiceReading(miss=CHOICE_MISS_NO_SUCH_ROW)

    # Nothing but a number: the position on the card, and not an id. Trailing "." or ")"
    # is allowed because a person answering a numbered list writes the number the way
    # the list is written.
    alone = re.fullmatch(r"#?\s*(\d+)\s*[.)]?", reply)
    if alone:
        position = int(alone.group(1))
        if 1 <= position <= len(ids) and ids[position - 1] > 0:
            return ChoiceReading(chosen=ids[position - 1])
        # Out of range, so not a position. It may still be an id, which the reading
        # below decides — "7" against three rows means id 7 or it means nothing.

    readings, shared, overshot = _rows_named(choices, ids, reply)
    if shared:
        return ChoiceReading(miss=CHOICE_MISS_AMBIGUOUS)

    if len(readings) > 1:
        # Several rows, each named by a different part of the reply. Ambiguous in the
        # same way and for the same reason: the person aimed at the list and hit more of
        # it than they meant to.
        return ChoiceReading(miss=CHOICE_MISS_AMBIGUOUS)
    if not readings:
        return _unread(reply, bool(alone), overshot)
    chosen = readings.pop()
    if chosen in ids and chosen > 0:
        return ChoiceReading(chosen=chosen)
    return _unread(reply, bool(alone), overshot)


def answer_pending(spec: CapabilitySpec, pending_arguments: dict[str, Any],
                   missing: tuple[str, ...], text: str) -> dict[str, Any] | None:
    """Merge a reply into a remembered question, or decline.

    Declining is the important half. The rule is that *every* field the question asked
    about must come back filled: a partial answer to a two-field question leaves the
    runtime in the same place it started, and the alternative — asking again with one
    field crossed off — is a conversation the person did not agree to have with a
    system that cannot yet hold one.

    The remembered arguments are the base and the reply may only add to them. This is
    what stops the second turn from moving the target. "Change alert 3" then "95000"
    must retarget alert 3; if the reply were allowed to overwrite ``alert_id`` as well,
    a reply containing any number at all could point the write somewhere else, and the
    person would have approved a card for an alert they never named.
    """
    fields = {item.name: item for item in spec.fields}
    filled = dict(pending_arguments)
    for name in missing:
        field = fields.get(name)
        if field is None:
            return None
        value = answer_for_field(field, text)
        if value is None:
            return None
        filled[name] = value
    return filled


def resolve_arguments(user_id: int, spec: CapabilitySpec, text: str,
                      arguments: dict[str, Any], *,
                      reference_resolver: Any = None) -> Resolution:
    """Turn a sentence into the arguments a capability needs.

    Moved out of ``handle`` unchanged. It was a hundred and thirty lines in the middle
    of the request path, which had two consequences worth undoing. It could not be
    exercised without a database, a user, a gateway and a live registry, so nothing
    tested it directly and its coverage was whatever the end-to-end tests happened to
    walk through. And it could not be *asked* anything: there was no way to pose the
    question "does every capability the matcher can reach have a way to fill its
    required fields?", because the only way to run the extraction was to run a turn.

    The order of the branches is preserved exactly, including the ones that overlap.
    ``messages.search`` is reached by both its own query rule and the generic ``query``
    rule below it; the specific one runs first and the generic one then finds the field
    already set. That is load-bearing and easy to break by tidying, so it is stated
    here rather than left to be rediscovered.

    Only the alert branch needs ``user_id``: resolving "my Bitcoin alert" means reading
    the account's alerts, and ownership is checked by that read. Everything else is a
    function of the sentence.

    ``reference_resolver`` exists for callers that want to ask what a *sentence* yields
    without standing up an account — the benchmark, principally, which measures whether
    the extraction can fill a capability's fields across eight hundred phrasings and has
    no business also testing alert ownership. It is an injection point, not a bypass:
    the request path never passes it, so the ownership-checked read is what runs in
    production, and a stub cannot be reached from there.
    """
    arguments = dict(arguments)
    resolved_count = 1
    unresolved_reference: Reference | None = None

    # Resolve which resource the request is about. A capability that names an
    # ``alert_id`` field needs exactly one, and the count travels to the gateway so
    # that ambiguity is refused there rather than guessed at here.
    if any(item.name == "alert_id" for item in spec.fields):
        resolver = reference_resolver or resolve_alert_reference
        reference = resolver(int(user_id), text, explicit_id=arguments.get("alert_id"))
        resolved_count = reference.count
        if reference.unique:
            arguments["alert_id"] = reference.resource_id
        else:
            # Noted and carried, not returned. Returning here would be the natural
            # shape and is wrong: the rest of this function is what reads the price,
            # the direction, the query out of the sentence, and a message whose
            # *target* is ambiguous has usually said everything else perfectly well.
            # "Change my alert to 95000" names one number and two alerts; abandoning
            # extraction at the first difficulty threw the 95000 away, so the turn
            # that answered "which one?" arrived with nothing but an id and had to ask
            # for the price the person had already given.
            unresolved_reference = reference

    if any(item.name == "push" for item in spec.fields):
        derived = resolve_notification_arguments(text, arguments)
        if derived is None:
            # The sentence named a setting but not a direction. Answered here, for the
            # same reason ambiguous alert references are: the gateway would reject this
            # as a missing required field, which is true and unhelpful.
            return Resolution(
                arguments, resolved_count=resolved_count,
                unresolved=Reference(0, detail="Tell UNDX whether to turn that on or off."))
        arguments = derived
    if spec.capability_id == "saved.items.list":
        arguments = resolve_saved_arguments(text, arguments)
    if spec.capability_id == "saved.post.set":
        arguments = resolve_saved_post_write_arguments(text, arguments)
    if spec.capability_id == "social.followers.list":
        arguments = resolve_relationship_arguments(text, arguments)
    if spec.capability_id in {"social.follow", "social.unfollow"}:
        arguments = resolve_user_target_arguments(text, arguments)
    if spec.capability_id in {"crypto.alerts.create", "crypto.alerts.update"}:
        arguments = resolve_alert_write_arguments(text, arguments)
    if spec.capability_id in {
        "messages.list", "messages.search", "conversations.summarize",
        "messages.suggest", "messages.draft",
    } and not arguments.get("conversation_id"):
        found = resource_reference(text, _RESOURCE_NOUNS["conversation_id"])
        if found:
            arguments["conversation_id"] = found
    if spec.capability_id == "messages.search" and not arguments.get("query"):
        match = re.search(r"(?:where|for)\s+(.+?)(?:\s+in\s+conversation\s+\d+)?[.!?]?$", text, re.IGNORECASE)
        if match:
            arguments["query"] = clean(match.group(1), 120)
    if spec.capability_id == "messages.draft" and not arguments.get("body"):
        match = re.search(r"(?:saying|say|body)\s+(.+?)(?:\s+to\s+conversation\s+\d+)?[.!?]?$", text, re.IGNORECASE)
        if match:
            arguments["body"] = clean(match.group(1), 2000)
    if spec.capability_id in {
        "feed.posts.get", "comments.list", "feed.posts.like", "feed.posts.unlike",
        "feed.post.performance.summary", "feed.comments.summary",
    } and not arguments.get("post_id"):
        found = resource_reference(text, _RESOURCE_NOUNS["post_id"])
        if found:
            arguments["post_id"] = found
    if spec.capability_id.startswith("reels.") and spec.capability_id != "reels.search" and not arguments.get("reel_id"):
        found = resource_reference(text, _RESOURCE_NOUNS["reel_id"])
        if found:
            arguments["reel_id"] = found
    if spec.capability_id.startswith("status.") and spec.capability_id != "status.list" and not arguments.get("status_id"):
        found = resource_reference(text, _RESOURCE_NOUNS["status_id"])
        if found:
            arguments["status_id"] = found
    if spec.capability_id == "profile.preferences.update" and not arguments.get("preferred_language"):
        lowered = text.lower()
        for label, code in (("english", "en"), ("spanish", "es"), ("french", "fr")):
            if label in lowered:
                arguments["preferred_language"] = code
                break
    if spec.capability_id == "feed.posts.list":
        lowered = text.lower()
        if "my latest" in lowered or "my posts" in lowered:
            arguments["feed"] = "my_posts"
        elif "trending" in lowered:
            arguments["feed"] = "trending"
        elif "following" in lowered:
            arguments["feed"] = "following"
    query_field = next((item for item in spec.fields if item.name == "query"), None)
    if query_field is not None and not arguments.get("query"):
        # Search phrases are filters, never authority. Keep the extraction bounded
        # and let the owner-scoped domain service decide which records are visible.
        found = resolve_query_argument(
            text, strip_preamble=query_field.required and query_field.default is None)
        if found:
            arguments["query"] = found
    for field_name in ("notification_id", "listing_id", "order_id", "live_id"):
        if any(item.name == field_name for item in spec.fields) and not arguments.get(field_name):
            found = resource_reference(text, _RESOURCE_NOUNS[field_name])
            if found:
                arguments[field_name] = found
    if spec.capability_id == "settings.explain" and not arguments.get("section"):
        lowered = text.lower()
        arguments["section"] = next(
            (section for section in ("privacy", "notifications", "language", "accessibility")
             if section in lowered),
            "all",
        )

    missing = missing_required(spec, arguments)
    if unresolved_reference is not None:
        # "Which one?" outranks "what price?", and the order is not arbitrary: the
        # person is being shown a list of their own rows and asked to point at one,
        # which is a smaller thing to ask than a value they have to compose. The price
        # they may also owe is asked on the turn after, by which time the target is
        # settled and the question can name it.
        return Resolution(arguments, resolved_count=resolved_count, missing=missing,
                          unresolved=unresolved_reference, choice_field="alert_id")
    if missing:
        # Asked rather than passed on, for the same reason an ambiguous alert reference
        # is. The gateway would reject this too, and correctly, but as a schema error
        # naming a field — accurate, unanswerable, and identical whether the person
        # forgot to say something or said something the product does not support.
        return Resolution(arguments, resolved_count=resolved_count, missing=missing,
                          unresolved=Reference(0, detail=_missing_field_question(spec, missing)))
    return Resolution(arguments, resolved_count=resolved_count)


# ---------------------------------------------------------------------------
# Native result cards
# ---------------------------------------------------------------------------


def _card_type(spec: CapabilitySpec, status: str) -> str:
    """Choose the card by outcome first, capability second.

    A failed setting change is a failure card, not a settings receipt. Rendering a
    receipt-shaped card for something that did not happen is how an interface ends
    up implying success that the data does not support.
    """
    if status == AgentOutcome.CONFIRMATION_REQUIRED:
        return CardType.ACTION_CONFIRMATION
    if status == AgentOutcome.CLARIFICATION_REQUIRED:
        # A chooser is built by ``_unresolved_response``, which knows whether it has
        # rows to offer; this line is for a clarification arriving by any other route.
        # Without it the fall-through below would hand back ``spec.result_card`` — the
        # capability's *success* card — which is precisely how a question about which
        # alert to pause came to be drawn as though an alert had been paused.
        return CardType.CLARIFICATION_REQUIRED
    if status == AgentOutcome.PERMISSION_DENIED:
        return CardType.PERMISSION_DENIED
    if status == AgentOutcome.UNSUPPORTED_CAPABILITY:
        return CardType.UNSUPPORTED_CAPABILITY
    if status == AgentOutcome.RECOVERABLE_FAILURE:
        return CardType.RETRY_ACTION
    if status == AgentOutcome.TERMINAL_FAILURE:
        return CardType.ACTION_FAILURE
    return spec.result_card


def build_card(spec: CapabilitySpec, outcome: undx_tool_gateway.GatewayOutcome) -> dict[str, Any]:
    """The structured payload the native client renders.

    Contains no prose the client must parse and no HTML. Everything the UI needs to
    decide what to draw — the outcome, the verification state, whether an undo
    exists — is a field, so a client can render this correctly without inspecting
    the human-readable sentence that accompanies it.
    """
    receipt = outcome.receipt
    card: dict[str, Any] = {
        "component": _card_type(spec, receipt.status),
        "capability_id": spec.capability_id,
        "status": receipt.status,
        "verification_state": receipt.verification_state,
        "verified": receipt.verification_state == VerificationState.VERIFIED,
        "title": spec.description,
        "message": receipt.user_explanation,
        "risk": spec.risk,
        "deep_link": receipt.native_deep_link,
        "canonical_resource_ids": list(receipt.canonical_resource_ids),
        "task_id": receipt.task_id,
        "undo_capability_id": receipt.undo_capability_id,
        # The client needs both halves to offer Undo. Sending the capability id alone
        # would leave the app to guess the arguments, and the obvious guess — replay
        # what was just sent — is the one that re-applies a preference change instead
        # of reversing it. The gateway clears both together, so `can_undo` reading only
        # the id is not a shortcut: an id without arguments cannot occur.
        "undo_arguments": dict(receipt.undo_arguments),
        "can_undo": bool(receipt.undo_capability_id),
        "timestamp": receipt.timestamp,
    }
    # The evidence reading, carried as fields so the client renders the conclusion the
    # gateway enforced instead of re-deriving one from ``status`` and ``verified`` and
    # arriving somewhere else. ``verified`` above answers "did a read-back confirm the
    # value"; ``may_claim_done`` answers "may this be described to the person as
    # finished". They are not the same question — a lookup can verify perfectly and have
    # completed nothing — and a client drawing "done" from the first field alone is
    # drawing it from a fact that does not support the sentence.
    assessment = outcome.assessment
    if assessment is not None:
        card["evidence_state"] = assessment.state.value
        card["requires_disclosure"] = bool(assessment.requires_disclosure)
        if assessment.contradiction:
            # Present only when the outcome and the read-back actually disagreed, so its
            # presence in a payload is itself the signal. An always-present field that is
            # usually empty gets filtered out of logs and stops being read.
            card["evidence_contradiction"] = assessment.contradiction
    card["may_claim_done"] = outcome.may_claim_done
    if outcome.confirmation is not None:
        grant = outcome.confirmation
        card.update({
            "confirmation_id": grant.confirmation_id,
            "confirmation_token": grant.confirmation_token,
            "expires_at": grant.expires_at,
            "action_name": grant.action_name,
            "target": grant.target,
            # Both, and in that order of precedence on the client. ``target`` is the
            # identity the approval is bound to and has to keep travelling; the label
            # is what the sentence under the title is built from. Sending only the
            # label would leave nothing to bind, and sending only the target is the
            # state this field was added to end.
            "resource_label": grant.resource_label,
            "current_value": grant.current_value,
            "proposed_value": grant.proposed_value,
            "risk_summary": grant.risk_summary,
        })
    if outcome.result is not None:
        if outcome.result.records:
            card["records"] = outcome.result.records
            card["record_count"] = len(outcome.result.records)
        if outcome.result.data:
            card["data"] = outcome.result.data
        if outcome.result.idempotent_replay:
            card["idempotent_replay"] = True
        if outcome.result.degraded_sources:
            # A field rather than prose, so the client can visibly mark the answer
            # partial. Without it a degraded read renders identically to a complete
            # one and an empty section reads as "there is nothing here".
            card["complete"] = False
            card["degraded_sources"] = list(outcome.result.degraded_sources)
    if outcome.verification is not None and outcome.verification.state != VerificationState.VERIFIED:
        # Surfaced so the client can visibly downgrade its own confidence rather than
        # rendering an unverified change identically to a confirmed one.
        card["verification_detail"] = outcome.verification.detail
    return card


# ---------------------------------------------------------------------------
# The runtime entry point
# ---------------------------------------------------------------------------


class AgentResponse:
    """One complete agent turn: what happened, the card, and what to say."""

    __slots__ = ("handled", "receipt", "card", "reply", "capability_id", "latency_ms")

    def __init__(self, *, handled: bool, receipt: AgentReceipt | None = None,
                 card: dict[str, Any] | None = None, reply: str = "",
                 capability_id: str = "", latency_ms: int = 0) -> None:
        self.handled = handled
        self.receipt = receipt
        self.card = card
        self.reply = reply
        self.capability_id = capability_id
        self.latency_ms = latency_ms

    def __bool__(self) -> bool:
        """An unhandled turn is falsy, so ``handle(...) or None`` falls through.

        Without this a caller writing the natural ``if response:`` would treat "this
        was ordinary conversation" as "the agent answered", and reply to the user with
        an empty string. Truthiness tracks ``handled`` and nothing else — a handled
        refusal is still a real answer and must stay truthy.
        """
        return bool(self.handled)

    @property
    def status(self) -> str:
        return self.receipt.status if self.receipt else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "capability_id": self.capability_id,
            "status": self.status,
            "reply": self.reply,
            "card": self.card,
            "latency_ms": self.latency_ms,
            "receipt": (self.receipt.to_dict() if self.receipt and hasattr(self.receipt, "to_dict")
                        else None),
        }


#: What each write capability will leave the resource in, for the confirmation card.
_PROPOSED_STATE = {
    "crypto.alerts.pause": "paused",
    "crypto.alerts.resume": "active",
    "crypto.alerts.delete": "deleted",
}


#: Re-exported under its historical private name so the many call sites inside this
#: module keep working. The definition moved to :mod:`services.undx_agent_contracts`
#: when the *result* sentence had to name its subject too: response intelligence
#: cannot import the runtime, and a second copy of the composition would have let the
#: confirmation card and the receipt word the same alert differently — which is
#: precisely the comparison ``describe_alert`` exists to keep fair.
_amount = format_amount


def preview(user_id: int, spec: CapabilitySpec,
            arguments: dict[str, Any]) -> tuple[Any, Any, str]:
    """Read the row the action would change: its current value, and its name.

    A card that says "confirm this change" without naming the change is not consent,
    it is a habituation exercise — the user learns to press the button without
    reading it. So the current value is fetched with a real read, not inferred from
    what the agent believes it set earlier.

    The third value is the same row in words, and it comes from this read rather than
    a later one on purpose. Two reads would let the card name a row the before-value
    was not taken from — a narrow window, but the one field whose job is to prove the
    card and the action are about the same thing should not be the field that opens
    it. Empty for capabilities whose target is already a word the person typed.

    This is strictly read-only and best-effort. If the read fails the card still
    appears with an unknown current value and no label, because failing to render a
    confirmation is worse than rendering one with a gap in it: the alternative is
    either acting unconfirmed or refusing an action the user is entitled to take. A
    gap is visibly a gap; that is why the label is left empty rather than guessed.
    """
    try:
        if (spec.capability_id.startswith("crypto.alerts.") and arguments.get("alert_id")
                and _read_permitted(user_id, "crypto.alerts.get")):
            result = undx_agent_tools.crypto_alerts_get(int(user_id), {"alert_id": int(arguments["alert_id"])})
            alert = result.data or {}
            label = describe_alert(alert)
            if spec.capability_id == "crypto.alerts.update":
                return alert.get("threshold"), arguments.get("threshold"), label
            return alert.get("status"), _PROPOSED_STATE.get(spec.capability_id), label
        if (spec.capability_id == "notifications.preference.update"
                and _read_permitted(user_id, "notifications.preference.read")):
            category = clean(arguments.get("category") or "global", 40)
            result = undx_agent_tools.notification_preferences_read(int(user_id), {"category": category})
            # No label: the canonical target here *is* the category, which is a word
            # the person typed. A field repeating it would be noise, and this one is
            # only worth having where the target is opaque.
            return (result.data or {}).get("push"), bool(arguments.get("push")), ""
        if spec.capability_id == "saved.post.set" and arguments.get("post_id"):
            from services.saved_content_service import get_post_saved

            state = get_post_saved(int(user_id), int(arguments["post_id"])) or {}
            return state.get("saved"), bool(arguments.get("saved")), ""
        if spec.capability_id in {"social.follow", "social.unfollow"} and arguments.get("target_user_id"):
            from services.social_relationship_service import is_following

            before = is_following(int(user_id), int(arguments["target_user_id"]))
            return before, spec.capability_id == "social.follow", ""
        if spec.capability_id in {"feed.posts.like", "feed.posts.unlike"} and arguments.get("post_id"):
            from services.feed_intelligence_service import get_post_like

            before = get_post_like(int(user_id), int(arguments["post_id"]))
            return before, spec.capability_id == "feed.posts.like", ""
    except Exception:  # pragma: no cover - a preview must never block the action
        return None, None, ""
    return None, None, ""


def _bare_receipt(spec: CapabilitySpec, *, user_id: int, request_id: str, status: str,
                  explanation: str, evidence: dict[str, Any] | None = None) -> AgentReceipt:
    """A receipt for an outcome that never reached the gateway.

    Refusals get the same shape as successes so no caller can mistake a refusal for
    an absence of one — and so the client renders every outcome through one path.
    """
    return AgentReceipt(
        task_id=request_id, request_id=request_id, capability_id=spec.capability_id,
        action=spec.description, status=status, owner_user_id=int(user_id),
        verification_state=VerificationState.IMPOSSIBLE,
        evidence=dict(evidence or {}), native_deep_link=spec.deep_link({}),
        user_explanation=clean(explanation, 400), risk_level=spec.risk,
    )


def _numbered(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp each candidate with the position it occupies in the question.

    "The first one" is only a meaningful answer if the person and the runtime agree on
    which one is first. The server already fixed an order by returning this list; making
    that order explicit in the payload means a client that sorts its chooser differently
    is visibly disagreeing with the server rather than silently doing so, and the
    ordinal a reply carries is an index into the numbering shown here.

    Copies rather than mutates. The candidate rows come from a tool result, and a
    presentation concern has no business writing into evidence.
    """
    return [{**choice, "choice_index": position}
            for position, choice in enumerate(candidates, 1)]


def _unresolved_response(spec: CapabilitySpec, reference: Reference, request_id: str,
                         user_id: int, started: float,
                         awaiting: tuple[str, ...] = ()) -> AgentResponse:
    """Ask which one, instead of picking one.

    The candidate list travels with the card so the client can render a chooser and
    the next turn arrives with an explicit id. Nothing has been mutated at this
    point, which is the property that makes asking cheap and guessing expensive.

    ``awaiting`` names the fields a remembered question is now waiting on, and it is
    what separates the two things this function is asked to render. With fields
    outstanding the runtime is holding a question open and the next message may close
    it; without them, "you do not have an alert matching that" is a statement, and
    reporting a statement as a question would leave the account holding an invitation
    nobody issued.

    Both used to report ``terminal_failure``. That was wrong in a way with a cost:
    anything counting terminal failures counted every question the runtime asked as
    something breaking, so the metric got worse the more carefully it behaved. Worse
    was what the client did with the chooser — ``spec.result_card`` for crypto alerts
    is ``crypto_alert_card``, which the client's classifier files under *receipt*, so a
    question about which of two alerts to pause was drawn as a result card. Observed
    directly by running the client's own ``kindOf`` rules over real cards from this
    runtime: chooser -> kind ``receipt``, kicker ``RESULT``; missing field -> kind
    ``failure``, kicker ``NOT DONE``.

    The distinct outcomes were deferred across four batches on the belief that an old
    client meeting an unknown enum renders nothing. Reading the client settled it:
    ``kindOf`` defaults to ``failure``, which is what a question renders as today, so
    an old client is unchanged and a new one is correct. The deferral is now spent.
    """
    asking = bool(awaiting)
    receipt = _bare_receipt(
        spec, user_id=user_id, request_id=request_id,
        status=(AgentOutcome.CLARIFICATION_REQUIRED if asking
                else AgentOutcome.TERMINAL_FAILURE),
        explanation=(reference.detail or "UNDX needs to know which one you mean."),
        evidence={"resolved_matches": reference.count,
                  "awaiting_fields": list(awaiting)},
    )
    if not asking:
        component = CardType.ACTION_FAILURE
    elif reference.candidates:
        # A chooser, never ``spec.result_card``. Borrowing the capability's success
        # card to draw a question is how "which of these two?" came to be rendered
        # under the kicker the client reserves for something that already happened.
        component = CardType.CHOICE_REQUIRED
    else:
        component = CardType.CLARIFICATION_REQUIRED
    card = {
        "component": component,
        "capability_id": spec.capability_id,
        "status": receipt.status,
        "verification_state": receipt.verification_state,
        "verified": False,
        "title": spec.description,
        "message": receipt.user_explanation,
        "risk": spec.risk,
        "deep_link": receipt.native_deep_link,
        "task_id": request_id,
        "needs_disambiguation": reference.count > 1,
        # True when the next message can complete this by itself. The client does not
        # have to do anything with it; it is what makes "was this a failure or a
        # question?" answerable from the payload rather than from the prose.
        "needs_answer": bool(awaiting),
        "awaiting_fields": list(awaiting),
        "candidates": _numbered(reference.candidates),
        "record_count": len(reference.candidates),
        "timestamp": receipt.timestamp,
    }
    return AgentResponse(handled=True, receipt=receipt, card=card, reply=receipt.user_explanation,
                         capability_id=spec.capability_id,
                         latency_ms=int((time.monotonic() - started) * 1000))


def _error_response(spec: CapabilitySpec, exc: AgentError, request_id: str,
                    user_id: int, started: float) -> AgentResponse:
    status = getattr(exc, "outcome", "") or AgentOutcome.TERMINAL_FAILURE
    receipt = _bare_receipt(
        spec, user_id=user_id, request_id=request_id, status=status,
        explanation=str(exc), evidence={"code": getattr(exc, "code", "")},
    )
    return AgentResponse(
        handled=True, receipt=receipt,
        card={
            "component": _card_type(spec, status),
            "capability_id": spec.capability_id,
            "status": status,
            "verification_state": receipt.verification_state,
            "verified": False,
            "title": spec.description,
            "message": receipt.user_explanation,
            "risk": spec.risk,
            "deep_link": receipt.native_deep_link,
            "task_id": request_id,
            "timestamp": receipt.timestamp,
        },
        reply=receipt.user_explanation, capability_id=spec.capability_id,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _fault_response(spec: CapabilitySpec, exc: Exception, request_id: str,
                    user_id: int, started: float) -> AgentResponse:
    """An untyped fault, after the turn was known to be an action.

    Everything reaching here provably precedes any mutation — the gateway settles its
    own tail and never raises once an executor has been entered, which is now a tested
    property rather than a docstring. So this is safe to report as "nothing happened",
    and that is what makes it *recoverable* rather than terminal: retrying is not only
    permitted, it is the sensible next move, and ``RECOVERABLE_FAILURE`` already renders
    as a retry card. No new wire enum is needed, which is the difference between this
    and the clarification outcome still carried as unmade.

    Why this exists at all. Falling through was never a lie — nothing had happened — but
    it was a silence. The person asked PulseSoc to pause an alert, an index was missing,
    and what came back was a language model answering something adjacent with no sign
    the request had ever been understood. Truthful and useless are not the same thing,
    and they are distinguishable here precisely because ``spec`` exists: the message was
    recognised. Before that point falling through is still right, because a fault while
    deciding whether "how are you" is an action must not turn a greeting into an error.

    The exception type is recorded and the message is not. Exception text routinely
    carries schema fragments, file paths and identifiers belonging to other rows, and a
    fault is the worst moment to start quoting the database at somebody.

    Built without ``_bare_receipt``, and for the reason Batch 9 established: a handler
    that calls back into the code that may be why it was entered is not a handler.
    ``_bare_receipt`` reaches for ``spec.deep_link({})`` and ``clean()``; this reaches
    for neither.
    """
    try:
        logger.error("undx_turn_faulted capability=%s user=%s error=%s",
                     spec.capability_id, int(user_id), exc.__class__.__name__)
    except Exception:  # pragma: no cover - defensive
        pass
    status = AgentOutcome.RECOVERABLE_FAILURE
    explanation = ("Something went wrong on PulseSoc's side before I could do that, so "
                   "nothing has changed. Please try again.")
    receipt = AgentReceipt(
        task_id=request_id, request_id=request_id, capability_id=spec.capability_id,
        action=spec.description, status=status, owner_user_id=int(user_id),
        verification_state=VerificationState.IMPOSSIBLE,
        evidence={"fault": exc.__class__.__name__, "reached_executor": False},
        user_explanation=explanation, risk_level=spec.risk,
    )
    return AgentResponse(
        handled=True, receipt=receipt,
        card={
            "component": _card_type(spec, status),
            "capability_id": spec.capability_id,
            "status": status,
            "verification_state": receipt.verification_state,
            "verified": False,
            "title": spec.description,
            "message": explanation,
            "risk": spec.risk,
            "deep_link": "",
            "task_id": request_id,
            "timestamp": receipt.timestamp,
        },
        reply=explanation, capability_id=spec.capability_id,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


# ---------------------------------------------------------------------------
# Continuation: the question, and the turn that answers it
# ---------------------------------------------------------------------------
#
# Three functions, and one property shared by all of them: none may raise. The
# continuation layer is strictly additive. Every turn that worked before this existed
# must still work if the store is unreachable, the table is absent, or the cursor is a
# stand-in that does not speak SQL — and the way that is guaranteed is that each of
# these degrades to the pre-existing behaviour rather than propagating. A remembered
# question that fails to persist costs the person a second sentence. An exception here
# would cost them the turn.


def is_renewal(asking: tuple[str, ...], answered: tuple[str, ...]) -> bool:
    """Would remembering this question ask again for something just supplied?

    A backstop, and labelled as one. Every path through :func:`handle` that has been
    instrumented reaches this with ``asking`` and ``answered`` disjoint, because the two
    ways a field leaves the question also remove it from the next one: answering a value
    takes it out of ``missing``, and choosing a candidate resolves the reference so no
    chooser is offered. A mutation that forces this to ``False`` therefore kills no
    journey test, which is worth stating plainly rather than leaving as an apparent gap
    in the suite — the branch is insurance against a future resolver that rejects the
    value it was just handed, not a rule that fires today.

    Kept as a named predicate for two reasons. It can be tested directly, so the
    property is protected even though no journey exercises the branch. And a reader
    comparing this against the no-renewal rule in the docs sees one definition rather
    than an inline expression they have to reconstruct.
    """
    return bool(answered) and bool(asking) and not set(asking) - set(answered)


def _question_fields(resolution: Resolution) -> tuple[str, ...]:
    """What the turn is actually asking about, in one place.

    A question is one of two shapes, and which one it is decides what the reply will be
    read against. An unresolved *reference* is waiting for a choice among rows the
    person was shown; anything else is waiting for a value they have to compose. A
    reference question asks about its own field and nothing else even when other fields
    are also outstanding — the person is looking at a list, and burying "and what price?"
    underneath it would be two questions presented as one.

    Computed here rather than at each use because three decisions read it — what to
    remember, what the card advertises, and whether a continued turn is making progress
    or repeating itself — and those three going out of step is exactly how a question
    becomes unanswerable again.
    """
    reference = resolution.unresolved
    if resolution.choice_field and reference is not None:
        if reference.candidates:
            return (resolution.choice_field,)
        if reference.count == 0:
            # "You do not have an alert matching that" is a statement, not a question.
            # Remembering it would leave the account holding an invitation nobody
            # issued, ready for the next unrelated number to accept.
            return ()
        # More rows than could be compared at once. Nothing to choose between, but the
        # person was genuinely asked to name one, so the reply is read as a value.
    return resolution.missing


def _remember_question(cur, user_id: int, spec: CapabilitySpec, resolution: Resolution,
                       asking: tuple[str, ...]) -> None:
    """Persist what was just asked, durably, before the question is shown."""
    from services import undx_architecture

    # Choices only travel with a reference question. Their presence is what tells
    # recall to read the reply as a choice rather than as a value.
    choices = (_numbered(resolution.unresolved.candidates)
               if resolution.choice_field and resolution.unresolved is not None else [])

    try:
        undx_architecture.create_continuation(
            cur, int(user_id), capability_id=spec.capability_id,
            arguments=resolution.arguments, missing=asking, choices=choices)
        # Durable before the person sees the question. An asked question that rolls
        # back is precisely the defect this layer exists to remove, reintroduced by
        # the transaction rather than by the parser.
        undx_tool_gateway.checkpoint(cur)
    except Exception:  # noqa: BLE001
        logger.warning("undx_continuation_mint_failed capability=%s user=%s",
                       spec.capability_id, int(user_id))


#: What a re-asked question says, by the reason the reply missed. These are the two
#: things the runtime can prove about a reply it could not read, so they are the two
#: things it is willing to say. Neither apologises and neither guesses: each states the
#: fact that stopped the reading and leaves the rows on screen to be answered again.
_REASK_DETAIL = {
    CHOICE_MISS_AMBIGUOUS: "That matches more than one of these. Which number?",
    CHOICE_MISS_NO_SUCH_ROW: "That is not one of these. Which number?",
    CHOICE_MISS_EXCLUDED: "That rules one out rather than picking one. Which number?",
    CHOICE_MISS_UNORDERED: "These are not listed by date, so I cannot tell which is "
                           "newest. Which number?",
    # Does not end in "Which number?" like the others, and that is the point: repeating
    # the question is what a person who just said they cannot answer it least needs.
    # Nothing has changed, the rows are still on screen, and the sentence says where to
    # go and look. It promises nothing UNDX cannot do.
    CHOICE_MISS_UNDECIDED: "Nothing has changed and these are still here. Open your "
                           "alerts to check which one you mean, then tell me the number.",
    CHOICE_MISS_EVERY_ROW: "UNDX changes these one at a time, so it needs one of them. "
                           "Which number?",
}


class _Reask:
    """A reply that was aimed at the open question and did not land.

    Carried out of ``_resume_pending`` as its own thing rather than folded into the
    ``None`` that means "about something else", because the two owe the person opposite
    treatment. A message about something else owes silence — the agent declines and the
    conversation layer takes the turn. A failed answer owes a sentence and the question
    back, and before this existed it got neither: ``handled=False``, no card, and the
    question already burned, so the person could not even try again without retyping the
    request that produced it.
    """

    __slots__ = ("spec", "arguments", "missing", "choices", "miss")

    def __init__(self, spec: CapabilitySpec, arguments: dict[str, Any],
                 missing: tuple[str, ...], choices: list[dict[str, Any]], miss: str) -> None:
        self.spec = spec
        self.arguments = arguments
        self.missing = missing
        self.choices = choices
        self.miss = miss


def _reask_response(cur, reask: _Reask, *, user_id: int, request_id: str,
                    started: float) -> AgentResponse:
    """Say why the reply missed, show the rows again, and re-arm the question.

    Re-arming looks like it contradicts burn-on-use, and the distinction is the whole
    argument for doing it. Burn-on-use exists because a question that outlives a
    non-answer sits waiting to be triggered by an unrelated sentence later, and "the
    second firing is the dangerous one because by then nobody remembers being asked".
    That danger is a *memory* problem, not a lifetime problem. Here the question is
    re-asked in the same turn, on screen, with its rows — so the next message is
    answering something the person was just shown, which is the condition the burn rule
    was protecting and not the lifetime it happened to enforce.

    The re-armed question is a fresh continuation rather than the old one revived. The
    old one is already burned and stays burned; nothing is un-spent, and a store that
    failed to burn still refuses to answer, exactly as before. If minting the new one
    fails the person still gets the sentence and the rows — they simply have to restate
    the request, which is where they were before this function existed.
    """
    from services import undx_architecture

    try:
        undx_architecture.create_continuation(
            cur, int(user_id), capability_id=reask.spec.capability_id,
            arguments=reask.arguments, missing=reask.missing, choices=reask.choices)
        undx_tool_gateway.checkpoint(cur)
    except Exception:  # noqa: BLE001
        logger.warning("undx_reask_mint_failed capability=%s user=%s",
                       reask.spec.capability_id, int(user_id))

    reference = Reference(max(len(reask.choices), 2), 0, list(reask.choices),
                          detail=_REASK_DETAIL.get(reask.miss, _REASK_DETAIL[CHOICE_MISS_NO_SUCH_ROW]))
    return _unresolved_response(reask.spec, reference, request_id, int(user_id), started,
                                awaiting=reask.missing)


def _abandon_pending(cur, user_id: int) -> None:
    """Drop any outstanding question, because the person has moved on."""
    from services import undx_architecture

    try:
        pending = undx_architecture.pending_continuation(cur, int(user_id))
        if pending:
            undx_architecture.burn_continuation(cur, int(user_id),
                                                pending["continuation_id"])
    except Exception:  # noqa: BLE001
        logger.debug("undx_continuation_abandon_failed user=%s", int(user_id))


def _withdraw_pending(cur, user_id: int, text: str, request_id: str,
                      started: float) -> AgentResponse | None:
    """Read this message as calling off a staged action, and kill the grant if it is.

    Returns ``None`` for every message that is not a withdrawal, which is nearly all of
    them, so the caller falls through to its existing behaviour untouched.

    Reached only when the message routed nowhere, and that is the same consult rule
    :func:`_resume_pending` runs under, for the same reason. "Cancel my bitcoin alert"
    is a request to delete an alert and must keep routing to the capability that does
    it; only a message that matched nothing can safely be read as a bare withdrawal.

    Ordered so the cheapest and most selective test runs first. The regex rejects the
    overwhelming majority of traffic without touching the database, which matters
    because this sits on the path every ordinary conversational message takes.

    A live question wins over a withdrawal, and that ordering is what makes bare "no"
    safe to admit at all. With a chooser on screen, "no" is answering the chooser, and
    Batch 15 already settled what it means there; only with a confirmation card and no
    question outstanding is "no" unambiguously "don't". The two are never both live in
    practice — the runtime either asked something or staged something — so deferring to
    the question costs nothing and closes the one case where the word is ambiguous.

    Losing the race to a tapped Confirm is silence, deliberately. If every revoke
    matched no row, the grant was spent microseconds ago and the action has happened;
    saying "cancelled" then would be a lie about a write that is already durable. The
    person sees the receipt the tap produced, which is the truth.
    """
    if not _WITHDRAWN.match(text or ""):
        return None

    from services import undx_architecture

    try:
        grants = undx_architecture.pending_approvals(cur, int(user_id))
    except Exception:  # noqa: BLE001
        logger.debug("undx_withdraw_read_failed user=%s", int(user_id))
        return None
    if not grants:
        return None

    try:
        if undx_architecture.pending_continuation(cur, int(user_id)):
            return None
    except Exception:  # noqa: BLE001
        # Unable to tell whether a question is open. Declining here rather than
        # guessing: reading a reply to an unseen question as a withdrawal destroys
        # permission, and the cost of being wrong the other way is one unanswered
        # "never mind".
        logger.debug("undx_withdraw_continuation_check_failed user=%s", int(user_id))
        return None

    revoked = 0
    for grant in grants:
        try:
            if undx_architecture.revoke_approval(cur, int(user_id), grant["confirmation_id"]):
                revoked += 1
        except Exception:  # noqa: BLE001
            logger.warning("undx_withdraw_revoke_failed user=%s", int(user_id))
    if not revoked:
        return None

    try:
        undx_tool_gateway.checkpoint(cur)
    except Exception:  # noqa: BLE001
        # The revoke could not be made durable, so it did not happen. Reporting a
        # cancellation over an uncommitted transaction would leave a live token behind
        # a card that says it is dead — the worse of the two failures by far, because
        # the button on screen would still work.
        logger.warning("undx_withdraw_commit_failed user=%s", int(user_id))
        return None

    newest = grants[0]
    spec = get(newest["action_id"])
    if spec is None:
        # A grant for a capability this build no longer has. It is still revoked, and
        # that is the part that mattered; there is simply no spec to describe it with,
        # and inventing one would put a made-up action name on the card.
        logger.info("undx_withdraw_unknown_capability action=%s", newest["action_id"])
        return AgentResponse(handled=True, reply="Cancelled — nothing was changed.",
                             latency_ms=int((time.monotonic() - started) * 1000))

    label = ""
    try:
        _, _, label = preview(int(user_id), spec, dict(newest["arguments"] or {}))
    except Exception:  # noqa: BLE001
        label = ""
    # Named where a name is available, for the same reason Batch 16 put the label on the
    # confirmation card: "cancelled" without a subject is only reassuring if you can
    # remember what you staged, and someone who just changed their mind is exactly the
    # person who is unsure what was about to happen.
    message = (f"Cancelled — {label} is unchanged."
               if label else "Cancelled — nothing was changed.")

    receipt = _bare_receipt(
        spec, user_id=int(user_id), request_id=request_id,
        status=AgentOutcome.CANCELLED, explanation=message,
        evidence={"revoked_grants": revoked, "resource_label": label},
    )
    card = {
        "component": CardType.ACTION_CANCELLED,
        "capability_id": spec.capability_id,
        "status": receipt.status,
        "verification_state": receipt.verification_state,
        "verified": False,
        "title": spec.description,
        "message": message,
        "risk": spec.risk,
        "deep_link": receipt.native_deep_link,
        "task_id": request_id,
        "resource_label": label,
        "revoked_grants": revoked,
        "timestamp": receipt.timestamp,
    }
    return AgentResponse(handled=True, receipt=receipt, card=card, reply=message,
                         capability_id=spec.capability_id,
                         latency_ms=int((time.monotonic() - started) * 1000))


def _resume_pending(
    cur, user_id: int, text: str,
) -> tuple[CapabilitySpec, dict[str, Any], tuple[str, ...]] | _Reask | None:
    """Treat this message as the answer to the last question, or decline.

    Returns the fields the reply answered alongside the merged arguments, because the
    caller has to be able to tell "this turn made progress" from "this turn went round
    again" — and the only evidence for that is which question was just closed.

    Reached only when the message routes nowhere, which is the whole of the consult
    rule and is load-bearing in both directions. A message that routes stands on its
    own and must never be reinterpreted as an answer — otherwise a pending "which
    post?" would turn "delete alert 3" into a like on post 3. And a message that
    routes nowhere is, today, a message the agent declines entirely, so consulting a
    remembered question here can only add outcomes, never change existing ones.

    The question is burned on the way through, before the answer is judged. Burning on
    *use* rather than on *success* means a reply that turns out not to be an answer
    still ends the question — which is what a person means when they say something
    else. A question that survived a non-answer would sit waiting to be triggered by
    an unrelated sentence later on, and the second firing is the dangerous one because
    by then nobody remembers being asked.
    """
    from services import undx_architecture

    try:
        pending = undx_architecture.pending_continuation(cur, int(user_id))
    except Exception:  # noqa: BLE001
        logger.debug("undx_continuation_read_failed user=%s", int(user_id))
        return None
    if not pending:
        return None

    spec = get(pending["capability_id"])
    try:
        undx_architecture.burn_continuation(cur, int(user_id), pending["continuation_id"])
        undx_tool_gateway.checkpoint(cur)
    except Exception:  # noqa: BLE001
        # A question that could not be burned must not be answered. Proceeding would
        # leave it live, and a live question that has already been acted on can be
        # acted on again by the next stray number.
        logger.warning("undx_continuation_burn_failed user=%s", int(user_id))
        return None
    if spec is None or not pending["missing"]:
        return None

    choices = pending.get("choices") or []
    if choices:
        # "Which one?" — the reply names a row, not a value. One field, by construction:
        # a reference resolver reports a single unresolved target.
        reading = read_choice(choices, text)
        if not reading.chosen:
            # Not a row. Whether that is worth saying depends on whether the runtime can
            # show the reply was aimed at the question — a word matching several rows, or
            # a bare number matching none. With no such evidence this stays ``None`` and
            # the turn declines exactly as it always has.
            if reading.miss:
                return _Reask(spec, dict(pending["arguments"]),
                              tuple(pending["missing"]), list(choices), reading.miss)
            return None
        chosen = reading.chosen
        answered = (pending["missing"][0],)
        filled = {**pending["arguments"], answered[0]: chosen}
        # Deliberately not returned as final. The id goes back through
        # ``resolve_arguments``, which re-reads it under the owner-scoped
        # ``explicit_id`` path — so a candidate row that has since been deleted, or one
        # belonging to somebody else because the store was tampered with, fails there
        # exactly as a typed id would. What was stored is a memory of a question, and
        # it is not permitted to become evidence of permission.
        return spec, filled, answered

    filled = answer_pending(spec, pending["arguments"], pending["missing"], text)
    if filled is None:
        # Not an answer. The person changed the subject, and the honest outcome is the
        # one they would have got without a pending question at all.
        return None
    return spec, filled, tuple(pending["missing"])


def available(user_id: int) -> bool:
    """Whether the agent should be consulted for this account at all.

    Checked before any work so that a disabled agent costs nothing and, more
    importantly, so that conversation continues to function normally when the agent
    is switched off. The agent is an enhancement to UNDX, not a prerequisite for it.
    """
    return policy.user_enabled(user_id)


def recent_replies(cur, user_id: int, conversation_id: int, *, limit: int = 5) -> tuple[str, ...]:
    """The assistant's last few replies in this conversation, newest last.

    Read for one purpose — so the response layer can notice it is about to say the
    same thing again — and scoped by both conversation and user id, because a
    repetition check is not a reason to widen who can see a message.

    Every failure is swallowed to an empty tuple, and that is a deliberate ranking of
    harms: a missing history makes an answer slightly more repetitive, while a raised
    exception here would abort a turn that may already have changed the user's data.
    Nothing downstream treats an empty history as meaningful.
    """
    if int(user_id or 0) <= 0 or int(conversation_id or 0) <= 0:
        return ()
    try:
        cur.execute(
            """SELECT body FROM pulse_ai_messages
               WHERE conversation_id=? AND user_id=? AND role='assistant'
               ORDER BY id DESC LIMIT ?""",
            (int(conversation_id), int(user_id), max(1, int(limit))),
        )
        rows = cur.fetchall() or []
    except Exception:
        # Includes the table simply not existing yet, which is the normal state in a
        # fresh test fixture and is not worth a log line on every turn.
        return ()
    bodies = [clean(row[0] if not isinstance(row, dict) else row.get("body"), MAX_TEXT_CHARS)
              for row in rows]
    return tuple(reversed([body for body in bodies if body]))


#: Brain goal shape -> the vocabulary the response layer publishes. One table, in one
#: place, so that "what did the person want" has exactly one translation between the
#: module that reads it and the module that answers it.
#:
#: ``retrieve`` is absent on purpose: it splits into ``show`` and ``find``, and the
#: split is not a second reading of the sentence. It is decided by
#: :func:`goal_shape_for` from whether argument resolution pinned the request to one
#: named resource — a fact the runtime has already computed by the time it asks.
_GOAL_SHAPE_NAMES: dict[str, str] = {
    "explain": "explain",
    "repair": "repair",
    "manage": "manage",
    "act": "act",
}

#: Identifier fields that name *the actor* rather than the thing being acted on. Every
#: other ``*_id`` a capability declares points at one resource, which is the property
#: :func:`narrowed_to_one_resource` is actually looking for.
_ACTOR_FIELDS = frozenset({"user_id", "owner_user_id", "actor_id"})


def narrowed_to_one_resource(spec: Any, resolution: Any) -> bool:
    """Whether this turn pinned the request to a single named resource.

    This is the whole of the ``show`` / ``find`` distinction, and it is derived rather
    than read off the sentence a second time: "show my alerts" and "find my Bitcoin
    alert" are one shape to the Brain — retrieval — and differ only in whether the
    request narrowed. Deriving it keeps intent with exactly one reader.

    **What this replaced, and why the first version was wrong.** The first version asked
    whether ``resolution.resolved_count == 1``. That attribute defaults to ``1`` and is
    overwritten only when a capability declares an ``alert_id`` and the reference
    resolver actually runs, so for every capability that does not — which is nearly all
    of them — it reported "narrowed" unconditionally. The effect was that ``show`` was
    all but unreachable and almost every retrieval rendered as ``find``, including "show
    me my alerts". The proxy was not measuring narrowing; it was measuring whether
    anybody had bothered to count, and reading silence as one.

    The property asked for now is the one that was meant: the capability declares a
    field naming a resource, and this turn filled it. A capability whose only fields are
    ``limit`` and ``query`` cannot narrow to one thing by construction — a search that
    returns a set is a set, however specific the words were — so it is a ``show``, and
    the ambiguity note in the response layer covers the case where the person wanted
    fewer than they got.
    """
    arguments = getattr(resolution, "arguments", None) or {}
    target = clean(str(getattr(spec, "target_field", "") or ""), 80)
    for field_spec in getattr(spec, "fields", ()) or ():
        name = getattr(field_spec, "name", "")
        names_a_resource = (name.endswith("_id") and name not in _ACTOR_FIELDS) \
            or (bool(target) and name == target)
        if names_a_resource and arguments.get(name):
            return int(getattr(resolution, "resolved_count", 1)) == 1
    return False


def goal_shape_for(goal: Any, *, narrowed: bool) -> str:
    """Translate a Brain goal into the response layer's vocabulary, or "".

    Returns "" for exactly the cases in which the answer should be what it was before
    the goal layer existed: no goal object, a goal the flags disabled, a sentence no
    shape was read from, or a shape with no counterpart in the response vocabulary.

    **Settledness is deliberately not consulted, and the reason matters.** An earlier
    version of this function refused unsettled goals, on the stated grounds that they
    "never reach here, because ``handle`` answers those with a question instead of an
    action". That was false, and the falseness had a consequence. :func:`handle` diverts
    an unsettled goal only when it also has somewhere to look — the guard is
    ``not settled and inspect_with`` — so an unsettled goal whose activated areas
    contain nothing readable falls straight through to :func:`_act`. Since
    :data:`services.undx_brain.goals.Shape.REPAIR` and
    :data:`~services.undx_brain.goals.Shape.MANAGE` are *never* settled by construction,
    refusing unsettled goals made ``repair`` and ``manage`` unreachable, which made
    :data:`services.undx_response_intelligence.ResponseMode.DIAGNOSIS` unreachable from
    the runtime, which made the diagnosis branch of the response layer dead code
    dressed as a feature.

    Consulting settledness was a category error on top of a factual one.
    :attr:`~services.undx_brain.goals.Goal.settled` answers "did the sentence name an
    operation"; :attr:`~services.undx_brain.goals.Goal.shape` answers "what was the
    person trying to do". The second is read confidently — a repair frame matched, or it
    did not — and stays true whether or not the first could be resolved. The turn only
    arrives here once something upstream has already decided to execute; all that is
    left to choose is how the answer is written, and a request framed as "my alerts
    aren't firing" deserves an account of the evidence rather than a recital of it
    *especially* when the goal layer could not settle it.

    Nothing is widened by this. The shape reaches the response layer and nowhere else,
    after all nine gateway checks have run. The worst a wrong shape can do is produce a
    longer answer about the same evidence.

    ``narrowed`` is the show/find distinction and nothing more. It is supplied by the
    caller from :func:`narrowed_to_one_resource`, so the only thing separating "show my
    alerts" from "find my Bitcoin alert" is whether the turn actually picked one out —
    which is what the words "show" and "find" are for, and is already known without
    reading the sentence a second time.
    """
    if goal is None or not getattr(goal, "ok", False):
        return ""
    shape = getattr(getattr(goal, "shape", None), "value", "")
    if shape == "retrieve":
        return "find" if narrowed else "show"
    return _GOAL_SHAPE_NAMES.get(shape, "")


def _act(cur, spec: CapabilitySpec, *, user_id: int, text: str,
         arguments: dict[str, Any], answered: tuple[str, ...],
         request_id: str, conversation_id: int, confirmation_token: str,
         client_request_id: str, correlation_id: str,
         started: float, brain_goal: Any = None) -> AgentResponse:
    """Everything that happens once the turn is known to be an action.

    Split out of :func:`handle` so the boundary between "this might be
    conversation" and "this is a request PulseSoc understood" is a function call
    rather than a position in a long body — the same argument :func:`_settle`
    makes in the gateway, for the same reason: the guard around it has to name
    exactly what it is guarding.

    Nothing here is allowed to escape as an exception. Not because a fault would
    be dangerous — every path in here provably precedes any mutation, which is
    what makes falling through safe in the first place — but because falling
    through is not the same as answering. See :func:`_fault_response`.
    """
    resolution = resolve_arguments(int(user_id), spec, text, arguments)
    if resolution.unresolved is not None:
        # The sentence named a resource the runtime could not pin down to exactly one,
        # or named a setting without a direction. Both are questions, not failures, and
        # both are answered here rather than passed to the gateway, which would reject
        # them as schema errors phrased in the language of required fields.
        # Both shapes of question are answerable, so both are remembered. An ambiguous
        # reference qualifies only when there is actually something to choose between:
        # ``count`` of zero means no such alert exists, and a chooser with nothing in it
        # is a question whose only honest answer is "none of them".
        asking = _question_fields(resolution)
        # A continued turn may ask again, but only about something else. Answering
        # "which one?" and then being asked "what price?" is the conversation working;
        # answering "what price?" and being asked it again is one unanswerable question
        # renewing itself for as long as the person keeps typing. The test is the
        # question, not the turn. See :func:`is_renewal` for why this is a backstop.
        renewing = is_renewal(asking, answered)
        if asking and not renewing:
            # Remember what was asked, so the reply has somewhere to land.
            _remember_question(cur, int(user_id), spec, resolution, asking)
        return _unresolved_response(spec, resolution.unresolved, request_id,
                                    int(user_id), started,
                                    awaiting=asking if (asking and not renewing) else ())
    arguments = resolution.arguments
    resolved_count = resolution.resolved_count

    # Predict from the registry before the gateway is entered.  This is advisory for
    # reads and restrictive for writes: a prediction can withhold a completion claim,
    # but it can never authorise an operation or replace the gateway's verifier.
    prediction = None
    try:
        from services.undx_brain import prediction as brain_prediction
        prediction = brain_prediction.predict(spec.capability_id, arguments)
    except Exception:  # pragma: no cover - Brain package is optional at runtime
        logger.warning("undx_prediction_failed capability=%s", spec.capability_id,
                       exc_info=True)
    if spec.is_write:
        try:
            from services.undx_brain import config as brain_config
            brain_values = brain_config.resolve().values
            prediction_required = bool(brain_values.get("UNDX_BRAIN_ENABLED")) and bool(
                brain_values.get("UNDX_BRAIN_PREDICTION_ENABLED")
            )
        except Exception:  # pragma: no cover
            prediction_required = False
        if prediction_required and (prediction is None or not prediction.ok):
            return _error_response(
                spec,
                AgentError(
                    "brain_prediction_unavailable",
                    "I cannot safely predict and verify this change right now, so I did not run it.",
                    outcome=AgentOutcome.RECOVERABLE_FAILURE,
                ),
                request_id, int(user_id), started,
            )

    # Read the before/after pair now, while nothing has changed, so a confirmation
    # card can state plainly what it is asking permission for. The same read names
    # the resource, which is the other half of stating it plainly: a before and an
    # after with no subject is a sentence about nothing in particular.
    current_value, proposed_value, resource_label = (
        preview(int(user_id), spec, arguments) if spec.is_write else (None, None, ""))

    def _gateway_call():
        return undx_tool_gateway.execute(
            cur, user_id=int(user_id), capability_id=spec.capability_id,
            proposed_arguments=arguments, current_value=current_value,
            proposed_value=proposed_value, resource_label=resource_label,
            request_id=request_id, task_id=request_id,
            client_request_id=client_request_id, correlation_id=correlation_id,
            confirmation_token=confirmation_token, explicit_request=is_explicit(text),
            resolved_resource_count=resolved_count, question=text,
            goal_shape=goal_shape_for(
                brain_goal, narrowed=narrowed_to_one_resource(spec, resolution)),
            recent_replies=recent_replies(cur, int(user_id), int(conversation_id)),
        )

    execution_run = None
    try:
        # When enabled, even the canonical single operation spends from the same ledger
        # a future multi-step plan uses.  The performer remains the existing gateway;
        # the Brain executor neither imports tools nor gains authority.
        from services.undx_brain import execution as brain_execution
        captured: dict[str, Any] = {}

        def _perform(_step, _attempt):
            captured["outcome"] = _gateway_call()
            status = captured["outcome"].receipt.status
            if status in AgentOutcome.COMPLETED:
                return brain_execution.StepOutcome.SUCCEEDED
            if status == AgentOutcome.RECOVERABLE_FAILURE:
                return brain_execution.StepOutcome.FAILED
            return brain_execution.StepOutcome.UNKNOWN

        execution_run = brain_execution.execute(
            (brain_execution.Step(
                step_id=f"execute:{spec.capability_id}",
                capability_id=spec.capability_id,
                is_write=spec.is_write,
            ),),
            _perform,
        )
        if "outcome" in captured:
            outcome = captured["outcome"]
        elif execution_run.refusal.bound == "flag":
            # Executor off or refused before dispatch: preserve the documented legacy
            # path. A refusal cannot silently turn into a second call after dispatch,
            # because captured is populated before the perform callback returns.
            outcome = _gateway_call()
        else:
            return _error_response(
                spec,
                AgentError(
                    "brain_execution_refused",
                    execution_run.refusal.message or "UNDX stopped this plan at its safety bound.",
                    outcome=AgentOutcome.RECOVERABLE_FAILURE,
                ),
                request_id,
                int(user_id),
                started,
            )
    except AgentError as exc:
        # A typed refusal from validation or the registry. It is already a canonical
        # outcome; it should reach the user as one rather than as a 500. Every raising
        # path inside ``execute`` lies *before* the executor runs — the gateway settles
        # its own tail — so arriving here always means nothing was changed.
        return _error_response(spec, exc, request_id, int(user_id), started)

    # Past this line the action may have really happened, so the turn must be reported
    # as handled no matter what. Card construction is presentation: a defect in it is a
    # missing card, not a reason to hand the turn back to a language model that would
    # then answer as though nothing had occurred.
    try:
        card = build_card(spec, outcome)
    except Exception as exc:  # pragma: no cover - defensive
        logger.critical("undx_card_build_failed capability=%s user=%s error=%s",
                        spec.capability_id, int(user_id), exc.__class__.__name__)
        card = None
    if card is not None and prediction is not None and prediction.ok:
        observed = {}
        if outcome.verification is not None and isinstance(outcome.verification.observed, dict):
            observed = dict(outcome.verification.observed)
        try:
            checked = brain_prediction.check(
                prediction,
                observed,
                canonical_ids=outcome.receipt.canonical_resource_ids,
            )
        except Exception:  # pragma: no cover - a self-check may only narrow claims
            checked = None
            logger.warning("undx_prediction_check_failed capability=%s", spec.capability_id,
                           exc_info=True)
        card["brain_prediction"] = {
            "target": prediction.target,
            "expected_fields": [item.field_name for item in prediction.expected],
            "reversal": prediction.reversal.value,
            "verifier": "canonical_read_back",
        }
        if checked is not None and checked.ok:
            card["brain_prediction"]["fidelity"] = checked.fidelity.value
            card["brain_prediction"]["contradicted_fields"] = [
                item[0] for item in checked.contradicted
            ]
            if checked.contradicted:
                # The gateway remains authoritative about status, but a second
                # independent mismatch is never allowed to coexist with success prose.
                card["may_claim_done"] = False
                card["evidence_contradiction"] = checked.reason
                if outcome.receipt.may_claim_completed:
                    outcome.receipt.user_explanation = (
                        "The operation returned, but the observed state contradicted "
                        "the expected result. I cannot claim it completed."
                    )
                    card["message"] = outcome.receipt.user_explanation
    if card is not None and execution_run is not None:
        card["brain_execution"] = {
            "active": bool(execution_run.attempts),
            "completed_steps": list(execution_run.completed),
            "writes_in_doubt": list(execution_run.writes_in_doubt),
            "bounded": True,
        }
    if card is not None:
        # Final self-check: cards and prose are two renderings of one receipt.  It may
        # change language, never facts, resource identity, or verification status.
        #
        # The question "does this sentence claim a completion?" is asked of
        # :func:`~services.undx_response_intelligence.completion_claim`, which is the
        # same list :func:`validate_consistency` checks the composed answer against. It
        # was previously asked of a four-word tuple local to this line, and the mismatch
        # ran in both directions: the tuple missed every phrase this system actually uses
        # to claim a completion — "Done — …", "I confirmed this against your account",
        # "the change went through", "I had already done that" — while matching bare
        # state descriptions like " is paused" that a *read* has every right to say. The
        # guard fired on true sentences and let false ones through.
        # Imported here rather than at module scope because this module is imported *by*
        # the response layer's callers and a top-level cycle is a real risk; the failure
        # direction is named rather than swallowed silently, because unlike the frame
        # reader this guard genuinely has nothing behind it.
        try:
            from services.undx_response_intelligence import completion_claim
            claimed = completion_claim(outcome.receipt.user_explanation)
        except Exception:  # pragma: no cover - the response layer is a hard dependency
            logger.exception("completion-claim self-check unavailable")
            claimed = ""
        if claimed and not card.get("may_claim_done"):
            outcome.receipt.user_explanation = (
                "The request returned without enough independent evidence to claim completion."
            )
            card["message"] = outcome.receipt.user_explanation
            card["metacognitive_revision"] = "completion_claim_removed"
            card["revised_claim"] = claimed
        card["brain_homeostasis"] = {
            "verification_available": outcome.verification is not None or not spec.is_write,
            "degraded": bool(outcome.result and outcome.result.degraded_sources),
            "writes_fail_closed": True,
        }
    return AgentResponse(
        handled=True,
        receipt=outcome.receipt,
        card=card,
        reply=outcome.receipt.user_explanation,
        capability_id=spec.capability_id,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def question_framed_write_refusal(
    spec: Any, text: str, *, chosen_by_caller: bool, brain_goal: Any,
    started: float,
) -> AgentResponse | None:
    """Refuse to answer a question by performing the thing it asks about.

    Returns a clarification response when this turn was framed as a question or a
    problem and the capability about to run would change something, and ``None`` — the
    overwhelmingly common case — otherwise.

    **The defect this closes, stated exactly, because it was live in the default
    configuration.** :mod:`services.undx_brain.goals` already refused to settle a goal
    whose frame asks *about* a subject onto a write that merely shares its vocabulary:
    "explain why my alert was deleted" matches the delete capability because the matcher
    reads words and the word is there. That refusal was real. It was also conditional on
    something unrelated to it. :func:`handle` acted on an unsettled goal only when the
    goal also carried reads to offer — the guard is ``not settled and inspect_with`` —
    so whenever the activated areas happened to contain nothing readable, the goal
    layer's deliberate ``capability_id=""`` was discarded and the legacy matcher's write
    ran in its place. An adversarial sweep over every registered write found 68 such
    sentences across seven product areas. "Why did notify me when bitcoin goes above
    90000" reached ``crypto.alerts.create``; "fix set my preferred language" reached
    ``profile.preferences.update``. The suppression was not weak, it was contingent: it
    held exactly when something else was true, and nothing tied the two together.

    **Why this is not behind the Brain flags.** Everything in
    :mod:`~services.undx_brain.goals` is gated, correctly, because it is reasoning, and
    reasoning is a capacity to roll out and withdraw. This is not that. With the flags
    off — the shipped default — the legacy matcher routes "tell me about unfollow user
    42" straight to ``social.unfollow``, which carries ``confirmation="never"`` and so
    reaches the executor with nothing in front of it. Four capabilities are in that
    position: ``social.follow``, ``social.unfollow``, ``feed.posts.like`` and
    ``saved.post.set``. Gating the refusal would leave the worst case ungoverned
    precisely in the configuration most users are in.

    **What it does not do.** It never suppresses a write the *caller* named:
    ``chosen_by_caller`` covers an explicit ``capability_id``, a confirmation token, and
    a turn recovered from a pending question. A tap on a confirmation card carries the
    original sentence, question frame and all, and refusing there would make every
    confirmation for a question-framed request unapprovable. The person can always have
    the write — by asking for it, or by approving it — and this only declines to infer
    it from a sentence that named a subject rather than an operation.

    **Why it does not offer a read to look at instead, which it obviously should.** It
    cannot, and the reason is worth writing down because the first draft did offer one
    and the offer was unreachable. With the Brain on, every frame this function
    recognises is also a frame :func:`~services.undx_brain.goals.understand` recognises,
    so the goal always comes back unsettled; and ``handle`` diverts an unsettled goal
    the moment it carries reads. This guard therefore runs on exactly the complement —
    the turns where the goal layer had *nothing* to suggest — plus the turns where the
    goal layer was switched off and so suggested nothing either. "Here is what I could
    read instead" is the upstream branch's sentence, and it is a better one; this is the
    branch that has to say no without it, which is why ``brain_goal`` is taken only to
    report what the Brain concluded and not to dress the refusal up.

    **Fails closed around its own dependency.** The frame reader lives in the Brain
    package, so an ``ImportError`` is possible in a stripped deployment. It is caught and
    treated as *no frame*, which permits the write. That is the honest failure direction
    and it is worth naming rather than burying: the alternative — refusing every write
    when the module is missing — would turn a packaging fault into a total loss of
    function, and this guard is not the only thing between a request and a mutation. It
    is the last one that can tell a question from an instruction.
    """
    if not getattr(spec, "is_write", False) or chosen_by_caller:
        return None
    try:
        from services.undx_brain import goals as _goals
        frame = _goals.asks_about_rather_than_for(text)
    except Exception:  # pragma: no cover - see the docstring on failing open
        return None
    if not frame:
        return None

    described = clean(str(getattr(spec, "description", "") or ""), 120).rstrip(".")
    subject = described[0].lower() + described[1:] if described else "change something"
    goal_shape = str(getattr(getattr(brain_goal, "shape", None), "value", "") or "") if (
        brain_goal is not None and getattr(brain_goal, "ok", False)) else ""
    return AgentResponse(
        handled=True,
        reply=(f'You asked about this rather than for it — "{frame}" — and the only '
               f"thing I found that matches would {subject}, which would answer the "
               f"question by doing the thing. I have not done it. Tell me plainly if "
               f"you want that and I will."),
        card={
            "component": CardType.CLARIFICATION_REQUIRED,
            "status": AgentOutcome.CLARIFICATION_REQUIRED,
            "question_frame": frame,
            "declined_capability_id": str(getattr(spec, "capability_id", "")),
            "declined_because": "a question was framed as one and the match was a write",
            "goal_type": goal_shape,
            "may_claim_done": False,
        },
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def handle(
    cur,
    *,
    user_id: int,
    text: str,
    request_id: str = "",
    conversation_id: int = 0,
    capability_id: str = "",
    arguments: dict[str, Any] | None = None,
    confirmation_token: str = "",
    client_request_id: str = "",
    correlation_id: str = "",
) -> AgentResponse:
    """Run one agent turn.

    ``capability_id`` and ``arguments`` are the planner's proposal. When they are
    absent the built-in matcher tries to derive them from ``text``; when they are
    present the text is still used for reference resolution and for judging whether
    the instruction was explicit. Neither route confers any privilege — both end up
    at the same gateway call.
    """
    started = time.monotonic()
    request_id = clean(request_id or new_id("undx_req"), 120)
    text = clean(text, MAX_TEXT_CHARS)
    arguments = dict(arguments or {})

    if int(user_id or 0) <= 0:
        raise AgentError("unauthenticated", "Sign in to let UNDX do that.",
                         outcome=AgentOutcome.PERMISSION_DENIED)

    if not available(int(user_id)):
        return AgentResponse(handled=False, reply="", latency_ms=int((time.monotonic() - started) * 1000))

    blocked = _blocked_operational_response(text, started)
    if blocked is not None:
        return blocked

    # Brain activation is deliberately upstream of the deterministic matcher.  It may
    # abstain, narrow, or choose the safer member of a contested band; it may never add
    # a capability that the canonical registry and policy do not already expose.
    brain_focus = None
    brain_workspace = None
    brain_goal = None
    brain_selection = None
    try:
        from services.undx_brain import attention as brain_attention
        from services.undx_brain import goals as brain_goals
        from services.undx_brain import selection as brain_selection_module

        brain_focus = brain_attention.attend(text)
        brain_goal = brain_goals.understand(text, focus=brain_focus)
        brain_selection = brain_selection_module.select(text)
        try:
            from services.undx_brain import workspace as brain_workspace_module
            brain_workspace = brain_workspace_module.open_workspace(int(user_id))
            refusals = brain_attention.place_into(brain_focus, brain_workspace)
            if refusals:
                logger.info(
                    "UNDX_BRAIN_WORKSPACE correlation_id=%s accepted=%s refused=%s",
                    correlation_id, len(brain_workspace), len(refusals),
                )
        except Exception:  # pragma: no cover - bounded context is optional
            brain_workspace = None
        if brain_goal.ok:
            logger.info(
                "UNDX_BRAIN_DECISION correlation_id=%s active_domains=%s goal=%s "
                "settled=%s selected=%s clarification=%s",
                correlation_id,
                list(brain_goal.areas),
                brain_goal.shape.value,
                brain_goal.settled,
                brain_selection.capability_id if brain_selection and brain_selection.ok else "",
                bool(not brain_goal.settled and brain_goal.inspect_with),
            )
    except Exception:  # pragma: no cover - flags and package permit legacy fallback
        logger.warning("undx_brain_decision_failed correlation_id=%s", correlation_id,
                       exc_info=True)

    # An understood repair/scope request does not name a mutation.  The old matcher
    # could turn "fix my alert" into delete; the active goal layer instead offers the
    # read that can settle the request and asks one focused question.
    if brain_goal is not None and brain_goal.ok and not brain_goal.settled and brain_goal.inspect_with:
        options = ", ".join(brain_goal.inspect_with[:3])
        return AgentResponse(
            handled=True,
            reply=("I need to inspect the current state before choosing an action. "
                   f"I can start with {options}. What outcome do you want?"),
            card={
                "component": CardType.CLARIFICATION_REQUIRED,
                "status": AgentOutcome.CLARIFICATION_REQUIRED,
                "goal_type": brain_goal.shape.value,
                "active_domains": list(brain_goal.areas),
                "inspect_with": list(brain_goal.inspect_with),
                "may_claim_done": False,
            },
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # The fields this turn just supplied by answering a question, empty on a turn that
    # stood on its own.
    answered: tuple[str, ...] = ()
    if capability_id:
        # An explicitly named capability that does not exist is a refusal, not a
        # conversation. Falling through would let a planner — or an attacker who has
        # shaped one — probe for capability names and receive a chatty answer instead
        # of a typed ``unsupported_capability``. ``require`` raises that outcome.
        spec = require(capability_id)
        _abandon_pending(cur, int(user_id))
    else:
        if brain_selection is not None and brain_selection.ok and not brain_selection.decided \
                and brain_selection.contested:
            return AgentResponse(
                handled=True,
                reply="I found more than one consequential action that fits. Which one do you mean?",
                card={
                    "component": CardType.CLARIFICATION_REQUIRED,
                    "status": AgentOutcome.CLARIFICATION_REQUIRED,
                    "candidate_capability_ids": list(brain_selection.contested),
                    "may_claim_done": False,
                },
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        selected_id = (
            brain_selection.capability_id
            if brain_selection is not None and brain_selection.ok and brain_selection.decided
            else ""
        )
        # Attention is relevance, never authority.  Its only influence here is
        # restrictive: a selection outside the bounded focus is not dispatched.
        focus_rejected_selection = bool(
            selected_id and brain_focus is not None and brain_focus.ok
                and brain_focus.capability_ids
                and selected_id not in brain_focus.capability_ids
        )
        if focus_rejected_selection:
            selected_id = ""
        spec = (get(selected_id) if selected_id else
                None if focus_rejected_selection else match_capability(text))
        if spec is None:
            # Nothing actionable was recognised on its own. Before treating that as a
            # conversation, check whether it withdraws something already staged —
            # "never mind" is unroutable by design and used to mean silence, which left
            # the approval live and the button on screen still working.
            #
            # Ahead of the answer check because the two never compete: the withdrawal
            # path returns ``None`` outright whenever a question is open, so a reply to
            # a chooser reaches ``_resume_pending`` exactly as it did before.
            withdrawn = _withdraw_pending(cur, int(user_id), text, request_id, started)
            if withdrawn is not None:
                return withdrawn
            # Otherwise, check whether it is an answer: the runtime may have asked this
            # account a question one message ago, and "9" is a complete reply to
            # "which post?" while being, by design, unroutable in isolation.
            recovered = _resume_pending(cur, int(user_id), text)
            if recovered is None:
                # Nothing actionable and nothing outstanding. The normal case for the
                # overwhelming majority of messages, which want a conversation.
                return AgentResponse(handled=False, latency_ms=int((time.monotonic() - started) * 1000))
            if isinstance(recovered, _Reask):
                # An answer that missed. Handled here rather than below because there is
                # no action to take: nothing was resolved, so ``_act`` has nothing to
                # act on, and the turn's whole content is the sentence saying so.
                return _reask_response(cur, recovered, user_id=int(user_id),
                                       request_id=request_id, started=started)
            spec, arguments, answered = recovered
        else:
            # The message stands on its own, so it wins outright and any outstanding
            # question is over. Dropping it here rather than letting it lapse closes
            # the window in which a forgotten question eats an unrelated number three
            # messages later.
            _abandon_pending(cur, int(user_id))

    # A question is not an instruction, and this is the last place that can still be
    # true. See :func:`question_framed_write_refusal` for the whole argument.
    refusal = question_framed_write_refusal(
        spec, text,
        chosen_by_caller=bool(capability_id) or bool(confirmation_token) or bool(answered),
        brain_goal=brain_goal, started=started,
    )
    if refusal is not None:
        return refusal

    # Calibration is owner-scoped and advisory until it has enough judged answers.
    # Once conclusive, a capability corrected more often than approved is not allowed
    # to perform a write without one focused clarification.
    try:
        from services.undx_brain import calibration as brain_calibration_module
        from services.undx_brain import learning as brain_learning
        from services.undx_brain import memory as brain_memory
        scope = brain_memory.open_scope(int(user_id))
        window = brain_learning.load(scope, cur)
        calibration = brain_calibration_module.calibrate(
            window, capability_id=spec.capability_id
        )
    except Exception:  # pragma: no cover - sparse/missing learning store is degradation
        calibration = None
    if (calibration is not None and calibration.ok and calibration.conclusive
            and calibration.corrected > calibration.approved and spec.is_write):
        return AgentResponse(
            handled=True,
            reply="Past corrections make this action uncertain. Please confirm the exact result you want.",
            capability_id=spec.capability_id,
            card={
                "component": CardType.CLARIFICATION_REQUIRED,
                "status": AgentOutcome.CLARIFICATION_REQUIRED,
                "capability_id": spec.capability_id,
                "calibration_scope": calibration.scope,
                "may_claim_done": False,
            },
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # From here the turn is an action, not a conversation. ``spec`` exists, which
    # means the message was recognised as a request PulseSoc knows how to serve, and
    # from that point on a fault owes the person an answer rather than a silence.
    try:
        return _act(cur, spec, user_id=int(user_id), text=text, arguments=arguments,
                    answered=answered, request_id=request_id,
                    conversation_id=int(conversation_id),
                    confirmation_token=confirmation_token,
                    client_request_id=client_request_id, correlation_id=correlation_id,
                    started=started, brain_goal=brain_goal)
    except AgentError as exc:
        # A typed refusal that escaped its own handler — from ``preview`` or a
        # resolver rather than from the gateway. It already carries a canonical
        # outcome, so it reaches the user as that rather than as a generic fault.
        return _error_response(spec, exc, request_id, int(user_id), started)
    except Exception as exc:
        return _fault_response(spec, exc, request_id, int(user_id), started)


__all__ = [
    "AgentResponse", "Reference", "available", "handle", "build_card",
    "match_capability", "is_explicit", "goal_shape_for", "narrowed_to_one_resource",
    "question_framed_write_refusal", "recent_replies",
    "resolve_alert_reference",
    "resolve_notification_arguments",
    "resolve_saved_arguments",
    "resolve_relationship_arguments",
    "resolve_saved_post_write_arguments",
]
