"""Tests for the premium advanced-alert workstream in services.alert_engine.

Runs on stdlib unittest + sqlite3 only. Everything with a network or
notification side effect is stubbed:

* ``alert_engine.user_context`` -> a scratch sqlite database per test
* ``alert_engine.live_market_service`` -> canned quote payloads
* ``alert_engine.dispatch_alert_event`` / ``channel_warnings`` -> no-ops
* ``services.crypto_premium_gate.has_crypto_capability`` -> a boolean flag
* ``services.portfolio_intelligence.compute_portfolio_valuation`` -> canned dict
"""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from services import alert_engine as ae
from services import crypto_premium_gate as gate
from services import market_observations as mo
from services import portfolio_intelligence as pi


USER_ID = 42


class AdvancedAlertEngineTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(os.unlink, self.db_path)

        # Behaviour knobs the stubs consult.
        self.premium = True
        self.market = {"price": 100.0, "volume_24h": 5e9, "market_cap": 2e12}
        self.quote_ok = True
        self.portfolio = {"ok": False}

        # alert_engine -> scratch sqlite
        self._orig_user_context = ae.user_context
        ae.user_context = SimpleNamespace(
            connect=self._connect,
            row_to_dict=lambda row: dict(row) if row else None,
        )
        self._orig_schema_ready = ae._ALERT_SCHEMA_READY
        ae._ALERT_SCHEMA_READY = False

        # market_observations -> the same scratch sqlite
        self._orig_mo_connect = mo._connect
        self._orig_mo_ready = mo._SCHEMA_READY
        self._orig_mo_prune = mo._LAST_PRUNE_AT
        mo._connect = self._connect
        mo._SCHEMA_READY = False
        mo._LAST_PRUNE_AT = 0.0

        # Quotes, delivery and channel checks -> stubs
        self._orig_live_market = ae.live_market_service
        ae.live_market_service = SimpleNamespace(get_crypto_quote=self._get_quote)
        self._orig_dispatch = ae.dispatch_alert_event
        ae.dispatch_alert_event = lambda event, rule=None: {"ok": True, "status": "sent"}
        self._orig_warnings = ae.channel_warnings
        ae.channel_warnings = lambda user_id, channels: []

        # Premium gate + portfolio valuation -> flags on self
        self._orig_capability = gate.has_crypto_capability
        gate.has_crypto_capability = lambda user_id, capability: self.premium
        self._orig_valuation = pi.compute_portfolio_valuation
        pi.compute_portfolio_valuation = lambda user_id: self.portfolio

        self.addCleanup(self._restore)

    def _restore(self):
        ae.user_context = self._orig_user_context
        ae._ALERT_SCHEMA_READY = self._orig_schema_ready
        mo._connect = self._orig_mo_connect
        mo._SCHEMA_READY = self._orig_mo_ready
        mo._LAST_PRUNE_AT = self._orig_mo_prune
        ae.live_market_service = self._orig_live_market
        ae.dispatch_alert_event = self._orig_dispatch
        ae.channel_warnings = self._orig_warnings
        gate.has_crypto_capability = self._orig_capability
        pi.compute_portfolio_valuation = self._orig_valuation

    # -- plumbing ----------------------------------------------------------

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_quote(self, symbol):
        if not self.quote_ok:
            return {"ok": False, "message": "quote unavailable"}
        return {
            "ok": True,
            "source": "stub",
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "asset": {
                "symbol": symbol,
                "price": self.market.get("price"),
                "volume_24h": self.market.get("volume_24h"),
                "market_cap": self.market.get("market_cap"),
                "change_24h": self.market.get("change_24h"),
            },
        }

    def _create_advanced(self, conditions, match="all", frequency="every_crossing",
                         symbol="BTC", cooldown_seconds=0, has_premium=True, **extra):
        payload = {
            "symbol": symbol,
            "conditions": conditions,
            "match": match,
            "frequency": frequency,
            "cooldown_seconds": cooldown_seconds,
            "rule_type": "advanced",
        }
        payload.update(extra)
        return ae.create_mobile_crypto_alert(USER_ID, payload, has_premium=has_premium)

    def _evaluate(self, alert_id):
        return ae.evaluate_alert_rule(ae.get_alert_rule(alert_id, USER_ID))

    def _db_rule(self, alert_id):
        conn = self._connect()
        row = conn.execute("SELECT * FROM alert_rules WHERE id=?", (alert_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    # -- validation --------------------------------------------------------

    def test_validation_rejects_too_many_conditions(self):
        payload = {
            "operator": "AND",
            "conditions": [{"type": "price_above", "threshold": index} for index in range(6)],
        }
        result = ae.validate_advanced_conditions(payload)
        self.assertFalse(result["ok"])
        self.assertIn("5", result["message"])
        created = self._create_advanced(payload["conditions"])
        self.assertEqual(created.get("code"), "invalid_conditions")

    def test_validation_rejects_unknown_type_and_bad_windows(self):
        bad_type = ae.validate_advanced_conditions(
            {"conditions": [{"type": "price_eval_injection", "threshold": 1}]}
        )
        self.assertFalse(bad_type["ok"])
        self.assertIn("Unknown condition type", bad_type["message"])
        too_small = ae.validate_advanced_conditions(
            {"conditions": [{"type": "price_move_pct", "threshold": 5, "window_minutes": 10}]}
        )
        self.assertFalse(too_small["ok"])
        too_big = ae.validate_advanced_conditions(
            {"conditions": [{"type": "price_move_pct", "threshold": 5, "window_minutes": 2000}]}
        )
        self.assertFalse(too_big["ok"])
        missing = ae.validate_advanced_conditions(
            {"conditions": [{"type": "volume_move_pct", "threshold": 5}]}
        )
        self.assertFalse(missing["ok"])
        window_on_static = ae.validate_advanced_conditions(
            {"conditions": [{"type": "price_above", "threshold": 5, "window_minutes": 60}]}
        )
        self.assertFalse(window_on_static["ok"])
        good = ae.validate_advanced_conditions(
            {
                "operator": "OR",
                "conditions": [
                    {"type": "price_above", "threshold": 100},
                    {"type": "price_move_pct", "threshold": 5, "window_minutes": 60, "direction": "up"},
                ],
            }
        )
        self.assertTrue(good["ok"])
        self.assertEqual(good["match"], "any")

    # -- AND / OR matching -------------------------------------------------

    def test_and_matching_arms_then_fires(self):
        created = self._create_advanced(
            [
                {"type": "price_above", "threshold": 100},
                {"type": "volume_above", "threshold": 1e9},
            ],
            match="all",
        )
        self.assertTrue(created["ok"], created)
        alert_id = created["item"]["id"]
        self.market = {"price": 110.0, "volume_24h": 2e9, "market_cap": 2e12}
        first = self._evaluate(alert_id)
        self.assertFalse(first["triggered"])
        self.assertTrue(first.get("armed"))
        second = self._evaluate(alert_id)
        self.assertTrue(second["triggered"], second)
        # Latched + every_crossing => silent while still met.
        third = self._evaluate(alert_id)
        self.assertFalse(third["triggered"])
        self.assertTrue(third.get("latched"))

    def test_and_matching_one_false_condition_blocks(self):
        created = self._create_advanced(
            [
                {"type": "price_above", "threshold": 100},
                {"type": "volume_above", "threshold": 1e12},
            ],
            match="all",
        )
        alert_id = created["item"]["id"]
        self.market = {"price": 110.0, "volume_24h": 2e9, "market_cap": 2e12}
        for _ in range(3):
            result = self._evaluate(alert_id)
            self.assertFalse(result["triggered"])
        self.assertEqual(result["state"], ae.STATE_ARMED)

    def test_or_matching_single_true_condition_fires(self):
        created = self._create_advanced(
            [
                {"type": "price_above", "threshold": 1e9},
                {"type": "volume_above", "threshold": 1e9},
            ],
            match="any",
        )
        alert_id = created["item"]["id"]
        self.market = {"price": 110.0, "volume_24h": 2e9, "market_cap": 2e12}
        self.assertTrue(self._evaluate(alert_id).get("armed"))
        self.assertTrue(self._evaluate(alert_id)["triggered"])

    # -- crossing semantics ------------------------------------------------

    def test_crossing_fires_only_on_genuine_crossing(self):
        created = self._create_advanced([{"type": "price_crosses_above", "threshold": 110}])
        alert_id = created["item"]["id"]
        self.market["price"] = 100.0
        first = self._evaluate(alert_id)  # arms the crossing baseline
        self.assertFalse(first["triggered"])
        self.market["price"] = 105.0
        second = self._evaluate(alert_id)  # below threshold: no crossing
        self.assertFalse(second["triggered"])
        # Restart safety: the per-condition baseline lives in the DB column.
        state = json.loads(self._db_rule(alert_id)["advanced_state"])
        self.assertEqual(state["last_values"]["0"], 105.0)
        self.market["price"] = 115.0
        third = self._evaluate(alert_id)  # 105 <= 110 < 115: genuine crossing
        self.assertTrue(third["triggered"], third)
        state = json.loads(self._db_rule(alert_id)["advanced_state"])
        self.assertEqual(state["last_values"]["0"], 115.0)

    def test_crossing_starting_above_never_fires(self):
        created = self._create_advanced([{"type": "price_crosses_above", "threshold": 110}])
        alert_id = created["item"]["id"]
        self.market["price"] = 120.0
        for price in (120.0, 125.0, 130.0):
            self.market["price"] = price
            result = self._evaluate(alert_id)
            self.assertFalse(result["triggered"], result)

    def test_crosses_below_mirrors(self):
        created = self._create_advanced([{"type": "price_crosses_below", "threshold": 90}])
        alert_id = created["item"]["id"]
        self.market["price"] = 100.0
        self.assertFalse(self._evaluate(alert_id)["triggered"])
        self.market["price"] = 95.0
        self.assertFalse(self._evaluate(alert_id)["triggered"])
        self.market["price"] = 85.0
        self.assertTrue(self._evaluate(alert_id)["triggered"])

    # -- windowed conditions + real observations ---------------------------

    def test_windowed_condition_with_real_baseline(self):
        created = self._create_advanced(
            [{"type": "price_move_pct", "threshold": 5, "window_minutes": 60, "direction": "up"}]
        )
        alert_id = created["item"]["id"]
        mo.record_observation(
            "BTC", price=100.0, observed_at=datetime.utcnow() - timedelta(minutes=60)
        )
        self.market["price"] = 110.0  # +10% over the hour
        first = self._evaluate(alert_id)
        self.assertTrue(first.get("armed"), first)
        second = self._evaluate(alert_id)
        self.assertTrue(second["triggered"], second)

    def test_windowed_condition_without_baseline_skips_insufficient_data(self):
        created = self._create_advanced(
            [{"type": "price_move_pct", "threshold": 5, "window_minutes": 60, "direction": "any"}]
        )
        alert_id = created["item"]["id"]
        self.market["price"] = 110.0
        result = self._evaluate(alert_id)
        self.assertFalse(result["triggered"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("status"), "insufficient_data")
        state = json.loads(self._db_rule(alert_id)["advanced_state"])
        self.assertEqual(state["last_status"], "insufficient_data")

    def test_windowed_condition_outside_tolerance_is_insufficient(self):
        # Only sample is 90 minutes old; a 60 minute window tolerates +/-12
        # minutes around the window start, so this must NOT be used.
        created = self._create_advanced(
            [{"type": "price_move_pct", "threshold": 5, "window_minutes": 60, "direction": "any"}]
        )
        alert_id = created["item"]["id"]
        mo.record_observation(
            "BTC", price=100.0, observed_at=datetime.utcnow() - timedelta(minutes=90)
        )
        self.market["price"] = 200.0
        result = self._evaluate(alert_id)
        self.assertEqual(result.get("status"), "insufficient_data")

    def test_quote_fetch_records_observation(self):
        created = self._create_advanced([{"type": "price_above", "threshold": 1}])
        alert_id = created["item"]["id"]
        self.market["price"] = 123.0
        self._evaluate(alert_id)
        rows = mo.get_observations("BTC")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 123.0)
        self.assertEqual(rows[0]["source"], "stub")

    # -- premium gating at evaluation time ---------------------------------

    def test_lost_premium_skips_but_keeps_rule(self):
        created = self._create_advanced([{"type": "price_above", "threshold": 100}])
        alert_id = created["item"]["id"]
        self.premium = False
        self.market["price"] = 150.0
        result = self._evaluate(alert_id)
        self.assertFalse(result["triggered"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("status"), "premium_required")
        row = self._db_rule(alert_id)
        self.assertEqual(row["status"], "active")  # kept, never deleted
        state = json.loads(row["advanced_state"])
        self.assertEqual(state["last_status"], "premium_required")
        # Premium restored: evaluation resumes normally.
        self.premium = True
        resumed = self._evaluate(alert_id)
        self.assertTrue(resumed.get("armed"))

    def test_basic_alerts_unaffected_by_gate(self):
        self.premium = False
        created = ae.create_mobile_crypto_alert(
            USER_ID,
            {
                "symbol": "BTC",
                "conditions": [{"type": "price_above", "threshold": 100}],
                "match": "all",
                "cooldown_seconds": 0,
            },
            has_premium=False,
        )
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["item"]["rule_type"], "basic")
        self.assertFalse(created["item"]["premium"])
        alert_id = created["item"]["id"]
        self.market["price"] = 150.0
        first = self._evaluate(alert_id)
        self.assertTrue(first.get("armed"))
        second = self._evaluate(alert_id)
        self.assertTrue(second["triggered"], second)

    # -- premium gating + limits at API time -------------------------------

    def test_free_user_advanced_and_watchlist_denied(self):
        advanced = self._create_advanced(
            [{"type": "price_above", "threshold": 1}, {"type": "volume_above", "threshold": 1}],
            has_premium=False,
        )
        self.assertEqual(advanced.get("code"), "premium_required")
        self.assertEqual(advanced.get("capability"), gate.CAP_CRYPTO_ADVANCED_ALERTS)
        watchlist = ae.create_mobile_crypto_alert(
            USER_ID,
            {"symbol": "*", "conditions": [{"type": "price_above", "threshold": 1}]},
            has_premium=False,
        )
        self.assertEqual(watchlist.get("code"), "premium_required")

    def test_free_basic_limit_is_five(self):
        for index in range(ae.MOBILE_FREE_BASIC_RULE_LIMIT):
            created = ae.create_mobile_crypto_alert(
                USER_ID,
                {"symbol": f"AS{index}", "conditions": [{"type": "price_above", "threshold": 1}]},
                has_premium=False,
            )
            self.assertTrue(created["ok"], created)
        sixth = ae.create_mobile_crypto_alert(
            USER_ID,
            {"symbol": "XRP", "conditions": [{"type": "price_above", "threshold": 1}]},
            has_premium=False,
        )
        self.assertEqual(sixth.get("code"), "limit_reached")

    def test_premium_total_limit_is_hundred(self):
        for index in range(ae.MOBILE_PREMIUM_TOTAL_RULE_LIMIT):
            created = ae.create_mobile_crypto_alert(
                USER_ID,
                {"symbol": f"A{index}", "conditions": [{"type": "price_above", "threshold": 1}]},
                has_premium=True,
            )
            self.assertTrue(created["ok"], created)
        overflow = self._create_advanced([{"type": "price_above", "threshold": 1}])
        self.assertEqual(overflow.get("code"), "limit_reached")

    def test_update_structural_change_requires_premium(self):
        created = self._create_advanced([{"type": "price_above", "threshold": 100}])
        alert_id = created["item"]["id"]
        denied = ae.update_mobile_crypto_alert(
            USER_ID, alert_id, {"conditions": [{"type": "price_below", "threshold": 90}]},
            has_premium=False,
        )
        self.assertEqual(denied.get("code"), "premium_required")
        # But pause/resume of an existing advanced rule stays allowed.
        paused = ae.update_mobile_crypto_alert(USER_ID, alert_id, {"enabled": False}, has_premium=False)
        self.assertTrue(paused["ok"], paused)
        self.assertFalse(paused["item"]["enabled"])
        self.assertEqual(paused["item"]["status"], "paused")

    # -- portfolio conditions ----------------------------------------------

    def test_portfolio_value_and_allocation_conditions(self):
        self.portfolio = {
            "ok": True,
            "total_value": 50000.0,
            "holdings": [{"symbol": "BTC", "amount": 1.0, "allocation_pct": 80.0}],
        }
        created = self._create_advanced(
            [
                {"type": "portfolio_value_above", "threshold": 40000},
                {"type": "allocation_above", "threshold": 50},
            ],
            match="all",
        )
        alert_id = created["item"]["id"]
        self.assertTrue(self._evaluate(alert_id).get("armed"))
        result = self._evaluate(alert_id)
        self.assertTrue(result["triggered"], result)

    def test_portfolio_unavailable_is_insufficient_data(self):
        self.portfolio = {"ok": False}
        created = self._create_advanced([{"type": "portfolio_value_above", "threshold": 1}])
        alert_id = created["item"]["id"]
        result = self._evaluate(alert_id)
        self.assertEqual(result.get("status"), "insufficient_data")
        self.assertTrue(result.get("skipped"))

    # -- frequency ---------------------------------------------------------

    def test_frequency_once_completes_after_firing(self):
        created = self._create_advanced(
            [{"type": "price_above", "threshold": 100}], frequency="once"
        )
        alert_id = created["item"]["id"]
        self.market["price"] = 150.0
        self.assertTrue(self._evaluate(alert_id).get("armed"))
        fired = self._evaluate(alert_id)
        self.assertTrue(fired["triggered"])
        self.assertTrue(fired.get("completed"))
        row = self._db_rule(alert_id)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(int(row["active"]), 0)
        after = self._evaluate(alert_id)
        self.assertFalse(after["triggered"])  # completed rules are not evaluated

    # -- mobile contract + history -----------------------------------------

    def test_mobile_alert_json_contract(self):
        created = self._create_advanced(
            [
                {"type": "price_above", "threshold": 65000},
                {"type": "price_move_pct", "threshold": 5, "window_minutes": 60, "direction": "up"},
            ],
            match="any",
            frequency="recurring",
            cooldown_seconds=600,
            name="BTC breakout",
            asset_id="bitcoin",
        )
        item = created["item"]
        self.assertEqual(item["symbol"], "BTC")
        self.assertEqual(item["asset_id"], "bitcoin")
        self.assertEqual(item["name"], "BTC breakout")
        self.assertEqual(item["rule_type"], "advanced")
        self.assertEqual(item["match"], "any")
        self.assertEqual(item["frequency"], "recurring")
        self.assertEqual(item["cooldown_seconds"], 600)
        self.assertTrue(item["enabled"])
        self.assertEqual(item["status"], "active")
        self.assertTrue(item["premium"])
        self.assertEqual(len(item["conditions"]), 2)
        self.assertEqual(item["conditions"][1]["window_minutes"], 60)
        listed = ae.list_mobile_crypto_alerts(USER_ID)
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["items"]), 1)
        deleted = ae.delete_mobile_crypto_alert(USER_ID, item["id"])
        self.assertTrue(deleted["ok"])
        self.assertEqual(ae.list_mobile_crypto_alerts(USER_ID)["items"], [])

    def test_history_pagination_and_filter(self):
        created = self._create_advanced([{"type": "price_above", "threshold": 100}])
        rule = ae.get_alert_rule(created["item"]["id"], USER_ID)
        for seq in (1, 2, 3):
            ae._create_event(rule, 100.0 + seq, "triggered", f"msg {seq}", trigger_seq=seq)
        page = ae.list_mobile_alert_history(USER_ID, limit=2, offset=0)
        self.assertTrue(page["ok"])
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["has_more"])
        rest = ae.list_mobile_alert_history(USER_ID, limit=2, offset=2)
        self.assertEqual(len(rest["items"]), 1)
        self.assertFalse(rest["has_more"])
        item = rest["items"][0]
        self.assertEqual(item["alert_id"], rule["id"])
        self.assertEqual(item["symbol"], "BTC")
        self.assertIn("msg", item["condition_summary"])
        filtered = ae.list_mobile_alert_history(USER_ID, alert_id=rule["id"] + 999)
        self.assertEqual(filtered["items"], [])
        self.assertFalse(filtered["has_more"])


if __name__ == "__main__":
    unittest.main()
