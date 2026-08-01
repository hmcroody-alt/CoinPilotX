"""The first reader of the learning log, and the first one that opens a row.

``pulse_ai_learning_events`` has been accumulating since it was created. Eleven call
sites in ``pulse_ai_service`` write to it — rate limits, safety refusals, agent actions,
provider failures, answered messages, conversation resets, settings changes, memory
corrections and deletions, feedback. One reads it. ``admin_learning_dashboard`` runs::

    SELECT COUNT(*) AS total FROM pulse_ai_learning_events

beside eight other tables. It opens no row. It never touches ``event_type``, ``source``
or ``metadata_json``, and it filters by no owner. It answers how many events exist,
which is the one question about them whose answer cannot be wrong and cannot be useful.

This module is the second reader. It goes through :mod:`~services.undx_brain.memory`
rather than opening its own connection, so the owner clause is bound by the layer that
exists to make forgetting it impossible, and it aggregates in Python over parsed rows
rather than in SQL, so every conclusion below is reachable without a database.

Three findings from reading the writer, each of which shaped something here.

*Some events can never be attributed.* ``_record_learning_event`` stores
``int(user_id or 0) or None``, so an event recorded for user ``0`` lands with a NULL
``user_id`` — indistinguishable from one recorded for nobody. No owner-scoped read will
ever return those rows, because ``WHERE user_id = ?`` does not match NULL, and
:func:`memory.owner_id` refuses ``0`` so no scope can even be opened for them. That is
the correct outcome and it is not a gap to be closed; it is a limit to be stated, which
is why :attr:`Window.unattributable` exists and why nothing here reports a total.

*The interesting dimensions live inside a JSON blob.* ``event_type`` and ``source`` are
columns; ``capability_id``, ``status``, ``rating`` and ``reason`` are keys in
``metadata_json``. So a dimension may name a metadata key, and an event that does not
carry that key is counted as *absent* and excluded from the denominator rather than
quietly folded into it. A share computed over events that could not have had the key is
not a share of anything.

*A count of a thing is not evidence about the thing.* Two guards follow from that, and
they are the reason this module is not just ``collections.Counter`` with a docstring.
A distribution names a leader only when the leader's margin survives one event going
missing — and events are known to go missing, see above. A succession is only a pattern
when the conditional rate beats the base rate: "a provider failure is followed by an
answered message 80% of the time" says nothing if 80% of all events are answered
messages, and reporting it as a finding would be the most confident-sounding way to say
nothing in this file.

Everything is time-qualified through :mod:`~services.undx_brain.facts`, not through a
second scale invented here. That has a consequence worth stating plainly rather than
engineering around: the horizon for what these events are is short, so a finding is
essentially always ``AS_OF`` the newest event it rests on. This is right. A pattern
across thirty days of history is a true statement about those thirty days and an
unfounded one about this afternoon, and the sentence in :attr:`Finding.answer` is
assembled to say the first.

Behind ``UNDX_BRAIN_LEARNING_ENABLED``, which defaults off. Off, every entry point
answers ``ok=False``, so a disabled reader is never mistaken for one that looked and
found nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from . import config as brain_config
from . import facts
from . import memory
from .memory import MemoryKind, Scope
from .truth import TrustLevel

__all__ = [
    "TABLE",
    "EVENT_TRUST",
    "MAX_EVENTS",
    "MIN_SUPPORT",
    "MIN_MARGIN",
    "METADATA_PREFIX",
    "Dimension",
    "Event",
    "Window",
    "Bucket",
    "Finding",
    "from_row",
    "load",
    "dimension_of",
    "distribution",
    "succession",
]

#: The one table this module reads. Named once so a second spelling cannot drift in.
TABLE = "pulse_ai_learning_events"

#: What a learning event is worth as evidence. Each row was written by PulseSoc's own
#: instrumentation at the moment the thing happened, which is a live observation of that
#: moment — and of nothing since. Declared rather than inferred from a column, because
#: the table carries no confidence and guessing one would manufacture provenance.
EVENT_TRUST: TrustLevel = TrustLevel.LIVE_VERIFIED

#: The most rows one call will load. A bound rather than a page: the questions below are
#: about shape, and a shape that needs more than this many events to show up is not a
#: shape a response should be built on.
MAX_EVENTS = 500

#: Below this many usable observations, nothing is concluded. Four events split three to
#: one and four events split two to two are the same evidence about the underlying rate,
#: and the honest answer is how many more are needed rather than a percentage.
MIN_SUPPORT = 5

#: How far ahead of the runner-up a leader must be. One event, because one event is
#: exactly the amount of evidence that is known to be able to vanish here — an event
#: written for user ``0`` is unattributable and simply never arrives — so a leader whose
#: margin is one event is a leader who might not be.
MIN_MARGIN = 2

#: A dimension naming a key inside ``metadata_json`` rather than a column.
METADATA_PREFIX = "metadata:"

_COLUMNS = ("id", "user_id", "event_type", "source", "metadata_json", "created_at")

#: The two columns a dimension may name directly. ``public_id`` and ``id`` identify a
#: row rather than describe it, and ``created_at`` is the axis rather than a dimension;
#: grouping by any of them produces one bucket per event, which is a list wearing a
#: distribution's clothes.
_COLUMN_DIMENSIONS = frozenset({"event_type", "source"})


class Dimension(str, Enum):
    """The two columns worth grouping by. A metadata key is spelled as a string."""

    EVENT_TYPE = "event_type"
    SOURCE = "source"


@dataclass(frozen=True)
class Event:
    """One row, parsed. Never a raw row, and never a raw ``metadata_json`` string."""

    event_id: int = 0
    owner_id: int = 0
    event_type: str = ""
    source: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    #: Why this row is less than it looks — unreadable metadata, missing timestamp.
    notes: tuple[str, ...] = field(default=(), repr=False)

    @property
    def usable(self) -> bool:
        """Whether this event can take part in a finding at all."""
        return bool(self.event_type and self.created_at)


@dataclass(frozen=True)
class Window:
    """The events one owner's scope could reach, and what it could not."""

    ok: bool = False
    owner_id: int = 0
    events: tuple[Event, ...] = ()
    #: Rows returned that could not be parsed into a usable event. Listed rather than
    #: dropped, because a denominator that silently shrinks is a denominator that lies.
    unusable: int = 0
    #: True when the read hit :data:`MAX_EVENTS` and older events exist unread. Every
    #: finding built from a truncated window says so.
    truncated: bool = False
    #: How many events this owner has that no owner-scoped read can ever return, because
    #: their ``user_id`` is NULL. Always this sentence and never a number: the count is
    #: not merely unmeasured here, it is unmeasurable from inside an owner scope, and
    #: reporting zero would turn a boundary into a false reassurance.
    unattributable: str = (
        "unknown: events stored with a NULL user_id are unreachable from any owner scope"
    )
    first_at: str = ""
    last_at: str = ""
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.ok and bool(self.events)

    @property
    def span_seconds(self) -> float | None:
        """How long a stretch of history this is, or ``None`` if that is unreadable."""
        first, _ = facts.parse_moment(self.first_at)
        last, _ = facts.parse_moment(self.last_at)
        if first is None or last is None:
            return None
        return max(0.0, (last - first).total_seconds())


@dataclass(frozen=True)
class Bucket:
    """One value of a dimension, with how much of the window it accounts for."""

    name: str
    count: int = 0
    #: Of the events that *could* have this dimension. Never of the whole window.
    share: float = 0.0


@dataclass(frozen=True)
class Finding:
    """One answer, with everything needed to judge how much it is worth.

    Truthiness is :attr:`conclusive`, not :attr:`ok`. A finding that was computed
    correctly over too little evidence is a successful call and an unusable answer, and
    a caller that checks the wrong one of those states a coin flip as a result.
    """

    ok: bool = False
    kind: str = ""
    question: str = ""
    #: A sentence that is safe to say as-is: already hedged, already time-qualified,
    #: already refusing where it should. Assembled here so the response layer does not
    #: assemble it and occasionally assemble it weaker.
    answer: str = ""
    conclusive: bool = False
    #: Events the answer rests on.
    support: int = 0
    #: Events that could have carried this dimension. The denominator.
    considered: int = 0
    #: Events that could not have carried it, excluded from the denominator and counted
    #: here so their exclusion is visible.
    absent: int = 0
    buckets: tuple[Bucket, ...] = ()
    leader: str = ""
    #: Populated instead of ``leader`` when the top values are within :data:`MIN_MARGIN`.
    tied: tuple[str, ...] = ()
    #: Succession only: the rate of the consequent after the antecedent.
    conditional_rate: float | None = None
    #: Succession only: the rate of the consequent anywhere in the window.
    base_rate: float | None = None
    #: Succession only: conditional over base. Below 1.0 the antecedent makes the
    #: consequent *less* likely, which is a finding and not a failure.
    lift: float | None = None
    reading: facts.Reading | None = None
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.conclusive


# ---------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------


def from_row(row: Mapping[str, Any] | Any) -> Event:
    """Parse one ``pulse_ai_learning_events`` row. Never raises.

    A row with unreadable ``metadata_json`` becomes an event with empty metadata and a
    note, not an exception and not a dropped row: the ``event_type`` and the timestamp
    are still true and still count toward a distribution over those.
    """
    data = _mapping(row)
    notes: list[str] = []

    raw = data.get("metadata_json")
    metadata: dict[str, Any] = {}
    if isinstance(raw, Mapping):
        metadata = {str(key): value for key, value in raw.items()}
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            notes.append("metadata_json could not be parsed and was not guessed at")
        else:
            if isinstance(parsed, dict):
                metadata = {str(key): value for key, value in parsed.items()}
            else:
                notes.append(
                    f"metadata_json held a {type(parsed).__name__} rather than an object"
                )

    created = _text(data.get("created_at"))
    if not created:
        notes.append("the event carries no timestamp, so it cannot be placed in time")
    event_type = _text(data.get("event_type"))
    if not event_type:
        notes.append("the event carries no event_type, so there is nothing to group it by")

    owner = memory.owner_id(data.get("user_id"))
    if owner is None:
        notes.append(
            "the event names no owner, which is what an event recorded for user 0 "
            "looks like once stored"
        )

    return Event(
        event_id=_int(data.get("id")),
        owner_id=owner or 0,
        event_type=event_type,
        source=_text(data.get("source")),
        metadata=metadata,
        created_at=created,
        notes=tuple(notes),
    )


def load(
    scope: Scope,
    cur: Any,
    *,
    limit: int = MAX_EVENTS,
    since: str = "",
    env: Mapping[str, str] | None = None,
) -> Window:
    """Load one owner's learning events, newest first, bounded.

    The statement is handed to :func:`memory.read` with the owner written as a marker,
    so this module never holds an owner value it could get wrong, and a scope opened for
    another kind cannot be used to reach this table.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Window(
            reason="reading the learning log is disabled; it is still only counted",
            notes=notes,
        )

    bound = _bounded(limit)
    sql = (
        f"SELECT {', '.join(_COLUMNS)} FROM {TABLE} "
        "WHERE user_id = {owner} AND COALESCE(created_at, '') >= ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?"
    )
    result = memory.read(scope, MemoryKind.LEARNING_EVENT, cur, sql, (_text(since), bound))
    if not result.ok:
        return Window(
            owner_id=getattr(scope, "owner_id", 0) or 0,
            reason=result.reason or "the learning log could not be read",
            notes=notes,
        )

    parsed = [from_row(row) for row in result.rows]
    usable = tuple(event for event in parsed if event.usable)
    stamps = sorted(event.created_at for event in usable if event.created_at)

    window_notes = list(notes)
    if len(parsed) >= bound:
        window_notes.append(
            f"the read stopped at {bound} events; older ones exist and were not seen"
        )
    unusable = len(parsed) - len(usable)
    if unusable:
        window_notes.append(
            f"{unusable} row(s) lacked an event_type or a timestamp and take part in "
            "no finding"
        )

    return Window(
        ok=True,
        owner_id=getattr(scope, "owner_id", 0) or 0,
        events=usable,
        unusable=unusable,
        truncated=len(parsed) >= bound,
        first_at=stamps[0] if stamps else "",
        last_at=stamps[-1] if stamps else "",
        notes=tuple(window_notes),
    )


# ---------------------------------------------------------------------------------
# questions a count cannot answer
# ---------------------------------------------------------------------------------


def dimension_of(event: Event, dimension: Dimension | str) -> str | None:
    """The value of one dimension for one event, or ``None`` when it has none.

    ``None`` and ``""`` are different answers and are kept different. An event with no
    ``capability_id`` could not have had one; an event whose ``capability_id`` is the
    empty string had the key and it was blank. Only the first is excluded from a
    denominator.
    """
    name = str(getattr(dimension, "value", dimension) or "")
    if name in _COLUMN_DIMENSIONS:
        value = getattr(event, name, "")
        return str(value) if value else None
    if name.startswith(METADATA_PREFIX):
        key = name[len(METADATA_PREFIX):]
        if not key or key not in event.metadata:
            return None
        raw = event.metadata.get(key)
        if raw is None:
            return None
        if isinstance(raw, bool):
            return "true" if raw else "false"
        if isinstance(raw, (str, int, float)):
            return str(raw)
        # A list or an object is a value this cannot group by without inventing an
        # ordering or a spelling for it, and either would make two equal things unequal.
        return None
    return None


def distribution(
    window: Window,
    dimension: Dimension | str,
    *,
    now: Any = None,
    env: Mapping[str, str] | None = None,
) -> Finding:
    """How this owner's events divide across one dimension, and whether one leads.

    This is the "which capability is corrected most" question. It answers with a leader
    only when the leader is far enough ahead to survive an event going missing; the
    honest alternative is not silence but a named tie.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Finding(
            reason="reading the learning log is disabled; it is still only counted",
            notes=notes,
        )
    name = str(getattr(dimension, "value", dimension) or "")
    question = f"how do these events divide across {name}?"
    if not _known_dimension(name):
        return Finding(
            ok=False, kind="distribution", question=question,
            reason=(
                f"{name!r} is not a dimension: name one of "
                f"{', '.join(sorted(_COLUMN_DIMENSIONS))} or a "
                f"{METADATA_PREFIX}<key>"
            ),
            notes=tuple(notes),
        )
    if not isinstance(window, Window) or not window.ok:
        return Finding(
            ok=False, kind="distribution", question=question,
            reason=getattr(window, "reason", "") or "no window to read",
            notes=tuple(notes) + tuple(getattr(window, "notes", ())),
        )

    counts: dict[str, int] = {}
    absent = 0
    for event in window.events:
        value = dimension_of(event, name)
        if value is None:
            absent += 1
            continue
        counts[value] = counts.get(value, 0) + 1

    considered = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    buckets = tuple(
        Bucket(name=key, count=count, share=(count / considered) if considered else 0.0)
        for key, count in ordered
    )

    finding_notes = list(notes) + list(window.notes)
    if absent:
        finding_notes.append(
            f"{absent} event(s) could not carry {name} and are excluded from the "
            "denominator rather than counted as a value"
        )

    if considered < MIN_SUPPORT:
        return Finding(
            ok=True, kind="distribution", question=question,
            answer=_not_yet(considered, name),
            conclusive=False, support=considered, considered=considered, absent=absent,
            buckets=buckets,
            reason=(
                f"{considered} usable event(s) is below the floor of {MIN_SUPPORT}; "
                f"{MIN_SUPPORT - considered} more would reach it"
            ),
            notes=tuple(finding_notes),
        )

    top = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0
    margin = top[1] - runner_up
    leader = ""
    tied: tuple[str, ...] = ()
    if margin >= MIN_MARGIN:
        leader = top[0]
    else:
        tied = tuple(key for key, count in ordered if top[1] - count < MIN_MARGIN)

    reading = _reading(window, now=now, env=env)
    # A finding that cannot be placed in time is not a finding that may be stated, so
    # the reading has to have been obtainable. Note that ``bool(reading)`` is False for
    # every time-qualified reading and that is the expected case here — what is required
    # is ``reading.ok``, not a reading that claims the present.
    conclusive = bool(leader) and reading.ok

    if not reading.ok:
        finding_notes.append(
            "the finding could not be time-qualified, so it is reported but not "
            f"concluded: {reading.reason}"
        )

    if leader:
        share = counts[leader] / considered
        answer = (
            f"{leader} accounts for {counts[leader]} of {considered} "
            f"{name} values ({share:.0%})"
        )
    else:
        answer = (
            f"no {name} value leads: "
            + ", ".join(f"{key} ({counts[key]})" for key in tied)
            + f" are within {MIN_MARGIN} events of each other"
        )
    answer = _in_time(answer, reading)

    return Finding(
        ok=True, kind="distribution", question=question, answer=answer,
        conclusive=conclusive, support=counts[leader] if leader else considered,
        considered=considered, absent=absent, buckets=buckets,
        leader=leader, tied=tied, reading=reading,
        reason="" if conclusive else (
            reading.reason if not reading.ok
            else f"the top values are within {MIN_MARGIN} events of each other"
        ),
        notes=tuple(finding_notes),
    )


def succession(
    window: Window,
    antecedent: str,
    consequent: str,
    *,
    within_seconds: int = 3600,
    now: Any = None,
    env: Mapping[str, str] | None = None,
) -> Finding:
    """Whether one kind of event tends to be followed by another.

    This is the "does a provider failure predict the next one" question, and it is the
    one where a rate on its own is worthless. The consequent's rate across the whole
    window is computed too, and nothing is concluded unless the conditional rate beats
    it by :data:`MIN_MARGIN` events' worth of difference — otherwise the finding is
    reporting the background and calling it a signal.

    Only the *next* event counts, not any event in the window. "Followed at some point
    within an hour" grows more true the busier the account is, which would make the
    finding a measure of activity wearing a prediction's clothes.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Finding(
            reason="reading the learning log is disabled; it is still only counted",
            notes=notes,
        )
    first = _text(antecedent)
    then = _text(consequent)
    question = f"is {first or '?'} followed by {then or '?'}?"
    if not first or not then:
        return Finding(
            ok=False, kind="succession", question=question,
            reason="both an antecedent and a consequent event_type are required",
            notes=tuple(notes),
        )
    if not isinstance(window, Window) or not window.ok:
        return Finding(
            ok=False, kind="succession", question=question,
            reason=getattr(window, "reason", "") or "no window to read",
            notes=tuple(notes) + tuple(getattr(window, "notes", ())),
        )

    limit = max(0, int(within_seconds or 0))
    # ``load`` returns newest first; succession is a statement about time moving
    # forwards, so it is read in the other direction.
    ordered = sorted(window.events, key=lambda event: (event.created_at, event.event_id))

    occurrences = 0
    followed = 0
    unreadable = 0
    for index, event in enumerate(ordered[:-1] if ordered else []):
        if event.event_type != first:
            continue
        occurrences += 1
        nxt = ordered[index + 1]
        gap = _gap_seconds(event.created_at, nxt.created_at)
        if gap is None:
            unreadable += 1
            continue
        if gap <= limit and nxt.event_type == then:
            followed += 1

    # The base rate is the consequent's share of every event that *could* have been the
    # next one — that is, everything except the first event in the window.
    eligible = max(0, len(ordered) - 1)
    base_hits = sum(1 for event in ordered[1:] if event.event_type == then)
    base_rate = (base_hits / eligible) if eligible else None
    conditional = (followed / occurrences) if occurrences else None
    lift = (
        (conditional / base_rate)
        if conditional is not None and base_rate not in (None, 0.0)
        else None
    )

    finding_notes = list(notes) + list(window.notes)
    if unreadable:
        finding_notes.append(
            f"{unreadable} pair(s) had an unreadable gap and count as not followed"
        )
    if window.truncated:
        finding_notes.append(
            "the window was truncated, so the oldest antecedent here may have had a "
            "successor that was not loaded"
        )

    if occurrences < MIN_SUPPORT:
        return Finding(
            ok=True, kind="succession", question=question,
            answer=(
                f"{first} has happened {occurrences} time(s) here, which is not enough "
                f"to say what follows it; {MIN_SUPPORT - occurrences} more would be"
            ),
            conclusive=False, support=occurrences, considered=eligible,
            conditional_rate=conditional, base_rate=base_rate, lift=lift,
            reason=(
                f"{occurrences} occurrence(s) of {first} is below the floor of "
                f"{MIN_SUPPORT}"
            ),
            notes=tuple(finding_notes),
        )

    reading = _reading(window, now=now, env=env)
    # The margin, expressed in events rather than in percentage points: the conditional
    # rate has to beat the base rate by enough that MIN_MARGIN of these occurrences
    # going the other way would not erase the difference.
    expected = (base_rate or 0.0) * occurrences
    beats = (followed - expected) >= MIN_MARGIN
    conclusive = bool(beats and reading.ok)

    if not reading.ok:
        finding_notes.append(
            "the finding could not be time-qualified, so it is reported but not "
            f"concluded: {reading.reason}"
        )

    if beats:
        answer = (
            f"{first} is followed by {then} in {followed} of {occurrences} cases "
            f"({conditional:.0%}), against {base_rate:.0%} for {then} generally"
        )
    else:
        answer = (
            f"{first} is followed by {then} in {followed} of {occurrences} cases "
            f"({conditional:.0%}), which is not above the {base_rate:.0%} that "
            f"{then} runs at anyway"
        )
    answer = _in_time(answer, reading)

    return Finding(
        ok=True, kind="succession", question=question, answer=answer,
        conclusive=conclusive, support=occurrences, considered=eligible,
        conditional_rate=conditional, base_rate=base_rate, lift=lift,
        reading=reading,
        reason="" if conclusive else (
            reading.reason if not reading.ok
            else f"{then} is no more likely after {first} than it is generally"
        ),
        notes=tuple(finding_notes),
    )


# ---------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------


def _enabled(env: Mapping[str, str] | None) -> tuple[bool, tuple[str, ...]]:
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    on = bool(values.get("UNDX_BRAIN_ENABLED", False)) and bool(
        values.get("UNDX_BRAIN_LEARNING_ENABLED", False)
    )
    return on, tuple(resolution.notes)


def _known_dimension(name: str) -> bool:
    if name in _COLUMN_DIMENSIONS:
        return True
    return name.startswith(METADATA_PREFIX) and len(name) > len(METADATA_PREFIX)


def _reading(window: Window, *, now: Any, env: Mapping[str, str] | None) -> facts.Reading:
    """Place a finding in time by asking :mod:`facts`, not by deciding here."""
    return facts.read(
        facts.Observation(
            subject=f"{TABLE}.window",
            value=f"{len(window.events)} events",
            source=TABLE,
            trust=EVENT_TRUST.value,
            observed_at=window.last_at,
        ),
        now=now,
        env=env,
    )


#: What every assembled sentence says about itself. Deliberately not
#: :func:`truth.hedge_for`, and the difference matters: the hedge for
#: :data:`EVENT_TRUST` reads "this was confirmed against a running system", which is true
#: of each event and false of a pattern computed over them. Nothing here was confirmed
#: against anything; it was counted. The events' own hedge is still reachable through
#: :attr:`Finding.reading` for a caller who wants to describe the inputs, but the
#: sentence must not borrow it.
GROUNDING = "in the events on record"


def _in_time(answer: str, reading: facts.Reading) -> str:
    """Attach the tense the finding is entitled to, and no more.

    Only the *qualifier* is taken from the reading — the "as of <time>" half, which is
    the half :mod:`facts` is authoritative about here.
    """
    if reading.ok and reading.qualifier:
        return f"{answer}, {GROUNDING} {reading.qualifier}"
    if reading.ok:
        return f"{answer}, {GROUNDING}"
    # Without a reading there is no honest tense at all, so the sentence gets the
    # weakest one available rather than the present.
    return f"{answer}, {GROUNDING}, at a time that could not be established"


def _not_yet(count: int, name: str) -> str:
    return (
        f"there are {count} usable {name} value(s) on record, which is below the "
        f"{MIN_SUPPORT} needed before a share means anything"
    )


def _gap_seconds(earlier: str, later: str) -> float | None:
    first, _ = facts.parse_moment(earlier)
    second, _ = facts.parse_moment(later)
    if first is None or second is None:
        return None
    return (second - first).total_seconds()


def _bounded(limit: Any) -> int:
    """Resolve a requested limit, erring towards reading less rather than more.

    Deliberately looser than :func:`memory.owner_id`, and for a reason that does not
    generalise from it: an owner id coerced wrongly reaches a different person's data,
    while a limit coerced wrongly reaches *less* of the same person's. So ``3.7``
    becomes three rather than being refused — the only direction the coercion can go is
    down — and only an unreadable or non-positive limit falls back to the ceiling, which
    is the value the caller would have got by not asking.
    """
    if isinstance(limit, bool):
        # ``True`` is an int, and a limit of one event is not what anybody meant.
        return MAX_EVENTS
    try:
        value = int(limit)
    except Exception:
        return MAX_EVENTS
    if value <= 0:
        return MAX_EVENTS
    return min(value, MAX_EVENTS)


def _mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    try:
        return dict(row)
    except Exception:
        return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0
