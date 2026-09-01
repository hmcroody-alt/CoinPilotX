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
import re

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
    # the catalogue. It now does so through the shared settlement path, which
    # is how this lane picked up the settled-order guard and the release reason
    # its own inline copy had been missing.
    assert "settle_failed_transactions" in code
    assert "REASON_CHECKOUT_ERROR" in code


def test_the_offers_lane_imports_the_cart_helpers_rather_than_copying_them():
    # Three checkout entry points with three private reservation
    # implementations is how one of them drifts.
    # The identity check follows the helper the offers lane actually settles
    # through. That used to be `release_inventory_reservation`; it is now
    # `settle_failed_transactions`, which wraps it together with the guarded
    # terminal-status write, so this asserts the same "no private copy"
    # property one layer up.
    assert offers.settle_failed_transactions is cart.settle_failed_transactions
    assert offers.stripe_shipping_checkout_params is cart.stripe_shipping_checkout_params
    assert offers._listing_metadata is cart._listing_metadata


def test_a_pickup_only_offer_is_never_asked_for_a_delivery_address():
    # Stripe is asked for an address only when PulseSoc has none: an order whose
    # address the buyer typed on the review step must not be asked again.
    assert "stripe_shipping_checkout_params([fulfillment_kind])" in _code(OFFERS)
    assert "{} if stripe_shipping_object" in _code(OFFERS)


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
    #
    # Two guards satisfy the invariant. The Stripe branches name the states they
    # refuse; the cash settlement route instead requires the row to still be
    # `cash_pending`, which is a strictly narrower predicate — it admits exactly
    # one state, and that state is neither paid nor refunded.
    code = " ".join(_code(BOT).split())
    needle = "seller_transactions SET status='paid'"
    guards = ("status NOT IN ('paid','refunded')", "status='cash_pending'")
    start = 0
    found = 0
    while True:
        at = code.find(needle, start)
        if at < 0:
            break
        found += 1
        statement = code[at:at + 260]
        assert any(guard in statement for guard in guards), statement
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
    #
    # This used to assert on a literal `release_inventory_reservation(cur,
    # tx_id, now=now)` call sitting inline in the branch. That call is gone on
    # purpose: four webhook branches each carried their own copy of "release the
    # hold, then move the transaction to a terminal status", and they are now a
    # single shared `settle_failed_transactions`. The property this test exists
    # to defend — a decline on the single-transaction lane gives the stock back
    # — is unchanged, so the assertion follows the call to where it moved rather
    # than pinning the old shape.
    single_lane_releases = [
        call for call in re.findall(
            r"marketplace_cart_service\.settle_failed_transactions\((?:[^()]|\([^()]*\))*\)", code)
        if "[tx_id]" in call and "REASON_PAYMENT_FAILED" in call
    ]
    assert single_lane_releases, "single-transaction decline lane no longer releases stock"


def test_the_sheet_header_names_the_store_not_the_account_holder():
    # Same canonical identity rule as every other buyer surface: the merchant
    # name on the sheet comes from marketplace_sellers, never from users.
    assert "seller_identity.display_store_name(lines[0])" in _code(CART)
    assert "seller_identity.display_store_name(listing)" in _code(OFFERS)
    assert "marketplace_seller_identity.display_store_name(item)" in _code(BOT)
    for source in (_code(CART), _code(OFFERS)):
        assert "merchant_display_name" in source
        assert "u.display_name" not in source
