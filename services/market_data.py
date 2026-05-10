import os
import time
import logging
import requests

CACHE = {"data": None, "created_at": 0}
CACHE_SECONDS = int(os.getenv("MARKETS_CACHE_SECONDS", "60"))


def normalize_market_item(item):
    return {
        "id": item.get("id") or (item.get("symbol") or "").lower(),
        "name": item.get("name") or (item.get("symbol") or "").upper(),
        "symbol": (item.get("symbol") or "").upper(),
        "image": item.get("image") or "",
        "price": item.get("current_price"),
        "volume_24h": item.get("total_volume"),
        "change_24h": item.get("price_change_percentage_24h"),
        "market_cap": item.get("market_cap"),
    }


def fetch_coingecko_markets():
    headers = {}
    api_key = os.getenv("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    response = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 50,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
        headers=headers,
        timeout=12,
    )
    response.raise_for_status()
    return [normalize_market_item(item) for item in response.json()]


def fetch_coinbase_fallback_markets():
    names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}
    markets = []
    for symbol, name in names.items():
        try:
            response = requests.get(f"https://api.exchange.coinbase.com/products/{symbol}-USD/ticker", timeout=8)
            response.raise_for_status()
            payload = response.json()
            markets.append({
                "id": symbol.lower(),
                "name": name,
                "symbol": symbol,
                "image": "",
                "price": float(payload.get("price")),
                "volume_24h": float(payload.get("volume", 0)),
                "change_24h": None,
                "market_cap": None,
            })
        except Exception as exc:
            logging.info("Coinbase market fallback failed for %s: %s", symbol, exc)
    return markets


def sort_markets(markets, category="top_volume"):
    category = (category or "top_volume").lower()
    if category in {"top_market_cap", "market_cap", "cap"}:
        return sorted(markets, key=lambda x: x.get("market_cap") or 0, reverse=True)
    if category == "gainers":
        return sorted(markets, key=lambda x: x.get("change_24h") if x.get("change_24h") is not None else -999, reverse=True)
    if category == "losers":
        return sorted(markets, key=lambda x: x.get("change_24h") if x.get("change_24h") is not None else 999)
    return sorted(markets, key=lambda x: x.get("volume_24h") or 0, reverse=True)


def summary_metrics(markets):
    loaded = [m for m in markets if m.get("change_24h") is not None]
    gainers = [m for m in loaded if (m.get("change_24h") or 0) > 0]
    losers = [m for m in loaded if (m.get("change_24h") or 0) < 0]
    btc = next((m for m in markets if m.get("symbol") == "BTC"), None)
    eth = next((m for m in markets if m.get("symbol") == "ETH"), None)
    avg_change = sum(float(m.get("change_24h") or 0) for m in loaded) / len(loaded) if loaded else None
    risk = "Elevated" if avg_change is not None and avg_change < -2 else "Medium" if avg_change is None else "Normal"
    trend = "mixed"
    if avg_change is not None:
        trend = "bullish" if avg_change > 1 else "bearish" if avg_change < -1 else "neutral"
    return {
        "btc_price": btc.get("price") if btc else None,
        "eth_price": eth.get("price") if eth else None,
        "market_trend": trend,
        "risk_level": risk,
        "gainers": len(gainers),
        "losers": len(losers),
        "average_change_24h": avg_change,
        "fallback": not any(m.get("change_24h") is not None for m in markets),
    }


def live_market_board(category="top_volume", limit=50):
    now = time.time()
    if CACHE["data"] and now - CACHE["created_at"] < CACHE_SECONDS:
        cached = dict(CACHE["data"])
        cached["markets"] = sort_markets(cached.get("markets", []), category)[:limit]
        return cached
    warning = None
    source = "coingecko"
    try:
        markets = fetch_coingecko_markets()
    except Exception as exc:
        logging.info("CoinGecko markets unavailable: %s", exc)
        markets = fetch_coinbase_fallback_markets()
        source = "coinbase_public_fallback"
        warning = "Live data source is partially connected. Showing Coinbase BTC/ETH/SOL fallback where available."
    if not markets:
        warning = "Live data source is not connected yet."
    payload = {
        "source": source if markets else "unavailable",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "warning": warning,
        "markets": sort_markets(markets, category)[:limit],
        "summary": summary_metrics(markets),
    }
    CACHE["data"] = dict(payload, markets=markets)
    CACHE["created_at"] = now
    return payload


def get_symbol(symbol):
    symbol = (symbol or "BTC").upper()
    data = live_market_board(limit=80)
    return next((item for item in data.get("markets", []) if item.get("symbol") == symbol), None)
