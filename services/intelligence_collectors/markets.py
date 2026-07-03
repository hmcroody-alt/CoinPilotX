"""Market Pulse collector using public, cached major-market quote data."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base import (
    BaseCollector,
    CollectorResult,
    IntelligenceCandidate,
    clamp,
    network_error_message,
    safe_float,
    source_status,
    utc_now_iso,
)


MARKET_DISCLAIMER = "Pulse AI provides educational market intelligence only. This is not financial advice."

MARKET_ALERT_SCHEDULE = {
    "normal_users": [
        {"name": "Pre-Market Brief", "time_et": "08:30", "purpose": "What matters before the market opens."},
        {"name": "Market Close Recap", "time_et": "16:15", "purpose": "What happened, why it mattered, and what to watch tomorrow."},
    ],
    "active_market_users": [
        {"name": "Pre-Market Brief", "time_et": "08:30", "purpose": "What matters before the market opens."},
        {"name": "Market Open Pulse", "time_et": "09:35", "purpose": "Only if S&P 500, NASDAQ, or VIX moves sharply after open."},
        {"name": "Power Hour Alert", "time_et": "15:15", "purpose": "Volatility, breakout, selloff, reversal, or unusual volume."},
        {"name": "Market Close Recap", "time_et": "16:15", "purpose": "What happened, why it mattered, and what to watch tomorrow."},
    ],
    "urgent_only": "Fed, CPI, jobs report, emergency market move, VIX spike, or major macro risk event.",
}

ASSETS: dict[str, dict[str, Any]] = {
    "^GSPC": {"asset_key": "sp500", "asset": "S&P 500", "proxy": "SPY", "threshold": 1.0, "asset_class": "index", "priority": 100},
    "SPY": {"asset_key": "sp500", "asset": "S&P 500", "proxy": "SPY", "threshold": 1.0, "asset_class": "etf", "priority": 90},
    "^IXIC": {"asset_key": "nasdaq", "asset": "NASDAQ", "proxy": "QQQ", "threshold": 1.5, "asset_class": "index", "priority": 100},
    "QQQ": {"asset_key": "nasdaq", "asset": "NASDAQ", "proxy": "QQQ", "threshold": 1.5, "asset_class": "etf", "priority": 90},
    "^DJI": {"asset_key": "dow", "asset": "Dow Jones", "proxy": "DIA", "threshold": 1.0, "asset_class": "index", "priority": 100},
    "DIA": {"asset_key": "dow", "asset": "Dow Jones", "proxy": "DIA", "threshold": 1.0, "asset_class": "etf", "priority": 90},
    "^RUT": {"asset_key": "russell2000", "asset": "Russell 2000", "proxy": "IWM", "threshold": 1.5, "asset_class": "index", "priority": 100},
    "IWM": {"asset_key": "russell2000", "asset": "Russell 2000", "proxy": "IWM", "threshold": 1.5, "asset_class": "etf", "priority": 90},
    "^VIX": {"asset_key": "vix", "asset": "VIX", "proxy": "^VIX", "threshold": 8.0, "asset_class": "volatility", "priority": 100},
    "GC=F": {"asset_key": "gold", "asset": "Gold", "proxy": "GLD", "threshold": 2.0, "asset_class": "commodity", "priority": 100},
    "GLD": {"asset_key": "gold", "asset": "Gold", "proxy": "GLD", "threshold": 2.0, "asset_class": "etf", "priority": 90},
    "CL=F": {"asset_key": "oil", "asset": "Oil", "proxy": "USO", "threshold": 2.0, "asset_class": "commodity", "priority": 100},
    "USO": {"asset_key": "oil", "asset": "Oil", "proxy": "USO", "threshold": 2.0, "asset_class": "etf", "priority": 90},
    "^TNX": {"asset_key": "ten_year_yield", "asset": "10Y Treasury Yield", "proxy": "^TNX", "threshold": 3.0, "asset_class": "yield", "priority": 100},
    "DX-Y.NYB": {"asset_key": "usd_index", "asset": "USD Index", "proxy": "DX-Y.NYB", "threshold": 0.7, "asset_class": "currency", "priority": 100},
    "XLK": {"asset_key": "technology_sector", "asset": "Technology Sector", "proxy": "XLK", "threshold": 1.7, "asset_class": "sector_etf", "priority": 70},
    "XLF": {"asset_key": "financial_sector", "asset": "Financial Sector", "proxy": "XLF", "threshold": 1.7, "asset_class": "sector_etf", "priority": 70},
    "XLE": {"asset_key": "energy_sector", "asset": "Energy Sector", "proxy": "XLE", "threshold": 2.0, "asset_class": "sector_etf", "priority": 70},
    "XLV": {"asset_key": "healthcare_sector", "asset": "Healthcare Sector", "proxy": "XLV", "threshold": 1.5, "asset_class": "sector_etf", "priority": 70},
    "XLY": {"asset_key": "consumer_discretionary_sector", "asset": "Consumer Discretionary Sector", "proxy": "XLY", "threshold": 1.8, "asset_class": "sector_etf", "priority": 70},
    "XLP": {"asset_key": "consumer_staples_sector", "asset": "Consumer Staples Sector", "proxy": "XLP", "threshold": 1.3, "asset_class": "sector_etf", "priority": 70},
    "XLU": {"asset_key": "utilities_sector", "asset": "Utilities Sector", "proxy": "XLU", "threshold": 1.3, "asset_class": "sector_etf", "priority": 70},
    "XLI": {"asset_key": "industrial_sector", "asset": "Industrial Sector", "proxy": "XLI", "threshold": 1.5, "asset_class": "sector_etf", "priority": 70},
    "XLB": {"asset_key": "materials_sector", "asset": "Materials Sector", "proxy": "XLB", "threshold": 1.6, "asset_class": "sector_etf", "priority": 70},
    "XLRE": {"asset_key": "real_estate_sector", "asset": "Real Estate Sector", "proxy": "XLRE", "threshold": 1.6, "asset_class": "sector_etf", "priority": 70},
    "XLC": {"asset_key": "communication_services_sector", "asset": "Communication Services Sector", "proxy": "XLC", "threshold": 1.6, "asset_class": "sector_etf", "priority": 70},
}

QUOTE_SYMBOLS = ",".join(ASSETS)
SESSION_WINDOWS = [
    ("pre_market_brief", 8, 30, "Pre-Market Brief"),
    ("market_open_pulse", 9, 35, "Market Open Pulse"),
    ("midday_check", 12, 30, "Midday Check"),
    ("power_hour_alert", 15, 15, "Power Hour Alert"),
    ("market_close_recap", 16, 15, "Market Close Recap"),
]


def _source_config_status(source_key: str, env_name: str) -> dict[str, Any]:
    return source_status(source_key, "skipped_config_missing", reason=f"{env_name} not configured")


def _market_window(now: datetime | None = None) -> dict[str, str]:
    eastern = ZoneInfo("America/New_York")
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(eastern)
    minutes = local.hour * 60 + local.minute
    selected = SESSION_WINDOWS[0]
    for window in SESSION_WINDOWS:
        _, hour, minute, _ = window
        if minutes >= hour * 60 + minute:
            selected = window
    key, hour, minute, name = selected
    return {
        "key": key,
        "label": name,
        "time_et": f"{hour:02d}:{minute:02d}",
        "current_time_et": local.replace(microsecond=0).isoformat(),
    }


def _choose_best_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        config = ASSETS.get(symbol)
        if not config:
            continue
        pct = safe_float(row.get("regularMarketChangePercent"))
        if pct == 0.0 and row.get("regularMarketChangePercent") in {None, ""}:
            continue
        asset_key = str(config["asset_key"])
        previous = best.get(asset_key)
        if not previous:
            best[asset_key] = row
            continue
        previous_config = ASSETS.get(str(previous.get("symbol") or "").upper(), {})
        previous_rank = int(previous_config.get("priority") or 0)
        current_rank = int(config.get("priority") or 0)
        if current_rank > previous_rank:
            best[asset_key] = row
    return list(best.values())


def _status_for(config: dict[str, Any], pct: float, threshold: float) -> dict[str, str]:
    asset_class = str(config.get("asset_class") or "")
    if asset_class == "volatility":
        if pct > 0:
            return {
                "status": "Risk elevated",
                "signal": "VIX volatility spike",
                "risk": "High" if pct >= threshold * 1.5 else "Medium",
                "why": "VIX is expanding, which can signal higher expected market volatility.",
                "impact": "Index swings may stay elevated through the next trading session if risk demand persists.",
                "action": "Review exposure and watch whether volatility confirms or fades. This is market intelligence, not financial advice.",
            }
        return {
            "status": "Risk easing",
            "signal": "Volatility cooling",
            "risk": "Medium",
            "why": "VIX is falling, which can signal calmer expected market conditions.",
            "impact": "If volatility keeps easing, broad-market risk appetite may improve.",
            "action": "Watch whether lower volatility is confirmed by price breadth and volume. This is market intelligence, not financial advice.",
        }
    if asset_class in {"yield", "currency"} and pct > 0:
        return {
            "status": "Macro pressure rising",
            "signal": "Yield or dollar strength",
            "risk": "Medium",
            "why": "Rates or dollar strength can affect equity valuation, commodities, and risk appetite.",
            "impact": "Market sensitivity may increase if the move continues into the next session.",
            "action": "Watch rate-sensitive sectors and confirm the move with broader market behavior.",
        }
    if pct > 0:
        return {
            "status": "Momentum improving",
            "signal": "Breakout watch",
            "risk": "Medium",
            "why": "The asset is making a meaningful positive move while market participation may be improving.",
            "impact": "Momentum may remain elevated over the next 1-5 trading days if volume and breadth confirm.",
            "action": "Watch for confirmation. This is market intelligence, not financial advice.",
        }
    return {
        "status": "Pullback risk rising",
        "signal": "Support test",
        "risk": "High" if abs(pct) >= threshold * 1.7 else "Medium",
        "why": "The asset is making a meaningful negative move that can pressure sentiment or trigger risk management.",
        "impact": "Defensive conditions may persist over the next 1-5 trading days if the move broadens.",
        "action": "Watch support, volatility, and confirmation before drawing conclusions. This is market intelligence, not financial advice.",
    }


def _severity_for(config: dict[str, Any], pct: float, threshold: float) -> str:
    asset_class = str(config.get("asset_class") or "")
    magnitude = abs(pct)
    if asset_class == "volatility" and pct >= 15:
        return "urgent"
    if asset_class in {"index", "etf"} and magnitude >= max(2.5, threshold * 2):
        return "urgent"
    if magnitude >= threshold:
        return "high"
    return "normal"


def _candidate_from_quote(row: dict[str, Any], *, source_key: str = "yahoo_finance") -> IntelligenceCandidate | None:
    symbol = str(row.get("symbol") or "").upper()
    config = ASSETS.get(symbol)
    if not config:
        return None
    pct = safe_float(row.get("regularMarketChangePercent"))
    threshold = safe_float(config.get("threshold"), 1.0)
    if abs(pct) < threshold:
        return None
    price = safe_float(row.get("regularMarketPrice"))
    volume = safe_float(row.get("regularMarketVolume"))
    asset = str(config.get("asset") or symbol)
    status = _status_for(config, pct, threshold)
    severity = _severity_for(config, pct, threshold)
    direction = "higher" if pct > 0 else "lower"
    confidence = clamp(0.76 + min(0.14, abs(pct) / max(threshold * 24, 1)) + (0.04 if severity == "urgent" else 0.0))
    impact = clamp(0.66 + min(0.24, abs(pct) / max(threshold * 10, 1)) + (0.06 if severity == "urgent" else 0.0))
    window = _market_window()
    asset_key = str(config.get("asset_key") or symbol.lower())
    status_card = {
        "asset": asset,
        "status": status["status"],
        "signal": status["signal"],
        "confidence": f"{int(confidence * 100)}%",
        "risk": status["risk"],
        "time_horizon": "1-5 trading days",
        "why_it_matters": status["why"],
        "suggested_action": status["action"],
        "disclaimer": MARKET_DISCLAIMER,
    }
    return IntelligenceCandidate(
        stream="market_pulse",
        source=source_key,
        source_keys=[source_key],
        source_url="https://finance.yahoo.com/",
        source_confidence=0.72 if source_key == "yahoo_finance" else 0.76,
        title=f"{asset} {status['signal'].lower()}",
        summary=f"{asset} is near {price:,.2f} with an observed {pct:+.2f}% move in public market data.",
        why_it_matters=status["why"],
        expected_impact=status["impact"],
        category="major_market_event",
        asset_symbol=symbol,
        region="global",
        severity=severity,
        confidence=confidence,
        freshness_score=0.88,
        impact_score=impact,
        dedupe_key=f"market:{asset_key}:{status['signal'].lower().replace(' ', '_')}:{'up' if pct > 0 else 'down'}:{utc_now_iso()[:10]}:{window['key']}",
        event_time=utc_now_iso(),
        evidence=[{
            "source": source_key,
            "symbol": symbol,
            "asset": asset,
            "proxy": config.get("proxy"),
            "price": price,
            "change_percent": pct,
            "volume": volume,
            "threshold_percent": threshold,
        }],
        metadata={
            "asset": asset,
            "asset_key": asset_key,
            "asset_class": config.get("asset_class"),
            "proxy_symbol": config.get("proxy"),
            "market_intelligence_only": True,
            "financial_advice": False,
            "no_buy_sell_commands": True,
            "major_market_event": True,
            "cadence_eligible": True,
            "market_alert_reason": f"{asset} moved {pct:+.2f}%, crossing the {threshold:.2f}% signal threshold.",
            "market_window": window,
            "market_alert_schedule": MARKET_ALERT_SCHEDULE,
            "status_card": status_card,
            "observed_change_percent": pct,
            "observed_price": price,
            "observed_volume": volume,
            "safe_language": [
                "Market strength increasing",
                "Risk elevated",
                "Pullback risk rising",
                "Momentum improving",
                "Volatility expanding",
                "Defensive conditions",
                "Watch zone",
                "Research zone",
                "Confirmation needed",
                "Overextended market",
                "Support test",
                "Breakout watch",
            ],
            "disclaimer": MARKET_DISCLAIMER,
        },
    )


class MarketPulseCollector(BaseCollector):
    stream = "market_pulse"
    collector_key = "market_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        statuses: list[dict[str, Any]] = []
        candidates: list[IntelligenceCandidate] = []

        for source_key, env_name in [
            ("polygon", "POLYGON_API_KEY"),
            ("finnhub", "FINNHUB_API_KEY"),
            ("alpha_vantage", "ALPHA_VANTAGE_API_KEY"),
        ]:
            if not os.getenv(env_name):
                statuses.append(_source_config_status(source_key, env_name))

        try:
            data, cached, duration = self.fetch_json(
                f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={QUOTE_SYMBOLS}",
                cache_key="yahoo_major_market_quote_matrix",
                ttl_seconds=90,
            )
            rows = (((data or {}).get("quoteResponse") or {}).get("result") or []) if isinstance(data, dict) else []
            statuses.append(source_status("yahoo_finance", "success_cached" if cached else "success", duration_ms=duration, candidates=len(rows)))
            for row in _choose_best_rows([item for item in rows if isinstance(item, dict)]):
                candidate = _candidate_from_quote(row)
                if candidate:
                    candidates.append(candidate)
        except Exception as exc:
            statuses.append(source_status("yahoo_finance", "failed", reason=network_error_message(exc)))

        status = "success" if any(item["status"].startswith("success") for item in statuses) else "failed"
        if not candidates and status == "success":
            message = "No major market event exceeded acceptance thresholds."
        elif not candidates:
            message = "No configured market source returned usable quote data."
        else:
            message = ""
        candidates.sort(key=lambda item: (item.metadata.get("asset_key") != "sp500", -abs(safe_float(item.metadata.get("observed_change_percent")))))
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status=status,
            candidates=candidates[: int(limit or 20)],
            source_statuses=statuses,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=message,
        )
