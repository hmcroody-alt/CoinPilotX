def exposure_summary(holdings=None, market=None):
    holdings = holdings or {}
    market = market or {}
    return {
        "risk_level": "Medium",
        "summary": "Portfolio intelligence is available when holdings and live market data are connected.",
        "holdings_count": len(holdings),
        "market_state": market.get("market_state", "unknown"),
    }
