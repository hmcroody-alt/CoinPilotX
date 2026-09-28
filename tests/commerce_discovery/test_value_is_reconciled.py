"""What a placement earned is read from the order, never claimed by the client.

``commerce_discovery_engagement_events.value_minor`` is an integer column named
after money, in a table an analyst will eventually sum, and its value used to be
whatever number arrived in the request body. Nothing *reads* the column yet, which
is what made it easy to mistake for a dormant hazard — but the write side was
live: ``MarketplaceProductScreen`` computed ``unitMinor * qty`` on the device and
sent it on every cart addition and every checkout entry. The amount credited to a
placement was an assertion by the party that benefits from it, and neither the
schema nor the function signature said so.

The amount is now derived, in three tiers by how much can be proven:

* ``purchase`` — settled money, read from ``marketplace_orders`` scoped to the
  buyer *and* the listing. The client sends an ``order_ref``, which is an
  identifier and therefore checkable, and nothing else. A forged or borrowed ref
  resolves to no row, and no row means no money.
* ``add_to_cart`` / ``checkout_started`` — intent, priced from the listing through
  the same parser the eligibility gate uses. **Price is a fact; quantity is a
  claim.** "Buy now" bypasses the cart by design, so at ``checkout_started`` there
  is no server-side row saying how many — the count falls back to a claim clamped
  to the listing's own stock. Keeping the signal and bounding it beat deleting a
  funnel stage the product actually reports on.
* everything else — nothing. A click is not worth its product's price.

The tests below split along the line that matters: an amount is written only when
it can be substantiated to the tier the verb allows, and every other case writes
zero *loudly* rather than silently.
"""

from __future__ import annotations

import logging

from services.commerce_discovery import events


def _stored(market, *, action: str = "purchase") -> dict:
    cur = market.conn.cursor()
    cur.execute(
        "SELECT value_minor, currency, order_ref FROM commerce_discovery_engagement_events "
        "WHERE action=? ORDER BY rowid DESC LIMIT 1",
        (action,),
    )
    row = cur.fetchone()
    assert row is not None, f"no {action} event was recorded"
    return dict(zip([c[0] for c in cur.description], row))


def _placement(market):
    placements = market.serve("feed")
    market.render(placements)
    return placements[0], int(placements[0]["product"]["listing_id"])


class TestAPaidOrderIsTheOnlySourceOfAnAmount:
    def test_the_amount_comes_from_the_order_row(self, market):
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=4999, currency="usd")
        market.engage(placement, "purchase", order_ref=str(order_id))
        stored = _stored(market)
        assert stored["value_minor"] == 4999
        assert stored["currency"] == "USD"

    def test_the_client_cannot_state_an_amount_at_all(self):
        # The strongest form of the guarantee is structural, not behavioural:
        # there is no parameter to pass. A caller that *can* assert revenue is a
        # caller whose assertion something will eventually trust, so the fix is
        # the absent argument rather than a validation rule on a present one.
        import inspect

        params = inspect.signature(events.record_engagement).parameters
        assert "value_minor" not in params
        assert "currency" not in params

    def test_a_provider_payment_id_also_resolves(self, market):
        # Stripe hands back a payment id; the caller may never learn the internal
        # row id. Accepting either keeps the contract usable without widening it —
        # both are still scoped by buyer and listing.
        placement, listing_id = _placement(market)
        market.purchase(listing_id, amount_cents=1200, provider_payment_id="pi_abc123")
        market.engage(placement, "purchase", order_ref="pi_abc123")
        assert _stored(market)["value_minor"] == 1200

    def test_the_order_ref_is_kept_even_when_it_reconciles(self, market):
        # The ref is evidence. Keeping it is what makes the stored amount auditable
        # rather than merely trustworthy.
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=777)
        market.engage(placement, "purchase", order_ref=str(order_id))
        assert _stored(market)["order_ref"] == str(order_id)


class TestEverythingElseIsWorthZero:
    def test_a_forged_order_ref_earns_nothing(self, market):
        placement, listing_id = _placement(market)
        market.purchase(listing_id, amount_cents=9999)
        market.engage(placement, "purchase", order_ref="99999")
        assert _stored(market)["value_minor"] == 0

    def test_another_buyers_order_cannot_be_borrowed(self, market):
        # The attack the buyer scope exists to stop: cite a real, paid, large
        # order that belongs to somebody else. Without the `buyer_user_id=?`
        # predicate this resolves and credits their money to this placement.
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=500000, buyer_user_id=4242)
        market.engage(placement, "purchase", order_ref=str(order_id))
        assert _stored(market)["value_minor"] == 0

    def test_an_order_for_a_different_product_does_not_count(self, market):
        # A real order of this viewer's, cited against a placement of something
        # else. The sale happened; it is not *this* placement's outcome.
        placement, listing_id = _placement(market)
        other = next(i for i in range(1, 30) if i != listing_id)
        order_id = market.purchase(other, amount_cents=8800)
        market.engage(placement, "purchase", order_ref=str(order_id))
        assert _stored(market)["value_minor"] == 0

    def test_an_unpaid_order_is_not_revenue(self, market):
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=3300, status="pending_payment")
        market.engage(placement, "purchase", order_ref=str(order_id))
        assert _stored(market)["value_minor"] == 0

    def test_a_refunded_order_is_not_revenue(self, market):
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=3300, status="refunded")
        market.engage(placement, "purchase", order_ref=str(order_id))
        assert _stored(market)["value_minor"] == 0

    def test_a_click_has_no_value_even_beside_a_real_order(self, market):
        # A click is not a sale. Attaching the order's amount to it would double
        # count the purchase the moment anything sums the column.
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=4999)
        market.engage(placement, "click", order_ref=str(order_id))
        assert _stored(market, action="click")["value_minor"] == 0

    def test_a_purchase_with_no_ref_is_recorded_and_worth_nothing(self, market):
        # Losing the event would be worse than losing the amount: the conversion
        # is the thing the engine exists to produce.
        placement, _ = _placement(market)
        market.engage(placement, "purchase")
        assert _stored(market)["value_minor"] == 0


class TestIntentIsPricedByTheServerAndCountedWithSuspicion:
    """The verbs where a value is *derivable but not provable* end to end.

    ``add_to_cart`` and ``checkout_started`` are the two events the app was
    actually attaching a device-computed total to, so this is the class that
    covers the live half of the defect. The split under test: the unit price is
    taken from the listing row and the client's number is only ever consulted for
    the count — and then only when the server has no count of its own.
    """

    def test_a_cart_addition_is_priced_from_the_listing_not_the_request(self, market):
        placement, listing_id = _placement(market)
        # The fixture prices listing N at $(20+N).00 and stocks 25 of it.
        unit = (20 + listing_id) * 100
        market.add_to_cart(listing_id, qty=2)
        market.engage(placement, "add_to_cart", quantity=9999)
        stored = _stored(market, action="add_to_cart")
        # 9999 was ignored outright: a cart row exists, so the server has its own
        # answer and never consults the claim.
        assert stored["value_minor"] == unit * 2
        assert stored["currency"] == "USD"

    def test_buy_now_has_no_cart_row_so_the_claim_is_used(self, market):
        # `handleBuyNow` deliberately bypasses the cart, which is precisely why the
        # claim cannot simply be discarded — discarding it would report every
        # Buy Now of three items as an order for one.
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        market.engage(placement, "checkout_started", quantity=3)
        assert _stored(market, action="checkout_started")["value_minor"] == unit * 3

    def test_the_claim_is_clamped_to_what_the_listing_can_sell(self, market):
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        cur = market.conn.cursor()
        cur.execute("UPDATE marketplace_listings SET quantity=2 WHERE id=?", (listing_id,))
        market.conn.commit()
        market.engage(placement, "checkout_started", quantity=500)
        # Two in stock is the ceiling on what an intent to buy can be worth. This is
        # the bound that keeps one hostile request from dominating an aggregate.
        assert _stored(market, action="checkout_started")["value_minor"] == unit * 2

    def test_an_unstocked_listing_still_has_a_ceiling(self, market):
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        cur = market.conn.cursor()
        # Digital goods and services carry no stock count, so the listing itself
        # offers no bound. A blanket bound stands in — not a business rule, just a
        # cap low enough that no single event can swamp a report.
        cur.execute("UPDATE marketplace_listings SET quantity=0 WHERE id=?", (listing_id,))
        market.conn.commit()
        market.engage(placement, "checkout_started", quantity=10**9)
        assert _stored(market, action="checkout_started")["value_minor"] == unit * events._MAX_CLAIMED_QUANTITY

    def test_a_nonsense_claim_counts_as_one(self, market):
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        market.engage(placement, "checkout_started", quantity="lots")
        assert _stored(market, action="checkout_started")["value_minor"] == unit

    def test_a_negative_claim_cannot_produce_a_negative_amount(self, market):
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        market.engage(placement, "add_to_cart", quantity=-7)
        # A negative line total would subtract from whatever sums this column, which
        # is a more useful thing to forge than an inflated one.
        assert _stored(market, action="add_to_cart")["value_minor"] == unit

    def test_an_unpriced_listing_is_worth_nothing(self, market):
        placement, listing_id = _placement(market)
        cur = market.conn.cursor()
        cur.execute("UPDATE marketplace_listings SET price_label=? WHERE id=?", ("Request access", listing_id))
        market.conn.commit()
        market.engage(placement, "checkout_started", quantity=4)
        # No resolvable price means no line total. Inventing one from the count alone
        # is how a "contact us" listing would show up in a revenue report.
        #
        # Worth being precise about what this pins: deleting the `priced is None`
        # guard cannot *invent* a price, it can only reach the unpack with `None`
        # and crash. So this is a branch-reachability test, not a guard against a
        # quiet weakening — and it is the only test in the file that gets there.
        assert _stored(market, action="checkout_started")["value_minor"] == 0

    def test_without_the_parser_the_server_says_nothing(self, market):
        # The parser is injected from `bot`, so the package cannot price anything on
        # its own. Failing to an invented number would be worse than failing to zero.
        placement, listing_id = _placement(market)
        market.engage(placement, "add_to_cart", quantity=3, parse_price=None)
        assert _stored(market, action="add_to_cart")["value_minor"] == 0

    def test_the_currency_comes_from_the_listing(self, market):
        placement, listing_id = _placement(market)
        cur = market.conn.cursor()
        cur.execute("UPDATE marketplace_listings SET currency=? WHERE id=?", ("eur", listing_id))
        market.conn.commit()
        market.engage(placement, "add_to_cart")
        assert _stored(market, action="add_to_cart")["currency"] == "EUR"

    def test_a_broken_listings_table_costs_the_amount_not_the_event(self, market, caplog):
        placement, _ = _placement(market)
        market.conn.cursor().execute("DROP TABLE marketplace_listings")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            market.engage(placement, "add_to_cart", quantity=2)
        assert any("COMMERCE_DISCOVERY_VALUE_LOOKUP_FAILED" in r.message for r in caplog.records)
        assert _stored(market, action="add_to_cart")["value_minor"] == 0

    def test_a_broken_cart_falls_back_to_the_claim_quietly(self, market, caplog):
        # The cart read is an *improvement* on the claim, not a precondition for
        # pricing at all — so losing it must degrade to the clamped claim rather
        # than to zero, and must not warn: there is nothing an operator can do.
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        market.conn.cursor().execute("DROP TABLE marketplace_cart_items")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            market.engage(placement, "add_to_cart", quantity=2)
        assert _stored(market, action="add_to_cart")["value_minor"] == unit * 2
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_another_buyers_cart_is_not_read(self, market):
        placement, listing_id = _placement(market)
        unit = (20 + listing_id) * 100
        cur = market.conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_cart_items (user_id, listing_id, qty) VALUES (?,?,?)",
            (4242, listing_id, 7),
        )
        market.conn.commit()
        # Somebody else holding seven of this product says nothing about this
        # viewer's intent. Without the `user_id=?` predicate this reads 7.
        market.engage(placement, "checkout_started", quantity=1)
        assert _stored(market, action="checkout_started")["value_minor"] == unit


class TestAnUnreconciledSaleIsLoud:
    def test_a_missing_order_warns(self, market, caplog):
        placement, listing_id = _placement(market)
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            market.engage(placement, "purchase", order_ref="does-not-exist")
        assert any("COMMERCE_DISCOVERY_VALUE_UNRECONCILED" in r.message for r in caplog.records)

    def test_the_warning_does_not_leak_the_whole_ref(self, market, caplog):
        placement, _ = _placement(market)
        secret = "pi_" + ("x" * 200)
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            market.engage(placement, "purchase", order_ref=secret)
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert secret not in logged

    def test_a_reconciled_sale_is_quiet(self, market, caplog):
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=100)
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            market.engage(placement, "purchase", order_ref=str(order_id))
        # An alert that fires on the normal case is an alert nobody reads.
        assert not [r for r in caplog.records if "UNRECONCILED" in r.getMessage()]

    def test_a_broken_orders_table_costs_the_amount_not_the_event(self, market, caplog):
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=4999)
        market.conn.cursor().execute("DROP TABLE marketplace_orders")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            market.engage(placement, "purchase", order_ref=str(order_id))
        # A read that did not run must not be reported as a sale that was worth
        # nothing — hence a distinct code from the unreconciled case.
        assert any("COMMERCE_DISCOVERY_VALUE_LOOKUP_FAILED" in r.message for r in caplog.records)
        assert _stored(market)["value_minor"] == 0


class TestTheAccountIsUsedButNotStored:
    def test_no_user_id_reaches_the_event_row(self, market):
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=4999)
        market.engage(placement, "purchase", order_ref=str(order_id))
        cur = market.conn.cursor()
        cur.execute("SELECT * FROM commerce_discovery_engagement_events ORDER BY rowid DESC LIMIT 1")
        row = dict(zip([c[0] for c in cur.description], cur.fetchone()))
        # The table is keyed by `subject_ref` deliberately. `buyer_user_id` is
        # passed in to authorise a lookup, and the pseudonymity of the behavioural
        # log survives that only if the identity is not written down beside it.
        assert "buyer_user_id" not in row
        assert row["subject_ref"] and str(market.viewer_id) not in str(row["subject_ref"])

    def test_without_a_buyer_nothing_reconciles_and_nothing_errors(self, market, caplog):
        # An anonymous or unauthenticated path cannot be allowed to reconcile by
        # omission — no buyer means no scope, and no scope means no amount.
        #
        # The second assertion is the one with teeth. Deleting the `not
        # buyer_user_id` guard *also* yields zero, because `int(None)` raises and
        # the exception path returns zero — so a test that only checked the amount
        # would pass while the code reached it by crashing. A missing buyer is an
        # ordinary shape, not database weather, and must not be logged as one.
        placement, listing_id = _placement(market)
        order_id = market.purchase(listing_id, amount_cents=4999)
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.events"):
            events.record_engagement(
                market.conn.cursor(),
                placement["placement_id"],
                placement["impression_token"],
                "purchase",
                conn=market.conn,
                buyer_user_id=None,
                order_ref=str(order_id),
            )
        assert _stored(market)["value_minor"] == 0
        assert not [r for r in caplog.records if "LOOKUP_FAILED" in r.getMessage()]
