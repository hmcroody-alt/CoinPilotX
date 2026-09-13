"""§23/§24 — what a live listing does when the supplier changes the deal.

These test the pure planners, which is where the rules that matter live:

* an unreadable read is never written as a value;
* a merchant's price is never overwritten;
* a stock-out stops checkout, and a stock *outage* does not.

The last one is the reason this file exists. ``lifecycle.inventory_available``
returns ``False`` for a NULL quantity, so there are two different ways to
accidentally delist a merchant's whole catalogue during a provider outage — write
0, or write NULL — and a planner that returned either would look correct in a
happy-path test.
"""

import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))

import pytest

from services.business_os.suppliers import pricing, revisions
from services.marketplace_supplier_schema import (
    STOCK_IN_STOCK,
    STOCK_OUT_OF_STOCK,
    STOCK_UNKNOWN,
    SYNC_STALE,
    SYNC_SYNCED,
)

TARGET_45 = {"type": pricing.TARGET_MARGIN, "value": 45.0}
MANUAL = {"type": pricing.MANUAL_PRICE}


#: The spelling every real writer records when a merchant takes the price.
#: ``sync_updates_allowed`` is an exact string match against ``overridden_fields``,
#: and both production paths store "price_label" -- ``drafts.edit_draft`` appends
#: it when handed ``price_cents``, and the seller reprice route passes it
#: literally. These tests previously used "price_cents", the name of the *column*,
#: which no writer ever records: the planner asked the same wrong question, so the
#: suite agreed with the bug and every merchant-set price was silently overwritten
#: by the next supplier cost change. Named once here so the two cannot drift apart
#: again.
MERCHANT_OWNS_PRICE = "price_label"


def source(cost=1000, overridden=()):
    return {"supplier_cost_cents": cost, "overridden_fields": list(overridden)}


def variant(price=2000):
    return {"price_cents": price}


# ---------------------------------------------------------------- §23 cost


def test_cost_rise_reprices_to_hold_the_merchants_rule():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=1818),
        rule=TARGET_45, observed_cost_cents=1500)
    assert plan["action"] == revisions.REPRICED
    assert plan["cost_cents"] == 1500
    # 1500 / (1 - 0.45) = 2727.27 -> 2727
    assert plan["price_cents"] == 2727
    assert plan["margin_state"] == pricing.HEALTHY


def test_cost_fall_reprices_down_too():
    """A rule is a rule in both directions.

    Only repricing upward would quietly turn a margin *target* into a floor, and
    the merchant would never see the cheaper price they were entitled to pass on.
    """
    plan = revisions.plan_cost_revision(
        source=source(cost=1500), variant=variant(price=2727),
        rule=TARGET_45, observed_cost_cents=1000)
    assert plan["action"] == revisions.REPRICED
    assert plan["price_cents"] == 1818


def test_unreadable_cost_writes_nothing_at_all():
    """The stored cost stands. This is the routine case, not the exotic one."""
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=2000),
        rule=TARGET_45, observed_cost_cents=None)
    assert plan["action"] == revisions.COST_UNREADABLE
    assert plan["cost_cents"] is None, "an absent cost must not overwrite a known one"
    assert plan["price_cents"] is None, "and must not move the price either"


def test_a_negative_cost_is_not_a_cost():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=2000),
        rule=TARGET_45, observed_cost_cents=-500)
    assert plan["action"] == revisions.COST_UNREADABLE
    assert plan["cost_cents"] is None


def test_an_unchanged_cost_is_not_a_revision():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=2000),
        rule=TARGET_45, observed_cost_cents=1000)
    assert plan["action"] == revisions.UNCHANGED
    assert plan["cost_cents"] is None
    assert plan["price_cents"] is None


def test_a_merchant_owned_price_is_never_repriced():
    """§44, and the reason `overridden_fields` exists.

    The cost is still recorded — the merchant needs to know what they are paying
    — but the number they typed is theirs.
    """
    plan = revisions.plan_cost_revision(
        source=source(cost=1000, overridden=[MERCHANT_OWNS_PRICE]),
        variant=variant(price=2000), rule=TARGET_45, observed_cost_cents=1500)
    assert plan["action"] == revisions.COST_RECORDED
    assert plan["cost_cents"] == 1500
    assert plan["price_cents"] is None, "the merchant typed this price"


def test_a_manual_pricing_store_gets_no_automatic_reprice():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=2000),
        rule=MANUAL, observed_cost_cents=1500)
    assert plan["action"] == revisions.COST_RECORDED
    assert plan["price_cents"] is None


def test_a_merchant_price_now_below_cost_is_reported_not_corrected():
    """The one case the merchant must not be left to discover from their bank.

    Their price stands, because it is theirs, so the only useful action is to
    say plainly that it is now below cost.
    """
    plan = revisions.plan_cost_revision(
        source=source(cost=1000, overridden=[MERCHANT_OWNS_PRICE]),
        variant=variant(price=1200), rule=TARGET_45, observed_cost_cents=1500)
    assert plan["price_cents"] is None
    assert plan["margin_state"] == pricing.NEGATIVE_MARGIN
    assert plan["attention"] == revisions.SELLING_BELOW_COST


def test_a_thinned_merchant_margin_is_flagged_before_it_goes_negative():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000, overridden=[MERCHANT_OWNS_PRICE]),
        variant=variant(price=2000), rule=TARGET_45, observed_cost_cents=1900)
    assert plan["margin_state"] == pricing.CRITICAL_MARGIN
    assert plan["attention"] == revisions.MARGIN_LOST


def test_a_healthy_reprice_raises_no_alarm():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=1818),
        rule=TARGET_45, observed_cost_cents=1100)
    assert plan["attention"] is None


def test_a_rule_that_would_price_at_zero_leaves_the_live_price_alone():
    """§8/§11 — never write free onto a listing a buyer can reach.

    A zero supplier cost under a margin rule computes to zero, which is a
    mathematically correct answer to the wrong question.
    """
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=2000),
        rule=TARGET_45, observed_cost_cents=0)
    assert plan["price_cents"] is None, "a live listing must never be repriced to free"
    assert plan["cost_cents"] == 0
    assert plan["attention"] == revisions.REPRICE_IMPOSSIBLE


def test_a_boolean_quantity_is_not_a_cost():
    plan = revisions.plan_cost_revision(
        source=source(cost=1000), variant=variant(price=2000),
        rule=TARGET_45, observed_cost_cents=True)
    assert plan["action"] == revisions.COST_UNREADABLE


def test_margin_is_unknown_not_healthy_when_there_is_no_cost_to_compare():
    plan = revisions.plan_cost_revision(
        source=source(cost=None), variant=variant(price=2000),
        rule=TARGET_45, observed_cost_cents=None)
    assert plan["margin_state"] == pricing.UNKNOWN
    assert plan["attention"] == revisions.COST_UNAVAILABLE


# ------------------------------------------------------------- §24 stock


def test_a_stock_out_zeroes_the_units_that_checkout_compares_against():
    plan = revisions.plan_stock_revision(
        observed_state=STOCK_OUT_OF_STOCK, observed_quantity=0)
    assert plan["stock_state"] == STOCK_OUT_OF_STOCK
    assert plan["units"] == 0, "checkout must stop taking orders nobody can fill"
    assert plan["attention"] == revisions.SUPPLIER_OUT_OF_STOCK


def test_an_unreadable_stock_read_does_not_touch_the_units():
    """The defect this module was written to avoid.

    `units: None` means "leave the column alone". Both 0 and NULL would take the
    listing off sale, and `inventory_available` would then report a provider
    outage to the merchant as "out of stock".
    """
    plan = revisions.plan_stock_revision(
        observed_state=STOCK_UNKNOWN, observed_quantity=None)
    assert plan["units"] is None, "an outage must not delist the catalogue"
    assert plan["stock_state"] == STOCK_UNKNOWN
    assert plan["sync_state"] == SYNC_STALE
    assert plan["attention"] == revisions.STOCK_UNREADABLE


def test_an_unreadable_read_and_an_uncounted_restock_are_told_apart():
    """Both carry `stock_quantity: None`, and they mean opposite things.

    In stock without a number is the supplier withdrawing a count they used to
    give, so the stored one has to go — reporting a number nobody claims is the
    same lie as reporting a stale price. An unreadable read asserts nothing, so
    the stored count stays. An applier that looked only at the value would erase
    a real count on every provider timeout, which is why the flag is explicit
    rather than inferred.
    """
    outage = revisions.plan_stock_revision(
        observed_state=STOCK_UNKNOWN, observed_quantity=None)
    uncounted = revisions.plan_stock_revision(
        observed_state=STOCK_IN_STOCK, observed_quantity=None)

    assert outage["stock_quantity"] is None and uncounted["stock_quantity"] is None
    assert outage["write_quantity"] is False, "a failed read keeps the last known count"
    assert uncounted["write_quantity"] is True, "a withdrawn count must not be kept"


def test_a_counted_read_always_writes_the_count():
    for state, quantity in ((STOCK_IN_STOCK, 132), (STOCK_OUT_OF_STOCK, 0)):
        plan = revisions.plan_stock_revision(
            observed_state=state, observed_quantity=quantity)
        assert plan["write_quantity"] is True, state


def test_a_stock_state_we_have_never_heard_of_is_unknown_not_available():
    """A future fourth state must not read as purchasable to code predating it."""
    plan = revisions.plan_stock_revision(
        observed_state="BACKORDERED", observed_quantity=50)
    assert plan["stock_state"] == STOCK_UNKNOWN
    assert plan["units"] is None
    # The 50 is real but we do not know what it counts, so it is not written
    # either: an unrecognised state makes its quantity unreadable too.
    assert plan["write_quantity"] is False


def test_low_stock_is_a_readable_in_stock_state_not_a_failed_read():
    """``normalize`` says LOW_STOCK for anything under five units.

    This function is handed ``normalize.stock_state``'s vocabulary, which is wider
    than the three states storage keeps: 1-4 units come back as ``LOW_STOCK``, not
    ``IN_STOCK``. Matching on ``IN_STOCK`` alone sent those to the unreadable
    branch, so a supplier honestly reporting its last three units was treated as a
    provider outage — the count never moved off the stale one, and the source sat
    ``STALE`` for as long as the product remained nearly sold out.

    That is the exact inverse of the right answer. Low stock is the most urgent
    *readable* state there is: it is where the ledger most needs to come down to
    the true number before the last few units are oversold.
    """
    plan = revisions.plan_stock_revision(
        observed_state="LOW_STOCK", observed_quantity=3)

    assert plan["stock_state"] == STOCK_IN_STOCK, "LOW_STOCK collapses upward"
    assert plan["write_quantity"] is True
    assert plan["stock_quantity"] == 3
    assert plan["units"] == 3, "three units are on offer, not zero and not one"
    assert plan["sync_state"] == SYNC_SYNCED, "a read that worked is not stale"
    assert plan["attention"] is None


def test_an_empty_stock_read_is_unknown_rather_than_zero():
    plan = revisions.plan_stock_revision(observed_state="", observed_quantity=None)
    assert plan["stock_state"] == STOCK_UNKNOWN
    assert plan["units"] is None


def test_a_counted_restock_offers_every_unit_the_supplier_holds():
    plan = revisions.plan_stock_revision(
        observed_state=STOCK_IN_STOCK, observed_quantity=132)
    assert plan["units"] == 132, "not a count of variants"
    assert plan["stock_state"] == STOCK_IN_STOCK
    assert plan["sync_state"] == SYNC_SYNCED
    assert plan["attention"] is None


def test_in_stock_without_a_number_offers_one_unit_at_a_time():
    """Mirrors `drafts._sellable_units`: trust the declaration, invent no count."""
    plan = revisions.plan_stock_revision(
        observed_state=STOCK_IN_STOCK, observed_quantity=None)
    assert plan["units"] == 1
    assert plan["stock_quantity"] is None


def test_in_stock_with_zero_units_is_a_stock_out_whatever_the_label_says():
    """The provider contradicted itself; the number is the operative fact."""
    plan = revisions.plan_stock_revision(
        observed_state=STOCK_IN_STOCK, observed_quantity=0)
    assert plan["stock_state"] == STOCK_OUT_OF_STOCK
    assert plan["units"] == 0
    assert plan["attention"] == revisions.SUPPLIER_OUT_OF_STOCK


def test_a_negative_quantity_is_treated_as_sold_out_not_as_stock():
    plan = revisions.plan_stock_revision(
        observed_state=STOCK_IN_STOCK, observed_quantity=-5)
    assert plan["units"] == 0
    assert plan["stock_state"] == STOCK_OUT_OF_STOCK


@pytest.mark.parametrize("reason", revisions.ATTENTION_REASONS)
def test_every_attention_reason_is_reachable_from_a_planner(reason):
    """A reason no planner can emit is a copy string nobody will ever read.

    Enumerating the constants and proving each one is produced by some input is
    what stops this vocabulary from growing entries that only exist in the tuple.
    """
    produced = {
        revisions.plan_cost_revision(
            source=source(cost=1000, overridden=[MERCHANT_OWNS_PRICE]),
            variant=variant(price=1200), rule=TARGET_45,
            observed_cost_cents=1500)["attention"],
        revisions.plan_cost_revision(
            source=source(cost=1000, overridden=[MERCHANT_OWNS_PRICE]),
            variant=variant(price=2000), rule=TARGET_45,
            observed_cost_cents=1900)["attention"],
        revisions.plan_cost_revision(
            source=source(cost=None), variant=variant(price=2000),
            rule=TARGET_45, observed_cost_cents=None)["attention"],
        revisions.plan_cost_revision(
            source=source(cost=1000), variant=variant(price=2000),
            rule=TARGET_45, observed_cost_cents=0)["attention"],
        revisions.plan_stock_revision(
            observed_state=STOCK_OUT_OF_STOCK, observed_quantity=0)["attention"],
        revisions.plan_stock_revision(
            observed_state=STOCK_UNKNOWN, observed_quantity=None)["attention"],
    }
    assert reason in produced
