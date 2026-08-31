"""Tests for the Market Pulse read model and its HTTP surface.

Runs on stdlib unittest with no network: the two market foundations
(``market_data.live_market_board`` and the briefings ``crypto_provider``) are
stubbed, which is also the point being asserted — Market Pulse must reach the
provider only through them, never directly.

The properties under test are the ones that would be expensive to discover in
production: that a shared cached board never carries one account's watchlist to
another account, that "trending" means the provider's trending and not a price
sort, that changing a chip or a sort does not cost a provider call, and that an
unknown number leaves as null rather than as zero.
"""

import unittest
from unittest import mock

from flask import Flask

from services import market_data, market_pulse, market_pulse_routes
from services.pulse_briefings import crypto_provider


def _board_row(symbol, price, change, rank, cap=1e11, vol=1e9):
    return {
        "id": {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(symbol, symbol.lower()),
        "name": {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}.get(symbol, symbol),
        "symbol": symbol.lower(),
        "image": f"https://img/{symbol.lower()}.png",
        "price": price,
        "change_24h": change,
        "market_cap": cap,
        "volume_24h": vol,
        "market_cap_rank": rank,
        "sparkline": [1.0, 2.0, 3.0],
    }


BOARD_ROWS = [
    _board_row("BTC", 60000.0, 2.5, 1, cap=1.2e12),
    _board_row("ETH", 3000.0, -1.1, 2, cap=4.0e11),
    _board_row("SOL", 150.0, 9.4, 5, cap=7.0e10),
]

OVERVIEW = {
    "generated_at": "2026-08-30T12:00:00Z",
    "provider": "coingecko",
    "stale": False,
    "total_market_cap": 2.4e12,
    "total_volume_24h": 9.0e10,
    "btc_dominance": 53.2,
    "eth_dominance": 17.4,
    "market_cap_change_24h_pct": -3.1,
    "market_direction": "down",
}


class MarketPulseFoundationTestCase(unittest.TestCase):
    """The composition layer over the existing board and briefings provider."""

    def setUp(self):
        self.board_calls = []

        def fake_board(category="top_volume", limit=50):
            self.board_calls.append((category, limit))
            rows = market_data.sort_markets(list(BOARD_ROWS), category)[:limit]
            return {
                "source": "coingecko",
                "updated_at": "2026-08-30T12:00:00",
                "observed_epoch": 1000.0,
                "age_seconds": 12,
                "warning": None,
                "markets": rows,
                "summary": {},
            }

        patches = [
            mock.patch.object(market_data, "live_market_board", fake_board),
            mock.patch.object(crypto_provider, "get_market_overview", lambda: dict(OVERVIEW)),
            mock.patch.object(crypto_provider, "is_stale", lambda fact, **kw: False),
            mock.patch.object(crypto_provider, "get_trending", lambda: [
                {"symbol": "sol", "name": "Solana", "rank": 5},
                {"symbol": "pepe", "name": "Pepe", "rank": 41},
            ]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_asset_normalizes_to_the_pulsesoc_market_object(self):
        asset = market_pulse.normalize_asset(BOARD_ROWS[0], "2026-08-30T12:00:00")
        self.assertEqual(asset["id"], "bitcoin")
        self.assertEqual(asset["symbol"], "BTC")
        self.assertEqual(asset["rank"], 1)
        self.assertEqual(asset["change24h"], 2.5)
        self.assertEqual(asset["updatedAt"], "2026-08-30T12:00:00")
        # No CoinGecko field name survives the boundary.
        for vendor_key in ("current_price", "price_change_percentage_24h", "total_volume"):
            self.assertNotIn(vendor_key, asset)

    def test_absent_numbers_stay_null_and_never_become_zero(self):
        row = dict(BOARD_ROWS[0], price=None, change_24h=None, market_cap=None)
        asset = market_pulse.normalize_asset(row)
        self.assertIsNone(asset["price"])
        self.assertIsNone(asset["change24h"])
        self.assertIsNone(asset["marketCap"])

    def test_global_strip_carries_btc_and_eth_dominance(self):
        metrics = market_pulse.global_metrics()
        self.assertTrue(metrics["available"])
        self.assertEqual(metrics["btcDominance"], 53.2)
        self.assertEqual(metrics["ethDominance"], 17.4)
        self.assertEqual(metrics["totalMarketCap"], 2.4e12)
        self.assertEqual(metrics["totalVolume24h"], 9.0e10)
        self.assertEqual(metrics["marketCapChange24hPct"], -3.1)

    def test_global_strip_reports_absence_rather_than_zero(self):
        with mock.patch.object(crypto_provider, "get_market_overview", lambda: None):
            metrics = market_pulse.global_metrics()
        self.assertFalse(metrics["available"])
        self.assertIsNone(metrics["totalMarketCap"])
        self.assertIsNone(metrics["btcDominance"])
        self.assertIsNone(metrics["ethDominance"])

    def test_chips_sort_the_shared_board_without_extra_provider_calls(self):
        gainers = market_pulse.market_rows("gainers")["assets"]
        losers = market_pulse.market_rows("losers")["assets"]
        self.assertEqual(gainers[0]["symbol"], "SOL")
        self.assertEqual(losers[0]["symbol"], "ETH")
        # Every chip resolves through live_market_board, which is cached and
        # sorts in memory. Nothing here reaches past it to a provider.
        self.assertTrue(all(call[0] in market_pulse._CATEGORY_SORT.values() for call in self.board_calls))

    def test_trending_uses_provider_trending_not_price_movement(self):
        payload = market_pulse.trending()
        self.assertEqual(payload["basis"], "coingecko_search_trending")
        symbols = [a["symbol"] for a in payload["assets"]]
        self.assertEqual(symbols, ["SOL", "PEPE"])
        # SOL is also the biggest gainer, but it is here because the provider
        # said so. PEPE proves the list is not a price sort: it is outside the
        # board entirely and still appears, priced null rather than guessed.
        pepe = payload["assets"][1]
        self.assertIsNone(pepe["price"])
        self.assertTrue(pepe["trending"])

    def test_trending_survives_a_provider_failure(self):
        def boom():
            raise RuntimeError("trending down")

        with mock.patch.object(crypto_provider, "get_trending", boom):
            payload = market_pulse.trending()
        self.assertEqual(payload["assets"], [])

    def test_freshness_reports_age_and_gates_the_live_label(self):
        freshness = market_pulse.market_rows("all")["freshness"]
        self.assertEqual(freshness["ageSeconds"], 12)
        self.assertTrue(freshness["live"])
        self.assertFalse(freshness["stale"])

    def test_fallback_source_is_never_labelled_live(self):
        def coinbase_board(category="top_volume", limit=50):
            return {
                "source": "coinbase_public_fallback",
                "updated_at": "2026-08-30T12:00:00",
                "age_seconds": 3,
                "warning": "Partially connected.",
                "markets": [_board_row("BTC", 60000.0, None, None)],
                "summary": {},
            }

        with mock.patch.object(market_data, "live_market_board", coinbase_board):
            freshness = market_pulse.market_rows("all")["freshness"]
        self.assertFalse(freshness["live"])
        self.assertTrue(freshness["degraded"])
        self.assertTrue(freshness["warning"])

    def test_board_outage_degrades_instead_of_raising(self):
        def boom(category="top_volume", limit=50):
            raise RuntimeError("provider down")

        with mock.patch.object(market_data, "live_market_board", boom):
            payload = market_pulse.market_rows("all")
        self.assertEqual(payload["assets"], [])
        self.assertFalse(payload["freshness"]["live"])

    def test_search_matches_symbol_name_and_coin_id(self):
        self.assertEqual([a["symbol"] for a in market_pulse.search("bitcoin")["assets"]], ["BTC"])
        self.assertEqual([a["symbol"] for a in market_pulse.search("eth")["assets"]], ["ETH"])
        self.assertEqual([a["symbol"] for a in market_pulse.search("sol")["assets"]], ["SOL"])

    def test_snapshot_advertises_only_categories_it_can_answer(self):
        payload = market_pulse.snapshot("all")
        self.assertEqual(payload["categories"], list(market_pulse.CATEGORIES))
        self.assertIn("global", payload)
        self.assertIn("freshness", payload)


class HistoryRangeTestCase(unittest.TestCase):
    """The chart's range vocabulary, including the new 90D tab."""

    def test_90d_and_30d_labels_alias_onto_stored_range_keys(self):
        self.assertEqual(market_data.HISTORY_RANGE_ALIASES["90D"], "3M")
        self.assertEqual(market_data.HISTORY_RANGE_ALIASES["30D"], "1M")
        self.assertEqual(market_data.HISTORY_RANGE_DAYS["3M"], 90)
        self.assertIn("3M", market_data.HISTORY_CACHE_SECONDS)

    def test_all_is_a_number_within_the_plan_and_never_max(self):
        # The Basic plan 401s on days=max and refuses 731. "ALL" that fails on
        # every request is a permanent plan limit wearing the costume of a blip.
        self.assertEqual(market_data.HISTORY_RANGE_DAYS["ALL"], market_data.HISTORY_MAX_DAYS)
        self.assertLessEqual(market_data.HISTORY_MAX_DAYS, 730)
        self.assertNotIn("max", [str(v) for v in market_data.HISTORY_RANGE_DAYS.values()])

    def test_every_range_alias_resolves_to_a_real_range(self):
        for alias, target in market_data.HISTORY_RANGE_ALIASES.items():
            self.assertIn(target, market_data.HISTORY_RANGE_DAYS, alias)

    def test_unknown_range_falls_back_to_24h_without_a_provider_call(self):
        with mock.patch.object(market_data, "fetch_coingecko_history", side_effect=AssertionError("no call")):
            with mock.patch.object(market_data.coingecko_client, "coin_id", lambda s: None):
                with mock.patch.object(market_data, "get_symbol", lambda s: None):
                    payload = market_data.asset_history("ZZZZ", "NONSENSE")
        self.assertEqual(payload["range"], "24H")
        self.assertEqual(payload["points"], [])
        self.assertFalse(payload["ok"])


class MarketPulseRouteTestCase(unittest.TestCase):
    """The HTTP surface, and the boundary between shared and per-account data."""

    def setUp(self):
        self.app = Flask(__name__)
        market_pulse_routes.register(self.app)
        self.client = self.app.test_client()

        self.user = {"user_id": 1}
        self.overlays = {
            1: {"watching": ["BTC"], "favorites": ["BTC"], "alertCounts": {"BTC": 2}},
            2: {"watching": ["SOL"], "favorites": [], "alertCounts": {}},
        }

        snapshot = {
            "category": "all",
            "assets": [market_pulse.normalize_asset(row, "2026-08-30T12:00:00") for row in BOARD_ROWS],
            "freshness": {"live": True, "ageSeconds": 12, "stale": False, "degraded": False,
                          "source": "coingecko", "observedAt": "2026-08-30T12:00:00", "warning": None},
        }
        patches = [
            mock.patch.object(market_pulse_routes, "_current_user", lambda: self.user),
            mock.patch.object(market_pulse, "snapshot", lambda category="all", limit=50: dict(
                snapshot, category=category,
                assets=[dict(a) for a in snapshot["assets"]],
                **{"global": {"available": True, "btcDominance": 53.2, "ethDominance": 17.4}})),
            mock.patch.object(
                market_pulse_routes, "_overlay",
                lambda user: dict(self.overlays[int(user["user_id"])], available=True)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_snapshot_requires_a_signed_in_user(self):
        with mock.patch.object(market_pulse_routes, "_current_user", lambda: None):
            response = self.client.get("/api/pulse/market/snapshot")
        self.assertEqual(response.status_code, 401)

    def test_snapshot_never_caches_prices_at_the_http_layer(self):
        response = self.client.get("/api/pulse/market/snapshot")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_overlay_is_scoped_to_the_calling_account(self):
        first = self.client.get("/api/pulse/market/snapshot").get_json()
        by_symbol = {a["symbol"]: a for a in first["assets"]}
        self.assertTrue(by_symbol["BTC"]["watching"])
        self.assertEqual(by_symbol["BTC"]["alertCount"], 2)
        self.assertFalse(by_symbol["SOL"]["watching"])

        # Same shared, cached board; a different account. User A's watchlist and
        # alert badges must not survive into User B's response.
        self.user = {"user_id": 2}
        second = self.client.get("/api/pulse/market/snapshot").get_json()
        by_symbol = {a["symbol"]: a for a in second["assets"]}
        self.assertFalse(by_symbol["BTC"]["watching"])
        self.assertEqual(by_symbol["BTC"]["alertCount"], 0)
        self.assertTrue(by_symbol["SOL"]["watching"])

    def test_watchlist_chip_filters_by_the_callers_own_symbols(self):
        payload = self.client.get("/api/pulse/market/snapshot?category=watchlist").get_json()
        self.assertEqual([a["symbol"] for a in payload["assets"]], ["BTC"])
        self.user = {"user_id": 2}
        payload = self.client.get("/api/pulse/market/snapshot?category=watchlist").get_json()
        self.assertEqual([a["symbol"] for a in payload["assets"]], ["SOL"])

    def test_a_failed_overlay_does_not_take_the_market_screen_down(self):
        with mock.patch.object(market_pulse_routes, "_overlay",
                               lambda user: {"watching": [], "favorites": [], "alertCounts": {}, "available": False}):
            payload = self.client.get("/api/pulse/market/snapshot").get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["personalized"])
        self.assertEqual(len(payload["assets"]), 3)

    def test_limit_is_bounded(self):
        with self.app.test_request_context("/api/pulse/market/snapshot?limit=99999"):
            self.assertEqual(market_pulse_routes._limit(), market_pulse_routes.MAX_LIMIT)
        with self.app.test_request_context("/api/pulse/market/snapshot?limit=abc"):
            self.assertEqual(market_pulse_routes._limit(), market_pulse_routes.DEFAULT_LIMIT)
        with self.app.test_request_context("/api/pulse/market/snapshot?limit=-4"):
            self.assertEqual(market_pulse_routes._limit(), 1)

    def test_history_route_passes_the_label_through_to_the_aliaser(self):
        seen = {}

        def fake_history(symbol, range_key="24H"):
            seen["args"] = (symbol, range_key)
            return {"symbol": symbol, "range": "3M", "points": [{"t": 1, "price": 2.0}], "source": "coingecko"}

        with mock.patch.object(market_pulse, "asset_history", fake_history):
            payload = self.client.get("/api/pulse/market/assets/BTC/history?range=90d").get_json()
        self.assertEqual(seen["args"], ("BTC", "90D"))
        self.assertTrue(payload["ok"])

    def test_history_with_no_points_is_not_reported_ok(self):
        with mock.patch.object(market_pulse, "asset_history",
                               lambda s, r="24H": {"symbol": s, "range": r, "points": [], "source": "unavailable"}):
            payload = self.client.get("/api/pulse/market/assets/BTC/history").get_json()
        self.assertFalse(payload["ok"])

    def test_every_route_in_the_pack_is_read_only(self):
        for rule in self.app.url_map.iter_rules():
            if not str(rule).startswith(market_pulse_routes.API_PREFIX):
                continue
            self.assertEqual(
                rule.methods - {"HEAD", "OPTIONS"}, {"GET"},
                f"{rule} is not GET-only; writes belong to the existing crypto API",
            )


if __name__ == "__main__":
    unittest.main()
