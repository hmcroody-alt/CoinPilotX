"""Focused cart eligibility and inventory reservation tests."""

import pathlib
import sqlite3

from services import marketplace_cart_routes as cart

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def public_listing(**overrides):
    value = {
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "product_type": "physical",
        "quantity": 3,
    }
    value.update(overrides)
    return value


def test_checkout_line_rejects_non_public_suspended_and_unapproved_seller():
    line = {"qty": 1, "price_snapshot_minor": 1200}
    assert cart._line_state(line, public_listing(), 1200) == "available"
    assert cart._line_state(line, public_listing(status="pending_review"), 1200) == "restricted"
    assert cart._line_state(line, public_listing(status="suspended"), 1200) == "restricted"
    assert cart._line_state(line, public_listing(seller_status="suspended"), 1200) == "restricted"


def test_checkout_line_enforces_stock_and_server_price():
    line = {"qty": 2, "price_snapshot_minor": 1200}
    assert cart._line_state(line, public_listing(quantity=1), 1200) == "sold"
    assert cart._line_state(line, public_listing(quantity=3), 1400) == "price_changed"


def test_inventory_release_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings (id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        seller_transaction_id INTEGER UNIQUE, listing_id INTEGER, quantity INTEGER,
        status TEXT, updated_at TEXT)""")
    cur.execute("INSERT INTO marketplace_listings VALUES (7, 1, '')")
    cur.execute("INSERT INTO marketplace_inventory_reservations VALUES (10, 7, 2, 'held', '')")

    cart.release_inventory_reservation(cur, 10, now="2026-08-11T00:00:00")
    cart.release_inventory_reservation(cur, 10, now="2026-08-11T00:00:01")

    assert cur.execute("SELECT quantity FROM marketplace_listings WHERE id=7").fetchone()[0] == 3
    assert cur.execute("SELECT status FROM marketplace_inventory_reservations WHERE seller_transaction_id=10").fetchone()[0] == "released"
    conn.close()


def _routing_bot():
    """Stands in for the bot module, which owns the payout-routing decision.

    Importing ``bot`` would pull in the whole Flask monolith for what is a pure
    function, so the cart contract is exercised against a mirror of it.
    """
    import types

    return types.SimpleNamespace(seller_destination_account_id=_seller_destination_account_id)


def _seller_destination_account_id(payout):
    """Mirror of ``bot.seller_destination_account_id``."""
    payout = dict(payout or {})
    account_id = str(payout.get("connected_account_id") or payout.get("provider_account_id") or "").strip()
    if not account_id:
        return ""

    def _enabled(value):
        return str(value).strip().lower() in {"1", "true", "yes", "t", "on"}

    if not (_enabled(payout.get("charges_enabled")) and _enabled(payout.get("payouts_enabled"))):
        return ""
    if str(payout.get("onboarding_status") or "").strip().lower() in {
        "onboarding_started", "pending", "restricted", "disabled", "rejected"
    }:
        return ""
    return account_id


def test_stripe_wiring_supports_platform_and_connect_charges():
    bot = _routing_bot()
    platform, account = cart._stripe_payment_intent_data(
        bot=bot, tx_ids=[11, 12], buyer_id=5, platform_fee=300, payout={}
    )
    assert account == ""
    assert platform["metadata"]["seller_transaction_ids"] == "11,12"
    assert "transfer_data" not in platform
    assert "application_fee_amount" not in platform

    destination, account = cart._stripe_payment_intent_data(
        bot=bot, tx_ids=[11], buyer_id=5, platform_fee=300,
        payout={
            "connected_account_id": "acct_test_contract",
            "charges_enabled": 1,
            "payouts_enabled": 1,
            "onboarding_status": "complete",
        },
    )
    assert account == "acct_test_contract"
    assert destination["transfer_data"]["destination"] == "acct_test_contract"
    assert destination["application_fee_amount"] == 300


def test_unfinished_seller_onboarding_still_lets_the_buyer_pay():
    # A payout row is written the moment a seller *starts* Connect onboarding,
    # so it carries a real account id while charges_enabled is still 0. Routing
    # a destination charge there makes Stripe reject the session — turning the
    # seller's paperwork into a buyer-facing checkout failure. The buyer must
    # fall through to a platform charge instead.
    bot = _routing_bot()
    started, account = cart._stripe_payment_intent_data(
        bot=bot, tx_ids=[11], buyer_id=5, platform_fee=300,
        payout={
            "connected_account_id": "acct_started_not_enabled",
            "charges_enabled": 0,
            "payouts_enabled": 0,
            "onboarding_status": "onboarding_started",
        },
    )
    assert account == ""
    assert "transfer_data" not in started
    assert "application_fee_amount" not in started
    # The buyer's money still moves; the seller is paid from the ledger.
    assert started["metadata"]["seller_transaction_ids"] == "11"


def test_buy_now_shares_the_same_payout_routing_rule():
    # The cart pack calls bot.seller_destination_account_id and the buy-now
    # route uses it directly, so the two lanes cannot diverge. Guard the name
    # and the capability check the mirror above depends on.
    source = (REPO_ROOT / "bot.py").read_text(encoding="utf-8", errors="ignore")
    assert "def seller_destination_account_id(payout):" in source
    assert 'payout.get("charges_enabled")' in source
    assert 'payout.get("payouts_enabled")' in source
    assert "connected_account_id = seller_destination_account_id(payout)" in source


def test_cart_upsert_avoids_sqlite_only_min():
    # MIN(a, b) is a SQLite scalar; PostgreSQL's min() is a one-argument
    # aggregate and aggregates are illegal in ON CONFLICT DO UPDATE SET. That
    # form passed locally and 500'd in production, so it must not come back.
    source = (REPO_ROOT / "services" / "marketplace_cart_routes.py").read_text(encoding="utf-8")
    # The prose above the statement names both dialects, so only executable
    # lines are searched.
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "MIN(qty" not in code
    assert "LEAST(" not in code  # Postgres-only in the other direction
    assert "CASE WHEN marketplace_cart_items.qty + excluded.qty" in code


def test_shipping_address_is_collected_only_for_shipped_orders(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SHIPPING_COUNTRIES", "US,CA,invalid,1")
    assert cart.stripe_shipping_checkout_params(["pickup"]) == {}
    assert cart.stripe_shipping_checkout_params(["shipping"]) == {
        "shipping_address_collection": {"allowed_countries": ["US", "CA"]}
    }
    assert cart.stripe_shipping_checkout_params(["both"])["shipping_address_collection"]["allowed_countries"] == ["US", "CA"]


def test_a_seller_offering_both_lanes_is_not_silently_resolved_to_shipping():
    # "Local pickup or shipping" was being collapsed to shipping before the
    # buyer ever saw a choice, which is how someone collecting in person ends up
    # entering a delivery address. The undecided lane has to survive to checkout.
    assert cart._fulfillment({"delivery_type": "both"}) == "both"
    assert cart._fulfillment({"delivery_type": "pickup_or_shipping"}) == "both"
    assert cart._fulfillment({"delivery_type": "pickup"}) == "pickup"
    assert cart._fulfillment({"delivery_type": "shipping"}) == "shipping"
    assert cart._fulfillment({"delivery_type": "digital"}) == "digital"


def test_checkout_refuses_to_guess_the_buyers_fulfillment_choice():
    # The refusal is what makes the choice real: without it an omitted answer
    # would default to shipping and collect an address nobody asked for.
    source = (REPO_ROOT / "services" / "marketplace_cart_routes.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert 'fulfillment_choice = str(payload.get("fulfillment")' in code
    assert 'code="FULFILLMENT_REQUIRED"' in code
    # Stripe must be handed the *resolved* lanes, never the raw ones — passing
    # `both` through would collect an address for a pickup buyer.
    assert "stripe_shipping_checkout_params(resolved_lanes)" in code
    assert 'stripe_shipping_checkout_params(l["fulfillment"] for l in lines)' not in code


def test_the_stripe_session_adds_nothing_to_the_displayed_subtotal():
    # The checkout screen tells the buyer the amount it shows is the amount
    # charged. That promise is only true while the session carries no shipping
    # options and no automatic tax, so the promise is pinned to the code here.
    source = (REPO_ROOT / "services" / "marketplace_cart_routes.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "shipping_options" not in code
    assert "automatic_tax" not in code
    assert '"unit_amount": l["price_snapshot_minor"]' in code
