"""Settling a cash / local-pickup Marketplace order by hand.

Card payments are paused for Marketplace, so a cash checkout writes a
``cash_pending`` transaction and nothing else ever moves it. This suite covers
the one route that closes it, and the two ways closing it could go wrong:
the wrong person calling it, and the stock hold being left open on a sold item.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="marketplace_cash_settlement_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


class CashSettlementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        self.now = datetime.utcnow().isoformat(timespec="seconds")
        self._real_api_account_user = bot.api_account_user
        self._real_require_account = bot.require_account
        self.seller = self._make_user("cash_seller")
        self.buyer = self._make_user("cash_buyer")
        self.listing_id = self._make_listing(self.seller)
        self.tx_id = self._make_cash_transaction()
        self._hold_stock(self.tx_id, quantity=2)

    def tearDown(self):
        bot.api_account_user = self._real_api_account_user
        bot.require_account = self._real_require_account

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def _make_user(self, username):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, display_name, email, account_status, created_at) VALUES (?,?,?,?,?)",
            (username, username, f"{username}@example.com", "active", self.now),
        )
        user_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return {"user_id": user_id, "username": username}

    def _make_listing(self, seller):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings (seller_user_id, title, price_label, currency, quantity, "
            "product_type, listing_type, status, approval_status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (int(seller["user_id"]), "Cash lamp", "$25.00", "USD", 3,
             "physical", "physical", "published", "approved", self.now, self.now),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def _make_cash_transaction(self, status="cash_pending"):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seller_transactions (buyer_user_id, seller_user_id, seller_type, item_type, item_id, "
            "amount_cents, currency, platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(self.buyer["user_id"]), int(self.seller["user_id"]), "merchant", "marketplace_product",
             self.listing_id, 5000, "USD", 0, 5000, status, '{"qty": 2}', self.now, self.now),
        )
        tx_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return tx_id

    def _hold_stock(self, tx_id, quantity):
        from services import marketplace_cart_routes as cart
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cart._ensure_schema(cur)
        cur.execute(
            "INSERT INTO marketplace_inventory_reservations (seller_transaction_id, listing_id, quantity, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (int(tx_id), int(self.listing_id), int(quantity), "held", self.now, self.now),
        )
        conn.commit()
        conn.close()

    @contextmanager
    def acting_as(self, user):
        previous_api, previous_require = bot.api_account_user, bot.require_account
        bot.api_account_user = lambda: dict(user)
        bot.require_account = lambda: dict(user)
        try:
            yield
        finally:
            bot.api_account_user, bot.require_account = previous_api, previous_require

    def settle(self, user, tx_id=None):
        with self.acting_as(user):
            return self.client.post(f"/api/pulse/orders/{tx_id or self.tx_id}/cash-collected", json={})

    def row(self, table, where, args):
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE {where} LIMIT 1", args)
        value = dict(cur.fetchone() or {})
        conn.close()
        return value

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------
    def test_seller_settles_a_cash_order_and_it_becomes_paid(self):
        response = self.settle(self.seller)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["already_settled"])
        self.assertEqual(body["payment_method"], "cash")
        self.assertEqual(self.row("seller_transactions", "id=?", (self.tx_id,))["status"], "paid")

    def test_the_buyer_cannot_mark_their_own_order_paid(self):
        # The buyer is holding the cash until handover. If they could close the
        # order themselves the seller would be recorded as paid for nothing.
        response = self.settle(self.buyer)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.row("seller_transactions", "id=?", (self.tx_id,))["status"], "cash_pending")

    def test_signed_out_callers_are_rejected(self):
        previous = bot.api_account_user
        bot.api_account_user = lambda: None
        try:
            response = self.client.post(f"/api/pulse/orders/{self.tx_id}/cash-collected", json={})
        finally:
            bot.api_account_user = previous
        self.assertEqual(response.status_code, 401)

    def test_settling_twice_converges_instead_of_double_settling(self):
        self.assertFalse(self.settle(self.seller).get_json()["already_settled"])
        second = self.settle(self.seller)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["already_settled"])
        self.assertEqual(self.row("seller_transactions", "id=?", (self.tx_id,))["status"], "paid")

    def test_a_card_order_cannot_be_settled_by_hand(self):
        # The whole point of the pause is that card money moves through Stripe.
        # A seller marking a `checkout_created` card order paid would fabricate
        # a payment that never arrived.
        card_tx = self._make_cash_transaction(status="checkout_created")
        response = self.settle(self.seller, card_tx)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json().get("error_code"), "NOT_A_CASH_ORDER")
        self.assertEqual(self.row("seller_transactions", "id=?", (card_tx,))["status"], "checkout_created")

    def test_settling_consumes_the_stock_hold_rather_than_leaving_it_open(self):
        # Mirrors the Stripe paid path. A reservation left `held` would be
        # released later and hand quantity back for an item already sold.
        self.settle(self.seller)
        reservation = self.row("marketplace_inventory_reservations", "seller_transaction_id=?", (self.tx_id,))
        self.assertEqual(reservation["status"], "captured")
        self.assertEqual(self.row("marketplace_listings", "id=?", (self.listing_id,))["quantity"], 3)

    def test_the_settled_order_is_projected_as_a_cash_order_not_a_stripe_one(self):
        self.settle(self.seller)
        order = self.row("marketplace_orders", "seller_transaction_id=?", (self.tx_id,))
        self.assertEqual(order["status"], "paid")
        self.assertEqual(order["payment_provider"], "cash")
        self.assertEqual(order["amount_cents"], 5000)


if __name__ == "__main__":
    unittest.main()
