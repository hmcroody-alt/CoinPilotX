"""Focused cart eligibility and inventory reservation tests."""

import sqlite3

from services import marketplace_cart_routes as cart


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


def test_stripe_wiring_supports_platform_and_connect_charges():
    platform, account = cart._stripe_payment_intent_data(
        tx_ids=[11, 12], buyer_id=5, platform_fee=300, payout={}
    )
    assert account == ""
    assert platform["metadata"]["seller_transaction_ids"] == "11,12"
    assert "transfer_data" not in platform
    assert "application_fee_amount" not in platform

    destination, account = cart._stripe_payment_intent_data(
        tx_ids=[11], buyer_id=5, platform_fee=300,
        payout={"connected_account_id": "acct_test_contract"},
    )
    assert account == "acct_test_contract"
    assert destination["transfer_data"]["destination"] == "acct_test_contract"
    assert destination["application_fee_amount"] == 300
