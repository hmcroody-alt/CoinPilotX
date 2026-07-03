#!/usr/bin/env python3
"""Audit PulseSoc market and crypto intelligence advisor wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    market_text = read("services/intelligence_collectors/markets.py")
    engine_text = read("services/pulsesoc_intelligence_engine.py")
    ai_text = read("services/pulse_ai_knowledge.py")
    admin_template = read("templates/admin_galaxy_intelligence_center.html")
    knowledge = json.loads(read("data/pulse_ai/pulsesoc_knowledge.json"))
    feature_map = json.loads(read("data/pulse_ai/pulsesoc_feature_map.json"))

    for token in [
        "S&P 500",
        "NASDAQ",
        "Dow Jones",
        "Russell 2000",
        "VIX",
        "10Y Treasury Yield",
        "USD Index",
        "Gold",
        "Oil",
        "Major sector ETFs",
    ]:
        require(token in engine_text or token in market_text, f"tracked asset missing: {token}", failures)

    for token in ["threshold\": 1.0", "threshold\": 1.5", "threshold\": 8.0", "Fed", "CPI", "jobs"]:
        require(token in engine_text or token in market_text or token in ai_text, f"market rule missing: {token}", failures)

    require("MARKET_INTELLIGENCE_ALERT_SCHEDULE" in engine_text, "central market cadence schedule missing", failures)
    require("MARKET_INTELLIGENCE_SIGNAL_RULES" in engine_text, "central market signal rules missing", failures)
    require("_cadence_event_allowed" in engine_text and "market_pulse" in engine_text, "market cadence guard missing", failures)
    require("major_market_event" in market_text and "cadence_eligible" in market_text, "collector does not mark major market cadence eligibility", failures)
    require("finnhub" in engine_text and "FINNHUB_API_KEY" in engine_text, "Finnhub source readiness missing", failures)
    require("polygon" in engine_text and "alpha_vantage" in engine_text, "market source registry incomplete", failures)
    require("skipped_config_missing" in market_text, "missing market provider keys are not skipped safely", failures)

    from services.intelligence_collectors import markets

    sp_candidate = markets._candidate_from_quote({
        "symbol": "^GSPC",
        "regularMarketChangePercent": 1.21,
        "regularMarketPrice": 6025.5,
        "regularMarketVolume": 1200000000,
    })
    require(sp_candidate is not None, "S&P 500 1% move does not create candidate", failures)
    if sp_candidate:
        metadata = sp_candidate.metadata
        card = metadata.get("status_card") or {}
        require(sp_candidate.stream == "market_pulse", "S&P candidate stream is wrong", failures)
        require(metadata.get("asset") == "S&P 500", "S&P candidate asset metadata missing", failures)
        require(metadata.get("major_market_event") is True, "S&P candidate not marked as major event", failures)
        require("not financial advice" in str(card.get("disclaimer", "")).lower(), "market status card missing disclaimer", failures)
        require(card.get("signal") in {"Breakout watch", "Support test", "VIX volatility spike", "Volatility cooling", "Yield or dollar strength"}, "status card signal is not safe language", failures)

    weak_candidate = markets._candidate_from_quote({
        "symbol": "^GSPC",
        "regularMarketChangePercent": 0.42,
        "regularMarketPrice": 6025.5,
    })
    require(weak_candidate is None, "weak S&P move should not create alert candidate", failures)

    vix_candidate = markets._candidate_from_quote({
        "symbol": "^VIX",
        "regularMarketChangePercent": 9.4,
        "regularMarketPrice": 18.7,
    })
    require(vix_candidate is not None, "VIX 8%+ move does not create candidate", failures)
    if vix_candidate:
        require(vix_candidate.metadata.get("status_card", {}).get("signal") == "VIX volatility spike", "VIX candidate uses wrong signal", failures)

    public_knowledge = "\n".join(json.dumps(item, sort_keys=True) for item in knowledge + feature_map)
    for token in ["S&P 500", "NASDAQ", "VIX", "Treasury yields", "Market Signals"]:
        require(token in public_knowledge or token in ai_text, f"Pulse AI market knowledge missing: {token}", failures)
    for forbidden in ["buy now", "sell now", "guaranteed profit", "financial advisor"]:
        require(forbidden not in market_text.lower(), f"collector contains unsafe wording: {forbidden}", failures)
    ai_compact = " ".join(ai_text.lower().split())
    require("do not give reckless buy/sell commands" in ai_compact, "Pulse AI safety rule for buy/sell commands missing", failures)
    require("educational market intelligence only" in ai_compact, "Pulse AI market disclaimer missing", failures)
    require("What is the S&P 500 doing today?" in ai_text, "Pulse AI quick prompt missing S&P 500 QA prompt", failures)

    require("Market Intelligence" in admin_template and "S&amp;P 500 + Major Markets" in admin_template, "admin market dashboard section missing", failures)
    require("dashboard.market_intelligence.source_health" in admin_template, "admin source health for market dashboard missing", failures)

    report_path = ROOT / "reports" / "market_and_crypto_intelligence.md"
    require(report_path.exists(), "market and crypto intelligence report missing", failures)

    if failures:
        print("Market and crypto intelligence audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Market and crypto intelligence audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
