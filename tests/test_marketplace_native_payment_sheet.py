"""Contracts for the in-app Stripe PaymentSheet checkout.

The buyer taps "Pay securely · $5.00" and must stay inside PulseSoc. That
requires a PaymentIntent (whose client secret the native sheet can present)
rather than a hosted Checkout Session (whose URL the phone can only open in
Safari). These tests pin the three properties that make the swap safe:

1. All three checkout lanes — buy now, cart, accepted offer — build the intent
   from the *same* eligibility, price, inventory and Connect-routing code. A
   fourth private copy is how one lane silently oversells or reintroduces a
   stale onboarding gate.
2. The sheet is settled by `payment_intent.succeeded`, which therefore has to do
   everything `checkout.session.completed` already did.
3. A marketplace intent can never be mistaken for a Premium subscription
   payment.

These are source-text contracts. The code they guard talks to live Stripe and a
live database, so it cannot be executed here; what can be proven statically is
that the dangerous shapes are absent and the shared calls are present.
"""

import pathlib

from services import marketplace_cart_routes as cart
from services import marketplace_offers_routes as offers

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

BOT = (REPO_ROOT / "bot.py").read_text(encoding="utf-8", errors="ignore")
CART = (REPO_ROOT / "services" / "marketplace_cart_routes.py").read_text(encoding="utf-8")
OFFERS = (REPO_ROOT / "services" / "marketplace_offers_routes.py").read_text(encoding="utf-8")


def _code(source: str) -> str:
    """Source with comment lines dropped, so prose cannot satisfy an assertion."""
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


def test_every_checkout_lane_can_return_a_native_payment_intent():
    # One opt-in flag, spelled identically, so the client has one contract.
    for source in (_code(BOT), _code(CART), _code(OFFERS)):
        assert 'payload.get("payment_mode")' in source
        assert '== "payment_sheet"' in source
        assert "PaymentIntent.create(" in source


def test_the_sheet_bootstrap_carries_what_initpaymentsheet_needs():
    # Missing any one of these makes initPaymentSheet fail at presentation
    # rather than degrade, which the buyer experiences as a dead Pay button.
    for source in (_code(BOT), _code(CART), _code(OFFERS)):
        assert '"payment_intent_client_secret"' in source
        assert '"publishable_key"' in source
        assert '"merchant_display_name"' in source
        assert '"apple_pay_merchant_id"' in source


def test_apple_pay_is_announced_only_when_the_binary_can_honour_it():
    # An unset merchant id must stay empty. Substituting a plausible-looking
    # default would advertise Apple Pay the signed app has no entitlement for,
    # and the sheet would fail instead of offering the card form.
    import os

    previous = os.environ.pop("APPLE_PAY_MERCHANT_ID", None)
    try:
        assert cart._apple_pay_merchant_id() == ""
        os.environ["APPLE_PAY_MERCHANT_ID"] = "  merchant.com.pulsesoc.app  "
        assert cart._apple_pay_merchant_id() == "merchant.com.pulsesoc.app"
    finally:
        os.environ.pop("APPLE_PAY_MERCHANT_ID", None)
        if previous is not None:
            os.environ["APPLE_PAY_MERCHANT_ID"] = previous


def test_the_amount_is_the_servers_not_the_clients():
    # The sheet renders the amount; it never supplies it. If a client-supplied
    # number could reach PaymentIntent.create, a tampered request would charge
    # a price the seller never set.
    assert "amount=total_minor," in _code(CART)
    assert "amount=amount," in _code(OFFERS)
    assert "amount=amount_cents," in _code(BOT)


def test_the_offers_lane_uses_the_shared_connect_capability_check():
    # The stale gate read the raw account id, which exists from the moment
    # onboarding *starts*. Stripe then rejects the transfer and the buyer sees
    # "Checkout could not be created." Seller paperwork is not a buyer
    # prerequisite — the shared check falls back to a platform charge.
    code = _code(OFFERS)
    assert "connected_account_id = bot.seller_destination_account_id(payout)" in code
    assert 'payout.get("connected_account_id") or payout.get("provider_account_id")' not in code
    # And the answer is reported, so the ledger fallback is visible rather than
    # inferred from a silent absence.
    assert '"payout_state": payout_state' in code


def test_the_offers_lane_reserves_stock_like_every_other_lane():
    # Two accepted offers on a one-of-a-kind item could both reach Stripe while
    # this lane skipped reservation entirely.
    code = _code(OFFERS)
    assert "marketplace_inventory_reservations" in code
    assert "quantity=quantity-?" in code
    assert 'code="OUT_OF_STOCK"' in code
    # Failure has to give the stock back, or a Stripe outage quietly empties
    # the catalogue.
    assert "release_inventory_reservation(cur, tx_id, now=now)" in code


def test_the_offers_lane_imports_the_cart_helpers_rather_than_copying_them():
    # Three checkout entry points with three private reservation
    # implementations is how one of them drifts.
    assert offers.release_inventory_reservation is cart.release_inventory_reservation
    assert offers.stripe_shipping_checkout_params is cart.stripe_shipping_checkout_params
    assert offers._fulfillment is cart._fulfillment


def test_a_pickup_only_offer_is_never_asked_for_a_delivery_address():
    assert "stripe_shipping_checkout_params([fulfillment])" in _code(OFFERS)


def test_payment_intent_success_settles_marketplace_orders_completely():
    # The hosted session branch captured inventory and created the order. The
    # sheet settles through payment_intent.succeeded instead, so that branch
    # has to do the same work or paid orders never appear and stock stays held.
    code = _code(BOT)
    assert 'marketplace_pi = metadata.get("cart_checkout") == "1" and metadata.get("seller_transaction_ids")' in code
    assert code.count("marketplace_cart_service.capture_inventory_reservation(cur, tx_id, now=now)") >= 2


def test_a_replayed_success_event_cannot_resurrect_a_refunded_order():
    # The old singular branch set status='paid' unconditionally, so a duplicate
    # delivery of the original event would walk a refund backwards.
    # Whitespace is collapsed because these statements wrap across lines, and a
    # per-line search would read the guard as missing.
    code = " ".join(_code(BOT).split())
    needle = "seller_transactions SET status='paid'"
    start = 0
    found = 0
    while True:
        at = code.find(needle, start)
        if at < 0:
            break
        found += 1
        statement = code[at:at + 260]
        assert "status NOT IN ('paid','refunded')" in statement, statement
        start = at + 1
    assert found >= 2


def test_a_marketplace_intent_never_grants_premium_pro():
    # Cart intents carry only the plural key, so neither legacy branch matched
    # and control reached activate_pro: paying $5 for a ball upgraded the
    # buyer's subscription. The guard stops that before the fall-through.
    code = _code(BOT)
    guard = 'if metadata.get("item_type") == "marketplace_product" or metadata.get("listing_ids") or metadata.get("cart_checkout") == "1":'
    assert guard in code
    assert code.index(guard) < code.index('customer_id = payment_intent.get("customer") or ""')


def test_a_declined_delayed_payment_releases_held_stock():
    # Delayed payment methods hold stock from checkout creation. Without this
    # event a decline never releases it and the listing is invisibly sold out.
    code = _code(BOT)
    assert '"checkout.session.async_payment_failed"' in code
    assert 'terminal_status = "checkout_expired" if event_type == "checkout.session.expired" else "checkout_failed"' in code
    # And a plain card decline on a single-transaction lane must release too.
    assert code.count("marketplace_cart_service.release_inventory_reservation(cur, tx_id, now=now)") >= 1


def test_the_sheet_header_names_the_store_not_the_account_holder():
    # Same canonical identity rule as every other buyer surface: the merchant
    # name on the sheet comes from marketplace_sellers, never from users.
    assert "seller_identity.display_store_name(lines[0])" in _code(CART)
    assert "seller_identity.display_store_name(listing)" in _code(OFFERS)
    assert "marketplace_seller_identity.display_store_name(item)" in _code(BOT)
    for source in (_code(CART), _code(OFFERS)):
        assert "merchant_display_name" in source
        assert "u.display_name" not in source
