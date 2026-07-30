"""Batch 2: what the evidence *means* inside its own domain.

Batch 1 gave the runtime a response layer that reasons about the **shape** of evidence:
how many records came back, whether a source degraded, whether a write was read back.
That reasoning is deliberately domain-blind, and it has to be — it is the same code for
all eighty capabilities, and a layer that special-cased one of them would eventually
special-case all of them.

Domain-blindness has a cost, and this module exists to pay it. Asked about account
standing, a shape-only answer says "you have three account health items", which is
arithmetic rather than an answer: it does not say whether the person is restricted,
what is about to expire, or whether anything needs doing. Asked about music, it says
"here are eight tracks" and stays silent about the only fact that decides whether a
creator may use them. The records carried that information the whole time, in fields
the domain services already emit; nothing was reading them.

So each analyser here reads the canonical record contract — ``kind``, ``title``,
``detail``, ``source``, ``timestamp``, ``confidence``, ``data`` — for one domain, and
returns a :class:`DomainReading` that the response plan folds into the slots it already
has. No new runtime, no new registry, no second prose engine: the reading is *input* to
:func:`services.undx_response_intelligence.build_plan`, and everything downstream of the
plan is unchanged.

Three rules hold everywhere in this file, and every analyser is written to obey them.

**Only what the fields say.** An analyser may count records, quote a field, and compare
a field to a value the schema defines. It may not judge. Creator analytics is where the
temptation is sharpest and the ground is thinnest: an average engagement score is a
number with no baseline attached, so this module reports it and says nothing about
whether it is good. "Your engagement is low" would be the module's opinion wearing the
runtime's evidence badge, which is precisely the failure the mission names.

**Every number is declared.** :attr:`DomainReading.numbers` carries each numeric token
the reading introduces, and :func:`build_reading` drops any string containing a digit
that was not declared. This is not defensive habit; it is a fix for a bug this codebase
has now produced twice. ``allowed_numbers`` in the response layer rejects prose
containing a number it cannot find in the evidence, and the rejection is silent and
total — one undeclared digit does not shorten an answer, it deletes it. Making each
analyser declare what it used means the check cannot be forgotten by a later author who
adds one more clause.

**Absent is not zero.** A domain with no rows is a domain with nothing to report, not a
domain in good standing. No analyser returns an all-clear from an empty result; the
shape layer already says "there are none", and adding "so you are fine" would convert a
lookup that found nothing into a reassurance nobody verified.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from services.undx_agent_contracts import ToolResult, clean

logger = logging.getLogger(__name__)

__all__ = [
    "DomainReading",
    "build_reading",
    "domain_for",
    "ANALYSERS",
]

_DIGITS = re.compile(r"\d+(?:\.\d+)?")

#: Longest a single reasoned clause may run. Shorter than the response layer's own cap
#: because these are clauses inside a sentence, not sentences.
MAX_CLAUSE_CHARS = 220


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainReading:
    """One domain's assessment of a result, in the vocabulary the plan already uses.

    Empty is the normal case and carries no meaning of its own: most capabilities have
    no analyser, an analyser declines whenever the evidence does not support a reading,
    and both arrive here as :meth:`empty`. The response plan is complete without this —
    a reading only ever adds.
    """

    #: The single most important thing the fields say, or "" when nothing is. Offered to
    #: the renderer as an additional *factual framing*, never as a replacement for the
    #: verified lead: the mission's rule that the plan "may never override the verified
    #: facts" applies to this module exactly as it applies to every other input.
    assessment: str = ""
    #: What the state is made of, in descending order of how much it matters.
    interpretations: tuple[str, ...] = ()
    #: Items the fields mark as needing the person's attention. Ranked, never invented:
    #: an entry here always corresponds to a record whose own status field says so.
    attention: tuple[str, ...] = ()
    #: Domain-specific follow-ups. Only where the domain genuinely has one; the shape
    #: layer already supplies the generic "open this screen" step.
    next_steps: tuple[str, ...] = ()
    #: What this domain cannot tell the person even when every source answered.
    uncertainties: tuple[str, ...] = ()
    #: Every numeric token introduced by the strings above. See the module docstring.
    numbers: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> "DomainReading":
        return cls()

    def __bool__(self) -> bool:
        return bool(self.assessment or self.interpretations or self.attention
                    or self.next_steps or self.uncertainties)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _records(result: ToolResult) -> list[dict[str, Any]]:
    """The record list, with anything that is not a mapping discarded.

    Executors are trusted to return the canonical contract and are not trusted to have
    no bugs. A stray ``None`` in the list would raise from inside an analyser, and an
    analyser that raises costs the turn its whole domain reading for the sake of one row.
    """
    return [row for row in (result.records or []) if isinstance(row, dict)]


def _field(record: dict[str, Any], name: str) -> str:
    """One ``data`` field as a cleaned lowercase string, or "".

    Lowercased because every comparison in this file is against a schema constant —
    ``"open"``, ``"pending"``, ``"approved"`` — and a service that writes ``"Open"`` is
    not reporting a different state.
    """
    data = record.get("data")
    if not isinstance(data, dict):
        return ""
    value = data.get(name)
    if value is None or isinstance(value, bool):
        return ""
    return clean(str(value), 80).strip().lower()


def _raw_field(record: dict[str, Any], name: str) -> str:
    """One ``data`` field with its case intact, for values that get *shown*.

    :func:`_field` lowercases because everything it feeds is compared against a schema
    constant. A timestamp is the exception: it is not compared to anything, it is read
    aloud, and lowercasing turned ``2026-08-05T09:00:00Z`` into
    ``2026-08-05t09:00:00z`` in the sentence a person actually reads.
    """
    data = record.get("data")
    if not isinstance(data, dict):
        return ""
    value = data.get(name)
    if value is None or isinstance(value, bool):
        return ""
    return clean(str(value), 80).strip()


def _flag(record: dict[str, Any], name: str) -> bool | None:
    """One ``data`` field as a boolean, or ``None`` when the field is absent.

    The three-way return is the point. A missing ``commercial_use_allowed`` means the
    catalogue did not say, which is a different fact from the catalogue saying no — and
    for licensing it is the difference between "you may not" and "check before you do".
    """
    data = record.get("data")
    if not isinstance(data, dict):
        return None
    if name not in data:
        return None
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "t"}:
        return True
    if text in {"0", "false", "no", "n", "f", ""}:
        return False
    return None


def _title(record: dict[str, Any]) -> str:
    return clean(str(record.get("title") or ""), 80).strip()


def _count(word_singular: str, word_plural: str, n: int) -> str:
    return f"{n} {word_singular if n == 1 else word_plural}"


def _named(records: Sequence[dict[str, Any]], limit: int = 2) -> str:
    """Up to ``limit`` record titles, joined. Empty when none of them are named.

    Names rather than counts wherever possible: "your marketplace restriction expires
    on the third" is actionable in a way that "one restriction is active" is not, and
    the title is already in the evidence.
    """
    titles = [t for t in (_title(r) for r in records) if t][:limit]
    return ", ".join(titles)


def _numbers_in(*texts: str) -> set[str]:
    found: set[str] = set()
    for text in texts:
        found.update(_DIGITS.findall(text or ""))
    return found


# ---------------------------------------------------------------------------
# Analysers
# ---------------------------------------------------------------------------
#
# Each takes the records and returns a reading. Each is free to return
# ``DomainReading.empty()``, and several do so far more often than not — an analyser
# that always has something to say is an analyser that has started guessing.


#: Statuses that mean a health item is still acting on the account. Anything else is
#: history. The service already filters resolved rows out of the query, so this is the
#: second line of the same defence rather than the only one.
_LIVE_HEALTH_STATUSES = frozenset({"", "open", "active", "pending", "under_review",
                                   "in_review", "enforced", "applied"})


def _account_health(records: list[dict[str, Any]]) -> DomainReading:
    """Standing, not a row count.

    The question behind "how is my account doing" is whether anything is *acting* on the
    account right now, and the record kinds answer it directly: a restriction limits what
    the person can do, a strike counts against them, a warning does neither yet. Ordering
    the reading by that hierarchy is the whole value — three warnings and one restriction
    is one situation, and "you have four account health items" describes none of it.
    """
    if not records:
        return DomainReading.empty()

    live = [r for r in records if _field(r, "status") in _LIVE_HEALTH_STATUSES]
    if not live:
        return DomainReading.empty()

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for record in live:
        by_kind.setdefault(str(record.get("kind") or "account_health"), []).append(record)

    restrictions = by_kind.get("account_restriction", [])
    strikes = by_kind.get("account_strike", [])
    warnings = by_kind.get("account_warning", [])
    expiring = [r for r in live if _field(r, "expires_at")]

    parts: list[str] = []
    if restrictions:
        parts.append(_count("restriction", "restrictions", len(restrictions)))
    if strikes:
        parts.append(_count("strike", "strikes", len(strikes)))
    if warnings:
        parts.append(_count("warning", "warnings", len(warnings)))

    # A live row whose ``kind`` is none of the three — the generic ``account_health``
    # the service emits, or a kind added after this file was written — leaves ``parts``
    # empty, and the final branch below then indexed ``parts[0]``. The reading is
    # declined instead of guessed: an unrecognised kind is a row this analyser cannot
    # place in the restriction/strike/warning hierarchy, and the hierarchy is the entire
    # reason the analyser exists. The shape layer still reports the rows.
    #
    # Found by wiring rather than by review: ``build_reading`` caught the ``IndexError``
    # exactly as designed and logged it, which meant the only visible symptom was a
    # missing reading — the failure mode this whole workstream keeps rediscovering.
    if not parts:
        return DomainReading.empty()

    if restrictions:
        named = _named(restrictions)
        assessment = (f"{parts[0]} is currently limiting your account"
                      if len(restrictions) == 1
                      else f"{parts[0]} are currently limiting your account")
        if named:
            assessment += f" ({named})"
    elif strikes:
        assessment = (f"no restrictions are limiting your account, but "
                      f"{parts[0]} {'is' if len(strikes) == 1 else 'are'} on record")
    else:
        assessment = (f"{parts[0]} {'is' if len(warnings) == 1 else 'are'} open, and "
                      "nothing is currently limiting what you can do")

    interpretations: list[str] = []
    if len(parts) > 1:
        interpretations.append("this is not one thing: it is " + ", ".join(parts))
    if expiring:
        dates = ", ".join(sorted({_raw_field(r, "expires_at") for r in expiring} - {""}))[:120]
        if dates:
            interpretations.append(
                f"{_count('item', 'items', len(expiring))} carries an expiry date "
                f"({dates}), so this state is time-limited rather than permanent")

    attention = [t for t in (_title(r) for r in restrictions + strikes) if t][:3]

    next_steps: list[str] = []
    if restrictions or strikes:
        next_steps.append(
            "open Account Health in PulseSoc to see the full notice and any appeal option")

    uncertainties = [
        "this reads what is on your account record; it does not predict how a review "
        "will be decided"
    ]

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=tuple(attention),
        next_steps=tuple(next_steps),
        uncertainties=tuple(uncertainties),
        numbers=frozenset(_numbers_in(assessment, *interpretations, *attention)),
    )


def _verification(records: list[dict[str, Any]]) -> DomainReading:
    """Which request is live, and what its status actually commits PulseSoc to.

    "Pending" is the state people most often misread, so it is the one stated most
    plainly: a request under review is not a request that has been refused, and it is
    not a promise either. Neither claim is available from the field, so neither is made.
    """
    if not records:
        return DomainReading.empty()

    def status_of(record: dict[str, Any]) -> str:
        return _field(record, "status") or clean(str(record.get("detail") or ""), 40).strip().lower()

    pending = [r for r in records if status_of(r) in {"pending", "submitted", "under_review", "in_review"}]
    approved = [r for r in records if status_of(r) in {"approved", "verified", "granted"}]
    rejected = [r for r in records if status_of(r) in {"rejected", "denied", "declined"}]

    if approved:
        newest = approved[0]
        kind = _field(newest, "verification_type") or "account"
        assessment = f"your {kind} verification is approved"
    elif pending:
        assessment = (f"{_count('verification request', 'verification requests', len(pending))} "
                      f"{'is' if len(pending) == 1 else 'are'} still under review")
    elif rejected:
        assessment = (f"your most recent verification request was not approved")
    else:
        return DomainReading.empty()

    interpretations: list[str] = []
    if pending and not approved:
        interpretations.append(
            "a request under review has neither been granted nor refused, so the "
            "account is unchanged until it is decided")
    if rejected and pending:
        interpretations.append(
            "an earlier request was declined and a newer one is open, so the declined "
            "one is history rather than the current state")

    attention = tuple(t for t in (_title(r) for r in pending) if t)[:3]

    uncertainties: list[str] = []
    if pending:
        uncertainties.append("PulseSoc does not publish a review time, so I cannot tell "
                             "you when this will be decided")

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=attention,
        next_steps=(),
        uncertainties=tuple(uncertainties),
        numbers=frozenset(_numbers_in(assessment, *interpretations, *attention)),
    )


_OPEN_TICKET_STATUSES = frozenset({"", "open", "new", "pending", "awaiting_reply",
                                   "in_progress", "escalated", "reopened"})
_WAITING_ON_USER = frozenset({"awaiting_reply", "needs_info", "waiting_on_customer"})


def _support(records: list[dict[str, Any]]) -> DomainReading:
    """Open versus closed, and — the useful part — who the ticket is waiting on.

    A support queue where every ticket is waiting on PulseSoc and a queue where two are
    waiting on the person are opposite situations with the same row count. The status
    field distinguishes them, so the reading does too.
    """
    if not records:
        return DomainReading.empty()

    open_tickets = [r for r in records if _field(r, "status") in _OPEN_TICKET_STATUSES]
    if not open_tickets:
        return DomainReading(
            assessment="none of your support tickets are still open",
            numbers=frozenset(),
        )

    waiting_on_you = [r for r in open_tickets if _field(r, "status") in _WAITING_ON_USER]
    urgent = [r for r in open_tickets
              if _field(r, "priority") in {"high", "urgent", "critical", "p1"}]

    assessment = (f"{_count('support ticket', 'support tickets', len(open_tickets))} "
                  f"{'is' if len(open_tickets) == 1 else 'are'} still open")

    interpretations: list[str] = []
    if waiting_on_you:
        interpretations.append(
            f"{_count('ticket', 'tickets', len(waiting_on_you))} "
            f"{'is' if len(waiting_on_you) == 1 else 'are'} marked as waiting on your "
            "reply, which is the part that is in your hands")
    if urgent:
        interpretations.append(
            f"{_count('ticket', 'tickets', len(urgent))} "
            f"{'is' if len(urgent) == 1 else 'are'} flagged at high priority")
    kinds = sorted({_field(r, "issue_type") for r in open_tickets} - {""})
    if len(kinds) > 1:
        interpretations.append("these are not all the same issue: " + ", ".join(kinds[:4]))

    attention = tuple(t for t in (_title(r) for r in waiting_on_you + urgent) if t)[:3]

    next_steps: list[str] = []
    if waiting_on_you:
        next_steps.append("replying to the tickets waiting on you is what moves them")

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=attention,
        next_steps=tuple(next_steps),
        uncertainties=(),
        numbers=frozenset(_numbers_in(assessment, *interpretations, *attention)),
    )


#: The creator metrics this module will read, with the words used to say them. Listed
#: explicitly so that a new column appearing in the snapshot does not silently start
#: being narrated with a machine-generated name.
_CREATOR_METRICS: tuple[tuple[str, str, str], ...] = (
    ("content_count", "post", "posts"),
    ("reel_count", "Reel", "Reels"),
    ("status_count", "status", "statuses"),
)


def _creator(records: list[dict[str, Any]]) -> DomainReading:
    """What was published, and what the recorded figures are. No verdict.

    This is the analyser most likely to drift, so the boundary is drawn hard. An average
    engagement score arrives with no baseline: not a target, not last month's figure, not
    a cohort. Anything of the form "that is good" or "that is down" would therefore be
    invented, and inventing it here would be worse than inventing it in a chat model,
    because it would arrive attached to a verified receipt. So the reading states the
    figures, states the window they cover, and says explicitly that it has nothing to
    compare them against — which is itself the most useful true thing available.
    """
    if not records:
        return DomainReading.empty()

    data = records[0].get("data")
    if not isinstance(data, dict):
        return DomainReading.empty()

    published: list[str] = []
    total = 0
    for key, singular, plural in _CREATOR_METRICS:
        raw = data.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        count = int(raw)
        if count > 0:
            published.append(_count(singular, plural, count))
            total += count

    if not published:
        return DomainReading(
            assessment="nothing was published in this window",
            interpretations=(
                "an empty window is a fact about the period, not about the account: "
                "content published outside it is not counted here",
            ),
            numbers=frozenset(),
        )

    assessment = "you published " + ", ".join(published) + " in this window"

    interpretations: list[str] = []
    engagement = data.get("average_engagement_score")
    if isinstance(engagement, (int, float)) and not isinstance(engagement, bool):
        interpretations.append(
            f"the recorded average engagement score across these is "
            f"{round(float(engagement), 2)}")
    completion = data.get("average_completion_rate")
    if isinstance(completion, (int, float)) and not isinstance(completion, bool) and completion:
        interpretations.append(
            f"Reels completed at an average rate of {round(float(completion), 2)}")

    uncertainties = [
        "these are your own recorded figures with nothing to compare them against — no "
        "target, no previous period and no cohort — so they describe what happened "
        "rather than whether it went well",
    ]

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=(),
        next_steps=(),
        uncertainties=tuple(uncertainties),
        numbers=frozenset(_numbers_in(assessment, *interpretations)),
    )


#: The licensing fields the catalogue may carry. A track is "described" when at least
#: one of them is present — see the guard in :func:`_music` for why presence and truth
#: have to be tested separately.
_LICENCE_FIELDS = ("is_creator_safe", "commercial_use_allowed", "attribution_required")


def _music(records: list[dict[str, Any]]) -> DomainReading:
    """Licensing, which is the only thing that decides whether a track can be used.

    A music search that returns eight tracks and says nothing about rights has answered
    a question nobody asked. The catalogue carries ``is_creator_safe``,
    ``commercial_use_allowed`` and ``attribution_required``, and the gap between "eight
    results" and "three you may use commercially, two of which need credit" is the gap
    between a list and an answer.

    Unknown is reported as unknown throughout — see :func:`_flag`. A track whose
    licensing fields are absent is not a track that may be used; it is a track the
    catalogue did not describe, and saying so is what keeps the person out of trouble.
    """
    if not records:
        return DomainReading.empty()

    safe = [r for r in records if _flag(r, "is_creator_safe") is True]
    commercial = [r for r in records if _flag(r, "commercial_use_allowed") is True]
    attribution = [r for r in records if _flag(r, "attribution_required") is True]
    unknown = [r for r in records
               if _flag(r, "is_creator_safe") is None
               and _flag(r, "commercial_use_allowed") is None]

    # The condition for declining is "the catalogue described none of these", and it has
    # to be written against the *presence* of the fields, not the truth of them. Written
    # against ``safe or commercial or attribution`` it also caught the case where every
    # field was present and every one said no — which is not an absence of information,
    # it is the single most decision-relevant answer this domain can give, and it was
    # the one answer the analyser threw away. A creator asking whether they may use a
    # track got the row count and silence about rights, which is the exact failure the
    # module docstring opens by describing.
    described = [r for r in records
                 if any(_flag(r, name) is not None for name in _LICENCE_FIELDS)]
    if not described:
        return DomainReading.empty()

    if commercial:
        assessment = (f"{_count('track', 'tracks', len(commercial))} of "
                      f"{len(records)} {'is' if len(commercial) == 1 else 'are'} "
                      "cleared for commercial use")
    elif safe:
        assessment = (f"{_count('track', 'tracks', len(safe))} of {len(records)} "
                      f"{'is' if len(safe) == 1 else 'are'} marked creator-safe")
    else:
        assessment = (f"none of these {len(records)} tracks is marked as cleared for "
                      "commercial use")

    interpretations: list[str] = []
    if attribution:
        interpretations.append(
            f"{_count('track', 'tracks', len(attribution))} "
            f"{'requires' if len(attribution) == 1 else 'require'} attribution, so "
            "credit has to appear wherever it is used")
    if safe and commercial and len(safe) != len(commercial):
        interpretations.append(
            "creator-safe and cleared-for-commercial-use are separate permissions here, "
            "and they do not cover the same tracks")

    uncertainties: list[str] = []
    if unknown:
        uncertainties.append(
            f"{_count('track', 'tracks', len(unknown))} came back with no licensing "
            "fields at all, which means the catalogue did not say rather than that the "
            "answer is no")

    next_steps = ["check the licence on the track page before publishing"] if records else []

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=(),
        next_steps=tuple(next_steps),
        uncertainties=tuple(uncertainties),
        numbers=frozenset(_numbers_in(assessment, *interpretations, *uncertainties)),
    )


def _groups(records: list[dict[str, Any]]) -> DomainReading:
    """The person's standing inside each group, which the row count hides entirely.

    ``viewer_role`` is the field that matters: being an owner of two groups and a member
    of nine is a different fact about someone's obligations than "eleven groups", and it
    is the fact they are usually asking about.
    """
    if not records:
        return DomainReading.empty()

    roles: dict[str, int] = {}
    for record in records:
        role = _field(record, "viewer_role")
        if role:
            roles[role] = roles.get(role, 0) + 1
    if not roles:
        return DomainReading.empty()

    leading = sum(count for role, count in roles.items()
                  if role in {"owner", "admin", "moderator"})

    if leading:
        assessment = (f"you help run {_count('group', 'groups', leading)} of the "
                      f"{len(records)} shown")
    else:
        ordered = sorted(roles.items(), key=lambda item: (-item[1], item[0]))
        assessment = (f"you are a {ordered[0][0]} in "
                      f"{_count('group', 'groups', ordered[0][1])} of {len(records)}")

    interpretations: list[str] = []
    if len(roles) > 1:
        listed = ", ".join(f"{count} as {role}" for role, count in
                           sorted(roles.items(), key=lambda item: (-item[1], item[0]))[:4])
        interpretations.append("your role is not the same in each: " + listed)

    attention = tuple(
        t for t in (_title(r) for r in records
                    if _field(r, "viewer_role") in {"owner", "admin", "moderator"}) if t
    )[:3]

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=attention,
        next_steps=(),
        uncertainties=(),
        # ``*attention`` is not optional here, and it is the one analyser that originally
        # left it out. These entries are group *titles*, and a title is user-authored
        # text: "Web3 Builders", "Cohort 4", "1000 True Fans". Every one of those carries
        # a digit that appears nowhere else in the reading, so ``_strip_undeclared``
        # would drop the clause — and dropping is silent, so the person asking which
        # groups they run would be told the count and never shown the names. The rule the
        # module docstring states is that every number is declared; a name is a place
        # numbers hide.
        numbers=frozenset(_numbers_in(assessment, *interpretations, *attention)),
    )


def _events(records: list[dict[str, Any]]) -> DomainReading:
    """Which of these is next, and whether any has been called off.

    A cancelled event still appears in an upcoming list — the row exists and its start
    time is still in the future — and a person reading "four upcoming events" will plan
    around four. The ``status`` field is what stops that.
    """
    if not records:
        return DomainReading.empty()

    cancelled = [r for r in records
                 if _field(r, "status") in {"cancelled", "canceled", "postponed"}]
    live = [r for r in records if r not in cancelled]
    if not live and not cancelled:
        return DomainReading.empty()

    if live:
        soonest = min(
            (r for r in live if _field(r, "starts_at")),
            key=lambda r: _field(r, "starts_at"), default=None)
        if soonest is not None and _title(soonest):
            # ``_raw_field`` for the timestamp: it is displayed, not compared, and the
            # lowercasing that makes status matching robust made the date unreadable.
            assessment = (f"the next one is {_title(soonest)}, "
                          f"starting {_raw_field(soonest, 'starts_at')}")
        else:
            assessment = f"{_count('event', 'events', len(live))} is still going ahead" \
                if len(live) == 1 else f"{len(live)} events are still going ahead"
    else:
        # "has been cancelled" is avoided deliberately, here and below. The response
        # layer's completion-claim guard matches ``(has|have) been ... cancelled``,
        # because that phrasing is how a runtime announces a cancellation *it performed*
        # — and the guard cannot distinguish that from a read reporting someone else's
        # status field. Since a rejected clause is dropped silently, the passive voice
        # costs the reader the entire cancellation warning. Naming the status field as
        # the source is both safe past the guard and more accurate: PulseSoc records
        # this event as cancelled; UNDX did not cancel anything.
        assessment = "every event on this list is marked cancelled or postponed"

    interpretations: list[str] = []
    if cancelled:
        interpretations.append(
            f"{_count('event', 'events', len(cancelled))} on this list "
            f"{'is' if len(cancelled) == 1 else 'are'} marked cancelled or postponed, "
            "so the count on screen is larger than the number you can attend")

    attention = tuple(t for t in (_title(r) for r in cancelled) if t)[:3]

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=attention,
        next_steps=(),
        uncertainties=(),
        numbers=frozenset(_numbers_in(assessment, *interpretations, *attention)),
    )


def _localization(records: list[dict[str, Any]]) -> DomainReading:
    """Configured versus defaulted, which is the difference people actually ask about.

    "Why is this in the wrong language" is nearly always answered by a preference that
    was never set rather than one that was set wrongly, and the two look identical in a
    settings dump.
    """
    if not records:
        return DomainReading.empty()

    region = next((r for r in records if r.get("kind") == "region_preference"), None)
    translation = next((r for r in records if r.get("kind") == "translation_preference"), None)

    # Raw, because every one of these is quoted back to the person rather than compared
    # to anything: ``en_GB``, ``Europe/Paris`` and ``USD`` are the strings they set and
    # the strings they expect to see. ``source`` is the one exception — it is tested
    # against ``"auto"`` below — so it keeps a folded copy for that test alone.
    locale = _raw_field(region, "locale") if region else ""
    zone = _raw_field(region, "time_zone") if region else ""
    currency = _raw_field(region, "currency") if region else ""
    target = _raw_field(translation, "target_language") if translation else ""
    source = _raw_field(translation, "source_language") if translation else ""
    source_is_auto = source.lower() == "auto"

    set_parts = [f"{label} is {value}" for label, value in
                 (("your locale", locale), ("your time zone", zone),
                  ("your currency", currency)) if value]
    unset = [label for label, value in
             (("locale", locale), ("time zone", zone), ("currency", currency)) if not value]

    if not set_parts and not target:
        return DomainReading(
            assessment="none of your language or region preferences have been set, so "
                       "PulseSoc is using its defaults",
            next_steps=("setting your locale and time zone is what changes how dates and "
                        "prices are shown",),
            numbers=frozenset(),
        )

    assessment = ", ".join(set_parts) if set_parts else \
        f"translation is set to {target}"

    interpretations: list[str] = []
    if target:
        interpretations.append(
            f"content is translated into {target}"
            + (f" from {source}" if source and not source_is_auto else
               ", with the source language detected automatically"))
    if unset:
        interpretations.append(
            "not everything is configured: " + ", ".join(unset) +
            (" is" if len(unset) == 1 else " are") +
            " still on the PulseSoc default")

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=(),
        next_steps=(),
        uncertainties=(),
        numbers=frozenset(_numbers_in(assessment, *interpretations)),
    )


def _presence(records: list[dict[str, Any]]) -> DomainReading:
    """Who can see you, said as a consequence rather than as three booleans.

    A privacy screen lists ``invisible_mode``, ``hide_last_seen`` and
    ``presence_privacy`` separately, and the person's question is a single one: can
    people tell I am here. The fields interact — invisible mode overrides the audience
    setting — so reporting them as a list is technically complete and practically
    useless.
    """
    if not records:
        return DomainReading.empty()

    record = records[0]
    invisible = _flag(record, "invisible_mode")
    hidden = _flag(record, "hide_last_seen")
    audience = _field(record, "presence_privacy")

    if invisible is None and hidden is None and not audience:
        return DomainReading.empty()

    if invisible:
        assessment = "invisible mode is on, so nobody sees you as online"
    elif audience:
        assessment = f"your online status is visible to {audience}"
    else:
        assessment = "your online status is visible"

    interpretations: list[str] = []
    if invisible and audience and audience != "nobody":
        interpretations.append(
            f"your audience setting still says {audience}, but invisible mode overrides "
            "it, so that setting is not what is deciding this right now")
    if hidden is not None:
        interpretations.append(
            "your last-seen time is hidden" if hidden
            else "your last-seen time is still visible, which is a separate setting from "
                 "whether you appear online")

    return DomainReading(
        assessment=assessment,
        interpretations=tuple(interpretations),
        attention=(),
        next_steps=(),
        uncertainties=(),
        numbers=frozenset(_numbers_in(assessment, *interpretations)),
    )


#: Capability id → analyser. Keyed on the capability rather than on a domain prefix
#: because the two are not the same: ``groups.list`` and ``groups.search`` return the
#: same records and share an analyser, while ``notifications.preference.update`` is a
#: write in a domain whose reads are analysed and must not borrow their reading.
ANALYSERS: dict[str, Any] = {
    "account.health.summary": _account_health,
    "verification.status": _verification,
    "support.tickets.list": _support,
    "creator.analytics.summary": _creator,
    "music.search": _music,
    "groups.list": _groups,
    "groups.search": _groups,
    "events.upcoming": _events,
    "localization.preferences": _localization,
    "presence.privacy.status": _presence,
}


def domain_for(capability_id: str) -> str:
    """The analyser key for a capability, or "" when it has none."""
    return capability_id if capability_id in ANALYSERS else ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _strip_undeclared(texts: Iterable[str], allowed: frozenset[str],
                      capability_id: str) -> tuple[str, ...]:
    """Drop any clause containing a number the analyser did not declare.

    Dropping rather than correcting, because there is nothing to correct to: an
    undeclared number is one this module cannot show came from the evidence, and the
    only safe thing to do with it is not say it. Logged at warning level so that the
    quieter answer is traceable to a cause — silent shortening is the failure mode this
    whole convention exists to prevent.
    """
    kept: list[str] = []
    for text in texts:
        body = clean(text or "", MAX_CLAUSE_CHARS).strip()
        if not body:
            continue
        undeclared = sorted(set(_DIGITS.findall(body)) - set(allowed))
        if undeclared:
            logger.warning(
                "undx_domain_reading_dropped capability=%s undeclared=%s",
                capability_id, ",".join(undeclared[:5]))
            continue
        kept.append(body)
    return tuple(kept)


def build_reading(capability_id: str, result: ToolResult) -> DomainReading:
    """The domain's reading of this result, or an empty one.

    Total by construction. An analyser is ordinary Python reading executor output, and
    executor output is the least trustworthy input in the system; a ``KeyError`` raised
    three frames down here would propagate into :func:`build_plan`, which the gateway
    calls after the point of no return. Losing the domain reading costs the answer some
    depth. Losing the turn costs the answer entirely, and hands the question to a
    language model that has no evidence at all.
    """
    analyser = ANALYSERS.get(clean(capability_id or "", 80))
    if analyser is None:
        return DomainReading.empty()

    try:
        reading = analyser(_records(result))
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("undx_domain_reading_failed capability=%s", capability_id)
        return DomainReading.empty()

    if not isinstance(reading, DomainReading) or not reading:
        return DomainReading.empty()

    allowed = frozenset(reading.numbers)
    assessment = _strip_undeclared([reading.assessment], allowed, capability_id)
    return DomainReading(
        assessment=assessment[0] if assessment else "",
        interpretations=_strip_undeclared(reading.interpretations, allowed, capability_id),
        attention=_strip_undeclared(reading.attention, allowed, capability_id),
        next_steps=_strip_undeclared(reading.next_steps, allowed, capability_id),
        uncertainties=_strip_undeclared(reading.uncertainties, allowed, capability_id),
        numbers=allowed,
    )
