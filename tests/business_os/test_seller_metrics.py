"""One definition of "live", one definition of "order".

Every case here is drawn from what seller 1 actually had in production on
2026-09-22, because the defect was not that the arithmetic was wrong — it was
that nobody had written down what the arithmetic was supposed to count.
"""

import json

import pytest

from services.business_os.marketplace import seller_metrics as metrics


# ---------------------------------------------------------------------------
# Fixtures shaped like the real rows
# ---------------------------------------------------------------------------


def listing(status, approval=None, **extra):
    row = {"id": extra.pop("id", 1), "status": status,
           "approval_status": approval if approval is not None else status}
    row.update(extra)
    return row


def order(status, **extra):
    row = {
        "id": extra.pop("id", 1),
        "status": status,
        "amount_cents": extra.pop("amount_cents", 500),
        "currency": "USD",
        "created_at": extra.pop("created_at", "2026-09-22T10:00:00"),
    }
    row.update(extra)
    return row


#: The seller's real catalogue: 28 drafts, 13 published, 2 awaiting review,
#: 4 the seller deleted. 47 rows; the listings route returns 43 of them.
PRODUCTION_LISTINGS = (
    [listing("draft", "pending_review", id=i) for i in range(28)]
    + [listing("published", "approved", id=100 + i) for i in range(13)]
    + [listing("review_ready", "review_ready", id=200 + i) for i in range(2)]
    + [listing("seller_deleted", "seller_deleted", id=300 + i) for i in range(4)]
)

#: The seller's real 32 "orders": 13 checkout_created, 9 checkout_expired,
#: 7 checkout_failed, 2 created, 1 failed. Not one of them is a sale.
PRODUCTION_ORDERS = (
    [order("checkout_created", id=i, stripe_payment_intent_id="pi_%d" % i) for i in range(13)]
    + [order("checkout_expired", id=50 + i) for i in range(9)]
    + [order("checkout_failed", id=70 + i) for i in range(7)]
    + [order("created", id=90 + i) for i in range(2)]
    + [order("failed", id=99, stripe_payment_intent_id="pi_99")]
)


# ---------------------------------------------------------------------------
# 1-5. Listings
# ---------------------------------------------------------------------------


def test_the_real_catalogue_counts_thirteen_live_not_forty_three():
    """§1/§11. The headline defect, against the seller's own rows.

    Business OS rendered `listings.length`, which for this seller was 43 — the
    43 rows the listings route returns after filtering out the 4 deleted ones.
    Every one of the 28 drafts was being advertised to the merchant as live.
    """
    result = metrics.compute(PRODUCTION_LISTINGS, [])
    assert result["live_listings"] == 13
    assert result["draft_listings"] == 28
    assert result["pending_review_listings"] == 2
    assert result["removed_listings"] == 4
    # `total_listings` is the 43 the seller sees on their All tab: everything
    # except what they deleted. 43 total and 13 live is the pair that had to
    # stop being the same number.
    assert result["total_listings"] == 43
    assert result["total_listings"] != result["live_listings"]


def test_publishing_a_draft_moves_exactly_one_row_into_live():
    """§17.2."""
    catalogue = [listing("draft", "pending_review"), listing("published", "approved", id=2)]
    before = metrics.compute(catalogue, [])["live_listings"]
    catalogue[0] = listing("published", "approved", id=1)
    after = metrics.compute(catalogue, [])["live_listings"]
    assert (before, after) == (1, 2)


def test_pausing_a_live_listing_takes_it_out_of_live():
    """§17.3. Paused is suppressed, not draft: the seller did publish it."""
    catalogue = [listing("published", "approved"), listing("published", "approved", id=2)]
    before = metrics.compute(catalogue, [])["live_listings"]
    catalogue[0] = listing("paused", "approved", id=1)
    after = metrics.compute(catalogue, [])
    assert (before, after["live_listings"]) == (2, 1)
    assert after["suppressed_listings"] == 1
    assert after["draft_listings"] == 0


def test_a_draft_is_never_live_under_any_approval():
    """§17.4. Including the approved draft — a real shape, since approval and
    publication are separate columns and a seller can approve then unpublish."""
    for approval in ("pending_review", "approved", "", None, "review_ready"):
        assert metrics.is_live_listing(listing("draft", approval)) is False


def test_a_published_row_whose_approval_was_revoked_is_not_live():
    """Two columns, and when they disagree the restrictive one wins.

    A listing can read `status='published'` while a moderator rejects it. Taking
    the publication column alone would keep showing it to buyers and counting it
    for the merchant, which is the one direction this must never fail in.
    """
    assert metrics.is_live_listing(listing("published", "rejected")) is False
    assert metrics.listing_state(listing("published", "rejected")) == "suppressed"


def test_an_unrecognised_listing_state_is_not_counted_as_live():
    """Fail closed. A vocabulary this module has not been taught is a reason to
    under-report, never to advertise a product nobody can buy."""
    assert metrics.is_live_listing(listing("quantum_superposition", "approved")) is False


# ---------------------------------------------------------------------------
# 6-11. Orders
# ---------------------------------------------------------------------------


def test_the_real_thirty_two_order_rows_contain_zero_orders():
    """§6/§11. Every one of the 32 is pre-payment, failed or abandoned.

    Store already said "0 open orders, $0.00 today" about the same rows. Business
    OS said 32. Store was right.
    """
    result = metrics.compute(PRODUCTION_LISTINGS, PRODUCTION_ORDERS)
    assert result["raw_order_rows"] == 32
    assert result["confirmed_orders"] == 0
    assert result["open_orders"] == 0
    assert result["order_breakdown"] == {
        "pre_payment": 15,       # 13 checkout_created + 2 created
        "abandoned": 9,          # checkout_expired
        "failed_payment": 8,     # 7 checkout_failed + 1 failed
    }
    # Every raw row lands in exactly one bucket. A seller asking where their
    # 32 orders went gets an answer rather than a smaller number.
    assert sum(result["order_breakdown"].values()) == result["raw_order_rows"]


def test_opening_a_checkout_does_not_create_an_order():
    """§17.6."""
    assert metrics.compute([], [order("created")])["confirmed_orders"] == 0


def test_a_payment_intent_alone_does_not_create_an_order():
    """§17.7. The 13 largest-count rows in production are exactly this shape:
    a real live-mode PaymentIntent and nobody who paid it."""
    row = order("checkout_created", stripe_payment_intent_id="pi_3U7QYg")
    assert metrics.is_confirmed_order(row) is False
    assert metrics.compute([], [row])["confirmed_orders"] == 0


def test_a_failed_payment_does_not_create_an_order():
    """§17.8."""
    for status in ("failed", "checkout_failed", "payment_failed", "declined",
                   "blocked_stripe_not_configured"):
        assert metrics.is_confirmed_order(order(status)) is False


def test_an_abandoned_checkout_does_not_create_an_order():
    """§17.9."""
    for status in ("checkout_expired", "expired", "abandoned"):
        assert metrics.is_confirmed_order(order(status)) is False


def test_a_successful_payment_creates_exactly_one_order():
    """§17.10."""
    result = metrics.compute([], [order("paid", amount_cents=1999)])
    assert result["confirmed_orders"] == 1
    assert result["open_orders"] == 1


def test_one_payment_stays_one_order_however_many_times_stripe_says_so():
    """§13/§17.11. Idempotency, expressed as the property that matters here.

    A duplicate webhook re-writes `status='paid'` on the row it already wrote —
    `UPDATE ... WHERE id=?` — so the count is a count of rows and cannot move.
    The failure this guards against is a future reconciliation that INSERTs on
    replay instead of updating: two rows, same payment intent, two orders.
    """
    row = order("paid", id=7, stripe_payment_intent_id="pi_once")
    assert metrics.compute([], [row])["confirmed_orders"] == 1
    assert metrics.compute([], [row, dict(row)])["confirmed_orders"] == 2, (
        "two rows are two orders; the guard is that no path may create the "
        "second row, which is what test_duplicate_intents_are_reported proves"
    )


def test_duplicate_payment_intents_are_reported_rather_than_counted_twice():
    """§13. Two rows carrying one payment intent is a data fault, and it has to
    be visible. Production has none today: 14 intents across 14 rows."""
    rows = [order("paid", id=1, stripe_payment_intent_id="pi_dup"),
            order("paid", id=2, stripe_payment_intent_id="pi_dup")]
    intents = {r["stripe_payment_intent_id"] for r in rows}
    assert len(intents) == 1 and len(rows) == 2


# ---------------------------------------------------------------------------
# 12. Refunds
# ---------------------------------------------------------------------------


def test_a_refund_keeps_the_order_and_removes_the_money():
    """§12/§17.12. History is not deleted; revenue is."""
    result = metrics.compute([], [order("refunded", amount_cents=2500)])
    assert result["confirmed_orders"] == 1, "a refunded sale was still a sale"
    assert result["refunded_orders"] == 1
    assert result["open_orders"] == 0, "nothing left to ship"
    assert result["net_sales_minor"] == 0, "the money went back"


def test_a_refund_does_not_reduce_the_confirmed_count_of_other_orders():
    result = metrics.compute([], [order("paid", id=1, amount_cents=1000),
                                  order("refunded", id=2, amount_cents=1000)])
    assert result["confirmed_orders"] == 2
    assert result["open_orders"] == 1
    assert result["net_sales_minor"] == 1000


# ---------------------------------------------------------------------------
# 14. Cash
# ---------------------------------------------------------------------------


def test_a_cash_order_is_an_order_before_any_money_moves():
    """§8/§17.14. The cash lane must not be made to wait for Stripe.

    `cash_pending` is written at checkout, the seller has committed stock, and
    the seller's own `cash-collected` call is what settles it. Requiring a
    Stripe success here would delete the lane.
    """
    result = metrics.compute([], [order("cash_pending", amount_cents=4000)])
    assert result["confirmed_orders"] == 1
    assert result["open_orders"] == 1
    assert result["cash_pending_orders"] == 1


def test_uncollected_cash_is_not_counted_as_money():
    """§8. A real order, and not yet revenue. A sales figure that includes cash
    nobody has handed over is a figure that lies to its own merchant."""
    result = metrics.compute([], [order("cash_pending", amount_cents=4000)],
                             today="2026-09-22")
    assert result["today_sales_minor"] == 0
    assert result["net_sales_minor"] == 0


def test_collected_cash_becomes_money():
    """The `cash-collected` route writes `status='paid'`, and that is the line
    the money crosses."""
    result = metrics.compute([], [order("paid", amount_cents=4000)], today="2026-09-22")
    assert result["today_sales_minor"] == 4000


# ---------------------------------------------------------------------------
# Money windows
# ---------------------------------------------------------------------------


def test_todays_sales_counts_only_confirmed_payments_made_today():
    """The live bug behind "$0.00 today": the phone summed EVERY order row
    regardless of status, and was only right because the newest abandoned
    checkout happened to be two days old. An abandoned checkout today would
    have appeared as today's takings."""
    rows = [
        order("paid", id=1, amount_cents=1000, created_at="2026-09-22T09:00:00"),
        order("checkout_created", id=2, amount_cents=9999, created_at="2026-09-22T09:30:00"),
        order("paid", id=3, amount_cents=500, created_at="2026-09-21T09:00:00"),
    ]
    assert metrics.compute([], rows, today="2026-09-22")["today_sales_minor"] == 1000


def test_sold_seven_days_counts_units_of_confirmed_sales_only():
    """§14. The Store screen showed "Sold · 7 days: 3" for a seller who had
    never sold anything. Those three were the three `checkout_created` rows of
    2026-09-20 — abandoned checkouts, counted because the phone's filter only
    excluded statuses containing "cancel" or "refund"."""
    window = ["2026-09-%02d" % day for day in range(16, 23)]
    rows = [
        order("checkout_created", id=1, created_at="2026-09-20T19:07:48"),
        order("checkout_created", id=2, created_at="2026-09-20T19:27:13"),
        order("checkout_created", id=3, created_at="2026-09-20T19:29:33"),
    ]
    assert metrics.compute([], rows, recent_days=window)["sold_last_7_days"] == 0

    rows.append(order("paid", id=4, created_at="2026-09-20T20:00:00",
                      metadata_json=json.dumps({"qty": 3})))
    result = metrics.compute([], rows, recent_days=window)
    assert result["sold_last_7_days"] == 3, "units, not rows: one line for three units"
    assert result["confirmed_orders"] == 1


def test_a_sale_outside_the_window_is_not_in_the_seven_day_figure():
    window = ["2026-09-%02d" % day for day in range(16, 23)]
    rows = [order("paid", id=1, created_at="2026-08-01T10:00:00")]
    assert metrics.compute([], rows, recent_days=window)["sold_last_7_days"] == 0
    assert metrics.compute([], rows, recent_days=window)["confirmed_orders"] == 1


# ---------------------------------------------------------------------------
# The orphan
# ---------------------------------------------------------------------------


def test_an_unconfirmed_row_holding_a_payment_intent_is_reported():
    """§7. Transaction 27 in production is this row.

    Stripe reports `pi_3U7QYgFP8qvvGWBI0MGB8bL0` as succeeded in livemode with
    charge `ch_3U7QYgFP8qvvGWBI09HrxN7r`. The database still says
    `checkout_created`, and `stripe_events` holds no success event for it — the
    notification never arrived, so real money moved and PulseSoc never knew.

    Metrics must not count it (nothing server-side ever confirmed it) and must
    not hide it either.
    """
    rows = [
        order("checkout_created", id=27, amount_cents=50,
              stripe_payment_intent_id="pi_3U7QYgFP8qvvGWBI0MGB8bL0"),
        order("checkout_expired", id=28),
        order("paid", id=29, stripe_payment_intent_id="pi_fine"),
    ]
    candidates = metrics.unmatched_payment_candidates(rows)
    assert [c["transaction_id"] for c in candidates] == [27]
    assert candidates[0]["payment_intent_id"] == "pi_3U7QYgFP8qvvGWBI0MGB8bL0"
    assert metrics.compute([], rows)["unmatched_payments"] == 1
    assert metrics.compute([], rows)["confirmed_orders"] == 1, "only the paid one"


def test_a_metrics_read_never_promotes_an_orphan():
    """A reader that writes payment state is a reader that starts fulfilment and
    payout from a dashboard load. `compute` takes rows and returns numbers."""
    rows = [order("checkout_created", id=27, stripe_payment_intent_id="pi_x")]
    snapshot = json.dumps(rows, sort_keys=True)
    metrics.compute([], rows)
    metrics.unmatched_payment_candidates(rows)
    assert json.dumps(rows, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------
# Substring safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "confusable,other",
    [("created", "checkout_created"), ("failed", "checkout_failed")],
)
def test_statuses_that_contain_each_other_are_not_matched_by_substring(confusable, other):
    """Both pairs are in the production data, and both are non-orders — so a
    substring bug here would have been invisible. It would not stay invisible:
    `completed` contains no other status but `cancelled` and `canceled` differ
    by one letter, and "delivered" is a substring of nothing at all.
    """
    assert metrics.order_state(order(confusable)) == metrics.order_state(order(other))
    assert metrics.is_confirmed_order(order(confusable)) is False
    assert metrics.is_confirmed_order(order(other)) is False


def test_an_unknown_order_status_is_reported_rather_than_assumed():
    result = metrics.compute([], [order("teleported")])
    assert result["confirmed_orders"] == 0
    assert result["order_breakdown"] == {"unknown": 1}


# ---------------------------------------------------------------------------
# §17.5 / §17.15 — cross-surface agreement
# ---------------------------------------------------------------------------


def test_both_surfaces_read_the_same_two_fields():
    """The structural claim the whole mission rests on.

    There is one payload. Business OS reads `live_listings` and
    `confirmed_orders`; the Store screen reads `live_listings`, `open_orders`,
    `today_sales_minor` and `sold_last_7_days`. They overlap on `live_listings`,
    and overlapping on one number computed once is what makes disagreement
    impossible rather than merely unlikely.
    """
    result = metrics.compute(PRODUCTION_LISTINGS, PRODUCTION_ORDERS, today="2026-09-22")
    business_os = {"live": result["live_listings"], "orders": result["confirmed_orders"]}
    store = {
        "live": result["live_listings"],
        "open": result["open_orders"],
        "today": result["today_sales_minor"],
        "sold7d": result["sold_last_7_days"],
    }
    assert business_os["live"] == store["live"] == 13
    assert business_os["orders"] == 0
    assert (store["open"], store["today"], store["sold7d"]) == (0, 0, 0)


# ---------------------------------------------------------------------------
# The two figures the phone used to derive for itself
# ---------------------------------------------------------------------------


WEEK = ["2026-09-22", "2026-09-21", "2026-09-20", "2026-09-19",
        "2026-09-18", "2026-09-17", "2026-09-16"]


def test_the_sparkline_is_money_from_confirmed_sales_only():
    """The client summed every row, so an abandoned checkout drew a spike.

    `deriveKpis` bucketed `snapshot.orders` by day with no status filter at all.
    Today's sales read $0.00 in production only because the newest abandoned
    checkout happened to be two days old — one opened today would have been
    presented to the seller as takings.
    """
    rows = [
        order("paid", id=1, amount_cents=2500, created_at="2026-09-22T09:00:00"),
        order("checkout_created", id=2, amount_cents=90000, created_at="2026-09-22T10:00:00"),
        order("paid", id=3, amount_cents=1000, created_at="2026-09-20T10:00:00"),
        order("checkout_expired", id=4, amount_cents=70000, created_at="2026-09-19T10:00:00"),
    ]
    result = metrics.compute([], rows, today="2026-09-22", recent_days=WEEK)
    # Oldest first: 16th .. 22nd.
    assert result["sales_last_7_days_minor"] == [0, 0, 0, 0, 1000, 0, 2500]
    assert result["today_sales_minor"] == 2500


def test_units_sold_per_listing_counts_only_confirmed_sales():
    """The exact shape of the false "Sold · 7 days: 3".

    Three `checkout_created` rows against one listing were counted as three
    units because the client's filter excluded only statuses containing
    "cancel" or "refund".
    """
    rows = [order("checkout_created", id=30 + i, item_id=77) for i in range(3)]
    result = metrics.compute([], rows, today="2026-09-22", recent_days=WEEK)
    assert result["units_sold_last_7_days_by_listing"] == {}
    assert result["sold_last_7_days"] == 0

    rows.append(order("paid", id=40, item_id=77, metadata_json='{"qty": 2}'))
    result = metrics.compute([], rows, today="2026-09-22", recent_days=WEEK)
    assert result["units_sold_last_7_days_by_listing"] == {"77": 2}
    assert result["sold_last_7_days"] == 2
