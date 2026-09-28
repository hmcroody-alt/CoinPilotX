"""The estimator states a window it can defend, or states nothing.

What this file is defending
---------------------------
A delivery date is a promise. Unlike most wrong numbers in this codebase, a wrong
one here is *actioned by a human*: the buyer waits, then escalates. So the
estimator's refusals are the assertions that matter most, and every one of them
is a single ``or 0`` away from being silently wrong:

* ``handling or 0`` — an undeclared handling time becomes same-day dispatch,
* ``buffer_days or 0`` — an undeclared buffer becomes no buffer,
* an unstated day basis read as calendar days — every window shortens ~40%,
* ``floor`` instead of ``ceil`` — every fractional bound lands a day early.

All four move the date *earlier*, which is the direction that generates refunds,
and none of them raises. This file is where they are noticed.

The tests are pure — no database, no network, no clock. The estimator takes
``now`` as an argument precisely so that its arithmetic is testable rather than
dependent on the day the suite happens to run.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import estimate as e

# A Monday, so that weekday arithmetic in the tests below is legible.
MONDAY = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


def days(low, high, basis=e.BASIS_UNSPECIFIED):
    return {"min_days": low, "max_days": high, "basis": basis}


def window(**overrides):
    kwargs = {"transit": days(7, 20), "handling": days(1, 3), "buffer_days": 0, "now": MONDAY}
    kwargs.update(overrides)
    return e.arrival_window(**kwargs)


# ---------------------------------------------------------------------------
# A missing component is never a substituted one
# ---------------------------------------------------------------------------

def test_unknown_transit_yields_unavailable_not_a_guess():
    result = window(transit=None)
    assert result["state"] == e.STATE_UNAVAILABLE
    assert result["reason"] == e.REASON_NO_TRANSIT
    assert result["earliest"] is None and result["latest"] is None


def test_undeclared_handling_yields_unavailable_not_same_day_dispatch():
    """``handling or 0`` is the bug this test exists for.

    Reading an undeclared handling time as zero produces a complete, plausible,
    confidently-rendered window that is short by however long the warehouse
    actually takes to pick — and nothing downstream can tell it apart from a
    real one.
    """
    result = window(handling=None)
    assert result["state"] == e.STATE_UNAVAILABLE
    assert result["reason"] == e.REASON_NO_HANDLING
    assert result["latest"] is None


def test_undeclared_buffer_is_distinct_from_a_declared_zero_buffer():
    """A buffer of 0 is a decision someone made. ``None`` is the absence of one.

    Collapsing the two is how an unconfigured deployment starts quoting
    unbuffered dates that look exactly like deliberately unbuffered dates.
    """
    assert window(buffer_days=None)["reason"] == e.REASON_NO_BUFFER
    assert window(buffer_days=0)["state"] == e.STATE_ESTIMATED


def test_an_unavailable_estimate_still_carries_no_guarantee():
    for result in (window(transit=None), window(handling=None), window(buffer_days=None)):
        assert result["guaranteed"] is False
        assert result["confidence"] == e.CONFIDENCE_NONE


def test_unsupported_route_is_its_own_state_so_checkout_can_block():
    """"We cannot reach the supplier" and "the supplier does not ship there" need
    different handling: one is a retry, the other must stop a purchase."""
    result = e.unavailable(e.REASON_UNSUPPORTED)
    assert result["state"] == e.STATE_UNSUPPORTED_ROUTE
    assert window(transit=None)["state"] == e.STATE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Components are additive, and handling is not folded into transit
# ---------------------------------------------------------------------------

def test_handling_and_transit_are_added_not_maxed_or_overlapped():
    """CJ's aging starts at carrier handover, so handling sits in front of it.

    The repo's own CJ forensic report is the evidence: the freight endpoint's
    aging is a transit estimate and catalog dispatch fields "are not a universal
    processing SLA". Treating the provider number as inclusive of handling would
    drop the pick time from every estimate.
    """
    result = window(transit=days(10, 10, e.BASIS_CALENDAR),
                    handling=days(2, 2), buffer_days=0)
    # Monday + 2 business days = Wednesday; + 10 calendar days = the 17th.
    assert result["components"]["dispatch_earliest"] == "2026-10-07"
    assert result["earliest"] == "2026-10-17" and result["latest"] == "2026-10-17"


def test_the_buffer_extends_only_the_far_end_of_the_window():
    base = window(buffer_days=0)
    padded = window(buffer_days=3)
    assert padded["earliest"] == base["earliest"]
    assert padded["latest"] > base["latest"]
    assert padded["components"]["buffer_days"] == 3


# ---------------------------------------------------------------------------
# Day counting
# ---------------------------------------------------------------------------

def test_an_unstated_basis_is_counted_as_business_days():
    """The conservative reading of CJ's silence.

    N business days is longer in wall-clock time than N calendar days, so
    assuming calendar here would shorten every single estimate the platform
    makes — against the one supplier whose strings never state a unit.
    """
    result = window(transit=days(10, 10), handling=days(0, 0), buffer_days=0)
    assert result["components"]["counted_as"] == e.BASIS_BUSINESS
    assert result["components"]["basis_was_stated"] is False
    # 10 business days from Monday the 5th lands on Monday the 19th, not the 15th.
    assert result["earliest"] == "2026-10-19"


def test_a_stated_basis_is_honoured_over_the_conservative_default():
    result = window(transit=days(10, 10, e.BASIS_CALENDAR), handling=days(0, 0), buffer_days=0)
    assert result["components"]["counted_as"] == e.BASIS_CALENDAR
    assert result["components"]["basis_was_stated"] is True
    assert result["earliest"] == "2026-10-15"


def test_business_day_counting_skips_weekends():
    # Friday + 1 business day is Monday, not Saturday.
    friday = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    result = window(now=friday, transit=days(1, 1, e.BASIS_BUSINESS),
                    handling=days(0, 0), buffer_days=0)
    assert result["earliest"] == "2026-10-12"


def test_declared_holidays_push_the_window_and_are_counted_in_components():
    """An empty holiday calendar is not a claim that there are no holidays.

    CJ ships from China; Chinese New Year moves real arrival dates by weeks. The
    count travels in ``components`` so an accuracy review can separate estimates
    made with a calendar from estimates made blind.
    """
    blind = window(transit=days(3, 3, e.BASIS_BUSINESS), handling=days(0, 0), buffer_days=0)
    assert blind["components"]["holidays_modelled"] == 0
    aware = window(transit=days(3, 3, e.BASIS_BUSINESS), handling=days(0, 0), buffer_days=0,
                   holidays=["2026-10-07"])
    assert aware["components"]["holidays_modelled"] == 1
    assert aware["earliest"] > blind["earliest"]


def test_zero_days_does_not_move_off_a_weekend():
    """Zero handling means it ships the day it is ordered. Advancing to Monday
    would invent a delay the caller never described."""
    saturday = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    result = window(now=saturday, transit=days(0, 0, e.BASIS_BUSINESS),
                    handling=days(0, 0), buffer_days=0)
    assert result["earliest"] == "2026-10-10"


# ---------------------------------------------------------------------------
# Dispatch cutoff
# ---------------------------------------------------------------------------

def test_an_order_after_the_cutoff_starts_handling_the_next_day():
    before = window(now=MONDAY.replace(hour=9), dispatch_cutoff_hour=14)
    after = window(now=MONDAY.replace(hour=15), dispatch_cutoff_hour=14)
    assert before["components"]["dispatch_from"] == "2026-10-05"
    assert after["components"]["dispatch_from"] == "2026-10-06"
    assert after["latest"] > before["latest"]


def test_without_a_declared_cutoff_no_cutoff_is_modelled():
    late = window(now=MONDAY.replace(hour=23))
    assert late["components"]["dispatch_from"] == "2026-10-05"


# ---------------------------------------------------------------------------
# Structural refusals
# ---------------------------------------------------------------------------

def test_a_naive_now_is_rejected_rather_than_assumed_utc():
    """Guessing the zone of a naive datetime is a silent off-by-one-day on every
    estimate produced near midnight."""
    with pytest.raises(e.EstimateRejected):
        window(now=datetime(2026, 10, 5, 9, 0))


def test_a_non_utc_origin_zone_is_honoured():
    """``now`` is the *warehouse's* clock, because the warehouse decides what
    "today" is for picking. A CN-zone afternoon is a different dispatch day from
    the same instant read in UTC."""
    shanghai = timezone(timedelta(hours=8))
    result = window(now=datetime(2026, 10, 6, 1, 0, tzinfo=shanghai))
    assert result["components"]["dispatch_from"] == "2026-10-06"


@pytest.mark.parametrize("bad", [
    {"transit": {"min_days": -1, "max_days": 5}},
    {"transit": {"min_days": 1.5, "max_days": 5}},
    {"transit": {"min_days": True, "max_days": 5}},
    {"transit": {"max_days": 5}},
    {"transit": "7-20"},
    {"transit": {"min_days": 1, "max_days": 5, "basis": "WEEKS"}},
    {"buffer_days": -1},
    {"buffer_days": 1.5},
    {"dispatch_cutoff_hour": 24},
    {"unspecified_basis": e.BASIS_UNSPECIFIED},
    {"confidence": "PROBABLY"},
])
def test_structurally_unusable_input_raises_rather_than_degrading(bad):
    """A programming error must not quietly become "delivery unavailable".

    An exception is a bug report; an ``UNAVAILABLE`` is a product state. If a
    malformed range degraded to the latter, a broken caller would present as a
    supplier outage and nobody would look at the code.
    """
    with pytest.raises(e.EstimateRejected):
        window(**bad)


def test_a_reversed_range_is_ordered_rather_than_inverting_the_window():
    result = window(transit=days(20, 7), handling=days(3, 1), buffer_days=0)
    assert result["earliest"] <= result["latest"]


def test_an_absurd_projection_is_refused_rather_than_shown():
    result = window(transit=days(1, 300, e.BASIS_BUSINESS), handling=days(0, 0), buffer_days=200)
    assert result["state"] == e.STATE_UNAVAILABLE


# ---------------------------------------------------------------------------
# The promise itself
# ---------------------------------------------------------------------------

def test_an_estimate_is_never_a_guarantee():
    """Mirrors the hard ``guaranteed: False`` at the CJ adapter. No argument to
    this function turns it on, because no supplier in this system offers one."""
    assert window()["guaranteed"] is False


def test_the_window_carries_dates_with_no_fabricated_time_of_day():
    result = window()
    for value in (result["earliest"], result["latest"]):
        assert len(value) == 10 and value.count("-") == 2
        assert "T" not in value and ":" not in value


def test_confidence_is_provenance_and_stays_within_the_vocabulary():
    assert window()["confidence"] == e.CONFIDENCE_PROVIDER_QUOTED
    cached = window(confidence=e.CONFIDENCE_PROVIDER_CACHED)
    assert cached["confidence"] in e.CONFIDENCES


def test_components_expose_every_input_that_moved_the_date():
    """Delivery accuracy cannot be measured later if the estimate does not record
    what it was built from. A bare pair of dates is unauditable."""
    parts = window(buffer_days=2, holidays=["2026-10-07"])["components"]
    assert parts["handling_days"] == {"min_days": 1, "max_days": 3, "basis": e.BASIS_UNSPECIFIED}
    assert parts["transit_days"] == {"min_days": 7, "max_days": 20, "basis": e.BASIS_UNSPECIFIED}
    assert parts["buffer_days"] == 2
    assert parts["counted_as"] == e.BASIS_BUSINESS
    assert parts["dispatch_from"] == "2026-10-05"
    assert parts["holidays_modelled"] == 1


def test_a_state_is_always_a_declared_member_of_the_vocabulary():
    for result in (window(), window(transit=None), e.unavailable(e.REASON_UNSUPPORTED)):
        assert result["state"] in e.STATES
        assert result["confidence"] in e.CONFIDENCES
