import logging
import os
import time
from datetime import datetime, timedelta

import requests


CACHE = {"markets": None, "created_at": 0, "status": {}}
CACHE_SECONDS = int(os.getenv("PREDICTIONS_CACHE_SECONDS", "90"))
PREDICTION_SAMPLE_WINDOW_DAYS = 30
PREDICTION_SAMPLE_RESOLUTION_DAYS = 37
CRYPTO_TERMS = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "solana",
    " sol",
    "xrp",
    "crypto",
    "stablecoin",
    "etf",
    "token",
    "blockchain",
)


def _sample_markets(limit=30):
    now = datetime.utcnow()
    samples = [
        ("btc-100k-scenario", "Will BTC test a six-figure zone this cycle?", 54, 1240000, 380000, "high", "BTC"),
        ("eth-spot-flow-scenario", "Will ETH see stronger spot-market demand this month?", 49, 685000, 210000, "medium", "ETH"),
        ("altcoin-liquidity-scenario", "Will altcoin liquidity expand before the next macro data release?", 43, 338000, 128000, "high", "ALT"),
        ("sol-network-activity", "Will Solana network activity accelerate this month?", 51, 315000, 120000, "medium", "SOL"),
    ]
    return [
        {
            "id": market_id,
            "title": title,
            "category": "crypto",
            "status": "active",
            "source": "educational_sample",
            "source_url": "",
            "outcomes": ["Yes", "No"],
            "yes_probability": probability,
            "no_probability": 100 - probability,
            "probability": probability,
            "price": round(probability / 100, 2),
            "volume": volume,
            "liquidity": liquidity,
            "close_time": (now + timedelta(days=PREDICTION_SAMPLE_WINDOW_DAYS)).isoformat(),
            "resolve_time": (now + timedelta(days=PREDICTION_SAMPLE_RESOLUTION_DAYS)).isoformat(),
            "last_updated": now.isoformat(),
            "risk_level": risk,
            "symbol": symbol,
            "is_live": False,
        }
        for market_id, title, probability, volume, liquidity, risk, symbol in samples[:limit]
    ]


def _number(value, default=0):
    try:
        if isinstance(value, list):
            value = value[0] if value else default
        return float(value)
    except Exception:
        return default


def _outcomes(value):
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                import json

                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except Exception:
                pass
        if stripped:
            return [part.strip().strip('"') for part in stripped.split(",") if part.strip()]
    return ["Yes", "No"]


def _probability(raw):
    candidates = [
        raw.get("lastTradePrice"),
        raw.get("bestAsk"),
        raw.get("bestBid"),
        raw.get("price"),
        raw.get("outcomePrices"),
    ]
    for value in candidates:
        num = _number(value, None)
        if num is None:
            continue
        if 0 <= num <= 1:
            return round(num * 100)
        if 1 < num <= 100:
            return round(num)
    return 50


def normalize_prediction_market(raw):
    market_id = str(raw.get("id") or raw.get("conditionId") or raw.get("slug") or "").strip()
    title = str(raw.get("question") or raw.get("title") or raw.get("description") or "Crypto prediction market").strip()
    yes_probability = _probability(raw)
    outcomes = _outcomes(raw.get("outcomes") or raw.get("shortOutcomes"))
    end_date = raw.get("endDate") or raw.get("end_date") or raw.get("closedTime") or raw.get("createdAt")
    updated = raw.get("updatedAt") or raw.get("lastUpdated") or datetime.utcnow().isoformat()
    source_url = raw.get("url") or raw.get("slug") or ""
    if source_url and not source_url.startswith("http"):
        source_url = "https://polymarket.com/event/" + source_url.strip("/")
    risk = "high" if _number(raw.get("volume"), 0) < 10000 else "medium"
    symbol = "BTC" if "btc" in title.lower() or "bitcoin" in title.lower() else "ETH" if "eth" in title.lower() or "ethereum" in title.lower() else "SOL" if "solana" in title.lower() else "CRYPTO"
    return {
        "id": market_id or str(abs(hash(title))),
        "title": title,
        "category": "crypto",
        "status": "active",
        "source": "polymarket",
        "source_url": source_url,
        "outcomes": outcomes,
        "yes_probability": yes_probability,
        "no_probability": max(0, 100 - yes_probability),
        "probability": yes_probability,
        "price": round(yes_probability / 100, 2),
        "volume": _number(raw.get("volume") or raw.get("volumeNum"), 0),
        "liquidity": _number(raw.get("liquidity") or raw.get("liquidityNum"), 0),
        "close_time": end_date or "",
        "resolve_time": raw.get("resolutionDate") or end_date or "",
        "last_updated": updated,
        "risk_level": risk,
        "symbol": symbol,
        "is_live": True,
    }


def _is_crypto_market(raw):
    haystack = " ".join(
        str(raw.get(key) or "")
        for key in ("question", "title", "description", "slug", "category")
    ).lower()
    tags = raw.get("tags") or []
    if isinstance(tags, list):
        haystack += " " + " ".join(str(tag.get("label") if isinstance(tag, dict) else tag) for tag in tags).lower()
    return any(term in haystack for term in CRYPTO_TERMS)


def _fetch_polymarket(limit):
    response = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={
            "active": "true",
            "closed": "false",
            "limit": max(limit * 3, 60),
            "order": "volume",
            "ascending": "false",
        },
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("markets") or payload.get("data") or []
    markets = []
    for raw in rows:
        if not isinstance(raw, dict) or not _is_crypto_market(raw):
            continue
        market = normalize_prediction_market(raw)
        if market.get("id") and market.get("title"):
            markets.append(market)
        if len(markets) >= limit:
            break
    return markets


def get_active_crypto_predictions(limit=30):
    now = time.time()
    if CACHE["markets"] and now - CACHE["created_at"] < CACHE_SECONDS:
        return CACHE["markets"][:limit]
    provider = (os.getenv("PREDICTIONS_PROVIDER") or "polymarket").lower()
    markets = []
    status = {"provider": provider, "reachable": False, "fallback": False, "error": "", "last_successful_fetch": ""}
    try:
        if provider == "polymarket":
            markets = _fetch_polymarket(limit)
        elif provider in {"kalshi", "propheseer"}:
            status["error"] = f"{provider} adapter is not configured."
        else:
            status["error"] = f"Unknown provider: {provider}"
        if markets:
            status.update({"reachable": True, "last_successful_fetch": datetime.utcnow().isoformat(), "count": len(markets)})
    except Exception as exc:
        logging.info("Predictions provider failed: %s", exc)
        status["error"] = str(exc)[:240]
    if not markets:
        markets = _sample_markets(limit)
        status["fallback"] = True
        status["count"] = len(markets)
    CACHE["markets"] = markets
    CACHE["created_at"] = now
    CACHE["status"] = status
    return markets[:limit]


def get_prediction_by_id(market_id):
    markets = get_active_crypto_predictions(limit=80)
    return next((market for market in markets if str(market.get("id")) == str(market_id)), None)


def get_prediction_context_for_ai(market_id):
    market = get_prediction_by_id(market_id)
    if not market:
        return "Prediction context unavailable."
    return (
        f"Prediction: {market.get('title')}\n"
        f"Source: {market.get('source')}\n"
        f"Probability: {market.get('yes_probability')}% yes / {market.get('no_probability')}% no\n"
        f"Volume: {market.get('volume')}\n"
        f"Liquidity: {market.get('liquidity')}\n"
        f"Close time: {market.get('close_time')}\n"
        f"Risk level: {market.get('risk_level')}\n"
        "Explain drivers, uncertainty, market psychology, and risk. Do not guarantee outcomes."
    )


def get_prediction_provider_status():
    markets = get_active_crypto_predictions(limit=30)
    status = dict(CACHE.get("status") or {})
    status["active_crypto_markets"] = len(markets)
    status["cache_age_seconds"] = max(0, int(time.time() - (CACHE.get("created_at") or 0)))
    return status


def cache_predictions():
    CACHE["created_at"] = 0
    return get_active_crypto_predictions()
