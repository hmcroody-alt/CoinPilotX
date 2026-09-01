"""A total the card networks will not carry must be refused, not attempted.

The incident: a seller listed an item at $0.10 and the buyer's review screen
showed "Total to pay $0.10" — correct, and completely unpayable. Stripe's floor
in USD is $0.50, so `PaymentIntent.create(amount=10, currency="usd")` raised an
`InvalidRequestError`, the Buy Now route flattened every exception into one
hard-coded 500, and the buyer read "Checkout could not be created." on a price
they could see was fine. Nothing in the app named the reason, and no retry could
ever succeed.

Two properties are pinned here:

  1. The floor is *per currency*. A single hard-coded 50 would refuse a valid
     ¥100 order in HUF and accept an invalid one in CZK, so the table is tested
     across currencies with genuinely different floors and decimal places.
  2. All three checkout lanes — Buy Now, cart, accepted offer — ask before
     calling the provider, and ask about the *buyer total*, not the list price.
     A lane that skips the check reintroduces the incident on its own surface.

The route bodies talk to live Stripe and a live database, so the lane coverage
is a source contract; the arithmetic underneath it is executed.
"""

import pathlib

from services.marketplace_payment_errors import (
    BELOW_MINIMUM_CODE,
    DEFAULT_MINIMUM_CHARGE_MINOR,
    below_minimum_charge_error,
    format_minor,
    minimum_charge_minor,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

BOT = (REPO_ROOT / "bot.py").read_text(encoding="utf-8", errors="ignore")
CART = (REPO_ROOT / "services" / "marketplace_cart_routes.py").read_text(encoding="utf-8")
OFFERS = (REPO_ROOT / "services" / "marketplace_offers_routes.py").read_text(encoding="utf-8")

BUY_NOW = BOT[
    BOT.index("def api_pulse_payments_checkout():") : BOT.index("\ndef _creator_checkout_for_item")
]


def _code(source: str) -> str:
    """Source with comment lines dropped, so prose cannot satisfy an assertion."""
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


class TestTheFloorItself:
    def test_ten_cents_is_refused_in_usd(self):
        # The reported order, exactly.
        error = below_minimum_charge_error(10, "USD")

        assert error is not None
        assert error["code"] == BELOW_MINIMUM_CODE
        # 400, not 500: this is the request being wrong, and a 500 would tell
        # the client to retry something that can never succeed.
        assert error["status"] == 400
        assert error["minimum_minor"] == 50

    def test_the_message_names_the_actual_floor_and_the_absence_of_a_charge(self):
        # "Checkout could not be created." named neither, which is why the
        # owner could not tell a pricing problem from an outage.
        message = below_minimum_charge_error(10, "USD")["message"]

        assert "USD 0.50" in message
        assert "No card was charged" in message

    def test_the_floor_itself_is_chargeable(self):
        # Off-by-one here refuses an order Stripe accepts, which is the more
        # expensive direction of the two.
        assert below_minimum_charge_error(50, "USD") is None

    def test_an_ordinary_order_passes_untouched(self):
        assert below_minimum_charge_error(1000, "USD") is None

    def test_the_floor_is_per_currency_not_a_fixed_fifty(self):
        # A hard-coded 50 passes for USD and is wrong for every row here.
        assert minimum_charge_minor("GBP") == 30
        assert minimum_charge_minor("CZK") == 1500
        assert minimum_charge_minor("HUF") == 17500
        assert minimum_charge_minor("MXN") == 1000
        assert minimum_charge_minor("HKD") == 400

    def test_a_zero_decimal_currency_is_not_multiplied_by_a_hundred(self):
        # ¥50 is fifty minor units, not five thousand. Treating JPY like USD
        # would refuse every order under ¥5,000.
        assert minimum_charge_minor("JPY") == 50
        assert below_minimum_charge_error(50, "JPY") is None
        assert below_minimum_charge_error(49, "JPY") is not None
        assert "JPY 50" in below_minimum_charge_error(10, "JPY")["message"]

    def test_a_forty_cent_order_is_refused_in_usd_but_allowed_in_gbp(self):
        # The one assertion that cannot pass with any single global constant.
        assert below_minimum_charge_error(40, "USD") is not None
        assert below_minimum_charge_error(40, "GBP") is None

    def test_currency_case_and_absence_do_not_change_the_answer(self):
        assert minimum_charge_minor("usd") == 50
        assert minimum_charge_minor("") == DEFAULT_MINIMUM_CHARGE_MINOR
        assert minimum_charge_minor(None) == DEFAULT_MINIMUM_CHARGE_MINOR

    def test_an_unknown_currency_defers_to_stripe_rather_than_guessing_high(self):
        # A floor invented for an unlisted currency would refuse checkouts
        # Stripe would have taken, and nothing in the app could explain it. Low
        # simply falls through to the provider, whose error is still classified.
        assert minimum_charge_minor("XYZ") == DEFAULT_MINIMUM_CHARGE_MINOR
        assert below_minimum_charge_error(100, "XYZ") is None

    def test_a_free_or_unpriced_total_is_still_refused(self):
        assert below_minimum_charge_error(0, "USD") is not None

    def test_display_follows_the_currency_decimals(self):
        assert format_minor(50, "USD") == "USD 0.50"
        assert format_minor(1000, "MXN") == "MXN 10.00"
        assert format_minor(50, "JPY") == "JPY 50"


class TestEveryLaneAsksBeforeTheProvider:
    def test_all_three_lanes_run_the_shared_pre_flight(self):
        # Three lanes with three private opinions about the floor is how one of
        # them drifts back to attempting the charge.
        for source in (_code(BUY_NOW), _code(CART), _code(OFFERS)):
            assert "below_minimum_charge_error(" in source

    def test_each_lane_checks_the_buyer_total_not_the_list_price(self):
        # The number handed to Stripe is the one that has to clear the floor.
        # Checking the unit price would pass a cart of ten 10c items and then
        # fail at the provider anyway.
        assert "below_minimum_charge_error(amount_cents, currency)" in _code(BUY_NOW)
        assert "below_minimum_charge_error(total_minor, currency)" in _code(CART)
        assert "below_minimum_charge_error(amount, currency)" in _code(OFFERS)

    def test_the_refusal_lands_before_the_transaction_row_and_the_stock_hold(self):
        # Refusing after either one leaves a checkout_failed row and a reserved
        # unit behind on every tap of a button that can never work.
        for source, insert in (
            (_code(BUY_NOW), "INSERT INTO seller_transactions"),
            (_code(CART), "INSERT INTO seller_transactions"),
            (_code(OFFERS), "INSERT INTO seller_transactions"),
        ):
            assert source.index("below_minimum_charge_error(") < source.index(insert)
            assert source.index("below_minimum_charge_error(") < source.index("PaymentIntent.create(")

    def test_the_code_reaches_the_client_on_every_lane(self):
        # The native client maps the code, never the prose.
        assert 'error_code=below_minimum["code"]' in _code(BUY_NOW)
        assert 'code=below_minimum["code"]' in _code(CART)
        assert 'code=below_minimum["code"]' in _code(OFFERS)


class TestBuyNowNoLongerFlattensEveryFailure:
    def test_buy_now_classifies_the_provider_exception_like_the_other_lanes(self):
        # A decline, an unreachable Stripe and a misconfigured key were one
        # message and one status here, so no failure suggested its own remedy.
        code = _code(BUY_NOW)
        assert "classified = classify_provider_exception(exc)" in code
        assert 'api_error("Checkout could not be created.", 500' not in code
        assert 'classified["message"], classified["status"]' in code
        assert 'error_code=classified["code"]' in code

    def test_the_raw_exception_text_no_longer_ships_to_the_buyer(self):
        # `error=str(exc)` put the provider's free text on the response, which
        # can carry account and parameter detail. The {type, code, param}
        # fingerprint is the diagnosable part and is safe.
        code = _code(BUY_NOW)
        assert "error=str(exc)" not in code
        assert 'provider_error=classified["provider_error"]' in code

    def test_a_failed_provider_call_still_gives_the_stock_back(self):
        # This once asserted an inline `release_inventory_reservation(cur,
        # tx_id, now=now)` followed by the branch's own hand-rolled status
        # update. The pairing now lives in `settle_failed_transactions`, shared
        # with the webhook branches — and routing this branch through it also
        # gave it the `NOT IN ('paid','refunded')` guard it never had, so a late
        # webhook can no longer downgrade an order that settled in the meantime.
        code = _code(BUY_NOW)
        assert "settle_failed_transactions" in code
        assert "REASON_CHECKOUT_ERROR" in code
