"""Batch 3: what the evidence means *across* domains.

Batch 1 reasons about the shape of a result — how many rows, whether a source dropped
out, whether a write was read back. Batch 2 reasons inside one domain — what a support
ticket's ``status`` field means, whether a track is cleared for commercial use. Both
stop at the same edge, and this module is about what lies past it.

Several PulseSoc reads are *already* cross-domain. ``activity.daily_summary`` returns
notifications, received messages, posts you published, reels, statuses, new followers
and price alerts in one list. ``search.global`` returns people, posts, messages and
activity. ``security.activity.summary`` returns device sessions alongside security
events. The records arrive together, from one authorised read, carrying a ``kind`` that
says which domain each came from — and until now nothing read the relationship between
them. The shape layer counts the kinds and prints a breakdown; a breakdown is arithmetic
about a list, not an answer about a life.

The four relationships this module is willing to state, and why each is defensible:

**Direction.** PulseSoc's activity kinds divide cleanly into things the person
published and things that arrived for them. "You published three things and eleven came
back to you" is a fact about two domains that neither domain can state alone. The
buckets are deliberately narrow: a ``post`` returned by a *search* is a search hit, not
something published today, so the search kinds are in neither bucket and the clause
simply does not fire for them.

**Attention.** ``read`` is the one field in the canonical contract that means "you have
not dealt with this yet". Counting it across kinds answers "what is waiting on me",
which is the question people actually ask and which no single capability owns.

**Concentration.** When one kind is the overwhelming majority, the honest summary is not
the breakdown but the fact that the breakdown barely matters. This is arithmetic on the
counts, not a judgement about them.

**Time.** Records of different kinds sharing a date is co-occurrence with a name on it.

And one rule governs all four:

**A correlation needs a complete view; a partial read supports observations, not
relationships.** Every clause above except attention is a claim about *proportion* —
three against eleven, nine out of twelve, most of it on Tuesday. When a source has
dropped out, every count is a floor, so every proportion built from those counts is
unsound in a direction nobody can bound. The shape layer's "a count from a partial read
is a floor" makes a count honest; it cannot make a ratio honest. So on a degraded read
this module says what it saw and refuses to say how it relates, which is the specific
discipline the whole workstream keeps having to relearn: the failure to avoid is not
saying too little, it is saying something shaped like an answer that the evidence does
not carry.

The three Batch 2 rules hold here unchanged — only what the fields say, every number is
declared, absent is not zero — and the reading is returned as a :class:`DomainReading`
rather than as a new type, so the response layer folds it through machinery that already
exists and has already been tested.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.undx_agent_contracts import ToolResult, clean
from services.undx_domain_reasoning import (
    DomainReading,
    # Imported rather than re-implemented, and the underscore is not an oversight. These
    # three encode rules — how a number is extracted, how a title is bounded, how a
    # tri-state flag is read — that must not exist in two places. A second copy would
    # not stay identical, and the first time it drifted the symptom would be a clause
    # silently dropped for carrying a digit the other copy had declared.
    _flag,
    _numbers_in,
    _strip_undeclared,
    _title,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CROSS_DOMAIN_CAPABILITIES",
    "build_cross_reading",
    "is_cross_domain",
]


#: Reads whose executors compose rows from more than one PulseSoc source. Named
#: explicitly rather than inferred from "this result happens to have two kinds", because
#: a single-domain read can produce two kinds by accident — a security read returning
#: sessions and events is designed to; a groups read returning two shapes of group row is
#: a schema wrinkle, and narrating it as a cross-domain relationship would be inventing
#: significance out of an implementation detail.
CROSS_DOMAIN_CAPABILITIES: frozenset[str] = frozenset({
    "activity.daily_summary",
    "search.global",
    "search.activity",
    "security.activity.summary",
    "notifications.group_summary",
    "profile.activity.summary",
})

#: Kinds that exist because the person made something. Kept narrow on purpose: every
#: entry here is emitted by ``_activity_daily_summary`` for a row the user authored in
#: the period, so "you published this" is a fact about the row rather than an inference.
_PUBLISHED_KINDS: frozenset[str] = frozenset({
    "post_created", "reel_activity", "status_activity",
})

#: Kinds that exist because something arrived for the person. ``crypto_alert`` is in
#: neither bucket: an alert row is a rule the person configured, so it is neither
#: something they published nor something that came back to them, and forcing it into
#: either side would put a false number on both.
_RECEIVED_KINDS: frozenset[str] = frozenset({
    "notification", "message_received", "new_follower",
})

#: Plurals for the kinds this module names aloud. The response layer has its own map for
#: the kinds it names; this one is not a duplicate of it but a complement — the activity
#: kinds are precisely the ones missing there, which is why the breakdown clause used to
#: render "three post createds".
_ACTIVITY_NOUNS: dict[str, tuple[str, str]] = {
    "post_created": ("post", "posts"),
    "reel_activity": ("reel", "reels"),
    "status_activity": ("status update", "status updates"),
    "message_received": ("received message", "received messages"),
    "new_follower": ("new follower", "new followers"),
    "notification": ("notification", "notifications"),
    "crypto_alert": ("price alert", "price alerts"),
    "security_session": ("device session", "device sessions"),
    "security_event": ("security event", "security events"),
    "profile": ("person", "people"),
    "post": ("post", "posts"),
    "message": ("message", "messages"),
}

#: At least this many records, of at least this many kinds, before any relationship is
#: stated. Two records of two kinds co-occurring is not a pattern; it is two records.
_MIN_RECORDS = 3
_MIN_KINDS = 2

#: A kind holding at least this share of the total is the answer rather than a line in
#: the breakdown.
_CONCENTRATION = 0.7

#: A date holding at least this share of the dated records is where the activity was.
_CLUSTER_SHARE = 0.6

_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
          6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
          11: "eleven", 12: "twelve"}


def _phrase(count: int, kind: str) -> str:
    """``"three posts"`` — a count and its noun, small numbers spelled out.

    Spelled out for the same reason the response layer does it, plus one this layer
    cares about more: a spelled number carries no digit, so it cannot be dropped by the
    declared-numbers check. Only counts above twelve reach the prose as digits, and those
    are declared explicitly by the caller.
    """
    singular, plural = _ACTIVITY_NOUNS.get(kind, (kind.replace("_", " "),
                                                  kind.replace("_", " ") + "s"))
    noun = singular if count == 1 else plural
    return f"{_WORDS.get(count, str(count))} {noun}"


def _plain(count: int, singular: str, plural: str) -> str:
    return f"{_WORDS.get(count, str(count))} {singular if count == 1 else plural}"


def _records(result: ToolResult) -> list[dict[str, Any]]:
    return [row for row in (result.records or []) if isinstance(row, dict)]


def _kind(record: dict[str, Any]) -> str:
    return clean(str(record.get("kind") or ""), 60).strip().lower()


def _date(record: dict[str, Any]) -> str:
    """The calendar day of a record's timestamp, or "".

    Sliced rather than parsed. Every timestamp in the canonical contract is written by
    ``_fact`` from an ISO-8601 column, so the first ten characters are the date; a value
    that is not shaped that way is not one this module is willing to guess about, and
    returning "" simply removes that row from the clustering arithmetic. Parsing would
    buy nothing here — the only question asked of the value is whether two records share
    it — and would add a failure mode on every unusual row.
    """
    stamp = clean(str(record.get("timestamp") or ""), 40).strip()
    if len(stamp) < 10 or stamp[4] != "-" or stamp[7] != "-":
        return ""
    head = stamp[:10]
    return head if head.replace("-", "").isdigit() else ""


def is_cross_domain(capability_id: str) -> bool:
    """Whether this capability composes its rows from more than one source."""
    return clean(capability_id or "", 80) in CROSS_DOMAIN_CAPABILITIES


# ---------------------------------------------------------------------------
# The relationships
# ---------------------------------------------------------------------------


def _attention(records: Sequence[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Rows whose own ``read`` field says the person has not dealt with them.

    Returns the clauses and the titles separately because they go to different slots:
    the count is an interpretation, the names are attention items, and the response
    layer renders those two things differently.

    ``read`` is used and nothing else is. A ticket's ``status``, an alert's ``status``
    and an order's state all *look* like they mean the same thing, and they do not — an
    ``active`` alert is working correctly and needs nobody. Widening this to any field
    that sounds urgent is how a summary starts telling people to act on things that are
    fine.
    """
    unread = [r for r in records if _flag(r, "read") is False]
    if not unread:
        return [], []
    kinds = sorted({_kind(r) for r in unread if _kind(r)})
    titles = [t for t in (_title(r) for r in unread) if t][:3]
    clause = f"{_plain(len(unread), 'of these is', 'of these are')} still unread"
    if len(kinds) > 1:
        clause += f", across {_plain(len(kinds), 'kind', 'kinds')} of item"
    return [clause], titles


def _direction(counts: dict[str, int]) -> list[str]:
    """What the person put out against what came back.

    Fires only when both sides are non-empty. A zero on either side is an *absence*
    claim — "you published nothing", "nothing came back" — and an absence is exactly
    what a list bounded by ``LIMIT 20`` per source cannot establish. The row that would
    have made the count non-zero may simply be the twenty-first.
    """
    published = sum(count for kind, count in counts.items() if kind in _PUBLISHED_KINDS)
    received = sum(count for kind, count in counts.items() if kind in _RECEIVED_KINDS)
    if not published or not received:
        return []
    return [
        f"{_plain(published, 'thing you published', 'things you published')} and "
        f"{_plain(received, 'thing that came back to you', 'things that came back to you')} "
        "are both in here, which is two different questions answered by one read"
    ]


def _concentration(counts: dict[str, int], total: int) -> list[str]:
    """When one kind dominates, say so instead of reading out the breakdown."""
    if total < 4 or len(counts) < 2:
        return []
    kind, count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if count / total < _CONCENTRATION or count == total:
        return []
    return [
        f"this is mostly one thing: {_phrase(count, kind)} out of "
        f"{_plain(total, 'item', 'items')}"
    ]


def _clustering(records: Sequence[dict[str, Any]]) -> list[str]:
    """A date that most of the records share, when they are not all the same kind.

    The ``kinds > 1`` condition is what makes this cross-domain rather than trivial. Ten
    notifications on one day is what a notification list looks like. Ten notifications
    *and* three posts *and* two new followers on one day is a day that happened.
    """
    dated = [(d, _kind(r)) for r in records if (d := _date(r))]
    if len(dated) < _MIN_RECORDS:
        return []
    tally: dict[str, list[str]] = {}
    for day, kind in dated:
        tally.setdefault(day, []).append(kind)
    day, kinds = max(tally.items(), key=lambda kv: (len(kv[1]), kv[0]))
    if len(kinds) / len(dated) < _CLUSTER_SHARE or len(set(kinds)) < 2:
        return []
    return [
        f"most of it lands on one day, {day}, across "
        f"{_plain(len(set(kinds)), 'kind', 'kinds')} of item"
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _reading(records: list[dict[str, Any]], degraded: bool) -> DomainReading:
    """Assemble the reading. Split out from :func:`build_cross_reading` so the entry
    point is nothing but its guards and its safety net."""
    counts: dict[str, int] = {}
    for record in records:
        kind = _kind(record)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    total = sum(counts.values())
    if total < _MIN_RECORDS or len(counts) < _MIN_KINDS:
        return DomainReading.empty()

    attention_clauses, attention_titles = _attention(records)

    if degraded:
        # The whole module in one branch. Attention survives because it is a count of
        # rows that were actually read — a floor on a floor is still something that is
        # true of every row named. Everything else is a proportion, and the denominator
        # just went missing.
        if not attention_clauses:
            return DomainReading.empty()
        uncertainty = (
            "a source dropped out of this read, so what is here is real but how it "
            "divides up is not something to read anything into"
        )
        strings = attention_clauses + attention_titles + [uncertainty]
        return DomainReading(
            assessment=attention_clauses[0],
            attention=tuple(attention_titles),
            uncertainties=(uncertainty,),
            numbers=frozenset(_numbers_in(*strings)),
        )

    relationships = (_direction(counts) + _concentration(counts, total)
                     + _clustering(records))
    interpretations = attention_clauses + relationships
    if not interpretations:
        return DomainReading.empty()

    # The assessment is whichever clause answers the question a person is most likely to
    # have asked. "What needs me" beats "what was it made of" every time, so attention
    # leads when there is any; otherwise the first relationship does.
    assessment = interpretations[0]
    rest = interpretations[1:]

    uncertainties = [
        "these arrived in one read, so they are known to have co-occurred and not "
        "known to have caused one another"
    ]
    next_steps: list[str] = []
    if attention_titles and len({_kind(r) for r in records
                                 if _flag(r, "read") is False}) > 1:
        next_steps.append(
            "these sit on different PulseSoc screens, so clearing them is more than "
            "one place to visit"
        )

    strings = ([assessment] + rest + attention_titles + uncertainties + next_steps)
    return DomainReading(
        assessment=assessment,
        interpretations=tuple(rest),
        attention=tuple(attention_titles),
        next_steps=tuple(next_steps),
        uncertainties=tuple(uncertainties),
        numbers=frozenset(_numbers_in(*strings)),
    )


def build_cross_reading(capability_id: str, result: ToolResult) -> DomainReading:
    """The cross-domain reading of this result, or an empty one.

    Total by construction, for the reason :func:`services.undx_domain_reasoning.
    build_reading` is: this runs inside ``build_plan``, which the gateway calls after
    the point of no return, and an exception here would cost the turn its receipt and
    hand the question to a language model holding no evidence at all.

    The declared-numbers pass at the end is the same function Batch 2 uses, applied to
    the same contract, for the same reason — a clause carrying a digit this module
    cannot show came from the evidence is dropped rather than corrected, because there
    is nothing to correct it to.
    """
    capability = clean(capability_id or "", 80)
    if not is_cross_domain(capability):
        return DomainReading.empty()

    try:
        reading = _reading(_records(result), bool(result.degraded_sources))
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("undx_cross_reading_failed capability=%s", capability)
        return DomainReading.empty()

    if not reading:
        return DomainReading.empty()

    allowed = frozenset(reading.numbers)
    assessment = _strip_undeclared([reading.assessment], allowed, capability)
    return DomainReading(
        assessment=assessment[0] if assessment else "",
        interpretations=_strip_undeclared(reading.interpretations, allowed, capability),
        attention=_strip_undeclared(reading.attention, allowed, capability),
        next_steps=_strip_undeclared(reading.next_steps, allowed, capability),
        uncertainties=_strip_undeclared(reading.uncertainties, allowed, capability),
        numbers=allowed,
    )
