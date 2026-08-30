"""Crypto Pulse collectors backed by public market data sources."""

from __future__ import annotations

import time
from typing import Any

from services import coingecko_client

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, network_error_message, safe_float, source_status, utc_now_iso


ASSETS = {
    "bitcoin": {"symbol": "BTC", "binance": "BTCUSDT"},
    "ethereum": {"symbol": "ETH", "binance": "ETHUSDT"},
    "solana": {"symbol": "SOL", "binance": "SOLUSDT"},
}


class CryptoPulseCollector(BaseCollector):
    stream = "crypto_pulse"
    collector_key = "crypto_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        candidates: list[IntelligenceCandidate] = []
        statuses: list[dict[str, Any]] = []
        coingecko: dict[str, Any] = {}
        binance: dict[str, Any] = {}

        try:
            data, cached, duration = self.fetch_json(
                coingecko_client.url("/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true"),
                cache_key="coingecko_simple_price_btc_eth_sol",
                ttl_seconds=45,
                headers=coingecko_client.auth_headers(),
            )
            coingecko = data if isinstance(data, dict) else {}
            statuses.append(source_status("coingecko", "success_cached" if cached else "success", duration_ms=duration, candidates=len(coingecko)))
        except Exception as exc:
            statuses.append(source_status("coingecko", "failed", reason=network_error_message(exc)))

        try:
            data, cached, duration = self.fetch_json(
                "https://api.binance.com/api/v3/ticker/24hr?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22SOLUSDT%22%5D",
                cache_key="binance_24hr_btc_eth_sol",
                ttl_seconds=30,
            )
            if isinstance(data, list):
                binance = {str(item.get("symbol")): item for item in data if isinstance(item, dict)}
            statuses.append(source_status("binance", "success_cached" if cached else "success", duration_ms=duration, candidates=len(binance)))
        except Exception as exc:
            statuses.append(source_status("binance", "failed", reason=network_error_message(exc)))

        for coin_id, asset in ASSETS.items():
            gecko_row = coingecko.get(coin_id) if isinstance(coingecko.get(coin_id), dict) else {}
            binance_row = binance.get(asset["binance"]) if isinstance(binance.get(asset["binance"]), dict) else {}
            gecko_change = safe_float(gecko_row.get("usd_24h_change")) if gecko_row else 0.0
            binance_change = safe_float(binance_row.get("priceChangePercent")) if binance_row else 0.0
            change_values = [value for value in [gecko_change, binance_change] if value]
            if not change_values:
                continue
            average_change = sum(change_values) / len(change_values)
            if abs(average_change) < 3.0:
                continue
            direction = "above" if average_change > 0 else "below"
            symbol = asset["symbol"]
            price = safe_float(gecko_row.get("usd") or binance_row.get("lastPrice"))
            volume = safe_float(gecko_row.get("usd_24h_vol") or binance_row.get("quoteVolume"))
            severity = "high" if abs(average_change) >= 6 else "normal"
            if abs(average_change) >= 10:
                severity = "urgent"
            source_keys = []
            if gecko_row:
                source_keys.append("coingecko")
            if binance_row:
                source_keys.append("binance")
            confidence = 0.78 + min(0.14, abs(average_change) / 100) + (0.04 if len(source_keys) > 1 else 0.0)
            title = f"{symbol} moved {average_change:+.2f}% over 24 hours"
            candidates.append(IntelligenceCandidate(
                stream=self.stream,
                source=source_keys[0],
                source_keys=source_keys,
                source_url="https://www.coingecko.com/",
                source_confidence=0.82 if len(source_keys) > 1 else 0.76,
                title=title,
                summary=f"{symbol} is trading near ${price:,.2f} after a {average_change:+.2f}% 24-hour move across available public market feeds.",
                why_it_matters="Large multi-source crypto moves can affect alert activity and user watchlists, but this is market intelligence only, not investment advice.",
                expected_impact=f"{symbol} volatility may stay elevated during the next 24 hours if volume remains high.",
                category="market_movement",
                asset_symbol=symbol,
                severity=severity,
                confidence=confidence,
                freshness_score=0.94,
                impact_score=min(0.92, 0.62 + abs(average_change) / 20),
                dedupe_key=f"crypto:{symbol}:24h_move:{direction}:{utc_now_iso()[:13]}",
                event_time=utc_now_iso(),
                evidence=[
                    {"source": "coingecko", "change_24h": gecko_change, "price_usd": gecko_row.get("usd"), "volume_usd": volume} if gecko_row else {},
                    {"source": "binance", "change_24h": binance_change, "last_price": binance_row.get("lastPrice")} if binance_row else {},
                ],
                metadata={"no_investment_advice": True, "direction": direction, "volume_usd": volume},
            ))

        status = "success" if any(item["status"].startswith("success") for item in statuses) else "failed"
        if not candidates and status == "success":
            message = "No crypto movement exceeded acceptance thresholds."
        else:
            message = ""
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status=status,
            candidates=candidates[: int(limit or 20)],
            source_statuses=statuses,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=message,
        )
