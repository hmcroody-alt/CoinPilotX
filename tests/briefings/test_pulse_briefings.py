"""Pulse Briefings test suite (Stage 64).

Covers: provider normalization + fallback, shared cache TTL + single-flight,
stale-serve + staleness omission, significance scoring, fingerprint dedupe,
idempotency claim, quiet hours (incl. wrap-around), timezone windows,
preferences validation/gating, template fallback + localization, UNDX
grounding rejection, advice rejection, push payload privacy + deeplink,
kill switch.

Run: python3 -m unittest tests.briefings.test_pulse_briefings -v
(pytest is unavailable in the build sandbox; unittest only.)
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from services.pulse_briefings import crypto_provider, engine, facts, summarizer


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE pulse_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
            title TEXT, body TEXT, is_read INTEGER DEFAULT 0, created_at TEXT)"""
    )
    cur.execute(
        """CREATE TABLE crypto_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symbol TEXT,
            type TEXT, threshold REAL, status TEXT DEFAULT 'active')"""
    )
    cur.execute(
        """CREATE TABLE pulse_region_preferences (
            user_id INTEGER PRIMARY KEY, preferred_timezone TEXT)"""
    )
    conn.commit()
    engine.ensure_schema(conn)
    return conn


def _clear_provider_cache() -> None:
    with crypto_provider._CACHE_LOCK:
        crypto_provider._CACHE.clear()
        crypto_provider._FLIGHT_LOCKS.clear()


CG_MARKETS_FIXTURE = [
    {"symbol": "btc", "name": "Bitcoin", "current_price": 65000.0, "market_cap": 1.28e12,
     "total_volume": 3.1e10, "price_change_percentage_1h_in_currency": 0.2,
     "price_change_percentage_24h_in_currency": -2.5,
     "price_change_percentage_7d_in_currency": 4.1, "market_cap_rank": 1},
    {"symbol": "eth", "name": "Ethereum", "current_price": 3200.0, "market_cap": 3.8e11,
     "total_volume": 1.4e10, "price_change_percentage_1h_in_currency": 0.1,
     "price_change_percentage_24h_in_currency": 1.4,
     "price_change_percentage_7d_in_currency": 2.2, "market_cap_rank": 2},
    {"symbol": "sol", "name": "Solana", "current_price": 150.0, "market_cap": 7.0e10,
     "total_volume": 2.0e9, "price_change_percentage_1h_in_currency": -0.4,
     "price_change_percentage_24h_in_currency": 6.3,
     "price_change_percentage_7d_in_currency": -1.0, "market_cap_rank": 3},
]

CG_GLOBAL_FIXTURE = {"data": {
    "total_market_cap": {"usd": 2.4e12}, "total_volume": {"usd": 9.0e10},
    "market_cap_percentage": {"btc": 53.2}, "market_cap_change_percentage_24h_usd": -1.1,
}}


class ProviderNormalizationTests(unittest.TestCase):
    def setUp(self):
        _clear_provider_cache()

    def test_coingecko_overview_normalized(self):
        def fake_cg(path, params=None):
            if path == "/coins/markets":
                return CG_MARKETS_FIXTURE
            if path == "/global":
                return CG_GLOBAL_FIXTURE
            return None
        with mock.patch.object(crypto_provider, "_cg_get", side_effect=fake_cg):
            overview = crypto_provider._load_overview()
        self.assertEqual(overview["provider"], "coingecko")
        self.assertFalse(overview["stale"])
        self.assertEqual(overview["btc"]["symbol"], "BTC")
        self.assertEqual(overview["btc"]["price"], 65000.0)
        self.assertEqual(overview["btc"]["change_24h"], -2.5)
        self.assertEqual(overview["total_market_cap"], 2.4e12)
        self.assertEqual(overview["btc_dominance"], 53.2)
        self.assertIn(overview["market_direction"], ("up", "down", "mixed"))
        for asset in overview["assets"]:
            self.assertTrue(asset["observed_at"])
            self.assertEqual(asset["provider"], "coingecko")

    def test_coinbase_fallback_when_primary_down(self):
        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"price": "64000.5", "volume": "12000"}
        fake_resp.raise_for_status.return_value = None
        with mock.patch.object(crypto_provider, "_cg_get", return_value=None), \
             mock.patch.object(crypto_provider.requests, "get", return_value=fake_resp):
            overview = crypto_provider._load_overview()
        self.assertEqual(overview["provider"], "coinbase")
        self.assertEqual(overview["btc"]["price"], 64000.5)
        self.assertIsNone(overview["btc"]["change_24h"])  # ticker has no % change

    def test_top_movers_ranked(self):
        with mock.patch.object(crypto_provider, "get_market_overview", return_value={
            "assets": [{"symbol": s, "change_24h": c} for s, c in
                       (("A", 5.0), ("B", -3.0), ("C", 1.0), ("D", -8.0))]}):
            movers = crypto_provider.get_top_movers(limit=2)
        self.assertEqual([a["symbol"] for a in movers["gainers"]], ["A", "C"])
        self.assertEqual([a["symbol"] for a in movers["losers"]], ["D", "B"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        _clear_provider_cache()

    def test_ttl_cache_one_load(self):
        calls = []
        loader = lambda: calls.append(1) or {"v": len(calls)}
        first = crypto_provider._cached("k1", 60, loader)
        second = crypto_provider._cached("k1", 60, loader)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)

    def test_single_flight_concurrent_callers_share_one_fetch(self):
        calls = []

        def slow_loader():
            calls.append(1)
            time.sleep(0.15)
            return {"v": 1}

        results = []
        threads = [threading.Thread(target=lambda: results.append(
            crypto_provider._cached("k2", 60, slow_loader))) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)  # N callers -> 1 provider call
        self.assertEqual(len(results), 5)

    def test_stale_serve_within_bound_marked(self):
        crypto_provider._cached("k3", 60, lambda: {"v": 1})
        with crypto_provider._CACHE_LOCK:  # expire the TTL, stay inside STALE_MAX
            crypto_provider._CACHE["k3"]["at"] = time.time() - 120
        value = crypto_provider._cached("k3", 60, lambda: None)  # loader fails
        self.assertEqual(value["v"], 1)
        self.assertTrue(value["stale"])

    def test_loader_failure_no_cache_returns_none(self):
        self.assertIsNone(crypto_provider._cached("k4", 60, lambda: None))


class StalenessTests(unittest.TestCase):
    def test_is_stale_variants(self):
        now = _iso(datetime.now(timezone.utc))
        old = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
        self.assertTrue(crypto_provider.is_stale(None))
        self.assertTrue(crypto_provider.is_stale({"stale": True, "generated_at": now}))
        self.assertTrue(crypto_provider.is_stale({"generated_at": old}))
        self.assertTrue(crypto_provider.is_stale({"generated_at": "garbage"}))
        self.assertFalse(crypto_provider.is_stale({"generated_at": now}))

    def test_stale_market_omitted_from_facts(self):
        conn = _fresh_conn()
        stale = {"generated_at": _iso(datetime.now(timezone.utc) - timedelta(hours=3)),
                 "provider": "coingecko", "btc": {}, "eth": {}, "assets": []}
        with mock.patch.object(facts.crypto_provider, "get_market_overview", return_value=stale):
            out = facts.collect_crypto_facts(conn.cursor(), 1, watchlist_enabled=True)
        self.assertFalse(out["available"])
        self.assertEqual(out["unavailable_reason"], "stale_or_provider_down")
        self.assertNotIn("btc_price", out)  # never present stale data as current
        conn.close()


class SignificanceTests(unittest.TestCase):
    def test_network_significance_weights(self):
        net = {"security_alerts": 1, "unread_messages": 2, "new_followers": 3}
        self.assertEqual(facts.network_significance(net), 50 + 16 + 9)

    def test_crypto_significance(self):
        crypto = {"available": True, "btc_change_24h": -2.5, "market_cap_change_24h_pct": -2.1,
                  "alert_proximity": [{"symbol": "BTC"}], "watchlist": [{"change_24h": 5.0}]}
        self.assertEqual(facts.crypto_significance(crypto), 10 + 6 + 8 + 4)
        self.assertEqual(facts.crypto_significance({"available": False}), 0)

    def test_fingerprint_dedupe(self):
        base = {"network": {"unread_messages": 2}, "crypto": {"btc_change_24h": -2.1,
                "market_direction": "down", "alert_proximity": []}}
        same = {"network": {"unread_messages": 2}, "crypto": {"btc_change_24h": -1.9,
                "market_direction": "down", "alert_proximity": []}}  # same 1% bucket
        changed = {"network": {"unread_messages": 5}, "crypto": {"btc_change_24h": -2.5,
                   "market_direction": "down", "alert_proximity": []}}
        self.assertEqual(facts.fact_fingerprint(base), facts.fact_fingerprint(same))
        self.assertNotEqual(facts.fact_fingerprint(base), facts.fact_fingerprint(changed))


class WindowAndQuietHoursTests(unittest.TestCase):
    def test_every_6h_windows(self):
        d = datetime(2026, 8, 30, 13, 5)
        key, start = engine.current_window(d, "every_6h")
        self.assertEqual(key, "2026-08-30:12")
        self.assertEqual(start.hour, 12)
        key, _ = engine.current_window(datetime(2026, 8, 30, 3, 0), "every_6h")
        self.assertEqual(key, "2026-08-30:00")

    def test_morning_evening_windows(self):
        key, _ = engine.current_window(datetime(2026, 8, 30, 7, 0), "morning_evening")
        self.assertEqual(key, "2026-08-30:06")
        self.assertIsNone(engine.current_window(datetime(2026, 8, 30, 13, 0), "morning_evening"))
        key, _ = engine.current_window(datetime(2026, 8, 30, 3, 0), "morning_evening")
        self.assertEqual(key, "2026-08-29:18")  # pre-dawn maps to prior evening

    def test_quiet_hours_wraparound(self):
        qa = engine._quiet_hours_active
        self.assertTrue(qa(datetime(2026, 8, 30, 23, 0), "22:00", "07:00"))
        self.assertTrue(qa(datetime(2026, 8, 30, 6, 30), "22:00", "07:00"))
        self.assertFalse(qa(datetime(2026, 8, 30, 12, 0), "22:00", "07:00"))
        self.assertTrue(qa(datetime(2026, 8, 30, 13, 0), "12:00", "14:00"))  # same-day range
        self.assertFalse(qa(datetime(2026, 8, 30, 15, 0), "12:00", "14:00"))
        self.assertFalse(qa(datetime(2026, 8, 30, 12, 0), "garbage", "also-bad"))  # falls back

    def test_jitter_deterministic_and_bounded(self):
        a, b = engine._jitter_offset_minutes(42), engine._jitter_offset_minutes(42)
        self.assertEqual(a, b)
        self.assertTrue(0 <= a <= engine.JITTER_MINUTES)

    def test_timezone_resolution(self):
        conn = _fresh_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO pulse_region_preferences VALUES (7, 'America/New_York')")
        conn.commit()
        self.assertEqual(engine._user_zone(cur, 7).key, "America/New_York")
        self.assertEqual(engine._user_zone(cur, 8).key, "UTC")  # missing row -> UTC
        conn.close()


class PreferencesTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        conn = _fresh_conn()
        prefs = engine.get_preferences(1, conn=conn)
        self.assertTrue(prefs["enabled"])
        self.assertEqual(prefs["frequency"], "every_6h")
        self.assertEqual(prefs["quiet_start"], "22:00")
        updated = engine.update_preferences(1, {
            "frequency": "hourly_spam",  # invalid -> ignored
            "quiet_start": "25:99",      # invalid -> ignored
            "quiet_end": "8:5",          # normalized
            "crypto_enabled": False,
        }, conn=conn)
        self.assertEqual(updated["frequency"], "every_6h")
        self.assertEqual(updated["quiet_start"], "22:00")
        self.assertEqual(updated["quiet_end"], "08:05")
        self.assertFalse(updated["crypto_enabled"])
        # persisted
        again = engine.get_preferences(1, conn=conn)
        self.assertEqual(again["quiet_end"], "08:05")
        conn.close()

    def test_disabled_user_never_evaluated(self):
        conn = _fresh_conn()
        engine.update_preferences(2, {"enabled": False}, conn=conn)
        out = engine.evaluate_user_briefing(conn, {"user_id": 2}, send=False)
        self.assertEqual(out["status"], "disabled")
        engine.update_preferences(2, {"enabled": True, "frequency": "off"}, conn=conn)
        out = engine.evaluate_user_briefing(conn, {"user_id": 2}, send=False)
        self.assertEqual(out["status"], "disabled")
        conn.close()


class EngineFlowTests(unittest.TestCase):
    """Evaluation flow with crypto disabled (no network) and UTC zone."""

    NOW = datetime(2026, 8, 30, 13, 30, tzinfo=timezone.utc)  # window 12, past jitter

    def _conn_for(self, user_id: int, notif_count: int = 0) -> sqlite3.Connection:
        conn = _fresh_conn()
        engine.update_preferences(user_id, {"crypto_enabled": False}, conn=conn)
        cur = conn.cursor()
        created = _iso(self.NOW - timedelta(hours=1))
        for _ in range(notif_count):
            cur.execute(
                "INSERT INTO pulse_notifications (user_id, type, is_read, created_at) VALUES (?,?,0,?)",
                (user_id, "message", created))
        conn.commit()
        return conn

    def test_suppressed_when_nothing_changed(self):
        conn = self._conn_for(10, notif_count=0)
        out = engine.evaluate_user_briefing(conn, {"user_id": 10}, now_utc=self.NOW, send=False)
        self.assertEqual(out["status"], "suppressed")
        self.assertEqual(out["reason"], "briefing_suppressed_no_change")
        conn.close()

    def test_idempotency_second_evaluation_already_claimed(self):
        conn = self._conn_for(11, notif_count=2)
        first = engine.evaluate_user_briefing(conn, {"user_id": 11}, now_utc=self.NOW, send=False)
        self.assertEqual(first["status"], "generated")
        second = engine.evaluate_user_briefing(conn, {"user_id": 11}, now_utc=self.NOW, send=False)
        self.assertEqual(second["status"], "already_claimed")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_briefings WHERE user_id=11")
        self.assertEqual(dict(cur.fetchone())["n"], 1)
        conn.close()

    def test_quiet_hours_defer(self):
        conn = self._conn_for(12, notif_count=2)
        late = datetime(2026, 8, 30, 23, 15, tzinfo=timezone.utc)
        out = engine.evaluate_user_briefing(conn, {"user_id": 12}, now_utc=late, send=False)
        self.assertEqual(out["status"], "quiet_hours")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_briefings WHERE user_id=12")
        self.assertEqual(dict(cur.fetchone())["n"], 0)  # window not consumed
        conn.close()

    def test_duplicate_fingerprint_suppressed_across_windows(self):
        conn = self._conn_for(13, notif_count=2)
        fake_push = types.SimpleNamespace(send_push=mock.Mock(return_value={"ok": True}))
        fake_notif = types.SimpleNamespace(send_in_app_notification=mock.Mock())
        import services
        with mock.patch.object(services, "push_service", fake_push, create=True), \
             mock.patch.object(services, "notification_service", fake_notif, create=True):
            first = engine.evaluate_user_briefing(conn, {"user_id": 13}, now_utc=self.NOW, send=True)
        self.assertEqual(first["status"], "sent")
        # next window: same unread state re-inserted after the send
        cur = conn.cursor()
        cur.execute("INSERT INTO pulse_notifications (user_id, type, is_read, created_at) VALUES (13,'message',0,?)",
                    (_iso(self.NOW + timedelta(hours=5)),))
        cur.execute("INSERT INTO pulse_notifications (user_id, type, is_read, created_at) VALUES (13,'message',0,?)",
                    (_iso(self.NOW + timedelta(hours=5)),))
        conn.commit()
        nxt = datetime(2026, 8, 30, 19, 30, tzinfo=timezone.utc)  # window 18
        out = engine.evaluate_user_briefing(conn, {"user_id": 13}, now_utc=nxt, send=False)
        self.assertEqual(out["status"], "suppressed")
        self.assertEqual(out["reason"], "duplicate_fingerprint")
        conn.close()

    def test_push_payload_privacy_and_deeplink(self):
        conn = self._conn_for(14, notif_count=3)
        fake_push = types.SimpleNamespace(send_push=mock.Mock(return_value={"ok": True}))
        fake_notif = types.SimpleNamespace(send_in_app_notification=mock.Mock())
        import services
        with mock.patch.object(services, "push_service", fake_push, create=True), \
             mock.patch.object(services, "notification_service", fake_notif, create=True):
            out = engine.evaluate_user_briefing(conn, {"user_id": 14}, now_utc=self.NOW, send=True)
        self.assertEqual(out["status"], "sent")
        args, kwargs = fake_push.send_push.call_args
        data = kwargs["data"]
        self.assertEqual(data["notification_type"], "pulse_briefing")
        self.assertTrue(data["deep_link"].startswith("pulse://notifications?briefing="))
        allowed = {"notification_type", "push_type", "briefing_id", "deep_link", "native_url", "generated_at"}
        self.assertEqual(set(data.keys()), allowed)  # no message bodies / facts in payload
        self.assertEqual(kwargs["push_type"], "pulse_briefing")
        conn.close()

    def test_owner_scoped_reads(self):
        conn = self._conn_for(15, notif_count=2)
        engine.evaluate_user_briefing(conn, {"user_id": 15}, now_utc=self.NOW, send=False)
        rows = engine.list_briefings(15, conn=conn)
        self.assertEqual(len(rows), 1)
        bid = rows[0]["id"]
        self.assertIsNotNone(engine.get_briefing(15, bid, conn=conn))
        self.assertIsNone(engine.get_briefing(999, bid, conn=conn))  # cross-account blocked
        self.assertEqual(engine.list_briefings(999, conn=conn), [])
        conn.close()


class KillSwitchTests(unittest.TestCase):
    def test_briefings_disabled_env(self):
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "true"}):
            self.assertFalse(engine.briefings_enabled())
            out = engine.run_scheduled_cycle()
            self.assertEqual(out["status"], "disabled")
            self.assertEqual(out["processed"], 0)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "false"}):
            self.assertFalse(engine.briefings_enabled())
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": ""}, clear=False):
            os.environ.pop("PULSE_BRIEFINGS_ENABLED", None)
            self.assertTrue(engine.briefings_enabled())


class SummarizerTests(unittest.TestCase):
    FACTS = {
        "user_id": 1, "locale": "en", "urgency": "normal",
        "network": {"unread_messages": 3, "security_alerts": 0, "friend_requests": 1,
                    "new_followers": 0, "marketplace_orders": 0},
        "crypto": {"available": True, "btc_price": 65000.0, "btc_change_24h": -2.5,
                   "eth_change_24h": 1.4},
    }

    def test_grounded_accepts_payload_numbers(self):
        self.assertTrue(summarizer.grounded("3 unread, BTC 65000 down 2.5%", self.FACTS))

    def test_grounded_rejects_invented_numbers(self):
        self.assertFalse(summarizer.grounded("BTC crashed to 42000", self.FACTS))
        self.assertFalse(summarizer.grounded("You have 99 messages", self.FACTS))

    def test_template_copy_localized_and_grounded(self):
        for locale, title in (("en", "Pulse Briefing"), ("es", "Resumen Pulse"),
                              ("fr", "Brief Pulse"), ("ht", "Rezime Pulse")):
            f = dict(self.FACTS, locale=locale)
            copy = summarizer.template_copy(f)
            self.assertEqual(copy["title"], title)
            self.assertEqual(copy["source"], "template")
            self.assertIn("3", copy["body"])
            self.assertIn("-2.5%", copy["body"])
            self.assertTrue(summarizer.grounded(f"{copy['title']} {copy['body']}", f))
            self.assertLessEqual(len(copy["body"]), summarizer.BODY_MAX)

    def _with_fake_undx(self, response_text):
        fake = types.ModuleType("undx_router")
        fake.route_structured_request = lambda *a, **k: {"ok": True, "response": response_text,
                                                        "provider": "fake"}
        return mock.patch.dict(sys.modules, {"undx_router": fake})

    def test_undx_grounded_copy_accepted(self):
        with self._with_fake_undx('{"title": "3 new updates", "body": "BTC -2.5% over 24h. 3 unread conversations."}'):
            copy = summarizer.summarize(self.FACTS)
        self.assertEqual(copy["source"], "undx:fake")
        self.assertIn("3 unread", copy["body"])

    def test_undx_invented_number_rejected_falls_back_to_template(self):
        with self._with_fake_undx('{"title": "Market alert", "body": "BTC dropped 12.7% to 51000!"}'):
            copy = summarizer.summarize(self.FACTS)
        self.assertEqual(copy["source"], "template")  # ungrounded LLM copy rejected

    def test_undx_advice_rejected(self):
        with self._with_fake_undx('{"title": "Act now", "body": "BTC down 2.5% - you should buy the dip"}'):
            copy = summarizer.summarize(self.FACTS)
        self.assertEqual(copy["source"], "template")

    def test_undx_failure_falls_back(self):
        fake = types.ModuleType("undx_router")
        fake.route_structured_request = mock.Mock(side_effect=RuntimeError("provider down"))
        with mock.patch.dict(sys.modules, {"undx_router": fake}):
            copy = summarizer.summarize(self.FACTS)
        self.assertEqual(copy["source"], "template")

    def test_undx_malformed_json_falls_back(self):
        with self._with_fake_undx("sorry, I cannot respond in JSON"):
            copy = summarizer.summarize(self.FACTS)
        self.assertEqual(copy["source"], "template")

    def test_quiet_network_template(self):
        copy = summarizer.template_copy({"locale": "en", "network": {"unread_messages": 0},
                                         "crypto": {"available": False}})
        self.assertIn("quiet", copy["body"].lower())


class PostgresCompatTests(unittest.TestCase):
    """The engine reads cur.lastrowid after the claim INSERT. That only works on
    Postgres if services.db appends RETURNING id, which requires registration in
    AUTO_PK_TABLES. Found broken in production acceptance (2026-08-30)."""

    def test_pulse_briefings_registered_for_returning_id(self):
        from services.db import AUTO_PK_TABLES
        self.assertEqual(AUTO_PK_TABLES.get("pulse_briefings"), "id")


if __name__ == "__main__":
    unittest.main()
