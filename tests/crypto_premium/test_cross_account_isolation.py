"""Cross-account isolation proofs for the premium crypto surfaces.

User A must never see user B's holdings, totals, cost basis, alert rules or
alert history. Runs on stdlib unittest + sqlite3 only (no flask/pytest).
"""

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from services import alert_engine as ae
from services import portfolio_intelligence as pi

USER_A = 1
USER_B = 2

BOARD = {
    "updated_at": "2026-08-23T10:00:00",
    "markets": [
        {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "price": 50000.0},
        {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "price": 2000.0},
    ],
}


class CrossAccountIsolationTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(os.unlink, self.db_path)

        conn = self._connect()
        conn.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, email TEXT);
            CREATE TABLE portfolio_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, symbol TEXT, coin_name TEXT,
                amount REAL, average_buy_price REAL, notes TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, total_value REAL, holdings_json TEXT,
                created_at TEXT, total_cost REAL, pnl_value REAL, pnl_percent REAL
            );
            INSERT INTO users VALUES (1, 'a@example.com'), (2, 'b@example.com');
            INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, average_buy_price)
                VALUES (1, 'BTC', 'Bitcoin', 1.0, 40000.0),
                       (2, 'ETH', 'Ethereum', 10.0, 1000.0);
            INSERT INTO portfolio_snapshots (user_id, total_value, holdings_json, created_at)
                VALUES (1, 48000.0, '[]', '2026-08-20T00:00:00'),
                       (2, 19000.0, '[]', '2026-08-20T00:00:00');
            """
        )
        conn.commit()
        conn.close()

        # portfolio_intelligence -> scratch sqlite + stub board
        self._orig_pi_connect = pi._connect
        self._orig_pi_board = pi._market_board
        pi._connect = self._connect
        pi._market_board = lambda: BOARD

        # alert_engine -> the same scratch sqlite
        self._orig_user_context = ae.user_context
        ae.user_context = SimpleNamespace(
            connect=self._connect,
            row_to_dict=lambda row: dict(row) if row else None,
            get_user_by_id=lambda user_id: {"user_id": user_id},
        )
        self._orig_schema_ready = ae._ALERT_SCHEMA_READY
        ae._ALERT_SCHEMA_READY = False

        self.addCleanup(self._restore)

    def _restore(self):
        pi._connect = self._orig_pi_connect
        pi._market_board = self._orig_pi_board
        ae.user_context = self._orig_user_context
        ae._ALERT_SCHEMA_READY = self._orig_schema_ready

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- portfolio

    def test_valuation_scoped_to_owner(self):
        a = pi.compute_portfolio_valuation(USER_A)
        b = pi.compute_portfolio_valuation(USER_B)
        self.assertTrue(a["ok"] and b["ok"])
        self.assertEqual([h["symbol"] for h in a["holdings"]], ["BTC"])
        self.assertEqual([h["symbol"] for h in b["holdings"]], ["ETH"])
        self.assertEqual(a["total_value"], 50000.0)
        self.assertEqual(b["total_value"], 20000.0)
        # No leakage of the other user's cost basis anywhere in the payload.
        self.assertNotIn("1000.0", repr(a))
        self.assertNotIn("40000.0", repr(b))

    def test_history_scoped_to_owner(self):
        a = pi.get_portfolio_history(USER_A, "30d")
        b = pi.get_portfolio_history(USER_B, "30d")
        a_vals = {p["value"] for p in a.get("points", [])}
        b_vals = {p["value"] for p in b.get("points", [])}
        self.assertNotIn(19000.0, a_vals)
        self.assertNotIn(48000.0, b_vals)

    # ---------------------------------------------------------------- alerts

    def test_alert_rules_and_history_scoped_to_owner(self):
        created = ae.create_mobile_crypto_alert(
            USER_A,
            {
                "asset_id": "bitcoin",
                "symbol": "BTC",
                "name": "Bitcoin",
                "conditions": [{"type": "price_above", "threshold": 60000}],
            },
            has_premium=False,
        )
        self.assertTrue(created.get("ok"), created)

        a_list = ae.list_mobile_crypto_alerts(USER_A)
        b_list = ae.list_mobile_crypto_alerts(USER_B)
        self.assertEqual(len(a_list["items"]), 1)
        self.assertEqual(b_list["items"], [])

        alert_id = a_list["items"][0]["id"]
        # B cannot read, mutate or delete A's rule.
        b_hist = ae.list_mobile_alert_history(USER_B, alert_id=alert_id)
        self.assertEqual(b_hist["items"], [])
        upd = ae.update_mobile_crypto_alert(USER_B, alert_id, {"enabled": False}, has_premium=True)
        self.assertFalse(upd.get("ok"))
        dele = ae.delete_mobile_crypto_alert(USER_B, alert_id)
        self.assertFalse(dele.get("ok"))
        # A's rule is untouched after B's attempts.
        again = ae.list_mobile_crypto_alerts(USER_A)
        self.assertEqual(len(again["items"]), 1)
        self.assertTrue(again["items"][0]["enabled"])


if __name__ == "__main__":
    unittest.main()
