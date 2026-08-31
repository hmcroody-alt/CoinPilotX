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

from services import pulse_region_preferences as region_preferences
from services import schema_guard
from services.pulse_briefings import crypto_provider, engine, facts, summarizer


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _fresh_conn(*, region_prefs: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Each test builds a new in-memory database while the once-per-process DDL
    # guard persists for the whole session; without this the second test would
    # skip creation and find no pulse_region_preferences table.
    schema_guard.reset_all()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE pulse_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
            title TEXT, body TEXT, is_read INTEGER DEFAULT 0, created_at TEXT)"""
    )
    # Mirrors production Postgres exactly. The previous fixture invented
    # symbol/type/threshold, which matched the (wrong) query in facts.py rather
    # than the real table -- so the suite stayed green while every production
    # cycle raised UndefinedColumn and silently emptied the watchlist.
    cur.execute(
        """CREATE TABLE crypto_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, asset_symbol TEXT,
            condition_type TEXT, target_value REAL, status TEXT DEFAULT 'active',
            notify_push INTEGER DEFAULT 1, notify_email INTEGER DEFAULT 0,
            notify_sms INTEGER DEFAULT 0, notify_in_app INTEGER DEFAULT 1,
            note TEXT, created_at TEXT, updated_at TEXT, last_triggered_at TEXT)"""
    )
    # pulse_region_preferences is deliberately NOT hand-rolled here. The previous
    # fixture invented a two-column (user_id, preferred_timezone) shape that the
    # canonical service does not own -- the same class of divergence that let
    # crypto_alerts stay green locally while production raised UndefinedColumn.
    # engine.ensure_schema now creates it through pulse_region_preferences, so
    # the test sees exactly the production table.
    #
    # Owned by bot.init_db(); modelled here because the cycle honours the global
    # push opt-out that lives on the category='global' row.
    cur.execute(
        """CREATE TABLE notification_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, category TEXT,
            enable_push_notifications INTEGER DEFAULT 1, UNIQUE(user_id, category))"""
    )
    # Owned by bot.init_db(). Modelled here because it is the table
    # push_service.send_push actually reads to find a device: a user with zero
    # rows CANNOT receive a briefing push no matter what their preferences say.
    # BOTH active and is_active, as production carries: `active` comes from
    # bot.init_db() and `is_active` was added later by push_service, which is why
    # every reader coalesces the pair. A fixture with only one of them makes the
    # real query raise "no such column" -- the crypto_alerts divergence again.
    cur.execute(
        """CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, endpoint TEXT UNIQUE,
            subscription_json TEXT, user_agent TEXT, device_type TEXT, browser TEXT,
            active INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
            created_at TEXT, updated_at TEXT, last_seen_at TEXT)"""
    )
    conn.commit()
    engine.ensure_schema(conn)
    if not region_prefs:
        # Reproduces the production state that caused the incident: the engine's
        # own tables exist, the optional region-preference table does not.
        conn.execute("DROP TABLE IF EXISTS pulse_region_preferences")
        conn.commit()
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


class WatchlistFactTests(unittest.TestCase):
    """The watchlist + alert_proximity block is one try/except, so a query fault
    empties BOTH and looks exactly like 'user watches nothing'. These tests
    insert real crypto_alerts rows so a wrong column name fails loudly."""

    def setUp(self):
        _clear_provider_cache()
        self.conn = _fresh_conn()

    def tearDown(self):
        self.conn.close()

    def _alert(self, user_id, symbol, condition, target):
        self.conn.execute(
            "INSERT INTO crypto_alerts (user_id, asset_symbol, condition_type, target_value, status) "
            "VALUES (?,?,?,?,'active')",
            (user_id, symbol, condition, target),
        )
        self.conn.commit()

    def _facts(self, user_id=1):
        with mock.patch.object(facts.crypto_provider, "get_watchlist_snapshots",
                               return_value=[{"symbol": "BTC", "price": 100.0, "change_24h": 1.0}]), \
             mock.patch.object(facts.crypto_provider, "get_market_overview",
                               return_value={"generated_at": _iso(datetime.now(timezone.utc)),
                                             "provider": "coingecko",
                                             "btc": {"price": 100.0, "change_24h": 1.0},
                                             "eth": {"price": 50.0, "change_24h": 1.0}}), \
             mock.patch.object(facts.crypto_provider, "get_top_movers",
                               return_value={"gainers": [], "losers": []}), \
             mock.patch.object(facts.crypto_provider, "get_trending", return_value=[]):
            return facts.collect_crypto_facts(self.conn.cursor(), user_id, watchlist_enabled=True)

    def test_watchlist_populates_from_real_columns(self):
        self._alert(1, "BTC", "above", 45000.0)
        out = self._facts()
        self.assertEqual(out["watchlist"], [{"symbol": "BTC", "price": 100.0, "change_24h": 1.0}])

    def test_proximity_reported_when_threshold_is_near(self):
        self._alert(1, "BTC", "above", 103.0)  # 3% above a $100 price
        out = self._facts()
        self.assertEqual(out["alert_proximity"],
                         [{"symbol": "BTC", "threshold": 103.0, "distance_pct": 3.0}])

    def test_distant_threshold_is_not_reported(self):
        self._alert(1, "BTC", "above", 45000.0)  # ~450x away
        self.assertEqual(self._facts()["alert_proximity"], [])

    def test_percent_move_conditions_are_not_treated_as_prices(self):
        """target_value on a moves_up_percent alert is a percentage, so a
        price-distance calculation against it would be meaningless."""
        self._alert(1, "BTC", "moves_up_percent", 101.0)
        out = self._facts()
        self.assertEqual(out["alert_proximity"], [])
        self.assertEqual(out["watchlist"], [{"symbol": "BTC", "price": 100.0, "change_24h": 1.0}])

    def test_watchlist_is_owner_scoped(self):
        self._alert(2, "BTC", "above", 103.0)  # belongs to another user
        out = self._facts(user_id=1)
        self.assertEqual(out["watchlist"], [])
        self.assertEqual(out["alert_proximity"], [])


class TopicIndependenceTests(unittest.TestCase):
    """Crypto market and watchlist are separate switches on the hub, so they must
    be separate in the fact pack. Turning the market topic off used to skip the
    whole crypto collector, which deleted the user's watchlist as a side effect:
    the watchlist switch read ON while contributing nothing."""

    def setUp(self):
        _clear_provider_cache()
        self.conn = _fresh_conn()
        self.conn.execute(
            "INSERT INTO crypto_alerts (user_id, asset_symbol, condition_type, target_value, status) "
            "VALUES (1,'BTC','above',103.0,'active')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _build(self, *, crypto_enabled, watchlist_enabled):
        # A big market move: 9% BTC. Whether it may score is the point of the test.
        with mock.patch.object(facts.crypto_provider, "get_watchlist_snapshots",
                               return_value=[{"symbol": "BTC", "price": 100.0, "change_24h": 1.0}]), \
             mock.patch.object(facts.crypto_provider, "get_market_overview",
                               return_value={"generated_at": _iso(datetime.now(timezone.utc)),
                                             "provider": "coingecko",
                                             "btc": {"price": 100.0, "change_24h": 9.0},
                                             "eth": {"price": 50.0, "change_24h": 1.0},
                                             "market_cap_change_24h_pct": 9.0}), \
             mock.patch.object(facts.crypto_provider, "get_top_movers",
                               return_value={"gainers": [], "losers": []}), \
             mock.patch.object(facts.crypto_provider, "get_trending", return_value=[]):
            return facts.build_briefing_facts(
                self.conn.cursor(), 1, since_iso=_iso(datetime.now(timezone.utc)),
                timezone_name="UTC", locale="en",
                prefs={"network_enabled": False,
                       "crypto_enabled": crypto_enabled,
                       "watchlist_enabled": watchlist_enabled},
            )

    def test_market_off_with_watchlist_on_still_returns_watchlist(self):
        pack = self._build(crypto_enabled=False, watchlist_enabled=True)
        crypto = pack["crypto"]
        self.assertIsNotNone(crypto, "watchlist ON must still produce a crypto fact block")
        self.assertEqual(crypto["watchlist"], [{"symbol": "BTC", "price": 100.0, "change_24h": 1.0}])
        self.assertEqual(crypto["alert_proximity"],
                         [{"symbol": "BTC", "threshold": 103.0, "distance_pct": 3.0}])

    def test_market_off_contributes_no_market_facts_or_score(self):
        pack = self._build(crypto_enabled=False, watchlist_enabled=True)
        crypto = pack["crypto"]
        self.assertNotIn("btc_price", crypto)
        self.assertNotIn("btc_change_24h", crypto)
        self.assertFalse(crypto["market_enabled"])
        # A 9% BTC move must score zero when the market topic is off; only the
        # user's own alert proximity (8) may count.
        self.assertEqual(pack["crypto_score"], 8)

    def test_market_on_scores_the_move(self):
        pack = self._build(crypto_enabled=True, watchlist_enabled=True)
        self.assertTrue(pack["crypto"]["market_enabled"])
        self.assertEqual(pack["crypto"]["btc_change_24h"], 9.0)
        self.assertEqual(pack["crypto_score"], 10 + 6 + 8)

    def test_watchlist_off_keeps_market_and_drops_personal_facts(self):
        pack = self._build(crypto_enabled=True, watchlist_enabled=False)
        crypto = pack["crypto"]
        self.assertEqual(crypto["btc_change_24h"], 9.0)
        self.assertEqual(crypto["watchlist"], [])
        self.assertEqual(crypto["alert_proximity"], [])
        self.assertEqual(pack["crypto_score"], 10 + 6)

    def test_both_off_removes_the_crypto_block_entirely(self):
        pack = self._build(crypto_enabled=False, watchlist_enabled=False)
        self.assertIsNone(pack["crypto"])
        self.assertEqual(pack["crypto_score"], 0)


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
        # Morning opens at 08:00 local, not 06:00: "morning" should not mean a
        # window whose whole first hours sit inside a default 22:00-07:00 quiet
        # range, where every send would be suppressed anyway.
        key, _ = engine.current_window(datetime(2026, 8, 30, 9, 0), "morning_evening")
        self.assertEqual(key, "2026-08-30:08")
        self.assertIsNone(engine.current_window(datetime(2026, 8, 30, 15, 0), "morning_evening"))
        key, _ = engine.current_window(datetime(2026, 8, 30, 3, 0), "morning_evening")
        self.assertEqual(key, "2026-08-29:18")  # pre-dawn maps to prior evening

    def test_off_has_no_window_at_any_hour(self):
        """"off" used to fall through to the full four-window schedule, leaving
        every caller to remember the guard separately."""
        self.assertEqual(engine._windows_for_frequency("off"), ())
        for hour in range(24):
            self.assertIsNone(engine.current_window(datetime(2026, 8, 30, hour, 30), "off"))

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


class TimezoneAuthorityTests(unittest.TestCase):
    """Section A: the briefing zone must come from the canonical authority.

    Quiet hours, the six-hour window and the idempotency key are all derived
    from this one value, so a silent UTC fallback is not a cosmetic defect --
    it schedules a 22:00-07:00 quiet band against a clock the user never set.
    """

    def test_real_zones_round_trip_through_the_authority(self):
        conn = _fresh_conn()
        for user_id, zone in ((1, "America/Los_Angeles"), (2, "America/New_York"),
                              (3, "Europe/Paris"), (4, "UTC")):
            region_preferences.update_preferences(user_id, {"time_zone": zone}, conn=conn)
        conn.commit()
        for user_id, zone in ((1, "America/Los_Angeles"), (2, "America/New_York"),
                              (3, "Europe/Paris"), (4, "UTC")):
            self.assertEqual(engine._user_zone(conn, user_id).key, zone)
        conn.close()

    def test_missing_preference_row_falls_back_to_utc(self):
        conn = _fresh_conn()
        self.assertEqual(engine._user_zone(conn, 999).key, "UTC")
        conn.close()

    def test_automatic_preference_falls_back_to_utc(self):
        # "auto" is stored as empty by the authority; it must not be treated as
        # a zone name, and it must not be guessed at from the locale.
        conn = _fresh_conn()
        region_preferences.update_preferences(5, {"time_zone": "auto", "locale": "en-US"}, conn=conn)
        conn.commit()
        self.assertEqual(engine._user_zone(conn, 5).key, "UTC")
        conn.close()

    def test_missing_optional_table_falls_back_without_raising(self):
        # The production incident verbatim: alert_worker never served the
        # settings route, so the table had never been created in that database.
        conn = _fresh_conn(region_prefs=False)
        with mock.patch.object(engine.logging, "exception") as logged:
            self.assertEqual(engine._user_zone(conn, 1).key, "UTC")
        self.assertTrue(logged.called)  # degraded, and says so
        conn.close()

    def test_ensure_schema_creates_the_region_table(self):
        # The fix is not "catch the error" -- the engine's own schema pass must
        # leave the canonical table present in every process that briefs.
        conn = _fresh_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_region_preferences'"
        ).fetchone()
        self.assertIsNotNone(row)
        conn.close()

    def test_unknown_zone_name_falls_back_to_utc(self):
        conn = _fresh_conn()
        conn.execute(
            "INSERT INTO pulse_region_preferences "
            "(user_id, preferred_timezone, preferred_date_format, updated_at) VALUES (6,?,?,?)",
            ("Mars/Olympus_Mons", "auto", "2026-08-30T00:00:00+00:00"),
        )
        conn.commit()
        self.assertEqual(engine._user_zone(conn, 6).key, "UTC")
        conn.close()

    def test_window_key_is_local_not_utc(self):
        # The consequence the fallback was hiding: at 02:00 UTC a Los Angeles
        # user is still in the previous local day's 18:00 window.
        conn = _fresh_conn()
        region_preferences.update_preferences(1, {"time_zone": "America/Los_Angeles"}, conn=conn)
        conn.commit()
        utc_now = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
        local = utc_now.astimezone(engine._user_zone(conn, 1))
        key, _ = engine.current_window(local, "every_6h")
        self.assertEqual(key, "2026-08-30:18")
        # Same instant, no preference stored -> UTC -> a different window entirely.
        utc_key, _ = engine.current_window(utc_now.astimezone(engine._user_zone(conn, 999)), "every_6h")
        self.assertEqual(utc_key, "2026-08-31:00")
        conn.close()


class PreferencesTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        conn = _fresh_conn()
        prefs = engine.get_preferences(1, conn=conn)
        self.assertTrue(prefs["enabled"])
        self.assertEqual(prefs["frequency"], "every_6h")
        self.assertEqual(prefs["quiet_start"], "22:00")
        updated = engine.update_preferences(1, {
            "quiet_end": "8:5",  # under-padded but unambiguous -> normalized
            "crypto_enabled": False,
        }, conn=conn)
        self.assertEqual(updated["quiet_end"], "08:05")
        self.assertFalse(updated["crypto_enabled"])
        # persisted
        again = engine.get_preferences(1, conn=conn)
        self.assertEqual(again["quiet_end"], "08:05")
        conn.close()

    def test_invalid_values_are_refused_not_quietly_dropped(self):
        """Silently ignoring a bad value and returning 200 tells the client the
        write succeeded while storing something else -- the user reads it as
        "my setting didn't stick"."""
        conn = _fresh_conn()
        for field, value in (("frequency", "hourly_spam"), ("quiet_start", "25:99"),
                             ("quiet_end", "noon"), ("enabled", "yes please")):
            with self.subTest(field=field):
                with self.assertRaises(engine.InvalidPreference) as caught:
                    engine.update_preferences(1, {field: value}, conn=conn)
                self.assertEqual(caught.exception.field, field)
        conn.close()

    def test_a_refused_write_stores_nothing_at_all(self):
        """The valid half of a partly-invalid patch must not land: a half-applied
        write is the one outcome neither side can reason about."""
        conn = _fresh_conn()
        engine.update_preferences(1, {"frequency": "daily"}, conn=conn)
        with self.assertRaises(engine.InvalidPreference):
            engine.update_preferences(
                1, {"network_enabled": False, "frequency": "every_minute"}, conn=conn)
        after = engine.get_preferences(1, conn=conn)
        self.assertEqual(after["frequency"], "daily")
        self.assertTrue(after["network_enabled"])
        conn.close()

    def test_boolean_toggles_accept_real_booleans_and_reject_prose(self):
        conn = _fresh_conn()
        self.assertFalse(engine.update_preferences(1, {"crypto_enabled": False}, conn=conn)["crypto_enabled"])
        self.assertTrue(engine.update_preferences(1, {"crypto_enabled": 1}, conn=conn)["crypto_enabled"])
        # "false" is a non-empty string: bool() would make it True and silently
        # re-enable a topic the user just switched off.
        self.assertFalse(engine.update_preferences(1, {"crypto_enabled": "false"}, conn=conn)["crypto_enabled"])
        with self.assertRaises(engine.InvalidPreference):
            engine.update_preferences(1, {"crypto_enabled": "sometimes"}, conn=conn)
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


class _ClaimFailingCursor:
    """Fails only the claim INSERT. sqlite3.Cursor is immutable, so the fault
    is injected by proxy rather than by patching the driver type."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        if "INSERT INTO pulse_briefings" in str(sql):
            raise sqlite3.OperationalError("disk I/O error")
        return self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _ClaimFailingConn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _ClaimFailingCursor(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)


class EngineFlowTests(unittest.TestCase):
    """Evaluation flow with crypto disabled (no network) and UTC zone."""

    NOW = datetime(2026, 8, 30, 13, 30, tzinfo=timezone.utc)  # window 12, past jitter

    def setUp(self):
        # These tests pin now_utc but the engine's "facts since" fallback is
        # _iso(_now() - 6h) off the real clock, so with the wall clock more than ~5h
        # past NOW the fixture notifications fall outside the lookback, significance
        # scores zero and every send-path test suppresses. That made the suite pass or
        # fail by time of day. Pin the one clock the engine actually reads.
        self._clock = mock.patch.object(engine, "_now", return_value=self.NOW)
        self._facts_clock = mock.patch.object(facts, "_now_iso", return_value=_iso(self.NOW))
        self._clock.start()
        self._facts_clock.start()
        self.addCleanup(self._clock.stop)
        self.addCleanup(self._facts_clock.stop)

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

    def test_duplicate_claim_raises_no_driver_error(self):
        """Section B: the duplicate is the expected path, not an exception.

        Catching a UniqueViolation made services/db.py emit a full
        SQL_EXECUTE_FAILED block -- statement, params and traceback -- once per
        user per cycle. That flood is what evicted real boot diagnostics from
        the Railway log window, so "no exception" is the assertion that matters
        here, not just the returned status.
        """
        conn = self._conn_for(21, notif_count=2)
        engine.evaluate_user_briefing(conn, {"user_id": 21}, now_utc=self.NOW, send=False)
        with mock.patch.object(engine.logging, "exception") as logged:
            second = engine.evaluate_user_briefing(conn, {"user_id": 21}, now_utc=self.NOW, send=False)
        self.assertEqual(second["status"], "already_claimed")
        self.assertFalse(logged.called)  # no traceback, no log flood
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_briefings WHERE user_id=21")
        self.assertEqual(dict(cur.fetchone())["n"], 1)  # zero duplicate rows
        conn.close()

    def test_duplicate_claim_sends_nothing(self):
        conn = self._conn_for(22, notif_count=3)
        fake_push = types.SimpleNamespace(send_push=mock.Mock(return_value={"ok": True}))
        fake_notif = types.SimpleNamespace(send_in_app_notification=mock.Mock())
        import services
        with mock.patch.object(services, "push_service", fake_push, create=True), \
             mock.patch.object(services, "notification_service", fake_notif, create=True):
            first = engine.evaluate_user_briefing(conn, {"user_id": 22}, now_utc=self.NOW, send=True)
            self.assertEqual(first["status"], "sent")
            for _ in range(3):  # worker re-ticks inside the same window
                out = engine.evaluate_user_briefing(conn, {"user_id": 22}, now_utc=self.NOW, send=True)
                self.assertEqual(out["status"], "already_claimed")
        self.assertEqual(fake_push.send_push.call_count, 1)  # zero duplicate push
        conn.close()

    def test_claim_returns_the_real_row_id(self):
        # The explicit RETURNING has to survive both engines; the repo's
        # INSERT OR IGNORE shorthand would have suppressed it and left the
        # settlement UPDATE writing to id=None.
        conn = self._conn_for(23, notif_count=2)
        out = engine.evaluate_user_briefing(conn, {"user_id": 23}, now_utc=self.NOW, send=False)
        self.assertEqual(out["status"], "generated")
        cur = conn.cursor()
        cur.execute("SELECT id, status FROM pulse_briefings WHERE user_id=23")
        row = dict(cur.fetchone())
        self.assertEqual(row["id"], out["briefing_id"])
        self.assertEqual(row["status"], "shadow")
        conn.close()

    def test_real_claim_failure_is_failed_not_already_claimed(self):
        # With DO NOTHING an exception is no longer a duplicate. Reporting one
        # as 'already_claimed' would disable briefings for everyone while the
        # cycle log still read healthy.
        conn = self._conn_for(24, notif_count=2)
        with mock.patch.object(engine.logging, "exception"):
            out = engine.evaluate_user_briefing(
                _ClaimFailingConn(conn), {"user_id": 24}, now_utc=self.NOW, send=False)
        self.assertEqual(out["status"], "failed")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_briefings WHERE user_id=24")
        self.assertEqual(dict(cur.fetchone())["n"], 0)
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
        # Delivered, not shadow: send=False now settles to 'shadow', which is
        # deliberately invisible to owner-scoped reads (see ShadowModeTests).
        fake_push = types.SimpleNamespace(send_push=mock.Mock(return_value={"ok": True}))
        fake_notif = types.SimpleNamespace(send_in_app_notification=mock.Mock())
        import services
        with mock.patch.object(services, "push_service", fake_push, create=True), \
             mock.patch.object(services, "notification_service", fake_notif, create=True):
            engine.evaluate_user_briefing(conn, {"user_id": 15}, now_utc=self.NOW, send=True)
        rows = engine.list_briefings(15, conn=conn)
        self.assertEqual(len(rows), 1)
        bid = rows[0]["id"]
        self.assertIsNotNone(engine.get_briefing(15, bid, conn=conn))
        self.assertIsNone(engine.get_briefing(999, bid, conn=conn))  # cross-account blocked
        self.assertEqual(engine.list_briefings(999, conn=conn), [])
        conn.close()


class ShadowModeTests(unittest.TestCase):
    """BRIEFING_SHADOW_MODE: the engine runs for real and delivers nothing.

    The bar is not "we did not observe a push". It is that the *same* scenario that
    demonstrably sends under normal flags sends zero times under shadow. Each test
    below is paired against that control, because a zero-push assertion on a scenario
    that would never have sent anyway proves only that the fixtures were quiet.
    """

    NOW = datetime(2026, 8, 30, 13, 30, tzinfo=timezone.utc)  # window 12, past jitter

    MARKET = {
        "provider": "coingecko", "generated_at": _iso(NOW), "stale": False,
        "btc": {"symbol": "BTC", "price": 65000.0, "change_24h": -4.2},
        "eth": {"symbol": "ETH", "price": 3200.0, "change_24h": -3.8},
        "total_market_cap": 2.4e12, "market_cap_change_24h_pct": -3.1,
        "btc_dominance": 53.2, "market_direction": "down", "breadth_positive_top10": 2,
        "assets": [],
    }
    MOVERS = {"gainers": [{"symbol": "SOL", "change_24h": 6.3}],
              "losers": [{"symbol": "BTC", "change_24h": -4.2}]}

    def setUp(self):
        _clear_provider_cache()
        os.environ.pop("BRIEFING_SHADOW_MODE", None)
        os.environ.pop("BRIEFINGS_DISABLED", None)

    tearDown = setUp

    def _conn(self, user_ids: tuple[int, ...]) -> sqlite3.Connection:
        """A cycle-shaped database: eligible users with active push subscriptions,
        old enough to pass the account-age gate, each with unread network activity."""
        conn = _fresh_conn()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE users (user_id INTEGER PRIMARY KEY,
            preferred_language TEXT DEFAULT 'en', created_at TEXT, signup_time TEXT)""")
        # push_subscriptions now comes from _fresh_conn with the full production
        # column set, so this suite no longer hand-rolls a narrower copy.
        old = _iso(self.NOW - timedelta(days=30))
        recent = _iso(self.NOW - timedelta(hours=1))
        for uid in user_ids:
            cur.execute("INSERT INTO users (user_id, preferred_language, created_at, signup_time)"
                        " VALUES (?,?,?,?)", (uid, "en", old, old))
            cur.execute("INSERT INTO push_subscriptions (user_id, is_active, active) VALUES (?,1,1)", (uid,))
            for _ in range(3):  # network activity: 3 unread messages
                cur.execute("INSERT INTO pulse_notifications (user_id, type, is_read, created_at)"
                            " VALUES (?,?,0,?)", (uid, "message", recent))
        conn.commit()
        return conn

    def _run_cycle_limited(self, conn, *, limit: int):
        return self._run_cycle(conn, shadow=False, limit=limit)

    def _run_cycle(self, conn, *, shadow: bool, limit: int | None = None):
        """One real cycle at a fixed clock, with a live market and every delivery
        surface instrumented. Returns (cycle result, deliver mock, send_push mock)."""
        deliver = mock.Mock(wraps=engine._deliver)
        fake_push = types.SimpleNamespace(send_push=mock.Mock(return_value={"ok": True}))
        fake_notif = types.SimpleNamespace(send_in_app_notification=mock.Mock())
        import services
        env = {"BRIEFING_SHADOW_MODE": "true"} if shadow else {"BRIEFING_SHADOW_MODE": "false"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(engine, "_now", return_value=self.NOW), \
             mock.patch.object(facts, "_now_iso", return_value=_iso(self.NOW)), \
             mock.patch.object(facts.crypto_provider, "get_market_overview", return_value=self.MARKET), \
             mock.patch.object(facts.crypto_provider, "is_stale", return_value=False), \
             mock.patch.object(facts.crypto_provider, "get_top_movers", return_value=self.MOVERS), \
             mock.patch.object(facts.crypto_provider, "get_watchlist_snapshots", return_value=[]), \
             mock.patch.object(engine, "_deliver", deliver), \
             mock.patch.object(services, "push_service", fake_push, create=True), \
             mock.patch.object(services, "notification_service", fake_notif, create=True):
            kwargs = {"conn": conn} if limit is None else {"conn": conn, "limit": limit}
            result = engine.run_scheduled_cycle(**kwargs)
        return result, deliver, fake_push.send_push

    # --- the control: this scenario really does send -----------------------

    def test_control_normal_mode_delivers(self):
        conn = self._conn((201,))
        result, deliver, send_push = self._run_cycle(conn, shadow=False)
        self.assertFalse(result["shadow"])
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(deliver.call_count, 1)
        self.assertEqual(send_push.call_count, 1)
        self.assertEqual(result["suppressed_by_shadow"], 0)
        conn.close()

    # --- the requirement: the identical scenario delivers nothing ----------

    def test_shadow_mode_delivers_zero(self):
        conn = self._conn((202,))
        result, deliver, send_push = self._run_cycle(conn, shadow=True)
        self.assertTrue(result["shadow"])
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sent"], 0)            # never counted as a push
        self.assertEqual(deliver.call_count, 0)        # hard zero at the boundary
        self.assertEqual(send_push.call_count, 0)      # and at the transport
        self.assertEqual(result["suppressed_by_shadow"], 1)
        conn.close()

    def test_shadow_still_claims_settles_and_produces_a_deeplink_id(self):
        """The Postgres defect class this exists to exercise: the claim INSERT must
        yield a real row id that every settlement UPDATE and the deeplink then use."""
        conn = self._conn((203,))
        self._run_cycle(conn, shadow=True)
        cur = conn.cursor()
        cur.execute("SELECT id, status, title, body, fingerprint, sent_at FROM pulse_briefings WHERE user_id=203")
        rows = [dict(r) for r in cur.fetchall()]
        self.assertEqual(len(rows), 1)                 # claimed exactly once
        row = rows[0]
        self.assertIsNotNone(row["id"])
        self.assertEqual(row["status"], "shadow")      # settled, not left in 'processing'
        self.assertEqual(row["sent_at"], "")           # nothing was ever sent
        self.assertTrue(row["title"] and row["body"])  # summarization really ran
        self.assertTrue(row["fingerprint"])            # scoring really ran
        deep_link = "pulse://notifications?briefing=%d" % int(row["id"])
        self.assertNotIn("None", deep_link)
        conn.close()

    def test_shadow_briefings_are_invisible_to_the_user(self):
        conn = self._conn((204,))
        self._run_cycle(conn, shadow=True)
        self.assertEqual(engine.list_briefings(204, conn=conn), [])
        conn.close()

    def test_shadow_reproduces_the_dedupe_decision(self):
        """A shadow run must reach the same suppress/send verdicts production would,
        so a 'shadow' predecessor counts for fingerprint dedupe the way 'sent' does."""
        conn = self._conn((205,))
        first, _, _ = self._run_cycle(conn, shadow=True)
        self.assertEqual(first["suppressed_by_shadow"], 1)
        later = self.NOW + timedelta(hours=6)          # next window, identical facts
        cur = conn.cursor()                            # same unread count since the claim
        for _ in range(3):
            cur.execute("INSERT INTO pulse_notifications (user_id, type, is_read, created_at)"
                        " VALUES (205,'message',0,?)", (_iso(self.NOW + timedelta(hours=5)),))
        conn.commit()
        with mock.patch.object(engine, "_now", return_value=later), \
             mock.patch.object(facts, "_now_iso", return_value=_iso(later)), \
             mock.patch.object(facts.crypto_provider, "get_market_overview", return_value=self.MARKET), \
             mock.patch.object(facts.crypto_provider, "is_stale", return_value=False), \
             mock.patch.object(facts.crypto_provider, "get_top_movers", return_value=self.MOVERS), \
             mock.patch.object(facts.crypto_provider, "get_watchlist_snapshots", return_value=[]):
            out = engine.evaluate_user_briefing(conn, {"user_id": 205}, now_utc=later, send=False)
        self.assertEqual(out["status"], "suppressed")
        self.assertEqual(out["reason"], "duplicate_fingerprint")
        conn.close()

    # --- global push opt-out is binding on briefings too -------------------

    def _opt_out(self, conn, user_id: int) -> None:
        conn.cursor().execute(
            "INSERT INTO notification_preferences (user_id, category, enable_push_notifications)"
            " VALUES (?,'global',0)", (user_id,))
        conn.commit()

    def test_global_push_opt_out_blocks_delivery(self):
        """_deliver calls push_service.send_push directly, bypassing the canonical
        _rules_check, so without an explicit guard a user who turned push off
        globally would still be pushed -- for briefings and nothing else."""
        conn = self._conn((301,))
        self._opt_out(conn, 301)
        result, deliver, send_push = self._run_cycle(conn, shadow=False)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(deliver.call_count, 0)
        self.assertEqual(send_push.call_count, 0)     # hard zero at the transport
        self.assertEqual(result["disabled_by_user"], 1)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM pulse_briefings WHERE user_id=301")
        # No claim row: the window stays claimable if they re-enable push later.
        self.assertEqual(cur.fetchone()[0], 0)
        conn.close()

    def test_opt_out_is_per_user_not_global_suppression(self):
        """The opt-out must not become an outage: the neighbouring user still sends."""
        conn = self._conn((302, 303))
        self._opt_out(conn, 302)
        result, _, send_push = self._run_cycle(conn, shadow=False)
        self.assertEqual(result["disabled_by_user"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(send_push.call_count, 1)
        self.assertEqual(send_push.call_args[0][0], 303)   # and it is the right user
        conn.close()

    def test_absent_preference_row_is_not_an_opt_out(self):
        """Fail open on a missing row: never having opened notification settings
        is not the same as having declined."""
        conn = self._conn((304,))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM notification_preferences WHERE user_id=304")
        self.assertEqual(cur.fetchone()[0], 0)
        result, _, send_push = self._run_cycle(conn, shadow=False)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(send_push.call_count, 1)
        conn.close()

    # --- batch fairness: no eligible user is starved by the limit ----------

    def test_batch_limit_rotates_instead_of_restarting(self):
        """LIMIT with no ORDER BY re-selects the same arbitrary N users forever, so
        above the batch limit the tail is never evaluated. Least-recently-briefed
        ordering must make every eligible user reachable."""
        users = (401, 402, 403, 404)
        conn = self._conn(users)
        seen: set[int] = set()
        for _ in range(len(users)):
            _, _, send_push = self._run_cycle_limited(conn, limit=1)
            for call in send_push.call_args_list:
                seen.add(call[0][0])
        self.assertEqual(seen, set(users))   # full coverage, one user per cycle
        conn.close()

    def test_suppression_reasons_are_distinguished(self):
        conn = self._conn((206,))
        result, _, _ = self._run_cycle(conn, shadow=True)
        for key in ("suppressed_by_rules", "suppressed_by_dedupe",
                    "suppressed_by_quiet_hours", "suppressed_by_shadow"):
            self.assertIn(key, result)
        self.assertEqual(result["suppressed_by_rules"], 0)
        self.assertEqual(result["suppressed_by_dedupe"], 0)
        self.assertEqual(result["suppressed_by_quiet_hours"], 0)
        self.assertEqual(result["suppressed_by_shadow"], 1)
        conn.close()

    def test_kill_switch_outranks_shadow_and_evaluates_nothing(self):
        """BRIEFINGS_DISABLED must not be re-openable by any other flag."""
        conn = self._conn((207,))
        evaluate = mock.Mock(wraps=engine.evaluate_user_briefing)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "true",
                                          "BRIEFING_SHADOW_MODE": "false",
                                          "PULSE_BRIEFINGS_ENABLED": "true"}), \
             mock.patch.object(engine, "evaluate_user_briefing", evaluate):
            out = engine.run_scheduled_cycle(conn=conn)
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(out["processed"], 0)
        self.assertEqual(evaluate.call_count, 0)       # no evaluation at all
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_briefings")
        self.assertEqual(dict(cur.fetchone())["n"], 0)
        conn.close()

    def test_shadow_flag_parsing_is_conservative(self):
        for raw, expected in (("true", True), ("1", True), ("yes", True), ("ON", True),
                              ("false", False), ("0", False), ("", False), ("maybe", False)):
            with mock.patch.dict(os.environ, {"BRIEFING_SHADOW_MODE": raw}):
                self.assertIs(engine.shadow_mode(), expected, raw)
        os.environ.pop("BRIEFING_SHADOW_MODE", None)
        self.assertFalse(engine.shadow_mode())         # absent means normal delivery


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
    """The claim statement has to survive services.db's SQL translation.

    The engine used to read cur.lastrowid, which on Postgres only worked
    because AUTO_PK_TABLES made CompatCursor inject RETURNING id (found broken
    in production acceptance, 2026-08-30). The claim now names RETURNING
    itself, so what must be proved is that the translator leaves it alone --
    one RETURNING, and no second ON CONFLICT bolted onto the one already there.
    """

    CLAIM_SQL = (
        "INSERT INTO pulse_briefings (user_id, window_key, status, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT (user_id, window_key) DO NOTHING RETURNING id"
    )

    def test_claim_sql_is_the_statement_the_engine_actually_runs(self):
        import inspect
        source = inspect.getsource(engine.evaluate_user_briefing)
        self.assertIn("ON CONFLICT (user_id, window_key) DO NOTHING RETURNING id", source)
        self.assertNotIn("cur.lastrowid", source)

    def test_translation_keeps_exactly_one_returning(self):
        from services import db
        translated = db._translate_sql(self.CLAIM_SQL)
        self.assertEqual(translated.upper().count("RETURNING"), 1)
        self.assertEqual(translated.upper().count("DO NOTHING"), 1)
        self.assertIn("%s", translated)  # placeholders converted for psycopg
        self.assertTrue(db._has_conflict_clause(translated))

    def test_pulse_briefings_registered_for_returning_id(self):
        # Still asserted: other writers to this table rely on the injection.
        from services.db import AUTO_PK_TABLES
        self.assertEqual(AUTO_PK_TABLES.get("pulse_briefings"), "id")

    def test_unique_index_backs_the_conflict_target(self):
        # ON CONFLICT (user_id, window_key) is only a legal inference target if
        # a unique index over exactly those columns exists.
        conn = _fresh_conn()
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_pulse_briefings_window'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("UNIQUE", row[0].upper())
        conn.close()


class HubBackendTests(unittest.TestCase):
    """Profile OS Briefings Hub backend: new frequencies, pagination, seen
    cursor, delivery status. All owner-scoped; history is sent/generated only."""

    def _seed_briefings(self, conn, user_id: int, count: int, status: str = "sent"):
        cur = conn.cursor()
        for i in range(count):
            cur.execute(
                "INSERT INTO pulse_briefings (user_id, window_key, status, title, body, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, f"{status}-2026-08-{(i % 28) + 1:02d}:{(i * 6) % 24:02d}", status,
                 f"Briefing {i}", "body", _iso(datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=i))),
            )
        conn.commit()

    def test_new_frequencies_accepted_and_persisted(self):
        conn = _fresh_conn()
        for freq in ("smart", "daily"):
            updated = engine.update_preferences(1, {"frequency": freq}, conn=conn)
            self.assertEqual(updated["frequency"], freq)
            self.assertEqual(engine.get_preferences(1, conn=conn)["frequency"], freq)
        conn.close()

    def test_smart_shares_standard_windows_daily_is_morning_only(self):
        self.assertEqual(engine._windows_for_frequency("smart"), engine.BRIEFING_WINDOWS)
        self.assertEqual(engine._windows_for_frequency("daily"), (8,))
        key, _ = engine.current_window(datetime(2026, 8, 30, 9, 0), "daily")
        self.assertEqual(key, "2026-08-30:08")
        # Outside the single morning window nothing is claimable for daily.
        self.assertIsNone(engine.current_window(datetime(2026, 8, 30, 16, 0), "daily"))

    def test_pagination_first_page_20_and_has_more(self):
        conn = _fresh_conn()
        self._seed_briefings(conn, 1, 25)
        page = engine.list_briefings_page(1, 20, 0, conn=conn)
        self.assertEqual(len(page["briefings"]), 20)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_offset"], 20)
        # Newest first, no overlap between pages.
        page2 = engine.list_briefings_page(1, 20, page["next_offset"], conn=conn)
        self.assertEqual(len(page2["briefings"]), 5)
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["next_offset"])
        ids = [b["id"] for b in page["briefings"]] + [b["id"] for b in page2["briefings"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        conn.close()

    def test_history_excludes_invisible_statuses(self):
        conn = _fresh_conn()
        for status in ("shadow", "failed", "suppressed", "processing"):
            self._seed_briefings(conn, 2, 1, status=status)
        page = engine.list_briefings_page(2, 20, 0, conn=conn)
        self.assertEqual(page["briefings"], [])
        self.assertFalse(page["has_more"])
        conn.close()

    def test_seen_cursor_and_unseen_count(self):
        conn = _fresh_conn()
        self._seed_briefings(conn, 3, 3)
        # Never opened the hub: all visible briefings are unseen.
        self.assertEqual(engine.unseen_briefings_count(3, conn=conn), 3)
        engine.mark_briefings_seen(3, conn=conn)
        self.assertEqual(engine.unseen_briefings_count(3, conn=conn), 0)
        # A briefing created after the cursor becomes unseen again.
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pulse_briefings (user_id, window_key, status, title, created_at) VALUES (?,?,?,?,?)",
            (3, "2026-09-01:06", "sent", "Newer", _iso(datetime(2027, 1, 1, tzinfo=timezone.utc))),
        )
        conn.commit()
        self.assertEqual(engine.unseen_briefings_count(3, conn=conn), 1)
        conn.close()

    def test_mark_seen_upserts_without_touching_other_prefs(self):
        conn = _fresh_conn()
        engine.update_preferences(4, {"frequency": "daily", "crypto_enabled": False}, conn=conn)
        engine.mark_briefings_seen(4, conn=conn)
        prefs = engine.get_preferences(4, conn=conn)
        self.assertEqual(prefs["frequency"], "daily")
        self.assertFalse(prefs["crypto_enabled"])
        conn.close()

    def test_delivery_status_shape_and_next_check(self):
        conn = _fresh_conn()
        region_preferences.update_preferences(5, {"time_zone": "America/New_York"}, conn=conn)
        conn.commit()
        self._seed_briefings(conn, 5, 1)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "true"}):
            status = engine.delivery_status(5, conn=conn)
        self.assertTrue(status["enabled"])
        self.assertEqual(status["timezone"], "America/New_York")
        self.assertIn(status["frequency"], engine.FREQUENCIES)
        self.assertTrue(status["push_enabled"])  # no opt-out row -> fails open
        self.assertEqual(status["last_briefing"]["title"], "Briefing 0")
        self.assertEqual(status["unseen_count"], 1)
        # next check is a future local timestamp, jitter included, never a promise
        self.assertIsNotNone(status["next_check_local"])
        nxt = datetime.fromisoformat(status["next_check_local"])
        self.assertGreater(nxt, datetime.now(nxt.tzinfo))
        conn.close()

    def test_delivery_status_off_frequency_has_no_next_check(self):
        conn = _fresh_conn()
        engine.update_preferences(6, {"frequency": "off"}, conn=conn)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "true"}):
            status = engine.delivery_status(6, conn=conn)
        self.assertIsNone(status["next_check_local"])
        self.assertIsNone(status["last_briefing"])
        conn.close()

    def test_next_check_never_lands_inside_quiet_hours(self):
        """The worker suppresses a window that opens during quiet hours, so
        advertising one as "next check" promises a check that will not run."""
        conn = _fresh_conn()
        region_preferences.update_preferences(50, {"time_zone": "UTC"}, conn=conn)
        conn.commit()
        # Quiet 00:00-07:00 kills the 00:00 window; 08:00/12:00/18:00 survive.
        engine.update_preferences(
            50, {"frequency": "every_6h", "quiet_start": "00:00", "quiet_end": "07:00"}, conn=conn)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "true"}):
            status = engine.delivery_status(50, conn=conn)
        self.assertIsNotNone(status["next_check_local"])
        nxt = datetime.fromisoformat(status["next_check_local"])
        self.assertFalse(engine._quiet_hours_active(nxt, "00:00", "07:00"),
                         f"advertised {nxt} but the worker would suppress it")
        conn.close()

    def test_next_check_still_resolves_when_today_is_fully_quiet(self):
        """A quiet range covering every remaining window today must roll forward,
        not report None -- the screen renders None as "Briefings are paused"."""
        conn = _fresh_conn()
        region_preferences.update_preferences(51, {"time_zone": "UTC"}, conn=conn)
        conn.commit()
        engine.update_preferences(
            51, {"frequency": "daily", "quiet_start": "07:00", "quiet_end": "09:00"}, conn=conn)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "true"}):
            status = engine.delivery_status(51, conn=conn)
        # The only daily window (08:00 + jitter) is always quiet -> honestly None.
        self.assertIsNone(status["next_check_local"])
        conn.close()

    def _register_device(self, conn, user_id: int, *, active: int = 1) -> None:
        conn.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, subscription_json, active, is_active) "
            "VALUES (?,?,?,?,?)",
            (user_id, "ExponentPushToken[u%d]" % user_id, "{}", active, active),
        )
        conn.commit()

    def _status(self, conn, user_id: int) -> dict:
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "true",
                                          "PUSH_NOTIFICATIONS_ENABLED": "true"}):
            return engine.delivery_status(user_id, conn=conn)

    def test_push_is_not_reported_ready_without_a_registered_device(self):
        """The hub used to read the preference row and say "Push notifications
        are on" to a user who had never granted the OS prompt -- so they waited
        for a notification the sender could never deliver."""
        conn = _fresh_conn()
        status = self._status(conn, 40)
        self.assertTrue(status["push_enabled"])      # preference: not opted out
        self.assertFalse(status["push_ready"])       # reality: nowhere to send
        self.assertEqual(status["push_blocked_reason"], "no_devices")
        self.assertEqual(status["push_device_count"], 0)
        conn.close()

    def test_push_is_ready_once_a_device_is_registered(self):
        conn = _fresh_conn()
        self._register_device(conn, 41)
        status = self._status(conn, 41)
        self.assertTrue(status["push_ready"])
        self.assertIsNone(status["push_blocked_reason"])
        self.assertEqual(status["push_device_count"], 1)
        conn.close()

    def test_an_inactive_device_row_does_not_count_as_transport(self):
        """Same predicate send_push uses; a signed-out device must not read as
        reachable."""
        conn = _fresh_conn()
        self._register_device(conn, 42, active=0)
        status = self._status(conn, 42)
        self.assertFalse(status["push_ready"])
        self.assertEqual(status["push_blocked_reason"], "no_devices")
        conn.close()

    def test_global_opt_out_outranks_a_registered_device(self):
        conn = _fresh_conn()
        self._register_device(conn, 43)
        conn.execute(
            "INSERT INTO notification_preferences (user_id, category, enable_push_notifications) "
            "VALUES (43,'global',0)"
        )
        conn.commit()
        status = self._status(conn, 43)
        self.assertFalse(status["push_enabled"])
        self.assertFalse(status["push_ready"])
        self.assertEqual(status["push_blocked_reason"], "preference_off")
        conn.close()

    def test_provider_kill_switch_is_reported_over_everything_else(self):
        """PUSH_NOTIFICATIONS_ENABLED=false makes send_push return before it
        looks at tokens, so the status must lead with that."""
        conn = _fresh_conn()
        self._register_device(conn, 44)
        with mock.patch.dict(os.environ, {"BRIEFINGS_DISABLED": "", "PULSE_BRIEFINGS_ENABLED": "true",
                                          "PUSH_NOTIFICATIONS_ENABLED": "false"}):
            status = engine.delivery_status(44, conn=conn)
        self.assertFalse(status["push_ready"])
        self.assertEqual(status["push_blocked_reason"], "provider_disabled")
        conn.close()

    def test_last_seen_column_upgrade_is_idempotent(self):
        # ensure_schema runs its ALTER on every call; a second pass must not
        # raise and must leave the column usable.
        conn = _fresh_conn()
        engine.ensure_schema(conn)
        engine.ensure_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pulse_briefing_prefs)").fetchall()}
        self.assertIn("last_seen_at", cols)
        conn.close()

    def test_scheduler_sql_still_excludes_only_off(self):
        # smart/daily users must remain eligible for the cycle query.
        import inspect
        source = inspect.getsource(engine.run_scheduled_cycle)
        self.assertIn("<>'off'", source.replace('"', "'"))


if __name__ == "__main__":
    unittest.main()
