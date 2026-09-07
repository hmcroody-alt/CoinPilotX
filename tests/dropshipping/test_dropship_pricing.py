"""Unknown cost produces unknown price and unknown margin — not zero.

What this file is defending
---------------------------
``pricing`` turns a supplier cost into a retail price and a margin. Two things
about it are easy to "simplify" and expensive to get wrong:

* ``cost or 0`` in ``apply_rule`` — a product whose cost could not be read then
  gets priced from zero, and every one of them is proposed at the markup value
  itself.
* ``(retail - cost) / retail`` without the ``cost is None`` guard — a product
  with unknown cost then reports a **100% margin**, which is the single most
  attractive number in the merchant's list and is entirely fabricated.

Both look like tidying up. Neither raises. A merchant acts on the second one.

Margin is also a *state*, not a number, above the arithmetic. A caller handed
``0.59`` has to decide what counts as healthy; a caller handed ``UNKNOWN``
cannot accidentally decide that ``None`` is a low number and sort it next to the
healthy rows.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.business_os.suppliers import pricing as p


# ---------------------------------------------------------------------------
# Rule validation
# ---------------------------------------------------------------------------

def test_no_rule_means_manual_pricing():
    # A merchant who expressed no rule has not consented to the system choosing
    # their prices. The default must be "you type it", not "we guess".
    assert p.normalize_rule(None) == {"type": p.MANUAL_PRICE}
    assert p.normalize_rule({}) == {"type": p.MANUAL_PRICE}


@pytest.mark.parametrize("rule", [
    "COST_PLUS_PERCENT", 5, [], {"type": "SURPRISE_ME"},
    {"type": "COST_PLUS_PERCENT"},                    # no value
    {"type": "COST_PLUS_PERCENT", "value": "50"},     # string, not numeric
    {"type": "COST_PLUS_PERCENT", "value": True},     # bool is not a number
    {"type": "COST_PLUS_PERCENT", "value": float("nan")},
    {"type": "COST_PLUS_PERCENT", "value": float("inf")},
    {"type": "COST_PLUS_PERCENT", "value": -5},
    {"type": "MULTIPLIER", "value": 0},               # zero retail
    {"type": "MULTIPLIER", "value": -2},
    {"type": "MULTIPLIER", "value": 10_000},
    {"type": "TARGET_MARGIN", "value": 100},          # division by zero
    {"type": "TARGET_MARGIN", "value": 101},
    {"type": "TARGET_MARGIN", "value": -1},
    {"type": "COST_PLUS_FIXED", "value": -1},
])
def test_unusable_rule_is_rejected(rule):
    with pytest.raises(p.PricingRejected):
        p.normalize_rule(rule)


def test_target_margin_of_one_hundred_percent_is_rejected_not_clamped():
    # cost/(1-1) has no finite answer. Clamping to 99.99 would invent a price
    # the merchant never asked for and would look deliberate in the listing.
    with pytest.raises(p.PricingRejected):
        p.normalize_rule({"type": p.TARGET_MARGIN, "value": 100})
    assert p.normalize_rule({"type": p.TARGET_MARGIN, "value": 99.9})["value"] == 99.9


def test_rule_type_is_case_insensitive_but_closed():
    assert p.normalize_rule({"type": "multiplier", "value": 2})["type"] == p.MULTIPLIER
    with pytest.raises(p.PricingRejected):
        p.normalize_rule({"type": "multiplier_v2", "value": 2})


# ---------------------------------------------------------------------------
# Unknown cost produces no price
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [
    {"type": p.COST_PLUS_FIXED, "value": 500},
    {"type": p.COST_PLUS_PERCENT, "value": 50},
    {"type": p.MULTIPLIER, "value": 2},
    {"type": p.TARGET_MARGIN, "value": 60},
])
def test_unknown_cost_produces_no_price_under_every_rule(rule):
    result = p.apply_rule(rule, None)
    assert result is None, f"{rule['type']} invented {result!r} from an unknown cost"


def test_unknown_cost_does_not_become_the_markup_itself():
    # The specific failure of `cost or 0`: every unreadable product is proposed
    # at exactly the markup, and they all look plausible.
    assert p.apply_rule({"type": p.COST_PLUS_FIXED, "value": 500}, None) != 500


def test_manual_pricing_proposes_nothing_even_with_a_known_cost():
    assert p.apply_rule({"type": p.MANUAL_PRICE}, 820) is None


def test_negative_or_unparseable_cost_produces_no_price():
    rule = {"type": p.MULTIPLIER, "value": 2}
    assert p.apply_rule(rule, -100) is None
    assert p.apply_rule(rule, "eight twenty") is None
    assert p.apply_rule(rule, None) is None


# ---------------------------------------------------------------------------
# Rule arithmetic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule,cost,expected", [
    ({"type": p.COST_PLUS_FIXED, "value": 500}, 820, 1320),
    ({"type": p.COST_PLUS_PERCENT, "value": 50}, 820, 1230),
    ({"type": p.COST_PLUS_PERCENT, "value": 0}, 820, 820),
    ({"type": p.MULTIPLIER, "value": 2}, 820, 1640),
    ({"type": p.MULTIPLIER, "value": 2.5}, 820, 2050),
    ({"type": p.TARGET_MARGIN, "value": 50}, 820, 1640),
    ({"type": p.TARGET_MARGIN, "value": 0}, 820, 820),
])
def test_rule_arithmetic(rule, cost, expected):
    assert p.apply_rule(rule, cost) == expected


def test_target_margin_actually_achieves_the_margin():
    price = p.apply_rule({"type": p.TARGET_MARGIN, "value": 60}, 820)
    assert p.margin_percent(price, 820) == pytest.approx(60, abs=0.05)


def test_a_free_product_still_prices():
    # cost 0 is a fact, not an absence. It must not take the unknown-cost path.
    assert p.apply_rule({"type": p.COST_PLUS_FIXED, "value": 500}, 0) == 500
    assert p.apply_rule({"type": p.MULTIPLIER, "value": 2}, 0) == 0


def test_absurd_result_is_refused_rather_than_stored():
    assert p.apply_rule({"type": p.MULTIPLIER, "value": 1000}, 999_999_999) is None


# ---------------------------------------------------------------------------
# Margin
# ---------------------------------------------------------------------------

def test_unknown_cost_gives_unknown_margin_not_a_hundred_percent():
    assert p.margin_cents(2000, None) is None
    assert p.margin_percent(2000, None) is None
    assert p.margin_state(2000, None) == p.UNKNOWN


def test_unknown_retail_gives_unknown_margin():
    assert p.margin_percent(None, 820) is None
    assert p.margin_state(None, 820) == p.UNKNOWN


def test_margin_is_a_percentage_of_retail_not_of_cost():
    # 59% of retail. Against cost the same numbers read as 144%, which would
    # overstate every margin the merchant ever sees.
    assert p.margin_percent(2000, 820) == 59.0
    assert p.margin_percent(2000, 820) != round((2000 - 820) * 100 / 820, 2)


def test_selling_below_cost_is_reported_not_clamped():
    assert p.margin_cents(500, 820) == -320
    assert p.margin_percent(500, 820) == -64.0
    assert p.margin_state(500, 820) == p.NEGATIVE_MARGIN


def test_zero_retail_is_unknown_margin_not_a_division_by_zero():
    assert p.margin_percent(0, 820) is None
    assert p.margin_state(0, 820) == p.UNKNOWN


@pytest.mark.parametrize("retail,cost,expected", [
    (1000, 1100, p.NEGATIVE_MARGIN),
    (1000, 1000, p.CRITICAL_MARGIN),   # 0%
    (1000, 950, p.CRITICAL_MARGIN),    # 5%
    (1000, 900, p.LOW_MARGIN),         # 10% — boundary belongs to LOW
    (1000, 800, p.LOW_MARGIN),         # 20%
    (1000, 750, p.HEALTHY),            # 25% — boundary belongs to HEALTHY
    (1000, 300, p.HEALTHY),            # 70%
])
def test_margin_state_boundaries(retail, cost, expected):
    assert p.margin_state(retail, cost) == expected


def test_unknown_margin_never_reads_as_healthy():
    # A merchant scanning a list for problems reads absence of a warning as
    # "fine". A product with unreadable economics must carry its own badge.
    for retail, cost in [(None, None), (2000, None), (None, 820), (0, 820)]:
        assert p.margin_state(retail, cost) == p.UNKNOWN
        assert p.margin_state(retail, cost) != p.HEALTHY


def test_every_margin_state_is_declared():
    seen = {p.margin_state(r, c) for r, c in
            [(2000, None), (500, 820), (1000, 1000), (1000, 800), (1000, 300)]}
    assert seen <= set(p.MARGIN_STATES)
    assert seen == {p.UNKNOWN, p.NEGATIVE_MARGIN, p.CRITICAL_MARGIN,
                    p.LOW_MARGIN, p.HEALTHY}


# ---------------------------------------------------------------------------
# The quote a screen renders
# ---------------------------------------------------------------------------

def test_quote_prefers_a_merchant_price_over_the_proposal():
    # The storefront half of the merchant/supplier field-ownership split: a
    # price the merchant typed always wins over a computed one.
    quote = p.quote({"type": p.MULTIPLIER, "value": 2}, 820, retail_cents=2500)
    assert quote["retail_cents"] == 2500
    assert quote["proposed_retail_cents"] == 1640
    assert quote["margin_cents"] == 1680


def test_quote_falls_back_to_the_proposal_when_unpriced():
    quote = p.quote({"type": p.MULTIPLIER, "value": 2}, 820)
    assert quote["retail_cents"] == 1640
    assert quote["margin_state"] == p.HEALTHY


def test_quote_with_unknown_cost_is_unknown_throughout():
    quote = p.quote({"type": p.MULTIPLIER, "value": 2}, None)
    assert quote["cost_cents"] is None
    assert quote["retail_cents"] is None
    assert quote["proposed_retail_cents"] is None
    assert quote["margin_cents"] is None
    assert quote["margin_percent"] is None
    assert quote["margin_state"] == p.UNKNOWN


def test_quote_with_merchant_price_but_unknown_cost_shows_unknown_margin():
    # The most dangerous shape: a real price on screen next to a margin that
    # cannot be computed. It must read UNKNOWN, not 100%.
    quote = p.quote(None, None, retail_cents=2500)
    assert quote["retail_cents"] == 2500
    assert quote["margin_percent"] is None
    assert quote["margin_state"] == p.UNKNOWN
