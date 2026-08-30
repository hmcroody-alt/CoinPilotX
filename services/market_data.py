import os
import time
import logging
import requests

from services import coingecko_client

CACHE = {"data": None, "created_at": 0}
CACHE_SECONDS = int(os.getenv("MARKETS_CACHE_SECONDS", "60"))

# One history series per (coin, range), kept for a range-appropriate time. A 1Y
# chart does not become wrong in ninety seconds, and re-fetching it on every
# screen open is what gets an API key rate-limited.
HISTORY_CACHE = {}
HISTORY_CACHE_SECONDS = {
    "1H": 60,
    "24H": 180,
    "7D": 900,
    "1M": 3600,
    "1Y": 21600,
    "ALL": 21600,
}
# CoinGecko `days` parameter per supported range. 1H has no `days` of its own —
# it is the tail of the 1-day series, which arrives at ~5 minute granularity.
#
# "ALL" is a number, not "max". The Basic plan rejects days=max outright (401)
# and caps numeric days at 730 — 730 answers, 731 is refused. So "max" made the
# ALL tab fail on every single request, surfacing as "temporarily unavailable"
# or an indefinitely stale series: a permanent plan limit wearing the costume of
# a provider blip. 730 is what this plan can actually answer. Widening it is a
# billing decision, not a code change, hence the env override.
HISTORY_MAX_DAYS = int(os.getenv("COINGECKO_HISTORY_MAX_DAYS", "730"))
HISTORY_RANGE_DAYS = {"1H": 1, "24H": 1, "7D": 7, "1M": 30, "1Y": 365, "ALL": HISTORY_MAX_DAYS}
HISTORY_RANGES = tuple(HISTORY_RANGE_DAYS)
# Enough points to draw a smooth line, few enough to keep the payload small.
HISTORY_MAX_POINTS = 120
SPARKLINE_MAX_POINTS = 24


def _downsample(values, limit):
    """Evenly thin a series to at most ``limit`` points, always keeping the last.

    The final point is the newest price, and a sparkline whose right edge is not
    the current price reads as a lie next to the price label beside it.
    """
    points = [value for value in (values or []) if isinstance(value, (int, float))]
    if len(points) <= limit:
        return points
    step = len(points) / float(limit)
    thinned = [points[min(len(points) - 1, int(index * step))] for index in range(limit)]
    thinned[-1] = points[-1]
    return thinned


def normalize_market_item(item):
    sparkline = (item.get("sparkline_in_7d") or {}).get("price") or []
    return {
        "id": item.get("id") or (item.get("symbol") or "").lower(),
        "name": item.get("name") or (item.get("symbol") or "").upper(),
        "symbol": (item.get("symbol") or "").upper(),
        "image": item.get("image") or "",
        "price": item.get("current_price"),
        "volume_24h": item.get("total_volume"),
        "change_24h": item.get("price_change_percentage_24h"),
        "market_cap": item.get("market_cap"),
        "market_cap_rank": item.get("market_cap_rank"),
        "circulating_supply": item.get("circulating_supply"),
        "max_supply": item.get("max_supply"),
        "price_change_24h": item.get("price_change_24h"),
        # Comes free with the board request, so a fifty-row watchlist costs the
        # provider exactly one call rather than fifty.
        "sparkline": _downsample(sparkline, SPARKLINE_MAX_POINTS),
    }


def fetch_coingecko_markets():
    # All auth/host/retry/telemetry lives in the canonical client. This function
    # keeps its raise-on-failure contract because `live_market_board` uses the
    # exception to trigger the Coinbase fallback ladder.
    data = coingecko_client.get_json(
        "/coins/markets",
        {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 50,
            "page": 1,
            # Costs one field on an existing request and removes the need for a
            # per-row history call. See `normalize_market_item`.
            "sparkline": "true",
            "price_change_percentage": "24h",
        },
        timeout=12,
    )
    if not isinstance(data, list):
        raise RuntimeError("coingecko markets unavailable")
    return [normalize_market_item(item) for item in data]


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
                # Coinbase's ticker carries no 24h percentage, no cap and no
                # history. These stay None so every consumer renders "--" rather
                # than inventing a number to fill the column.
                "change_24h": None,
                "market_cap": None,
                "market_cap_rank": None,
                "circulating_supply": None,
                "max_supply": None,
                "price_change_24h": None,
                "sparkline": [],
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
    """The provider's row for one symbol, or None if it cannot be resolved.

    "The board is unreachable" and "the board does not list this asset" are
    different causes with the same honest answer: we have no market row. Callers
    already render that as "--"/"Unavailable", so an outage must not reach them
    as an exception and turn a degraded screen into a failed one.
    """
    symbol = (symbol or "BTC").upper()
    try:
        data = live_market_board(limit=80)
    except Exception as exc:
        logging.info("Market board unavailable while resolving %s: %s", symbol, exc)
        return None
    return next((item for item in data.get("markets", []) if item.get("symbol") == symbol), None)


def fetch_coingecko_history(coin_id, days):
    # Raise-on-failure preserved: `asset_history` catches and serves its own
    # stale/unavailable ladder.
    data = coingecko_client.get_json(
        f"/coins/{coin_id}/market_chart",
        {"vs_currency": "usd", "days": days},
        timeout=12,
    )
    if not isinstance(data, dict):
        raise RuntimeError("coingecko history unavailable")
    return data.get("prices") or []


def asset_history(symbol, range_key="24H"):
    """Real price history for one asset, or an honest empty series.

    There is an older ``/api/quote/crypto/<symbol>/chart`` route that synthesises
    points with a sine wave around the current price. Nothing here may use it:
    a chart is a factual claim about what the price *did*, and a decorative
    wave that tracks spot is the most convincing kind of wrong. When the
    provider cannot answer, this returns no points and says so, and the caller
    is expected to render "Unavailable" rather than a line.
    """
    symbol = (symbol or "BTC").upper()
    range_key = str(range_key or "24H").upper()
    if range_key not in HISTORY_RANGE_DAYS:
        range_key = "24H"

    cache_key = (symbol, range_key)
    now = time.time()
    cached = HISTORY_CACHE.get(cache_key)
    if cached and now - cached["created_at"] < HISTORY_CACHE_SECONDS[range_key]:
        return dict(cached["payload"])

    # Known majors resolve from the canonical table first, so the id survives a
    # board outage. It also avoids a specific wrong answer: during Coinbase
    # fallback the board's `id` is a lowercased ticker ("btc"), and asking
    # CoinGecko for /coins/btc/market_chart is a 404 for an asset we can name
    # perfectly well. Unknown symbols still resolve from the live board, which
    # tracks new listings that no static table would.
    coin_id = coingecko_client.coin_id(symbol)
    if not coin_id:
        asset = get_symbol(symbol)
        coin_id = (asset or {}).get("id")
    if not coin_id:
        stale = HISTORY_CACHE.get(cache_key)
        if stale:
            # We have resolved this asset before, so the id is missing now
            # because the board is unreachable, not because the asset is
            # unknown. The last real series is the honest answer, labelled.
            payload = dict(stale["payload"])
            payload["stale"] = True
            payload["warning"] = "Showing the last available history while the market source reconnects."
            return payload
        # Outside the provider's board we have no id to ask about, so there is
        # no honest series to return.
        return {
            "ok": False,
            "symbol": symbol,
            "range": range_key,
            "points": [],
            "source": "unavailable",
            "warning": "Price history is not available for this asset yet.",
        }

    try:
        raw = fetch_coingecko_history(coin_id, HISTORY_RANGE_DAYS[range_key])
    except Exception as exc:
        logging.info("CoinGecko history unavailable for %s (%s): %s", symbol, range_key, exc)
        stale = HISTORY_CACHE.get(cache_key)
        if stale:
            # Last real series beats no chart, as long as it is labelled stale.
            payload = dict(stale["payload"])
            payload["stale"] = True
            payload["warning"] = "Showing the last available history while the market source reconnects."
            return payload
        return {
            "ok": False,
            "symbol": symbol,
            "range": range_key,
            "points": [],
            "source": "unavailable",
            "warning": "Price history is temporarily unavailable.",
        }

    pairs = [pair for pair in raw if isinstance(pair, (list, tuple)) and len(pair) >= 2]
    if range_key == "1H":
        cutoff = (pairs[-1][0] if pairs else 0) - 3600 * 1000
        hour = [pair for pair in pairs if pair[0] >= cutoff]
        # A provider that only returns daily granularity cannot answer "1H".
        pairs = hour if len(hour) >= 2 else []

    if len(pairs) > HISTORY_MAX_POINTS:
        step = len(pairs) / float(HISTORY_MAX_POINTS)
        thinned = [pairs[min(len(pairs) - 1, int(index * step))] for index in range(HISTORY_MAX_POINTS)]
        thinned[-1] = pairs[-1]
        pairs = thinned

    points = [{"t": int(pair[0]), "price": float(pair[1])} for pair in pairs]
    payload = {
        "ok": bool(points),
        "symbol": symbol,
        "range": range_key,
        "points": points,
        "source": "coingecko" if points else "unavailable",
        "warning": None if points else f"No {range_key} history is available for this asset.",
    }
    if points:
        HISTORY_CACHE[cache_key] = {"payload": dict(payload), "created_at": now}
    return payload


def supported_history_ranges(symbol):
    """Which chart ranges this asset can actually answer.

    A range is only offered when the provider knows the asset at all. Offering
    a tab that will always render "Unavailable" is worse than not offering it.
    """
    asset = get_symbol(symbol)
    if not (asset or {}).get("id"):
        return []
    return list(HISTORY_RANGES)
