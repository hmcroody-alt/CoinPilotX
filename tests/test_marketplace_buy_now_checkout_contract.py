"""Focused regression contract for the physical-goods Buy Now path.

This route lives in the legacy monolith, so the safest narrow test inspects the
function body without importing the full application and starting its workers.
The behavior itself reuses the already unit-tested reservation helpers.
"""

import sqlite3
from pathlib import Path

from services import marketplace_cart_routes as cart
from services import marketplace_reservation_policy as reservation_policy

BOT = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
START = BOT.index("def api_pulse_payments_checkout():")
END = BOT.index("\ndef _creator_checkout_for_item", START)
CHECKOUT = BOT[START:END]


def test_native_physical_goods_are_not_rejected_by_blanket_ios_gate():
    prefix = CHECKOUT[: CHECKOUT.index('payload = request.get_json')]
    assert "ios_paid_digital_unavailable_response" not in prefix
    assert 'item_type != "marketplace_product"' in CHECKOUT


def test_buy_now_is_idempotent_and_reserves_inventory():
    """The hold exists; how many units it holds is asserted by running the route.

    This test used to end with ``assert "quantity=quantity-1" in CHECKOUT`` — it
    read the route's source, found the hardcoded 1, and went green. That literal
    *was* the defect: Buy Now priced and reserved exactly one unit no matter what
    the buyer picked, so the assertion's subject and the bug were the same
    string. A test that pins source text cannot tell a contract from a mistake,
    because it never asks the route a question.

    What the shelf actually moves by now lives in
    ``tests/test_marketplace_buy_now_quantity.py``, which posts to the route and
    counts the stock afterwards.
    """
    assert 'idempotency_key = str(payload.get("idempotency_key")' in CHECKOUT
    assert "marketplace_cart_checkout_keys" in CHECKOUT
    assert "marketplace_inventory_reservations" in CHECKOUT


def test_buy_now_hands_a_failed_payment_to_the_shared_release_path():
    """The release moved out of this route; the guarantee did not.

    This file used to assert ``release_inventory_reservation`` appeared in the
    route body. Four webhook branches each carried their own copy of "release the
    hold, then mark the order failed", and they were consolidated into
    ``settle_failed_transactions`` with buy-now moved onto it. The literal left
    the route while the behaviour got strictly better — so the assertion failed
    and reported a missing inventory release on a path that has one, which is a
    worse outcome than no test at all.

    Pinning the new name would only move the trap. So the route is checked for
    the delegation, and the promise the delegation makes is checked by running
    it: reserve a unit, fail the payment, see the unit come back.
    """
    assert "settle_failed_transactions" in CHECKOUT
    assert "REASON_CHECKOUT_ERROR" in CHECKOUT

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings (id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        seller_transaction_id INTEGER UNIQUE, listing_id INTEGER, quantity INTEGER,
        status TEXT, updated_at TEXT)""")
    cur.execute("""CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, status TEXT, metadata_json TEXT, updated_at TEXT)""")
    # One unit sold from a shelf of three, held against transaction 10.
    cur.execute("INSERT INTO marketplace_listings VALUES (7, 2, '')")
    cur.execute("INSERT INTO marketplace_inventory_reservations VALUES (10, 7, 1, 'held', '')")
    cur.execute("INSERT INTO seller_transactions VALUES (10, 'checkout_created', NULL, '')")

    cart.settle_failed_transactions(
        cur, [10], reason=reservation_policy.REASON_CHECKOUT_ERROR,
        terminal_status="checkout_failed", now="2026-09-10T00:00:00")

    assert cur.execute("SELECT quantity FROM marketplace_listings WHERE id=7").fetchone()[0] == 3, \
        "the card was not charged and the unit did not come back"
    assert cur.execute(
        "SELECT status FROM marketplace_inventory_reservations WHERE seller_transaction_id=10"
    ).fetchone()[0] == "released"
    assert cur.execute("SELECT status FROM seller_transactions WHERE id=10").fetchone()[0] == "checkout_failed"
    conn.close()


def test_buy_now_reuses_cart_webhook_reconciliation_contract():
    assert '"cart_checkout": "1"' in CHECKOUT
    assert '"seller_transaction_ids": str(tx_id)' in CHECKOUT
    assert '"listing_ids": str(item_id)' in CHECKOUT
    # ``quantities`` used to be pinned to the literal "1" here. It is a Stripe
    # dashboard record with three writers and no readers — nothing reconciles
    # against it — so what belongs in the shared contract is that the key is
    # sent, not that it is sent wrong. The number that actually returns units to
    # the shelf is ``marketplace_inventory_reservations.quantity``, exercised
    # above.
    assert '"quantities": str(buy_quantity)' in CHECKOUT
    assert "marketplace-buy-now:" in CHECKOUT
