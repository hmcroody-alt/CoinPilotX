"""Market Pulse collector using public, cached market quote data."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, network_error_message, safe_float, source_status, utc_now_iso


WATCHED = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^DJI": "Dow Jones",
    "GC=F": "Gold",
    "CL=F": "Oil",
    "DX-Y.NYB": "US Dollar Index",
}


class MarketPulseCollector(BaseCollector):
    stream = "market_pulse"
    collector_key = "market_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        statuses: list[dict[str, Any]] = []
        candidates: list[IntelligenceCandidate] = []
        symbols = ",".join(WATCHED)
        try:
            data, cached, duration = self.fetch_json(
                f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}",
                cache_key="yahoo_major_market_quotes",
                ttl_seconds=90,
            )
            rows = (((data or {}).get("quoteResponse") or {}).get("result") or []) if isinstance(data, dict) else []
            statuses.append(source_status("yahoo_finance", "success_cached" if cached else "success", duration_ms=duration, candidates=len(rows)))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "")
                name = WATCHED.get(symbol) or row.get("shortName") or symbol
                pct = safe_float(row.get("regularMarketChangePercent"))
                price = safe_float(row.get("regularMarketPrice"))
                if abs(pct) < 1.0:
                    continue
                severity = "high" if abs(pct) >= 2.0 else "normal"
                direction = "higher" if pct > 0 else "lower"
                candidates.append(IntelligenceCandidate(
                    stream=self.stream,
                    source="yahoo_finance",
                    source_keys=["yahoo_finance"],
                    source_url="https://finance.yahoo.com/",
                    source_confidence=0.70,
                    title=f"{name} is moving {direction} today",
                    summary=f"{name} is near {price:,.2f} with an observed {pct:+.2f}% move in public market data.",
                    why_it_matters="Broad market moves can affect user watchlists, creator business timing, and daily digest context.",
                    expected_impact="Market activity may remain elevated through the current session if macro or earnings catalysts persist.",
                    category="major_market_move",
                    region="global",
                    severity=severity,
                    confidence=0.76 if severity == "normal" else 0.82,
                    freshness_score=0.86,
                    impact_score=min(0.9, 0.55 + abs(pct) / 4),
                    dedupe_key=f"market:{symbol}:{'up' if pct > 0 else 'down'}:{utc_now_iso()[:10]}",
                    event_time=utc_now_iso(),
                    evidence=[{"source": "yahoo_finance", "symbol": symbol, "price": price, "change_percent": pct}],
                    metadata={"symbol": symbol, "market_intelligence_only": True},
                ))
        except Exception as exc:
            statuses.append(source_status("yahoo_finance", "failed", reason=network_error_message(exc)))

        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success" if any(item["status"].startswith("success") for item in statuses) else "failed",
            candidates=candidates[: int(limit or 20)],
            source_statuses=statuses,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message="" if candidates else "No broad market move exceeded acceptance thresholds.",
        )
