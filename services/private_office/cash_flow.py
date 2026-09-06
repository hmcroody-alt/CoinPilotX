"""The Capital Graph's forward obligation schedule — outflows only, and it says so.

What this module is
-------------------
A time-bucketed view of the obligations the member has actually recorded a
**due date and an amount** for. It composes
:func:`obligation_projection.liabilities_view`, which is already owner-gated,
already bounded and already reports its own truncation. Like
:mod:`capital_overview`, this file performs no SQL, imports no writer, and
does no FX. It is a projection of a projection.

The three things it refuses to do
---------------------------------
**It does not fabricate recurrence.** PulseSoc records an obligation with one
``due_at``. Nothing in the store says a mortgage repeats monthly, and this
module will not infer it from the title, the kind, or the shape of the amount.
A member with one recorded mortgage payment due in March has exactly one
scheduled outflow here, not twelve. Inventing the other eleven would produce a
confident annual figure out of a single data point, and the member would have
no way to tell which part was theirs. If recurrence is wanted it must be
recorded as a field on the obligation and projected like any other fact, with
its own provenance.

**It does not claim to be cash flow.** Cash flow is inflows minus outflows.
PulseSoc has no income ledger: there is no salary record, no dividend record,
no rent-received record. A view that showed only outflows and called itself
"cash flow" would imply the inflow side had been checked and found to be zero.
So the payload is named for what it is — a schedule of known outflows — and
:data:`INFLOWS_BASIS` states the absence in the response rather than leaving a
client to assume it. ``net`` does not exist here, and adding it would require
an income source first.

**It does not treat undated as never.** An obligation with an amount but no due
date is real money owed at an unknown time. It is excluded from every bucket —
because it belongs to no bucket — and counted in ``excluded.undated``, so a
client can say "plus £X with no date recorded" instead of quietly showing a
schedule that omits it. The same holds in reverse: an obligation dated but
unquantified is counted, never bucketed as zero.

Currency
--------
Buckets are summed only within a single currency, decided the same way
:func:`obligation_projection.liabilities_view` decides it. If the member's
obligations span more than one real currency, no bucket total is produced at
all: the rows are still listed and every row keeps its own currency, but the
totals are ``None`` and ``complete`` is False. There is no rate table in this
file and there will not be one without a timestamped, approved source.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.private_office import capital_graph as _capital
from services.private_office import obligation_projection as _obligations

LOGGER = logging.getLogger("private_office.cash_flow")

#: Same matrix row as every other capital read. Named from ``capital_graph``
#: rather than repeated so a rename cannot strand this surface on a feature id
#: nothing else gates on.
FEATURE_ID = _capital.FEATURE_ID

#: The one refusal reason this surface can produce, spelled the same way the
#: liabilities view spells it — the denial is relayed from there, and a second
#: spelling would let a client special-case one and miss the other.
DENIED_NOT_OWNER = "actor_is_not_owner"

#: Stated in the payload. The reason this is a constant and not a docstring is
#: that the client must be able to render it: a schedule of outflows shown
#: without this sentence reads as a complete picture of the member's money.
INFLOWS_BASIS = (
    "Outflows only. PulseSoc records obligations but has no income ledger, so "
    "nothing here has been netted against earnings, dividends or rent "
    "received. This is what is owed and when, not what will be left."
)

#: Stated in the payload for the same reason.
RECURRENCE_BASIS = (
    "Each obligation appears once, on the due date recorded for it. Recurrence "
    "is never inferred: a monthly commitment recorded as a single dated "
    "obligation is scheduled once here, not twelve times."
)

#: Bucket edges in days from the read time. The first bucket is everything
#: already past due, which is deliberately separated rather than folded into
#: the nearest window — an overdue obligation is a different fact about the
#: member's affairs than one due next week, and averaging them hides it.
BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("overdue", None, 0),
    ("due_30", 0, 30),
    ("due_90", 30, 90),
    ("due_180", 90, 180),
    ("due_365", 180, 365),
    ("beyond_365", 365, None),
)

#: Rows returned in the schedule. The bound exists for the same reason it
#: exists on the liabilities read; the underlying view is bounded at 200 and
#: reports its own truncation, which this module passes through rather than
#: recomputing.
MAX_SCHEDULE_ROWS = _obligations.MAX_LIABILITY_ROWS


def _denied(reason: str) -> dict:
    """The refusal shape — never a thin schedule.

    An empty schedule and a refused schedule look identical to a chart, and
    one of them is a lie about the member's obligations. Every key a caller
    would read is present and empty, and ``ok`` is False.
    """
    return {
        "ok": False,
        "denied": {"reason": reason},
        "schedule": [],
        "buckets": {},
        "totals": {},
        "excluded": {},
        "sync": {},
    }


def _parse_due(raw: str | None) -> datetime | None:
    """A due date, or ``None`` if it cannot be read as one.

    Deliberately returns ``None`` rather than raising or defaulting to now: a
    date the store cannot parse is an unknown date, and an unknown date must
    land in ``excluded.undated`` with the genuinely undated rows. Defaulting to
    the read time would silently make it overdue.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _bucket_for(days: float) -> str:
    """The bucket a row falls in, by days from now. Total over the edges."""
    for name, lower, upper in BUCKETS:
        if lower is not None and days <= lower:
            continue
        if upper is not None and days > upper:
            continue
        return name
    return BUCKETS[-1][0]


def schedule(cur, *, owner_user_id: int, actor_user_id: int,
             now: datetime | None = None) -> dict:
    """The member's own dated, quantified obligations, bucketed by when.

    Owner-only. The gate is the underlying view's, not a second one: this
    module asks :func:`obligation_projection.liabilities_view` for the data and
    relays its refusal verbatim, so there is exactly one place where the owner
    check for liabilities lives.

    ``now`` is injectable so a test can place a due date in a known bucket
    without sleeping. It is not a filter and not a caller-supplied "as of" —
    the routes never pass it.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id or 0)
    if owner <= 0 or actor != owner:
        # Delegated rather than duplicated: liabilities_view writes the denial
        # audit row. Doing the check here as well would either double-log or,
        # worse, drift from it.
        view = _obligations.liabilities_view(
            cur, owner_user_id=owner, actor_user_id=actor)
        return _denied(str((view.get("denied") or {}).get("reason")
                           or DENIED_NOT_OWNER))

    view = _obligations.liabilities_view(
        cur, owner_user_id=owner, actor_user_id=actor)
    if not view.get("ok"):
        return _denied(str((view.get("denied") or {}).get("reason")
                           or DENIED_NOT_OWNER))

    moment = now or datetime.now(timezone.utc)
    totals = view.get("totals") or {}
    # The currency decision is the liabilities view's, reused. A schedule that
    # decided currency differently from the totals it sits beside would let the
    # same store report two different answers on one screen.
    currency = str(totals.get("currency") or "")
    summable = bool(currency)

    buckets: dict[str, dict] = {
        name: {"amount": 0.0 if summable else None, "count": 0}
        for name, _lower, _upper in BUCKETS
    }
    rows: list[dict] = []
    #: Rows whose currency disagrees with the one the totals name. The
    #: liabilities view's contract makes this impossible today; it is tracked
    #: so that a future change to that contract shows up as a broken invariant
    #: rather than as a quietly wrong total.
    mixed_currency_rows = 0
    excluded = {
        "undated": 0,
        "unquantified": 0,
        "undated_and_unquantified": 0,
    }
    # There is deliberately no "other_currency" counter here. It would never be
    # anything but zero: ``liabilities_view`` publishes a currency only when
    # exactly one real currency is present and nothing is unspecified, so when
    # ``summable`` is true every quantified row already carries that currency,
    # and when it is false no bucket is summed at all. An always-zero key that
    # looks like an FX guard is worse than no key, because it suggests a
    # conversion decision is being made somewhere. The invariant is asserted
    # instead — see ``mixed_currency_rows`` below.

    for liability in view.get("liabilities") or []:
        amount = liability.get("amount")
        due = _parse_due(liability.get("due_at"))
        quantified = amount is not None

        if not quantified and due is None:
            excluded["undated_and_unquantified"] += 1
            continue
        if due is None:
            # Real money, unknown when. Counted, never bucketed.
            excluded["undated"] += 1
            continue
        if not quantified:
            # Known when, unknown how much. Counted, never bucketed as zero —
            # a zero here would make the schedule look settled for that date.
            excluded["unquantified"] += 1
            continue

        row_currency = str(liability.get("currency") or "")
        days = (due - moment).total_seconds() / 86400.0
        name = _bucket_for(days)
        bucket = buckets[name]
        bucket["count"] += 1
        if summable:
            if row_currency != currency:
                # Unreachable while liabilities_view keeps its contract, and
                # counted rather than silently added if that ever changes. A
                # row in a currency the total does not name must never be
                # folded into it: that is FX by accident.
                mixed_currency_rows += 1
            else:
                bucket["amount"] += float(amount)

        rows.append({
            "node_id": liability.get("node_id"),
            "root_id": liability.get("root_id"),
            "title": liability.get("title"),
            "kind": liability.get("kind"),
            "amount": amount,
            "currency": row_currency,
            "due_at": liability.get("due_at"),
            "days_until": days,
            "overdue": days <= 0,
            "bucket": name,
            "evidence": liability.get("evidence"),
        })

    rows.sort(key=lambda row: row["days_until"])
    truncated = bool(totals.get("truncated")) or len(rows) > MAX_SCHEDULE_ROWS
    rows = rows[:MAX_SCHEDULE_ROWS]

    scheduled_total = (
        sum(bucket["amount"] for bucket in buckets.values())
        if summable else None
    )
    left_out = sum(excluded.values())
    complete = (bool(summable) and not truncated and left_out == 0
                and mixed_currency_rows == 0)

    return {
        "ok": True,
        "denied": {},
        "generated_at": moment.isoformat(),
        "schedule": rows,
        "buckets": buckets,
        "totals": {
            "currency": currency,
            # None, not 0.0, when there is no single currency to sum in. A
            # zero would read as "nothing is owed".
            "scheduled_amount": scheduled_total,
            "scheduled_count": sum(b["count"] for b in buckets.values()),
            "obligations_seen": len(view.get("liabilities") or []),
            "truncated": truncated,
            # False whenever anything at all was left out. A client that draws
            # the chart without reading this is the bug the mutation tests are
            # for.
            "complete": complete,
            "excluded_count": left_out,
            # Must always be 0. Published rather than asserted internally so a
            # break in the liabilities view's single-currency contract is
            # visible to callers and to the test suite, not only to this file.
            "mixed_currency_rows": mixed_currency_rows,
        },
        "excluded": excluded,
        "basis": {
            "inflows": INFLOWS_BASIS,
            "recurrence": RECURRENCE_BASIS,
            "buckets": [
                {"name": name, "from_days": lower, "to_days": upper}
                for name, lower, upper in BUCKETS
            ],
        },
        "sync": view.get("sync") or {},
    }


__all__ = [
    "BUCKETS", "DENIED_NOT_OWNER", "FEATURE_ID", "INFLOWS_BASIS",
    "MAX_SCHEDULE_ROWS", "RECURRENCE_BASIS", "schedule",
]
