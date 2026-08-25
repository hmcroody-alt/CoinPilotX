"""Tests for services.portfolio_intelligence (stdlib unittest + sqlite3 only).

The price feed and the database connection are both stubbed so no network or
production database is touched.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

from services import portfolio_intelligence as pi


BOARD = {
    "updated_at": "2026-08-23T10:00:00",
    "markets": [
        {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "price": 50000.0},
        {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "price": 2000.0},
        {"id": "solana", "symbol": "SOL", "name": "Solana", "price": 100.0},
    ],
}


class PortfolioIntelligenceTestCase(unittest.TestCase):
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
                user_id INTEGER,
                symbol TEXT,
                coin_name TEXT,
                amount REAL,
                average_buy_price REAL,
                notes TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                total_value REAL,
                holdings_json TEXT,
                created_at TEXT,
                total_cost REAL,
                pnl_value REAL,
                pnl_percent REAL
            );
            INSERT INTO users (user_id, email) VALUES (1, 'one@example.com');
            """
        )
        conn.commit()
        conn.close()

        self._orig_connect = pi._connect
        self._orig_board = pi._market_board
        pi._connect = self._connect
        pi._market_board = lambda: BOARD
        self.addCleanup(self._restore)

    def _restore(self):
        pi._connect = self._orig_connect
        pi._market_board = self._orig_board

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _add_item(self, user_id, symbol, amount, abp=0):
        conn = self._connect()
        conn.execute(
            "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, average_buy_price) VALUES (?, ?, ?, ?, ?)",
            (user_id, symbol, symbol, amount, abp),
        )
        conn.commit()
        conn.close()

    def _add_snapshot(self, user_id, total_value, created_at):
        conn = self._connect()
        conn.execute(
            "INSERT INTO portfolio_snapshots (user_id, total_value, holdings_json, created_at) VALUES (?, ?, '[]', ?)",
            (user_id, total_value, created_at),
        )
        conn.commit()
        conn.close()

    def _snapshot_count(self, user_id):
        conn = self._connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        conn.close()
        return count

    # ----- valuation -----

    def test_valuation_math_allocation_and_concentration(self):
        self._add_item(1, "BTC", 0.5, 40000)  # value 25000, cost 20000, pl 5000
        self._add_item(1, "ETH", 2, 0)  # value 4000, no buy price

        result = pi.compute_portfolio_valuation(1)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["total_value"], 29000.0)
        self.assertEqual(len(result["holdings"]), 2)

        btc = next(h for h in result["holdings"] if h["symbol"] == "BTC")
        eth = next(h for h in result["holdings"] if h["symbol"] == "ETH")
        self.assertEqual(btc["asset_id"], "bitcoin")
        self.assertEqual(btc["name"], "Bitcoin")
        self.assertAlmostEqual(btc["current_price"], 50000.0)
        self.assertAlmostEqual(btc["current_value"], 25000.0)
        self.assertAlmostEqual(btc["allocation_pct"], 25000.0 / 29000.0 * 100)
        self.assertAlmostEqual(btc["average_buy_price"], 40000.0)
        self.assertAlmostEqual(btc["unrealized_pl"], 5000.0)
        self.assertIsNone(eth["average_buy_price"])
        self.assertIsNone(eth["unrealized_pl"])
        self.assertAlmostEqual(eth["allocation_pct"], 4000.0 / 29000.0 * 100)

        # Unrealized total only covers holdings that have a buy price.
        self.assertAlmostEqual(result["unrealized_pl"], 5000.0)
        self.assertEqual(result["concentration"]["top_symbol"], "BTC")
        self.assertAlmostEqual(result["concentration"]["top_pct"], 25000.0 / 29000.0 * 100)
        self.assertEqual(result["market_data_observed_at"], "2026-08-23T10:00:00")
        self.assertTrue(result["calculated_at"])

    def test_unrealized_pl_none_when_no_buy_prices(self):
        self._add_item(1, "ETH", 3, 0)
        result = pi.compute_portfolio_valuation(1)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["unrealized_pl"])
        self.assertIsNone(result["holdings"][0]["unrealized_pl"])

    def test_change_24h_none_without_real_24h_data(self):
        self._add_item(1, "BTC", 1, 30000)
        # A fresh snapshot (2 minutes old) is NOT 24h-ago data.
        self._add_snapshot(1, 12345.0, (datetime.now() - timedelta(minutes=2)).isoformat())
        result = pi.compute_portfolio_valuation(1)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["change_24h_pct"])

    def test_change_24h_from_real_snapshot(self):
        self._add_item(1, "BTC", 0.5, 40000)
        self._add_item(1, "ETH", 2, 0)  # total now 29000
        self._add_snapshot(1, 20000.0, (datetime.now() - timedelta(hours=24)).isoformat())
        result = pi.compute_portfolio_valuation(1)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["change_24h_pct"], 45.0)

    def test_empty_portfolio_is_ok_with_zero_value(self):
        result = pi.compute_portfolio_valuation(1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_value"], 0.0)
        self.assertEqual(result["holdings"], [])
        self.assertIsNone(result["unrealized_pl"])
        self.assertIsNone(result["change_24h_pct"])
        self.assertIsNone(result["concentration"]["top_symbol"])

    def test_missing_user_fails(self):
        result = pi.compute_portfolio_valuation(999)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "user_not_found")

    def test_invalid_user_id_fails(self):
        self.assertFalse(pi.compute_portfolio_valuation(None)["ok"])
        self.assertFalse(pi.compute_portfolio_valuation("abc")["ok"])
        self.assertFalse(pi.compute_portfolio_valuation(-3)["ok"])

    def test_market_data_unavailable(self):
        self._add_item(1, "BTC", 1, 0)
        pi._market_board = lambda: None
        result = pi.compute_portfolio_valuation(1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "market_data_unavailable")

    def test_snapshot_append_on_read_once_per_hour(self):
        self._add_item(1, "BTC", 1, 40000)
        self.assertEqual(self._snapshot_count(1), 0)
        pi.compute_portfolio_valuation(1)
        self.assertEqual(self._snapshot_count(1), 1)
        # A second read inside the hour must not append another snapshot.
        pi.compute_portfolio_valuation(1)
        self.assertEqual(self._snapshot_count(1), 1)
        # An old latest snapshot allows a new append.
        conn = self._connect()
        conn.execute(
            "UPDATE portfolio_snapshots SET created_at=?",
            ((datetime.now() - timedelta(hours=2)).isoformat(),),
        )
        conn.commit()
        conn.close()
        pi.compute_portfolio_valuation(1)
        self.assertEqual(self._snapshot_count(1), 2)

    # ----- history -----

    def test_history_invalid_period(self):
        result = pi.get_portfolio_history(1, "3d")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_period")
        self.assertFalse(pi.get_portfolio_history(1, None)["ok"])

    def test_history_coverage_none(self):
        result = pi.get_portfolio_history(1, "7d")
        self.assertTrue(result["ok"])
        self.assertEqual(result["period"], "7d")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["coverage"], "none")

    def test_history_coverage_partial(self):
        now = datetime.now()
        # Snapshots only cover the last 2 days of a 7d window.
        for hours_ago in (48, 24, 1):
            self._add_snapshot(1, 1000.0 + hours_ago, (now - timedelta(hours=hours_ago)).isoformat())
        result = pi.get_portfolio_history(1, "7d")
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(len(result["points"]), 3)
        # Points are ordered oldest -> newest with real values.
        self.assertEqual([p["value"] for p in result["points"]], [1048.0, 1024.0, 1001.0])

    def test_history_coverage_full(self):
        now = datetime.now()
        for hours_ago in (167, 120, 72, 24, 1):
            self._add_snapshot(1, 500.0, (now - timedelta(hours=hours_ago)).isoformat())
        result = pi.get_portfolio_history(1, "7d")
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], "full")
        self.assertEqual(len(result["points"]), 5)

    def test_history_all_period_full_with_any_points(self):
        self._add_snapshot(1, 500.0, (datetime.now() - timedelta(days=400)).isoformat())
        result = pi.get_portfolio_history(1, "all")
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], "full")
        self.assertEqual(len(result["points"]), 1)
        # The 1y window excludes the 400-day-old snapshot entirely.
        year = pi.get_portfolio_history(1, "1y")
        self.assertEqual(year["coverage"], "none")
        self.assertEqual(year["points"], [])

    def test_history_missing_user_fails(self):
        result = pi.get_portfolio_history(42, "7d")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "user_not_found")

    def test_history_period_case_insensitive(self):
        self._add_snapshot(1, 500.0, (datetime.now() - timedelta(hours=1)).isoformat())
        result = pi.get_portfolio_history(1, "24H")
        self.assertTrue(result["ok"])
        self.assertEqual(result["period"], "24h")


if __name__ == "__main__":
    unittest.main()
