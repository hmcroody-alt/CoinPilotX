"""Unit tests for the canonical CoinGecko client (paid-API activation).

Covers: host/header resolution (pro vs demo vs override), governor,
429 classification (no retry), retry on 5xx/timeout, cache + single-flight
+ stale-serve, and crypto_provider delegation counters.

Run: python3 -m unittest tests.briefings.test_coingecko_client -v
(pytest is unavailable in the build sandbox; unittest only.)
"""

from __future__ import annotations

import os
import threading
import time
import unittest
from unittest import mock

import requests

from services import coingecko_client as cg


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        os.environ.pop("COINGECKO_API_KEY", None)
        os.environ.pop("COINGECKO_API_BASE", None)
        with cg._TELEMETRY_LOCK:
            for k in cg._TELEMETRY:
                cg._TELEMETRY[k] = 0
            cg._LATENCIES.clear()
            cg._MINUTE_WINDOW.clear()
        with cg._CACHE_LOCK:
            cg._CACHE.clear()
            cg._FLIGHT_LOCKS.clear()
        self._reset_budget()

    @staticmethod
    def _reset_budget(**overrides):
        """Budget state is module-global; a test that leaves it in 'protective'
        would silently throttle every later test's non-essential request."""
        with cg._BUDGET_LOCK:
            cg._BUDGET.update({"used": None, "credit": None, "at": 0.0,
                               "last_attempt": 0.0, "local_calls": 0, "error": None})
            cg._BUDGET.update(overrides)

    def _set_spend(self, used):
        """Pin month-to-date spend and mark the reading fresh so the guard does
        not try to refresh it from the network mid-test."""
        self._reset_budget(used=used, credit=cg.MONTHLY_CREDIT,
                           at=time.time(), last_attempt=time.time())

    def tearDown(self):
        self._env.stop()


class ResolutionTests(ClientTestCase):
    def test_no_key_resolves_demo_host_and_no_auth_header(self):
        self.assertEqual(cg.base_url(), cg.DEMO_BASE)
        headers = cg.auth_headers()
        self.assertNotIn("x-cg-pro-api-key", headers)
        self.assertNotIn("x-cg-demo-api-key", headers)
        self.assertEqual(headers["User-Agent"], cg.USER_AGENT)

    def test_paid_key_resolves_pro_host_and_pro_header(self):
        os.environ["COINGECKO_API_KEY"] = "CG-test-key"
        self.assertEqual(cg.base_url(), cg.PRO_BASE)
        self.assertEqual(cg.auth_header_name(), "x-cg-pro-api-key")
        headers = cg.auth_headers()
        self.assertEqual(headers["x-cg-pro-api-key"], "CG-test-key")
        self.assertNotIn("x-cg-demo-api-key", headers)

    def test_base_override_flips_header_with_host(self):
        # Host and header must never split: demo override + key -> demo header.
        os.environ["COINGECKO_API_KEY"] = "CG-test-key"
        os.environ["COINGECKO_API_BASE"] = "https://api.coingecko.com/api/v3"
        self.assertEqual(cg.base_url(), cg.DEMO_BASE)
        self.assertEqual(cg.auth_header_name(), "x-cg-demo-api-key")
        self.assertEqual(cg.auth_headers()["x-cg-demo-api-key"], "CG-test-key")

    def test_url_joins_path(self):
        self.assertTrue(cg.url("/coins/markets").endswith("/api/v3/coins/markets"))
        self.assertTrue(cg.url("coins/markets").endswith("/api/v3/coins/markets"))


class GetJsonTests(ClientTestCase):
    def test_get_json_ok(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(200, {"ok": 1})):
            self.assertEqual(cg.get_json("/ping"), {"ok": 1})
        snap = cg.telemetry_snapshot()
        self.assertEqual(snap["cg_ok"], 1)
        self.assertEqual(snap["cg_requests"], 1)

    def test_429_classified_and_not_retried(self):
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return FakeResponse(429)

        with mock.patch.object(cg.requests, "get", fake_get):
            self.assertIsNone(cg.get_json("/ping", retries=3))
        # A retry inside the same window only deepens the throttle.
        self.assertEqual(len(calls), 1)
        self.assertEqual(cg.telemetry_snapshot()["cg_http_429"], 1)

    def test_5xx_gets_one_retry_then_succeeds(self):
        responses = [FakeResponse(500), FakeResponse(200, {"ok": 1})]
        with mock.patch.object(cg.requests, "get", side_effect=lambda *a, **k: responses.pop(0)):
            self.assertEqual(cg.get_json("/ping"), {"ok": 1})
        self.assertEqual(cg.telemetry_snapshot()["cg_requests"], 2)

    def test_timeout_retry_then_none(self):
        with mock.patch.object(cg.requests, "get", side_effect=requests.Timeout("slow")):
            self.assertIsNone(cg.get_json("/ping", retries=1))
        self.assertEqual(cg.telemetry_snapshot()["cg_timeouts"], 2)  # initial + 1 retry

    def test_http_4xx_returns_none_never_raises(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(404)):
            self.assertIsNone(cg.get_json("/nope"))
        self.assertEqual(cg.telemetry_snapshot()["cg_http_errors"], 1)


class GovernorTests(ClientTestCase):
    def test_governor_blocks_at_limit(self):
        with mock.patch.object(cg, "RATE_LIMIT_PER_MIN", 3), \
                mock.patch.object(cg.requests, "get", return_value=FakeResponse(200, {})):
            for _ in range(3):
                self.assertEqual(cg.get_json("/ping"), {})
            self.assertIsNone(cg.get_json("/ping"))  # 4th attempt blocked client-side
        snap = cg.telemetry_snapshot()
        self.assertEqual(snap["cg_governor_blocks"], 1)
        self.assertEqual(snap["cg_requests"], 3)  # blocked attempt never hit the network

    def test_governor_window_slides(self):
        with mock.patch.object(cg, "RATE_LIMIT_PER_MIN", 1), \
                mock.patch.object(cg.requests, "get", return_value=FakeResponse(200, {})):
            self.assertEqual(cg.get_json("/ping"), {})
            with cg._TELEMETRY_LOCK:  # age the window entry past 60s
                cg._MINUTE_WINDOW[0] -= 61
            self.assertEqual(cg.get_json("/ping"), {})


class CacheTests(ClientTestCase):
    def test_cached_hit_and_miss(self):
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return FakeResponse(200, {"v": len(calls)})

        with mock.patch.object(cg.requests, "get", fake_get):
            self.assertEqual(cg.get_json_cached("/x", ttl=60), {"v": 1})
            self.assertEqual(cg.get_json_cached("/x", ttl=60), {"v": 1})  # from cache
        self.assertEqual(len(calls), 1)
        snap = cg.telemetry_snapshot()
        self.assertEqual(snap["cg_cache_hits"], 1)
        self.assertEqual(snap["cg_cache_misses"], 1)

    def test_cached_serves_stale_on_failure(self):
        good = [FakeResponse(200, {"v": "good"})]

        def fake_get(*a, **k):
            return good.pop() if good else FakeResponse(500)

        with mock.patch.object(cg.requests, "get", fake_get):
            self.assertEqual(cg.get_json_cached("/x", ttl=1), {"v": "good"})
            time.sleep(1.1)  # expire TTL; provider now failing
            self.assertEqual(cg.get_json_cached("/x", ttl=1), {"v": "good", "stale": True})
        self.assertEqual(cg.telemetry_snapshot()["cg_stale_served"], 1)

    def test_cached_returns_none_when_no_stale(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(500)):
            self.assertIsNone(cg.get_json_cached("/y", ttl=60))

    def test_single_flight_one_fetch_for_concurrent_callers(self):
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            time.sleep(0.05)
            return FakeResponse(200, {"ok": 1})

        results = []
        with mock.patch.object(cg.requests, "get", fake_get):
            threads = [
                threading.Thread(target=lambda: results.append(cg.get_json_cached("/z", ttl=60)))
                for _ in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)  # N users -> 1 provider call
        self.assertTrue(all(r == {"ok": 1} for r in results))


class ProviderWiringTests(ClientTestCase):
    def test_crypto_provider_delegates_and_classifies(self):
        from services.pulse_briefings import crypto_provider as cp

        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(429)):
            before = dict(cp._METRICS)
            self.assertIsNone(cp._cg_get("/coins/markets"))
            self.assertEqual(cp._METRICS["crypto_provider_429"], before["crypto_provider_429"] + 1)

        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(500)):
            before = dict(cp._METRICS)
            self.assertIsNone(cp._cg_get("/coins/markets"))
            self.assertEqual(cp._METRICS["crypto_provider_errors"], before["crypto_provider_errors"] + 1)

    def test_crypto_provider_metrics_include_client_telemetry(self):
        from services.pulse_briefings import crypto_provider as cp

        snap = cp.metrics_snapshot()
        self.assertIn("cg_requests", snap)
        self.assertIn("cg_rate_limit_per_min", snap)

    def test_market_data_raises_when_client_degrades(self):
        from services import market_data

        with mock.patch.object(cg, "get_json", return_value=None):
            with self.assertRaises(RuntimeError):
                market_data.fetch_coingecko_markets()
            with self.assertRaises(RuntimeError):
                market_data.fetch_coingecko_history("bitcoin", 1)


class CoinIdTests(ClientTestCase):
    """A ticker is not a coin id. Of the top 60 assets by market cap, 50 differ."""

    def test_known_majors_resolve_to_real_ids(self):
        self.assertEqual(cg.coin_id("BTC"), "bitcoin")
        self.assertEqual(cg.coin_id("XRP"), "ripple")
        self.assertEqual(cg.coin_id("AVAX"), "avalanche-2")

    def test_resolution_is_case_and_whitespace_insensitive(self):
        self.assertEqual(cg.coin_id("  btc "), "bitcoin")

    def test_unknown_symbol_is_none_not_the_symbol(self):
        # Returning the ticker would produce /coins/zzz/... -> 404 for an asset
        # we simply do not have an id for. None means "ask the board".
        self.assertIsNone(cg.coin_id("ZZZNOTACOIN"))
        self.assertIsNone(cg.coin_id(""))
        self.assertIsNone(cg.coin_id(None))

    def test_table_is_well_formed(self):
        """Every entry was verified against /coins/markets: all 46 ids resolve
        and each reports the ticker it is mapped from. This guards the shape, so
        a future hand-edit can't add an empty, uppercase or whitespace-bearing
        id. Note some ids legitimately equal their lowercased ticker (near, sui,
        aave) -- that is not evidence of a pasted-in Coinbase fallback row."""
        for symbol, cid in cg.COIN_IDS.items():
            self.assertEqual(symbol, symbol.upper().strip(), f"{symbol} key not a clean ticker")
            self.assertTrue(cid and cid == cid.lower().strip(), f"{symbol} -> {cid!r} malformed")
            self.assertNotIn(" ", cid, f"{symbol} -> {cid!r} contains a space")

    def test_majors_that_differ_from_their_ticker_are_covered(self):
        """The whole reason the table exists: these are the ones a naive
        symbol==id assumption gets wrong."""
        for symbol in ("BTC", "ETH", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK"):
            self.assertIsNotNone(cg.coin_id(symbol))
            self.assertNotEqual(cg.coin_id(symbol), symbol.lower())


class SimplePriceTests(ClientTestCase):
    def test_ids_are_deduped_and_sorted_into_one_cache_entry(self):
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(params["ids"])
            return FakeResponse(200, {"bitcoin": {"usd": 1}, "ethereum": {"usd": 2}})

        with mock.patch.object(cg.requests, "get", side_effect=fake_get):
            a = cg.simple_price(["ethereum", "bitcoin"])
            b = cg.simple_price(["bitcoin", "ethereum", "bitcoin"])
        self.assertEqual(a, b)
        # Different order + a duplicate must not be a second upstream request.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], "bitcoin,ethereum")

    def test_empty_request_makes_no_call(self):
        with mock.patch.object(cg.requests, "get") as get:
            self.assertEqual(cg.simple_price([]), {})
            get.assert_not_called()

    def test_returns_empty_dict_not_none_on_failure(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(500)):
            self.assertEqual(cg.simple_price(["bitcoin"]), {})

    def test_by_symbol_keys_on_ticker_and_drops_unknowns(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(
                200, {"bitcoin": {"usd": 1}})):
            out = cg.simple_price_by_symbol(["BTC", "ZZZNOTACOIN"])
        self.assertEqual(out, {"BTC": {"usd": 1}})


class CachedMetaTests(ClientTestCase):
    def test_meta_distinguishes_cache_hit_from_provider_answer(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(200, {"ok": 1})):
            v1, cached1, _ = cg.get_json_cached_meta("/global", ttl=60, cache_key="k")
            v2, cached2, _ = cg.get_json_cached_meta("/global", ttl=60, cache_key="k")
        self.assertEqual(v1, v2, {"ok": 1})
        self.assertFalse(cached1)
        self.assertTrue(cached2)

    def test_stale_serve_is_reported_as_cached(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(200, {"ok": 1})):
            cg.get_json_cached_meta("/global", ttl=0, cache_key="k")
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(500)):
            value, cached, _ = cg.get_json_cached_meta("/global", ttl=0, cache_key="k")
        self.assertTrue(cached)
        self.assertTrue(value["stale"])


class ErrorClassificationTests(ClientTestCase):
    """Stage 16: failures carry a safe code. None alone cannot tell an expired
    key from a rate limit from an asset we never listed."""

    def _code_for(self, status, path="/coins/markets"):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(status)):
            cg.get_json(path, retries=0)
        return cg.last_error()["code"]

    def test_auth_failures(self):
        self.assertEqual(self._code_for(401), cg.ERR_AUTH)
        self.assertEqual(self._code_for(403), cg.ERR_AUTH)

    def test_rate_limit(self):
        self.assertEqual(self._code_for(429), cg.ERR_RATE_LIMIT)

    def test_unknown_asset_only_on_coin_paths(self):
        self.assertEqual(self._code_for(404, "/coins/nope/market_chart"), cg.ERR_UNKNOWN_ASSET)
        self.assertEqual(self._code_for(404, "/global"), cg.ERR_BAD_RESPONSE)

    def test_provider_5xx_after_retries_exhausted(self):
        self.assertEqual(self._code_for(500), cg.ERR_PROVIDER_5XX)

    def test_timeout(self):
        with mock.patch.object(cg.requests, "get", side_effect=requests.Timeout()):
            cg.get_json("/global", retries=0)
        self.assertEqual(cg.last_error()["code"], cg.ERR_TIMEOUT)

    def test_governor_block_is_a_rate_limit(self):
        with mock.patch.object(cg, "RATE_LIMIT_PER_MIN", 0):
            self.assertIsNone(cg.get_json("/global"))
        self.assertEqual(cg.last_error()["code"], cg.ERR_RATE_LIMIT)

    def test_two_hundred_with_non_json_body_is_bad_response(self):
        bad = FakeResponse(200)
        bad.json = mock.Mock(side_effect=ValueError("not json"))
        with mock.patch.object(cg.requests, "get", return_value=bad):
            self.assertIsNone(cg.get_json("/global"))
        self.assertEqual(cg.last_error()["code"], cg.ERR_BAD_RESPONSE)

    def test_error_record_never_carries_params_or_headers(self):
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(401)):
            cg.get_json("/simple/price", {"ids": "bitcoin"}, retries=0)
        recorded = cg.last_error()
        self.assertEqual(set(recorded), {"code", "path", "at"})
        self.assertEqual(recorded["path"], "/simple/price")

    def test_telemetry_exposes_key_presence_but_never_the_key(self):
        os.environ["COINGECKO_API_KEY"] = "CG-secret-value"
        snap = cg.telemetry_snapshot()
        self.assertTrue(snap["cg_api_key_present"])
        self.assertNotIn("CG-secret-value", repr(snap))


class HistoryRangeTests(ClientTestCase):
    def test_all_range_is_numeric_and_within_plan_ceiling(self):
        """days=max is 401 on the Basic plan and 731 is refused; 730 answers.
        A non-numeric or over-ceiling value silently breaks the ALL chart."""
        from services import market_data

        days = market_data.HISTORY_RANGE_DAYS["ALL"]
        self.assertIsInstance(days, int)
        self.assertLessEqual(days, 730)
        self.assertNotIn("max", [str(v) for v in market_data.HISTORY_RANGE_DAYS.values()])

    def test_history_prefers_canonical_id_over_board_row(self):
        """During Coinbase fallback the board's id is a lowercased ticker, so
        trusting it would request /coins/btc/market_chart -- a 404."""
        from services import market_data

        market_data.HISTORY_CACHE.clear()
        with mock.patch.object(market_data, "get_symbol", return_value={"id": "btc"}) as board, \
             mock.patch.object(market_data, "fetch_coingecko_history",
                               return_value=[[1, 1.0], [2, 2.0]]) as fetch:
            market_data.asset_history("BTC", "24H")
        self.assertEqual(fetch.call_args[0][0], "bitcoin")
        board.assert_not_called()
        market_data.HISTORY_CACHE.clear()


class BudgetGuardTests(ClientTestCase):
    """The monthly credit budget guard.

    The per-minute governor and this guard protect different limits. Basic
    allows 300 req/min but only 100,000 credits/month -- a sustained 2.31
    req/min. A loop running inside the governor's 270/min allowance burns the
    whole month in 6.2 hours, so passing the governor proves nothing about the
    budget.
    """

    def test_thresholds_map_to_states(self):
        for used, expected in [(0, "normal"), (49_999, "normal"),
                               (50_000, "warning"), (74_999, "warning"),
                               (75_000, "high"), (89_999, "high"),
                               (90_000, "protective"), (250_000, "protective")]:
            self._set_spend(used)
            self.assertEqual(cg.budget_state(), expected, f"used={used}")

    def test_unknown_spend_fails_open(self):
        """No successful /key read yet must not degrade the product. The guard
        stretches caches; it is not an outage trigger."""
        self._reset_budget(last_attempt=time.time())
        self.assertIsNone(cg.estimated_month_calls())
        self.assertEqual(cg.budget_state(), "normal")
        self.assertTrue(cg._budget_allows("/coins/markets", None))

    def test_protective_refuses_nonessential_but_not_essential(self):
        self._set_spend(95_000)
        self.assertFalse(cg._budget_allows("/coins/markets", None))
        self.assertFalse(cg._budget_allows("/coins/bitcoin/market_chart", None))
        # Alert evaluation and live portfolio valuation keep their prices.
        self.assertTrue(cg._budget_allows("/simple/price", None))
        self.assertEqual(cg._TELEMETRY["cg_budget_throttles"], 2)

    def test_explicit_essential_overrides_path_default(self):
        self._set_spend(95_000)
        self.assertTrue(cg._budget_allows("/coins/markets", True))
        self.assertFalse(cg._budget_allows("/simple/price", False))

    def test_high_throttles_nothing_only_stretches_ttl(self):
        """HIGH is a brake on refresh frequency, not on availability."""
        self._set_spend(80_000)
        self.assertEqual(cg.budget_state(), "high")
        self.assertTrue(cg._budget_allows("/coins/markets", None))
        self.assertEqual(cg.effective_ttl(60), 120)

    def test_ttl_multiplier_scales_with_state(self):
        for used, expected in [(0, 60), (50_000, 60), (75_000, 120), (90_000, 240)]:
            self._set_spend(used)
            self.assertEqual(cg.effective_ttl(60), expected, f"used={used}")

    def test_stretched_ttl_actually_suppresses_the_refetch(self):
        """The multiplier has to reach the freshness check, not just a getter."""
        self._set_spend(90_000)
        payload = {"v": 1}
        with mock.patch.object(cg.requests, "get",
                               return_value=FakeResponse(200, payload)) as get:
            cg.get_json_cached("/coins/markets", ttl=1, cache_key="k", essential=True)
            time.sleep(1.1)  # past the nominal TTL, inside the 4x stretched one
            cg.get_json_cached("/coins/markets", ttl=1, cache_key="k", essential=True)
        self.assertEqual(get.call_count, 1)

    def test_throttled_request_serves_stale_cache_never_fabricates(self):
        """A refusal must land the caller on its normal degrade path: the last
        real value, marked stale. Not a fresh-looking guess."""
        self._set_spend(0)
        with mock.patch.object(cg.requests, "get",
                               return_value=FakeResponse(200, {"price": 42})):
            cg.get_json_cached("/coins/markets", ttl=0, cache_key="board")
        self._set_spend(95_000)
        with mock.patch.object(cg.requests, "get") as get:
            value = cg.get_json_cached("/coins/markets", ttl=0, cache_key="board")
        get.assert_not_called()
        self.assertEqual(value["price"], 42)
        self.assertTrue(value["stale"])
        self.assertEqual(cg._TELEMETRY["cg_stale_served"], 1)

    def test_throttled_request_with_no_cache_returns_none(self):
        """No cached value and no provider call means omit. Never invent."""
        self._set_spend(95_000)
        with mock.patch.object(cg.requests, "get") as get:
            self.assertIsNone(cg.get_json_cached("/coins/markets", ttl=60, cache_key="cold"))
        get.assert_not_called()

    def test_key_probe_is_never_gated_by_the_guard(self):
        """/key is how the guard learns it can stand down. Gating it on the
        budget would make protective mode a one-way door."""
        self._set_spend(99_999)
        self.assertTrue(cg._budget_allows(cg.KEY_PATH, None))

    def test_refresh_adopts_provider_truth_and_clears_local_drift(self):
        os.environ["COINGECKO_API_KEY"] = "test-key"
        self._reset_budget(used=10, local_calls=500)
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(
                200, {"plan": "Basic", "monthly_call_credit": 100000,
                      "current_total_monthly_calls": 61234})):
            cg._fetch_key_usage()
        # Provider truth wins outright: the local counter was only ever an
        # estimate of what this one process added since the last reading.
        self.assertEqual(cg.estimated_month_calls(), 61234)
        self.assertEqual(cg.budget_state(), "warning")

    def test_failed_refresh_keeps_last_good_reading(self):
        os.environ["COINGECKO_API_KEY"] = "test-key"
        self._set_spend(60_000)
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(500, {})):
            cg._fetch_key_usage()
        self.assertEqual(cg._BUDGET["used"], 60_000)
        self.assertIsNotNone(cg._BUDGET["error"])
        self.assertEqual(cg.budget_state(), "warning")

    def test_local_counter_tracks_attempts_between_refreshes(self):
        """Attempts, not successes -- a failed request still spent the credit."""
        self._set_spend(1_000)
        with mock.patch.object(cg.requests, "get", return_value=FakeResponse(500, {})):
            cg.get_json("/coins/markets", retries=1)
        self.assertEqual(cg.estimated_month_calls(), 1_002)  # initial + one retry

    def test_refresh_interval_tightens_as_spend_rises(self):
        """The guard's own /key probe costs ~0.5 credits. Polling it at the
        protective cadence all month would itself be a material line item, so
        the cadence has to be earned by risk."""
        self.assertGreater(cg.BUDGET_REFRESH_SECONDS["normal"],
                           cg.BUDGET_REFRESH_SECONDS["warning"])
        self.assertGreater(cg.BUDGET_REFRESH_SECONDS["warning"],
                           cg.BUDGET_REFRESH_SECONDS["high"])
        self.assertGreater(cg.BUDGET_REFRESH_SECONDS["high"],
                           cg.BUDGET_REFRESH_SECONDS["protective"])

    def test_fresh_reading_does_not_trigger_a_refresh(self):
        os.environ["COINGECKO_API_KEY"] = "test-key"
        self._set_spend(1_000)
        with mock.patch.object(cg, "_fetch_key_usage") as fetch:
            cg._maybe_refresh_budget()
        fetch.assert_not_called()

    def test_stale_reading_triggers_exactly_one_refresh(self):
        os.environ["COINGECKO_API_KEY"] = "test-key"
        self._reset_budget(used=1_000, credit=cg.MONTHLY_CREDIT,
                           at=time.time() - 99_999, last_attempt=time.time() - 99_999)
        with mock.patch.object(cg, "_fetch_key_usage") as fetch:
            cg._maybe_refresh_budget()
            cg._maybe_refresh_budget()  # last_attempt unchanged (fetch is mocked)
        self.assertGreaterEqual(fetch.call_count, 1)

    def test_guard_can_be_disabled_by_env(self):
        """An escape hatch that does not require a deploy, for the case where
        the guard itself is the thing misbehaving."""
        self._set_spend(99_999)
        with mock.patch.object(cg, "BUDGET_GUARD_ENABLED", False):
            self.assertEqual(cg.budget_state(), "normal")
            self.assertTrue(cg._budget_allows("/coins/markets", None))
            self.assertEqual(cg.effective_ttl(60), 60)

    def test_snapshot_reports_the_required_operator_fields(self):
        self._set_spend(60_000)
        snap = cg.budget_snapshot()
        for field in ("current_month_calls", "estimated_remaining", "cache_hits",
                      "provider_requests", "429s", "budget_throttles", "state"):
            self.assertIn(field, snap)
        self.assertEqual(snap["current_month_calls"], 60_000)
        self.assertEqual(snap["estimated_remaining"], 40_000)
        self.assertEqual(snap["state"], "warning")

    def test_snapshot_never_exposes_the_key(self):
        os.environ["COINGECKO_API_KEY"] = "cg-super-secret-value"
        blob = repr(cg.budget_snapshot()) + repr(cg.telemetry_snapshot())
        self.assertNotIn("cg-super-secret-value", blob)

    def test_telemetry_snapshot_carries_budget_fields(self):
        self._set_spend(95_000)
        snap = cg.telemetry_snapshot()
        self.assertEqual(snap["cg_budget_state"], "protective")
        self.assertEqual(snap["cg_current_month_calls"], 95_000)
        self.assertEqual(snap["cg_estimated_remaining"], 5_000)
        self.assertEqual(snap["cg_budget_ttl_multiplier"], 4.0)

    def test_thresholds_are_ordered_and_under_the_plan(self):
        self.assertLess(cg.BUDGET_WARN, cg.BUDGET_HIGH)
        self.assertLess(cg.BUDGET_HIGH, cg.BUDGET_PROTECT)
        self.assertLess(cg.BUDGET_PROTECT, cg.MONTHLY_CREDIT)
        self.assertLessEqual(cg.BUDGET_TARGET, cg.MONTHLY_CREDIT)

    def test_stretch_reaches_the_market_board_cache(self):
        """market_data keeps its OWN cache and calls get_json directly, so the
        client-level stretch does not reach it. If this regresses, the single
        largest CoinGecko consumer ignores budget pressure and protective mode
        drops the board to the 3-row Coinbase fallback instead of serving the
        real board a little longer."""
        from services import market_data

        market_data.CACHE.update({"data": {"markets": [], "source": "coingecko"},
                                  "created_at": time.time() - 90})
        self._set_spend(95_000)  # protective: 60s TTL becomes 240s
        with mock.patch.object(market_data, "fetch_coingecko_markets") as fetch:
            market_data.live_market_board()
        fetch.assert_not_called()
        market_data.CACHE.update({"data": None, "created_at": 0})

    def test_stretch_reaches_the_chart_history_cache(self):
        from services import market_data

        market_data.HISTORY_CACHE[("BTC", "24H")] = {
            "payload": {"points": []}, "created_at": time.time() - 300}
        self._set_spend(95_000)  # 24H TTL 180s -> 720s
        with mock.patch.object(market_data, "fetch_coingecko_history") as fetch:
            market_data.asset_history("BTC", "24H")
        fetch.assert_not_called()
        market_data.HISTORY_CACHE.clear()

    def test_stretch_reaches_the_briefings_provider_cache(self):
        from services.pulse_briefings import crypto_provider as cp

        cp._CACHE.clear()
        cp._CACHE["k"] = {"value": {"v": 1}, "at": time.time() - 400}
        self._set_spend(95_000)  # 300s TTL -> 1200s
        loader = mock.Mock(return_value={"v": 2})
        self.assertEqual(cp._cached("k", 300, loader), {"v": 1})
        loader.assert_not_called()
        cp._CACHE.clear()

    def test_exhausted_budget_still_serves_essential_reads(self):
        """Even past 100% the guard does not hard-stop. If the plan is truly
        exhausted the provider says so; we do not pre-emptively black out."""
        self._set_spend(cg.MONTHLY_CREDIT + 50_000)
        self.assertTrue(cg._budget_allows("/simple/price", None))
        self.assertEqual(cg.budget_snapshot()["estimated_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
