"""Route selection is deterministic, and it never invents an eligible route.

What this file is defending
---------------------------
Two failures, both invisible in a passing build:

* **Non-determinism.** If selection depends on the order the provider listed its
  channels, a product page and a checkout page quote different services for the
  same basket. Neither is wrong on its own, so no single-surface test fails.
* **A fallback pick.** A selector that returns its least-bad rejected candidate
  rather than nothing puts an unshippable or unpriced route behind a buy button.

The ranking assertions here deliberately shuffle their inputs, because a selector
that happens to be correct for one input order is not a selector.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import routing as r


def option(option_id, *, total="8.50", low=7, high=20, available=True, basis="UNSPECIFIED"):
    return {
        "option_id": option_id, "channel_id": f"ch-{option_id}", "service": f"svc-{option_id}",
        "provider_total": total, "available": available,
        "transit": None if high is None else {"min_days": low, "max_days": high, "basis": basis},
    }


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_selection_does_not_depend_on_provider_ordering():
    """The property that makes a PDP and a checkout agree."""
    candidates = [option("a", total="9.00", high=10), option("b", total="7.00", high=25),
                  option("c", total="7.00", high=12), option("d", total="12.00", high=5)]
    picks = set()
    for rotation in range(len(candidates)):
        rotated = candidates[rotation:] + candidates[:rotation]
        picks.add(r.select_route(rotated, ceiling_days=30)["selected"]["option_id"])
    assert picks == {"c"}, "selection flipped with input order"


def test_a_price_and_speed_tie_is_broken_stably_by_option_id():
    tied = [option("zulu"), option("alpha"), option("mike")]
    for rotation in range(3):
        rotated = tied[rotation:] + tied[:rotation]
        assert r.select_route(rotated)["selected"]["option_id"] == "alpha"


def test_a_one_cent_difference_decides_the_selection():
    """Freight differences are routinely in the cents, so the comparison has to
    carry them rather than round to the nearest useful-looking number.

    This pins the behaviour, not the numeric type: ``_freight`` uses ``Decimal``
    because money should not be binary floating point, but at two decimal places
    a float would rank these two identically. No test here distinguishes the two
    implementations, and claiming otherwise would be a guard that cannot fail.
    """
    result = r.select_route([option("a", total="8.10"), option("b", total="8.09")])
    assert result["selected"]["option_id"] == "b"


def test_equal_prices_written_differently_tie_rather_than_ordering_by_text():
    """"8.50" and "8.5" are the same price. Comparing the strings would order
    them, and the order would depend on how the provider chose to format."""
    result = r.select_route([option("zulu", total="8.5"), option("alpha", total="8.50")])
    assert result["selected"]["option_id"] == "alpha"


# ---------------------------------------------------------------------------
# No fallback pick
# ---------------------------------------------------------------------------

def test_nothing_eligible_selects_nothing_rather_than_the_least_bad_option():
    result = r.select_route([option("a", available=False), option("b", high=None)])
    assert result["selected"] is None
    assert result["reason"] == r.NO_ELIGIBLE


def test_an_empty_candidate_list_is_distinguished_from_an_all_rejected_one():
    """"The provider offered no routes" and "every route it offered is unusable"
    lead to different investigations."""
    assert r.select_route([])["reason"] == r.NO_CANDIDATES
    assert r.select_route(None)["reason"] == r.NO_CANDIDATES
    assert r.select_route([option("a", available=False)])["reason"] == r.NO_ELIGIBLE


def test_an_unknown_freight_cost_is_not_treated_as_the_cheapest_route():
    """``None`` sorted as zero makes the least-understood option always win.

    ``estimate_shipping`` already has a test asserting a missing total is not free
    shipping. The same claim has to hold when the number ranks rather than renders.
    """
    result = r.select_route([option("priced", total="9.00"), option("unpriced", total=None)])
    assert result["selected"]["option_id"] == "priced"
    assert {"option_id": "unpriced", "reason": r.EXCLUDED_NO_COST} in result["excluded"]


@pytest.mark.parametrize("total", [None, "", "  ", "abc", "-1.00", "NaN", "Infinity", True])
def test_an_unusable_freight_value_excludes_the_route_from_a_cost_policy(total):
    result = r.select_route([option("x", total=total)])
    assert result["selected"] is None
    assert result["excluded"][0]["reason"] == r.EXCLUDED_NO_COST


def test_an_option_without_transit_cannot_carry_a_delivery_promise():
    result = r.select_route([option("a", high=None)])
    assert result["selected"] is None
    assert result["excluded"][0]["reason"] == r.EXCLUDED_NO_TRANSIT


def test_a_provider_unavailable_route_is_excluded_before_it_is_ranked():
    result = r.select_route([option("cheap", total="1.00", available=False),
                             option("ok", total="9.00")])
    assert result["selected"]["option_id"] == "ok"
    assert {"option_id": "cheap", "reason": r.EXCLUDED_UNAVAILABLE} in result["excluded"]


@pytest.mark.parametrize("bad", [
    "not a dict", 42, None,
    # Every dict below is otherwise perfectly bookable — available, priced, with a
    # readable transit range — so the identifier is the only thing wrong with it.
    # An option missing two things can be excluded for the other one, and then the
    # assertion no longer sees the identifier check at all.
    {"available": True, "provider_total": "8.50", "transit": {"min_days": 1, "max_days": 5}},
    {"option_id": "", "available": True, "provider_total": "8.50",
     "transit": {"min_days": 1, "max_days": 5}},
    {"option_id": "   ", "available": True, "provider_total": "8.50",
     "transit": {"min_days": 1, "max_days": 5}},
    {"option_id": 7, "available": True, "provider_total": "8.50",
     "transit": {"min_days": 1, "max_days": 5}},
])
def test_an_unbookable_option_is_malformed_not_merely_unranked(bad):
    """A route with no usable identifier cannot be booked by fulfillment later, so
    it must never be selectable now — whatever else about it looks fine."""
    result = r.select_route([bad])
    assert result["selected"] is None
    assert result["excluded"][0]["reason"] == r.EXCLUDED_MALFORMED


# ---------------------------------------------------------------------------
# The ceiling, and the policies
# ---------------------------------------------------------------------------

def test_the_ceiling_stops_a_trivial_saving_buying_a_much_slower_delivery():
    result = r.select_route([option("slow_cheap", total="6.00", high=40),
                             option("ok", total="8.00", high=18)], ceiling_days=21)
    assert result["selected"]["option_id"] == "ok"
    assert {"option_id": "slow_cheap", "reason": r.EXCLUDED_TOO_SLOW} in result["excluded"]


def test_no_declared_ceiling_really_means_cheapest_and_says_so():
    """A missing ceiling is a legitimate choice, but it has to be visible in the
    result rather than inferred from the absence of an exclusion."""
    result = r.select_route([option("slow_cheap", total="6.00", high=40),
                             option("ok", total="8.00", high=18)])
    assert result["selected"]["option_id"] == "slow_cheap"
    assert result["ceiling_days"] is None


def test_fastest_policy_ranks_on_speed_and_still_breaks_ties_by_cost():
    candidates = [option("a", total="5.00", high=12), option("b", total="20.00", high=6),
                  option("c", total="9.00", high=6)]
    result = r.select_route(candidates, policy=r.POLICY_FASTEST)
    assert result["selected"]["option_id"] == "c"


def test_fastest_policy_may_select_a_route_whose_cost_is_unknown():
    """Speed ranking does not need a price, so excluding an unpriced route here
    would discard a usable option for a reason that does not apply."""
    result = r.select_route([option("x", total=None, high=5)], policy=r.POLICY_FASTEST)
    assert result["selected"]["option_id"] == "x"


def test_an_unknown_cost_loses_the_speed_tie_rather_than_winning_it():
    """``FASTEST`` admits an unpriced route, which is what makes its cost
    tie-break the one place ``None``-as-zero can still decide a selection.

    Two equally fast routes are separated on price. If the unpriced one sorts as
    free it wins every such tie, and the platform pays whatever that route turns
    out to cost — the failure the cost policy excludes it to avoid, reached by a
    different door.
    """
    result = r.select_route([option("unpriced", total=None, high=6),
                             option("priced", total="30.00", high=6)],
                            policy=r.POLICY_FASTEST)
    assert result["selected"]["option_id"] == "priced"


@pytest.mark.parametrize("kwargs", [
    {"policy": "CHEAPEST"}, {"policy": None},
    {"ceiling_days": 0}, {"ceiling_days": -1}, {"ceiling_days": 1.5}, {"ceiling_days": True},
])
def test_a_malformed_policy_request_raises_rather_than_selecting_something(kwargs):
    with pytest.raises(r.RoutingRejected):
        r.select_route([option("a")], **kwargs)


# ---------------------------------------------------------------------------
# The result is auditable
# ---------------------------------------------------------------------------

def test_the_result_names_the_route_fulfillment_must_book():
    """§84's failure is quoting one service and shipping another. The estimate can
    only be tied to the booking if the selection names the identifiers."""
    selected = r.select_route([option("a")])["selected"]
    assert selected["option_id"] == "a" and selected["channel_id"] == "ch-a"


def test_every_candidate_is_accounted_for_as_eligible_or_excluded():
    candidates = [option("a"), option("b", available=False), option("c", high=None),
                  option("d", total=None), option("e", high=40)]
    result = r.select_route(candidates, ceiling_days=21)
    assert len(result["eligible"]) + len(result["excluded"]) == len(candidates)
