"""An arrival window, or an honest refusal to state one.

What this module is
-------------------
The single place a delivery date is computed. Every surface that shows a buyer
when something arrives — product card, product page, cart, checkout, the order
confirmation, the receipt email — reads one of these estimates. None of them
performs day arithmetic of its own, because the failure mode of per-surface
arithmetic is a cart that promises Tuesday and a checkout that promises Friday
for the same basket, and nothing in a test suite notices two callers disagreeing.

The estimate is a range of calendar dates plus the components it was built from.
It is never a guarantee: ``guaranteed`` is hard ``False`` here for the same
reason it is hard ``False`` in the CJ adapter, and there is no argument that
turns it on.

Why the components are additive
-------------------------------
An arrival date is ``dispatch + handling + transit + buffer``, and the repo's own
CJ research is what establishes that handling and transit do not overlap. From
``reports/cj-discovery/CJ_DROPSHIPPING_FORENSIC_REPORT.md``: the freight
endpoint's aging is a *transit* estimate, "not guaranteed delivery dates", and
"catalog dispatch/deliveryCycle fields are not a universal processing SLA".
So the provider's number starts at carrier handover, and adding handling in front
of it double-counts nothing.

The refusal
-----------
That same research states the standing policy twice — "preserve unknown
processing time", and "show an estimate only when required components exist".
This module implements it literally:

**A missing component yields ``UNAVAILABLE``, never a substituted one.** There is
no default handling time and no default buffer. ``handling or 0`` is the whole
bug: it is invisible, it always moves the date *earlier*, and earlier is the
direction that produces a buyer who was promised Thursday and complains on
Friday. A caller holding ``UNAVAILABLE`` must say it does not know.

The distinction that makes a default acceptable elsewhere is the one
``suppliers.pricing`` draws for money: a default *margin* is a policy the
platform may choose, while a default *freight cost* is a claim about what a
supplier charges. Applied to time: a handling **allowance** PulseSoc commits to
quoting is a policy choice, but it has to be *declared* by the policy layer and
passed in. What is forbidden is this module inventing one when the caller
supplied nothing, because at that point nobody has decided anything and the
number is simply made up.

Every rounding choice goes later
--------------------------------
Delivery estimation has an asymmetric cost function. A window that closes too
early becomes a support ticket, a refund request and a buyer who does not return;
a window that closes too late costs a little conversion. So wherever this module
must choose, it chooses later: fractional days ceiling, handling is counted in
business days, an unstated day basis is read as business days, and the buffer
extends only the far end of the window.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

#: Terminal states of an estimate. There is no partial success: a caller either
#: has a window it may render or a reason it may not.
STATE_ESTIMATED = "ESTIMATED"
STATE_UNAVAILABLE = "UNAVAILABLE"
STATE_UNSUPPORTED_ROUTE = "UNSUPPORTED_ROUTE"
STATES = (STATE_ESTIMATED, STATE_UNAVAILABLE, STATE_UNSUPPORTED_ROUTE)

#: How much the window is worth trusting, strongest first. This is provenance,
#: not a probability — a percentage here would be a fabricated precision of its
#: own, since nothing in the system has yet measured promised against actual.
CONFIDENCE_PROVIDER_QUOTED = "PROVIDER_QUOTED"
CONFIDENCE_PROVIDER_CACHED = "PROVIDER_CACHED"
CONFIDENCE_NONE = "NONE"
CONFIDENCES = (CONFIDENCE_PROVIDER_QUOTED, CONFIDENCE_PROVIDER_CACHED, CONFIDENCE_NONE)

#: Why an estimate could not be produced. These are machine-readable so that a
#: surface can distinguish "we could not reach the supplier" (retry, show the
#: last known window) from "the supplier does not ship there" (block checkout).
REASON_NO_TRANSIT = "supplier_transit_unknown"
REASON_NO_HANDLING = "handling_time_undeclared"
REASON_NO_BUFFER = "buffer_policy_undeclared"
REASON_UNSUPPORTED = "route_unsupported"

#: Counting bases, re-exported from the provider boundary so that consumers of
#: this module never import the supplier package to name a day.
BASIS_UNSPECIFIED = "UNSPECIFIED"
BASIS_BUSINESS = "BUSINESS"
BASIS_CALENDAR = "CALENDAR"

#: Refuse to project further out than this. A window a year wide is not an
#: estimate a buyer can act on, and a range that lands here is far more likely to
#: be a configuration error than a real shipping corridor.
MAX_WINDOW_DAYS = 365


class EstimateRejected(ValueError):
    """A caller passed something structurally unusable.

    Raised only for programming errors — a naive datetime, a negative day count,
    a basis this module does not know. A *missing* component is not an error: it
    is the ordinary ``UNAVAILABLE`` outcome, and conflating the two would turn
    one unconfigured warehouse into a 500 on a product page.
    """


def unavailable(reason: str, *, detail: str | None = None) -> dict:
    """The one shape a surface may render as "we cannot say"."""
    return {
        "state": STATE_UNAVAILABLE if reason != REASON_UNSUPPORTED else STATE_UNSUPPORTED_ROUTE,
        "reason": reason, "detail": detail, "earliest": None, "latest": None,
        "confidence": CONFIDENCE_NONE, "guaranteed": False, "components": None,
    }


def arrival_window(
    *,
    transit,
    handling,
    buffer_days,
    now: datetime,
    dispatch_cutoff_hour: int | None = None,
    unspecified_basis: str = BASIS_BUSINESS,
    holidays=(),
    confidence: str = CONFIDENCE_PROVIDER_QUOTED,
) -> dict:
    """Compute the window a buyer may be shown, or a reason there is none.

    ``transit`` is a normalized range from the provider boundary —
    ``{"min_days", "max_days", "basis"}`` — or ``None`` when the supplier said
    nothing a range could be made from. ``handling`` is the same shape, supplied
    by the policy layer; ``None`` means nobody has declared one and is the
    ordinary reason an estimate does not exist yet.

    ``now`` must be timezone-aware, and its zone is the *origin's* — the
    warehouse decides what "today" is for dispatch purposes, because it is the
    warehouse that has to pick the item. ``dispatch_cutoff_hour`` models the
    daily handover: an order placed after it starts handling the next day. Left
    ``None``, no cutoff is modelled and handling starts today.

    The returned dates are plain calendar dates carrying no time of day. A
    whole-day range has no defensible hour, and attaching one ("arrives 09:00")
    would be exactly the fabricated precision this domain must not invent.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise EstimateRejected("now must be timezone-aware")
    if unspecified_basis not in (BASIS_BUSINESS, BASIS_CALENDAR):
        raise EstimateRejected(f"unspecified_basis must resolve to a real basis, got {unspecified_basis!r}")
    if confidence not in CONFIDENCES:
        raise EstimateRejected(f"unknown confidence {confidence!r}")
    if dispatch_cutoff_hour is not None and not (
            isinstance(dispatch_cutoff_hour, int) and not isinstance(dispatch_cutoff_hour, bool)
            and 0 <= dispatch_cutoff_hour <= 23):
        raise EstimateRejected("dispatch_cutoff_hour must be an hour of the day")

    transit_range = _range(transit, "transit")
    if transit_range is None:
        return unavailable(REASON_NO_TRANSIT)
    handling_range = _range(handling, "handling")
    if handling_range is None:
        return unavailable(REASON_NO_HANDLING)
    # A buffer of zero is a decision; a buffer of None is the absence of one.
    # Only the first may proceed.
    if buffer_days is None:
        return unavailable(REASON_NO_BUFFER)
    if isinstance(buffer_days, bool) or not isinstance(buffer_days, int) or buffer_days < 0:
        raise EstimateRejected("buffer_days must be a non-negative whole number of days")

    closed = _holiday_set(holidays)
    start = now.date()
    if dispatch_cutoff_hour is not None and now.hour >= dispatch_cutoff_hour:
        start = start + timedelta(days=1)

    transit_basis = transit_range["basis"]
    effective_basis = unspecified_basis if transit_basis == BASIS_UNSPECIFIED else transit_basis

    # Handling is counted in business days regardless of how the carrier counts
    # transit. A warehouse does not pick on Sunday, and of the two possible
    # readings this is the one that lands later.
    earliest_dispatch = _advance(start, handling_range["min_days"], BASIS_BUSINESS, closed)
    latest_dispatch = _advance(start, handling_range["max_days"], BASIS_BUSINESS, closed)

    earliest = _advance(earliest_dispatch, transit_range["min_days"], effective_basis, closed)
    latest = _advance(latest_dispatch, transit_range["max_days"], effective_basis, closed)

    # The buffer extends the far end only. The near end is already the optimistic
    # bound; padding it too would narrow the window against the evidence, while
    # the risk actually being managed is arriving after the date we committed to.
    latest = latest + timedelta(days=buffer_days)

    if latest < earliest:  # defensive: a reversed handling range cannot produce this
        earliest, latest = latest, earliest
    if (latest - start).days > MAX_WINDOW_DAYS:
        return unavailable(REASON_NO_TRANSIT, detail="projected window exceeds the plausible horizon")

    return {
        "state": STATE_ESTIMATED, "reason": None, "detail": None,
        "earliest": earliest.isoformat(), "latest": latest.isoformat(),
        "confidence": confidence, "guaranteed": False,
        "components": {
            "dispatch_from": start.isoformat(),
            "dispatch_earliest": earliest_dispatch.isoformat(),
            "dispatch_latest": latest_dispatch.isoformat(),
            "handling_days": dict(handling_range), "transit_days": dict(transit_range),
            "buffer_days": buffer_days,
            # What the transit integers were actually counted as, and whether
            # that was the provider's statement or this module's conservative
            # reading of its silence. An accuracy review that cannot tell those
            # apart cannot attribute its own error.
            "counted_as": effective_basis,
            "basis_was_stated": transit_basis != BASIS_UNSPECIFIED,
            "holidays_modelled": len(closed),
        },
    }


def _range(value, what: str) -> dict | None:
    """Validate a day range from the provider boundary or the policy layer."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EstimateRejected(f"{what} must be a normalized range or None")
    try:
        low, high = value["min_days"], value["max_days"]
    except (KeyError, TypeError):
        raise EstimateRejected(f"{what} is missing min_days/max_days")
    for bound in (low, high):
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
            raise EstimateRejected(f"{what} bounds must be non-negative whole days")
    basis = value.get("basis", BASIS_UNSPECIFIED)
    if basis not in (BASIS_UNSPECIFIED, BASIS_BUSINESS, BASIS_CALENDAR):
        raise EstimateRejected(f"{what} has unknown basis {basis!r}")
    if low > high:
        low, high = high, low
    return {"min_days": low, "max_days": high, "basis": basis}


def _holiday_set(holidays) -> frozenset:
    """Dates the origin is closed.

    An empty calendar is accepted and is the default, but it is not the same
    claim as "there are no holidays" — it means closures are not modelled, and
    the count travels in ``components`` so an accuracy review can see which
    estimates were made blind. This matters more than it looks: CJ ships from
    China, and Chinese New Year moves real delivery dates by weeks.
    """
    if not holidays:
        return frozenset()
    parsed = set()
    for entry in holidays:
        if isinstance(entry, datetime):
            parsed.add(entry.date())
        elif isinstance(entry, date):
            parsed.add(entry)
        elif isinstance(entry, str):
            try:
                parsed.add(date.fromisoformat(entry.strip()))
            except ValueError:
                raise EstimateRejected(f"holiday {entry!r} is not an ISO date")
        else:
            raise EstimateRejected(f"holiday {entry!r} is not a date")
    return frozenset(parsed)


def _advance(start: date, days: int, basis: str, closed: frozenset) -> date:
    """Move ``days`` forward from ``start`` under the given counting basis.

    Zero days returns ``start`` unchanged even when ``start`` is a closed day.
    That is correct: zero handling means the item ships the same day it is
    ordered, and moving the date because the calendar says Sunday would invent a
    delay the caller did not describe.
    """
    if days == 0:
        return start
    if basis == BASIS_CALENDAR:
        return start + timedelta(days=days)
    moved, remaining = start, days
    while remaining > 0:
        moved += timedelta(days=1)
        if moved.weekday() < 5 and moved not in closed:
            remaining -= 1
    return moved
