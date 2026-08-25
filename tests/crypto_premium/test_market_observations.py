"""Tests for services.market_observations (stdlib unittest + sqlite3 only)."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

from services import market_observations as mo


NOW = datetime(2026, 8, 23, 12, 0, 0)


class MarketObservationsTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(os.unlink, self.db_path)
        self._orig_connect = mo._connect
        self._orig_ready = mo._SCHEMA_READY
        self._orig_prune_at = mo._LAST_PRUNE_AT
        mo._connect = self._connect
        mo._SCHEMA_READY = False
        # A one-element list holding the last prune *instant* (or None), which
        # is the merged module's representation — the throttle compares
        # datetimes against the caller's `now` rather than wall-clock seconds,
        # so a test can drive it deterministically.
        mo._LAST_PRUNE_AT = [None]
        self.addCleanup(self._restore)

    def _restore(self):
        mo._connect = self._orig_connect
        mo._SCHEMA_READY = self._orig_ready
        mo._LAST_PRUNE_AT = self._orig_prune_at

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _count(self):
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0]
        conn.close()
        return total

    # -- schema ------------------------------------------------------------

    def test_schema_is_idempotent(self):
        self.assertTrue(mo.ensure_observation_schema()["ok"])
        mo._SCHEMA_READY = False
        self.assertTrue(mo.ensure_observation_schema()["ok"])

    # -- recording ---------------------------------------------------------

    def test_record_and_unique_dedupe(self):
        first = mo.record_observation("btc", price=100.0, volume_24h=5e9, market_cap=2e12,
                                      source="test", observed_at=NOW)
        self.assertTrue(first["ok"])
        self.assertTrue(first["recorded"])
        self.assertEqual(first["asset_id"], "BTC")
        duplicate = mo.record_observation("BTC", price=101.0, observed_at=NOW)
        self.assertTrue(duplicate["ok"])
        self.assertFalse(duplicate["recorded"])
        self.assertEqual(self._count(), 1)

    def test_record_refuses_missing_price(self):
        # The refusal happens before any write; ensure the table exists so the
        # count below proves nothing was inserted (rather than erroring).
        mo.ensure_observation_schema()
        result = mo.record_observation("BTC", price=None, observed_at=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(self._count(), 0)

    def test_record_quote_shape(self):
        quote = {
            "ok": True,
            "source": "coingecko",
            "updated_at": NOW.isoformat(),
            "asset": {"symbol": "ETH", "price": 2000.0, "volume_24h": 1e9, "market_cap": 2e11},
        }
        result = mo.record_quote(quote)
        self.assertTrue(result["recorded"])
        self.assertFalse(mo.record_quote({"ok": False, "asset": {"symbol": "ETH"}})["ok"])
        # A quote without a real price records nothing.
        self.assertFalse(mo.record_quote({"ok": True, "asset": {"symbol": "SOL"}})["recorded"])

    # -- reading -----------------------------------------------------------

    def test_get_observations_range_limit_and_positional_call(self):
        for offset in range(5):
            mo.record_observation("BTC", price=100 + offset, observed_at=NOW - timedelta(hours=offset))
        # Positional (asset, start, end) — the exact call shape
        # services.portfolio_intelligence uses.
        rows = mo.get_observations(
            "BTC",
            (NOW - timedelta(hours=3)).isoformat(),
            NOW.isoformat(),
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["price"], 103.0)  # oldest first
        self.assertEqual(rows[-1]["price"], 100.0)
        self.assertEqual(rows[0]["symbol"], "BTC")
        # Keyword limit — the call shape services.undx_agent_tools uses.
        capped = mo.get_observations("BTC", limit=2)
        self.assertEqual(len(capped), 2)
        self.assertEqual([row["price"] for row in capped], [101.0, 100.0])
        self.assertEqual(mo.get_observations("DOGE"), [])
        self.assertEqual(mo.get_observations(""), [])

    # -- window-start lookup ----------------------------------------------

    def test_window_start_within_tolerance(self):
        # 60 minute window => +/-12 minute tolerance around NOW-60m.
        mo.record_observation("BTC", price=90.0, observed_at=NOW - timedelta(minutes=70))
        mo.record_observation("BTC", price=95.0, observed_at=NOW - timedelta(minutes=55))
        best = mo.window_start_observation("BTC", 60, now=NOW)
        self.assertIsNotNone(best)
        # NOW-55m is 5 minutes from target, NOW-70m is 10 minutes: nearest wins.
        self.assertEqual(best["price"], 95.0)

    def test_window_start_outside_tolerance_returns_none(self):
        mo.record_observation("BTC", price=90.0, observed_at=NOW - timedelta(minutes=90))
        self.assertIsNone(mo.window_start_observation("BTC", 60, now=NOW))
        self.assertIsNone(mo.window_start_observation("BTC", 0, now=NOW))
        self.assertIsNone(mo.window_start_observation("BTC", "bogus", now=NOW))

    def test_window_start_boundary_exactly_20_percent(self):
        # Exactly at the tolerance edge (12 minutes for a 60 minute window)
        # is still valid; one second beyond is not.
        mo.record_observation("BTC", price=90.0, observed_at=NOW - timedelta(minutes=72))
        self.assertIsNotNone(mo.window_start_observation("BTC", 60, now=NOW))
        conn = self._connect()
        conn.execute("DELETE FROM market_observations")
        conn.commit()
        conn.close()
        mo.record_observation("BTC", price=90.0, observed_at=NOW - timedelta(minutes=72, seconds=1))
        self.assertIsNone(mo.window_start_observation("BTC", 60, now=NOW))

    # -- retention ---------------------------------------------------------

    def test_prune_retention(self):
        mo.record_observation("BTC", price=1.0, observed_at=NOW - timedelta(days=8))
        mo.record_observation("BTC", price=2.0, observed_at=NOW - timedelta(days=6))
        mo.record_observation("BTC", price=3.0, observed_at=NOW)
        # The retention is stated rather than defaulted. What this test is about
        # is the boundary — older goes, newer stays — and pinning it to whatever
        # the module's default happens to be turns a prune test into an assertion
        # about a constant. That default is now RETENTION_HOURS (72h), sized
        # against the longest window either surface offers (1440 minutes), not
        # the 7 days this file was originally written against.
        result = mo.prune_observations(max_age_days=7, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(self._count(), 2)

    def test_maybe_prune_is_throttled(self):
        mo.record_observation("BTC", price=1.0, observed_at=NOW - timedelta(days=8))
        first = mo.maybe_prune_observations(now=NOW)
        self.assertEqual(first.get("deleted"), 1)
        mo.record_observation("BTC", price=1.5, observed_at=NOW - timedelta(days=9))
        second = mo.maybe_prune_observations(now=NOW)
        self.assertTrue(second.get("skipped"))
        self.assertEqual(self._count(), 1)


if __name__ == "__main__":
    unittest.main()
