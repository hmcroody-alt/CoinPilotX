"""The funnel must not invent the numbers it cannot measure.

Every failure this file defends against produces a *plausible* number rather
than an error, which is why they are worth tests: a dashboard showing 0% where
it means "not measured", or showing a sale the ledger never confirmed, looks
exactly like a dashboard that is working.
"""

from __future__ import annotations

from services.pulse_analytics import funnel


def impression(listing_id=1001, *, surface="marketplace", self_view=0):
    return {"listing_id": listing_id, "surface": surface, "self_view": self_view}


def engagement(action, listing_id=1001):
    return {"listing_id": listing_id, "action": action}


def order(item_id, *, status="succeeded", amount_cents=1000):
    return {
        "id": f"ord-{item_id}-{status}",
        "item_id": item_id,
        "status": status,
        "amount_cents": amount_cents,
        "currency": "USD",
        "stripe_payment_intent_id": "pi_test",
        "created_at": "2026-03-02T09:00:00",
    }


METRICS = {"confirmed_orders": 1, "net_sales_minor": 1000, "currency": "USD"}


def test_an_unmeasured_rate_is_none_not_zero():
    """Nothing shown is not a 0% click rate. It is no click rate."""
    report = funnel.summarize([], [])

    assert report["impressions"] == 0
    assert report["click_through_rate"] is None


def test_a_measured_rate_of_zero_is_zero():
    """And the distinction has to cut both ways, or it is not a distinction."""
    report = funnel.summarize([impression()], [])

    assert report["click_through_rate"] == 0.0


def test_self_views_do_not_count_as_exposure():
    report = funnel.summarize([impression(), impression(self_view=1)], [engagement("click")])

    assert report["impressions"] == 1
    assert report["click_through_rate"] == 1.0


def test_steps_are_counted_independently_and_need_not_descend():
    """A purchase with no recorded click is real and must survive the aggregate.

    The user saved the product and came back days later. ``commerce_discovery``
    refuses to backfill the missing click, and this module must not quietly
    restore the pyramid by clamping each step to the one above it.
    """
    report = funnel.summarize(
        [impression()],
        [engagement("purchase"), engagement("add_to_cart")],
    )

    assert report["steps"]["click"] == 0
    assert report["steps"]["add_to_cart"] == 1
    assert report["client_reported_purchases"] == 1


def test_the_outcome_is_unavailable_rather_than_zero_without_seller_metrics():
    """A number this module invented for itself is the defect, not the fallback."""
    report = funnel.summarize([impression()], [engagement("click")])

    outcome = report["outcome"]
    assert outcome["source"] == "unavailable"
    assert outcome["confirmed_orders"] is None
    assert outcome["net_sales_minor"] is None
    assert outcome["exposed_order_share"] is None


def test_the_outcome_comes_from_seller_metrics_not_from_the_event_log():
    """Three client-reported purchases against one confirmed order reports one."""
    report = funnel.summarize(
        [impression()],
        [engagement("purchase"), engagement("purchase"), engagement("purchase")],
        seller_metrics=METRICS,
        orders=[order(1001)],
    )

    assert report["outcome"]["confirmed_orders"] == 1
    assert report["client_reported_purchases"] == 3


def test_the_client_server_gap_is_reported_rather_than_reconciled():
    """The gap is the size of the lie the old dashboard told. It is a number."""
    report = funnel.summarize(
        [impression()],
        [engagement("purchase"), engagement("purchase"), engagement("purchase")],
        seller_metrics=METRICS,
        orders=[order(1001)],
    )

    assert report["outcome"]["client_server_purchase_gap"] == 2


def test_orders_are_split_by_whether_exposure_preceded_them():
    """An order for a listing that was never shown is not a discovery outcome."""
    report = funnel.summarize(
        [impression(listing_id=1001)],
        [],
        seller_metrics={"confirmed_orders": 2, "net_sales_minor": 2000, "currency": "USD"},
        orders=[order(1001), order(9999)],
    )

    outcome = report["outcome"]
    assert outcome["orders_with_prior_exposure"] == 1
    assert outcome["orders_without_exposure"] == 1
    assert outcome["exposed_order_share"] == 0.5


def test_unconfirmed_orders_are_excluded_from_both_sides_of_the_split():
    """`checkout_created` is not a sale, and must not become one by being exposed.

    This is the exact shape of the original defect: 32 order rows, none paid,
    reported to the seller as 32 orders.
    """
    report = funnel.summarize(
        [impression(listing_id=1001)],
        [],
        seller_metrics={"confirmed_orders": 0, "net_sales_minor": 0, "currency": "USD"},
        orders=[order(1001, status="checkout_created"), order(1001, status="checkout_expired")],
    )

    outcome = report["outcome"]
    assert outcome["orders_with_prior_exposure"] == 0
    assert outcome["orders_without_exposure"] == 0
    assert outcome["exposed_order_share"] is None


def test_every_report_carries_the_path_only_disclaimer():
    """A caller has to type over this to present the split as causal."""
    for report in (
        funnel.summarize([], []),
        funnel.summarize([impression()], [engagement("click")], seller_metrics=METRICS,
                         orders=[order(1001)]),
    ):
        assert report["attribution_basis"] == "path_only"


def test_purchase_is_absent_from_the_client_reported_step_block():
    """Kept out of `steps` so a UI iterating the funnel cannot render it as one."""
    report = funnel.summarize([impression()], [engagement("purchase")])

    assert "purchase" not in report["steps"]
    assert report["client_reported_purchases"] == 1


def test_impressions_are_broken_down_by_surface():
    report = funnel.summarize(
        [impression(surface="marketplace"), impression(surface="reels"), impression(surface="reels")],
        [],
    )

    assert report["impressions_by_surface"] == {"marketplace": 1, "reels": 2}
