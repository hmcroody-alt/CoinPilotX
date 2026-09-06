"""Private Office Operations — the executive read model.

This module answers two questions the six primitives could not answer about
themselves: *what needs me today*, and *what is the shape of everything*. It is
a reader. It holds no table, writes no row, and defines no new record type; the
canonical writer remains :mod:`services.private_office.records` and there is
exactly one of it.

Why a separate module rather than more functions in ``records``
--------------------------------------------------------------
``records`` owns one record at a time: its columns, its transitions, its
provenance. Everything here is *across* records — ranking an obligation against
a risk, counting six tables into one summary — and that is a different kind of
correctness. Keeping it out of the writer also keeps the write-boundary guard's
allowlist honest: this module contains no SQL write, so it never needed to be
trusted with one.

What "attention" means here
---------------------------
An item is in the attention queue because a *field on it* says so, and the
reason is carried on the item. There is no scoring model, no learned relevance
and no "we thought you'd want to see this". Every reason in :data:`REASONS` maps
to a condition a reader can verify against the row, which is the property that
lets the screen say *why* each row is there — and the property that makes the
absence of a row meaningful.

Two reasons that a naive implementation would include are deliberately absent:

``EXPIRING_OPPORTUNITY``
    ``private_opportunities`` has no expiry column. Emitting this reason would
    require inventing a date, and a feature that renders "0 expiring" forever
    because the field is always NULL is worse than one that says the question
    cannot be answered. It is reported as unsupported, not as zero.

``ESCALATED``
    There is no escalation state in the model yet. It arrives with the slice
    that adds one.

Ranking
-------
The primary sort is the *strongest* reason on an item, never the sum of its
reasons. Summing lets three mild signals outrank one severe one, so a decision
that is merely open, merely low priority and merely old climbs above an overdue
tax filing — and the queue stops being a queue. :data:`REASON_RANK` is the whole
of the policy; ties break on a deterministic chain ending in the row id, so two
identical items never swap places between two reads of the same data.

Truthfulness of counts
----------------------
Counts come from ``records.count_records``, which counts in SQL. They are not
computed by measuring the bounded lists this module returns: the lists are
capped, so counting them would produce a summary that is correct for small
accounts and quietly wrong for large ones. Where the model genuinely cannot
answer, the field carries :data:`UNSUPPORTED` rather than ``0``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.private_office import audit as _audit
from services.private_office import records as _records

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------
#: The attention queue is a queue, not an export. Past a couple of screens the
#: member is not reading, they are scrolling, and the cost of the read grows for
#: nobody's benefit. The count in the header is not capped, so a member with 300
#: overdue items is told 300 and shown the worst 50.
MAX_ATTENTION_ITEMS = 50
MAX_RECENT_ACTIVITY = 25
#: Rows pulled per type when classifying. Bounded like everything else: six
#: fixed queries whose cost does not grow with the size of the account.
ATTENTION_SCAN_PER_TYPE = 120
#: What "recently" means for the completed list and the activity feed.
RECENT_WINDOW = timedelta(days=14)

#: The value a count carries when the model cannot answer the question. Not
#: ``0`` and not ``None``: zero is a claim, and ``None`` is indistinguishable
#: from a serialization bug at the far end of the wire.
UNSUPPORTED = "UNSUPPORTED"

# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------
REASON_OVERDUE = "OVERDUE"
REASON_HIGH_RISK = "HIGH_RISK"
REASON_DUE_SOON = "DUE_SOON"
REASON_RESPONSE_REQUIRED = "RESPONSE_REQUIRED"
REASON_MISSING_CONTEXT = "MISSING_REQUIRED_CONTEXT"
REASON_DECISION_REQUIRED = "DECISION_REQUIRED"
REASON_BLOCKED = "BLOCKED"

#: Strongest first. This ordering *is* the priority policy, and it is a tuple
#: rather than a dict of numbers so that inserting a reason forces a decision
#: about where it sits rather than letting someone pick a weight that happens to
#: land somewhere.
#:
#: The two placements worth defending:
#:
#: ``HIGH_RISK`` above ``DUE_SOON`` — a critical uninsured exposure outranks a
#: request due on Thursday, because the deadline will still be there on
#: Wednesday and the exposure may not be.
#:
#: ``BLOCKED`` last — a request waiting on a provider is on the queue so the
#: member can see it is moving, not because it needs them. Ranking it with the
#: things that do need them is how a queue fills with items nobody can act on,
#: which is how members learn to ignore the queue.
REASON_RANK: tuple[str, ...] = (
    REASON_OVERDUE,
    REASON_HIGH_RISK,
    REASON_DUE_SOON,
    REASON_RESPONSE_REQUIRED,
    REASON_MISSING_CONTEXT,
    REASON_DECISION_REQUIRED,
    REASON_BLOCKED,
)
REASONS: frozenset[str] = frozenset(REASON_RANK)

#: Reasons the mission names that the current model cannot support, with why.
#: Declared rather than omitted, so the screen can say "not tracked" instead of
#: implying "none found".
UNSUPPORTED_REASONS: dict[str, str] = {
    "EXPIRING_OPPORTUNITY": "private_opportunities has no expiry column",
    "ESCALATED": "no escalation state exists in the model",
}

#: Severities that make a live risk worth surfacing on their own.
ATTENTION_SEVERITIES: frozenset[str] = frozenset({"HIGH", "CRITICAL"})

_PRIORITY_RANK = {name: i for i, name in enumerate(reversed(_records.PRIORITIES))}
_REASON_INDEX = {name: i for i, name in enumerate(REASON_RANK)}


def _now() -> datetime:
    """Server time. A client-supplied clock must never decide what is overdue."""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def reasons_for(record: dict, *, record_type: str, blocked: bool = False) -> tuple[str, ...]:
    """Every attention reason true of *record*, strongest first.

    Takes a serialized record — the shape ``records.list_records`` returns — so
    it reads ``effective_status`` rather than recomputing time, and cannot
    disagree with what the member is shown next to the row.

    ``blocked`` is passed in rather than looked up here, because this function
    is given one record and has no cursor; resolving dependencies inside it
    would mean a query per row. :func:`attention` resolves the whole type in one
    pass and hands the answer down. It defaults to ``False`` so an existing
    caller keeps its current behaviour instead of silently gaining a reason.

    Returns ``()`` for a record that needs nothing. That is the common case and
    it is meant to be: a classifier that finds a reason for everything has not
    classified anything.
    """
    kind = str(record_type or record.get("record_type") or "").strip().upper()
    spec = _records.SPECS.get(kind)
    if spec is None:
        return ()

    stored = str(record.get("status") or "")
    # Closed is closed. Nothing below can put a resolved obligation or a
    # canceled request on the queue, which is the read-side half of the rule
    # `effective_status` enforces on the derivation side.
    if stored in spec["closing"]:
        return ()

    effective = str(record.get("effective_status") or stored)
    found: set[str] = set()

    if effective == _records.DERIVED_OVERDUE:
        found.add(REASON_OVERDUE)
    elif effective == _records.DERIVED_DUE_SOON:
        found.add(REASON_DUE_SOON)

    if kind == _records.TYPE_RISK:
        if str(record.get("severity") or "").upper() in ATTENTION_SEVERITIES:
            found.add(REASON_HIGH_RISK)
        # Two distinct fields, one meaning: somebody qualified has not looked.
        # `COVERAGE_STATES` deliberately has no value meaning "fine", so
        # PROVIDER_REQUIRED is an assertion that review is outstanding rather
        # than an absence being read as one.
        if record.get("review_required"):
            found.add(REASON_MISSING_CONTEXT)
        if str(record.get("coverage_state") or "").upper() == "PROVIDER_REQUIRED":
            found.add(REASON_MISSING_CONTEXT)

    if kind == _records.TYPE_REQUEST:
        if stored == "WAITING_ON_USER":
            found.add(REASON_RESPONSE_REQUIRED)
        elif stored == "WAITING_ON_PROVIDER":
            found.add(REASON_BLOCKED)

    # A record waiting on another record is blocked in exactly the sense
    # REASON_BLOCKED already means for a request waiting on a provider, so it
    # reuses the reason rather than introducing a second one that would need its
    # own place in REASON_RANK and its own explanation on the screen.
    #
    # It does not suppress the other reasons. An overdue obligation that is
    # blocked is still overdue — the blocker is what the member has to go and
    # chase, not a reason to stop showing them the deadline. Because
    # `primary_reason` is the strongest reason present and BLOCKED ranks last,
    # being blocked only decides the ranking for records that had nothing more
    # urgent to say.
    if blocked:
        found.add(REASON_BLOCKED)

    if kind == _records.TYPE_DECISION and stored in ("OPEN", "UNDER_REVIEW"):
        # An open decision is by definition a question waiting on the member.
        # It ranks near the bottom precisely because it is always true of every
        # open decision: it is a reason to list the row, not a reason to alarm.
        found.add(REASON_DECISION_REQUIRED)

    return tuple(r for r in REASON_RANK if r in found)


def _sort_key(item: dict) -> tuple:
    """Deterministic total order. Equal inputs always sort the same way.

    The chain is: strongest reason, then how soon, then how the member ranked
    it, then how recently it moved, then the row id. The id is what makes it a
    *total* order — without a final unique term two identical items can swap
    between reads, and a queue that reshuffles under a member's finger reads as
    broken even when the contents are right.
    """
    primary = _REASON_INDEX.get(item.get("primary_reason") or "", len(REASON_RANK))
    due = item.get("due_at") or ""
    # Empty sorts last: no deadline is not the same as a deadline in 1970.
    due_key = (1, "") if not due else (0, due)
    priority = -_PRIORITY_RANK.get(str(item.get("priority") or "").upper(), -1)
    updated = item.get("updated_at") or ""
    return (primary, due_key, priority, _Desc(updated), int(item.get("id") or 0))


class _Desc:
    """Sorts a string descending inside an otherwise ascending tuple."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __lt__(self, other: "_Desc") -> bool:
        return self.value > other.value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Desc) and self.value == other.value


def attention_item(record: dict, *, record_type: str, reasons: tuple[str, ...],
                   open_blocker_count: int = 0) -> dict:
    """One queue row: the record, why it is here, and what ranked it.

    ``primary_reason`` is on the item rather than derived by the caller so the
    screen, the API and the sort all read the same field. A UI that recomputes
    the reason is a UI that can display one thing while the order reflects
    another.

    ``open_blocker_count`` is carried alongside ``blocked`` for the same reason:
    "waiting on 3 things" is actionable and "blocked" is not, and a screen that
    wanted the number would otherwise have to ask per row.
    """
    kind = str(record_type).strip().upper()
    deadline_field = _records.DEADLINE_FIELDS.get(kind, "")
    blockers = max(0, int(open_blocker_count or 0))
    return {
        "blocked": blockers > 0,
        "open_blocker_count": blockers,
        "id": int(record.get("id") or 0),
        "record_type": kind,
        "title": record.get("title") or "",
        "status": record.get("status") or "",
        "effective_status": record.get("effective_status") or "",
        "due_at": record.get(deadline_field) if deadline_field else None,
        "deadline_field": deadline_field,
        "priority": record.get("priority") or "",
        "severity": record.get("severity") or "",
        "domain": record.get("domain") or "",
        "updated_at": record.get("updated_at") or "",
        "primary_reason": reasons[0] if reasons else "",
        "reasons": list(reasons),
    }


def attention(cur, *, owner_user_id: int, limit: int = MAX_ATTENTION_ITEMS) -> dict:
    """The ranked attention queue for one owner.

    Six fixed queries — one bounded scan per type — then classification and
    ranking in memory. No query is issued per record, so the cost is a function
    of the number of primitives, which is six and does not grow.

    Raises on a read failure rather than returning an empty queue. "Nothing
    needs your attention" and "I could not find out" are opposite claims, and
    the caller is responsible for keeping them apart; see the route layer, which
    turns the exception into an explicit unavailable state.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return {"items": [], "total": 0, "truncated": False,
                "unsupported_reasons": dict(UNSUPPORTED_REASONS)}

    now = _now()
    # Every dependency this owner has, resolved once, outside the type loop.
    # Per row it would be a query per record; per type it would be five queries
    # six times over, on a read that is otherwise thirty queries in total. Here
    # it is five, and it stays five however much the member has going on.
    blockers = _records.open_blocker_counts_all(cur, owner_user_id=owner)

    collected: list[dict] = []
    for kind in _records.RECORD_TYPES:
        rows = _records.list_records(
            cur, record_type=kind, owner_user_id=owner,
            statuses=_records.working_statuses(kind),
            limit=ATTENTION_SCAN_PER_TYPE,
        )
        waiting_by_id = blockers.get(kind, {})
        for row in rows:
            waiting = waiting_by_id.get(int(row.get("id") or 0), 0)
            found = reasons_for(row, record_type=kind, blocked=waiting > 0)
            if found:
                collected.append(attention_item(
                    row, record_type=kind, reasons=found,
                    open_blocker_count=waiting))

    collected.sort(key=_sort_key)
    bounded = max(1, min(int(limit or MAX_ATTENTION_ITEMS), MAX_ATTENTION_ITEMS))
    return {
        "items": collected[:bounded],
        # The true count, not the length of the page. A member with 300 items is
        # told 300; capping the number as well as the list would be the display
        # bound quietly editing the fact.
        "total": len(collected),
        # Counted over everything collected, not over the page, for the same
        # reason. A blocked record always carries REASON_BLOCKED and so is always
        # collected, which makes this complete within the same per-type scan
        # bound `total` already lives under.
        "blocked": sum(1 for item in collected if item.get("blocked")),
        "truncated": len(collected) > bounded,
        "scanned_at": _iso(now),
        "unsupported_reasons": dict(UNSUPPORTED_REASONS),
    }


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _end_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=23, minute=59, second=59, microsecond=999999)


def counts(cur, *, owner_user_id: int) -> dict:
    """Current counts across all six primitives.

    "Current" is one definition applied six times: ``lifecycle_state`` is
    ACTIVE, so a superseded revision is not counted alongside the row that
    replaced it, and the status is one the type does not consider an ending.
    Both halves matter — dropping the first inflates every count after the
    member edits anything, dropping the second means completed work never leaves
    the dashboard.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return {}
    out: dict = {}
    for kind in _records.RECORD_TYPES:
        out[kind] = _records.count_records(
            cur, record_type=kind, owner_user_id=owner, open_only=True)
    return out


def overview(cur, *, owner_user_id: int, now: datetime | None = None) -> dict:
    """The executive summary: what is live, what is late, what just happened.

    Every number here is a SQL count over the owner's rows. Where a question
    cannot be answered from the current model the field carries
    :data:`UNSUPPORTED` and the reason is listed in ``unsupported``, because a
    fabricated zero is indistinguishable from a real one and the member has no
    way to tell they are looking at a gap.
    """
    owner = int(owner_user_id or 0)
    reference = now or _now()
    if owner <= 0:
        return {"counts": {}, "attention": {"items": [], "total": 0},
                "recent_activity": [],
                "unsupported": dict(UNSUPPORTED_REASONS)}

    today_end = _iso(_end_of_day(reference))
    week_end = _iso(_end_of_day(reference + timedelta(days=7)))
    now_iso = _iso(reference)
    since_iso = _iso(reference - RECENT_WINDOW)

    deadline_types = tuple(
        k for k in _records.RECORD_TYPES if k in _records.DEADLINE_FIELDS)

    overdue = 0
    due_today = 0
    due_this_week = 0
    for kind in deadline_types:
        live = _records.working_statuses(kind)
        overdue += _records.count_records(
            cur, record_type=kind, owner_user_id=owner,
            statuses=live, due_before=now_iso)
        due_today += _records.count_records(
            cur, record_type=kind, owner_user_id=owner,
            statuses=live, due_after=now_iso, due_before=today_end)
        due_this_week += _records.count_records(
            cur, record_type=kind, owner_user_id=owner,
            statuses=live, due_after=now_iso, due_before=week_end)

    open_counts = counts(cur, owner_user_id=owner)
    queue = attention(cur, owner_user_id=owner)
    # Sourced from the audit ledger, not reconstructed from the rows as they
    # stand now. The distinction is the whole point: current rows can tell you
    # what a record *is*, never what happened to it, and inferring the second
    # from the first relabels every past change with today's outcome.
    activity = _audit.recent_record_activity(
        cur, owner_user_id=owner, limit=MAX_RECENT_ACTIVITY)

    recently_completed = 0
    for kind in _records.RECORD_TYPES:
        spec = _records.SPECS[kind]
        if not spec["closing"]:
            continue
        recently_completed += _records.count_records(
            cur, record_type=kind, owner_user_id=owner,
            statuses=spec["closing"], closed_since=since_iso)

    live_risk_statuses = _records.working_statuses(_records.TYPE_RISK)
    active_risks = _records.count_records(
        cur, record_type=_records.TYPE_RISK, owner_user_id=owner,
        statuses=live_risk_statuses)
    # Counted in SQL, not by filtering the attention page. The page is capped at
    # MAX_ATTENTION_ITEMS, so counting it would make the number agree with the
    # dashboard exactly until the member has more than fifty things wrong, which
    # is the moment the number matters.
    active_high_risks = _records.count_records(
        cur, record_type=_records.TYPE_RISK, owner_user_id=owner,
        statuses=live_risk_statuses, severities=sorted(ATTENTION_SEVERITIES))

    return {
        "as_of": now_iso,
        "needs_attention": queue["total"],
        # Records waiting on another record. Distinct from `needs_attention`
        # and deliberately not subtracted from it: something can be both
        # blocked and overdue, and a dashboard that moved those rows out of the
        # attention count would let a late obligation disappear from the number
        # the member actually reads by pointing it at a second late thing.
        "blocked": queue.get("blocked", 0),
        "due_today": due_today,
        "due_this_week": due_this_week,
        "overdue": overdue,
        "pending_decisions": open_counts.get(_records.TYPE_DECISION, 0),
        "open_requests": open_counts.get(_records.TYPE_REQUEST, 0),
        "awaiting_response": _records.count_records(
            cur, record_type=_records.TYPE_REQUEST, owner_user_id=owner,
            statuses=("WAITING_ON_USER",)),
        # Two numbers, two names. A single `active_high_risks` that actually
        # counted all live risks would be the same class of untruth as a
        # fabricated zero — a label overstating what it counts — just harder to
        # notice, because it is only wrong for members who have low-severity
        # risks, which is most of them.
        "active_risks": active_risks,
        "active_high_risks": active_high_risks,
        "active_opportunities": open_counts.get(_records.TYPE_OPPORTUNITY, 0),
        "recently_completed": recently_completed,
        # No expiry column exists, so this is not zero — it is unanswerable.
        "expiring_opportunities": UNSUPPORTED,
        "counts": open_counts,
        "attention": queue,
        "recent_activity": activity,
        "recent_window_days": RECENT_WINDOW.days,
        "unsupported": dict(UNSUPPORTED_REASONS),
    }
