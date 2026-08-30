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


if __name__ == "__main__":
    unittest.main()
