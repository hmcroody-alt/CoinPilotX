"""Provider orchestration for CoinPilotXAI live market intelligence.

This module is intentionally defensive: it normalizes provider output, caches
short lived responses, and returns honest fallback states instead of pretending
stale or missing data is live.
"""

import logging
import os
import time
from datetime import datetime

import requests

from . import market_data, predictions_service


CACHE = {}
DEFAULT_TIMEOUT = float(os.getenv("LIVE_PROVIDER_TIMEOUT_SECONDS", "7"))


def _now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def _cached(key, ttl):
    item = CACHE.get(key) or {}
    if item.get("data") is not None and time.time() - item.get("created_at", 0) < ttl:
        data = dict(item["data"])
        data["cache"] = {"hit": True, "age_seconds": int(time.time() - item.get("created_at", 0)), "ttl_seconds": ttl}
        return data
    return None


def _store(key, data):
    CACHE[key] = {"data": data, "created_at": time.time()}
    return data


def get_crypto_market(category="top_volume", limit=50):
    key = f"crypto:{category}:{limit}"
    cached = _cached(key, 45)
    if cached:
        return cached
    data = market_data.live_market_board(category=category, limit=limit)
    data.setdefault("ok", True)
    data.setdefault("providers", ["coingecko", "coinbase_public_fallback"])
    data.setdefault("updated_at", _now_iso())
    data["cache"] = {"hit": False, "age_seconds": 0, "ttl_seconds": 45}
    return _store(key, data)


def get_crypto_quote(symbol):
    symbol = (symbol or "BTC").upper()
    board = get_crypto_market(limit=80)
    item = market_data.get_symbol(symbol)
    if item:
        return {"ok": True, "asset": item, "source": board.get("source"), "updated_at": board.get("updated_at"), "stale": False}
    return {
        "ok": False,
        "asset": {"symbol": symbol, "name": symbol},
        "source": "unavailable",
        "updated_at": _now_iso(),
        "message": "Live source is reconnecting right now. Here is what I can safely tell you… this asset quote is temporarily unavailable.",
        "stale": True,
    }


def get_fear_greed():
    cached = _cached("fear_greed", 1800)
    if cached:
        return cached
    try:
        response = requests.get("https://api.alternative.me/fng/", timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        item = (payload.get("data") or [{}])[0]
        data = {
            "ok": True,
            "source": "alternative.me",
            "value": item.get("value"),
            "classification": item.get("value_classification"),
            "updated_at": _now_iso(),
            "cache": {"hit": False, "age_seconds": 0, "ttl_seconds": 1800},
        }
    except Exception as exc:
        logging.info("Fear & Greed provider unavailable: %s", exc)
        data = {"ok": False, "source": "alternative.me", "message": "Live source is reconnecting right now. Here is what I can safely tell you… sentiment data is temporarily unavailable.", "updated_at": _now_iso()}
    return _store("fear_greed", data)


def get_btc_network():
    cached = _cached("btc_network", 300)
    if cached:
        return cached
    try:
        response = requests.get("https://mempool.space/api/v1/fees/recommended", timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        fees = response.json()
        data = {"ok": True, "source": "mempool.space", "fees": fees, "updated_at": _now_iso(), "cache": {"hit": False, "age_seconds": 0, "ttl_seconds": 300}}
    except Exception as exc:
        logging.info("mempool.space unavailable: %s", exc)
        data = {"ok": False, "source": "mempool.space", "message": "Live source is reconnecting right now. Here is what I can safely tell you… BTC network data is temporarily unavailable.", "updated_at": _now_iso()}
    return _store("btc_network", data)


def get_predictions(limit=30):
    markets = predictions_service.get_active_crypto_predictions(limit=limit)
    status = predictions_service.get_prediction_provider_status()
    return {"ok": True, "markets": markets, "status": status, "source": status.get("provider", "polymarket"), "updated_at": _now_iso()}


def health():
    market = get_crypto_market(limit=10)
    fear = get_fear_greed()
    btc_network = get_btc_network()
    predictions = get_predictions(limit=10)
    return {
        "ok": True,
        "providers": {
            "coingecko_or_fallback": {"ok": bool(market.get("markets")), "source": market.get("source"), "cache": market.get("cache")},
            "fear_greed": {"ok": fear.get("ok"), "source": fear.get("source")},
            "mempool_space": {"ok": btc_network.get("ok"), "source": btc_network.get("source")},
            "predictions": predictions.get("status"),
            "coinmarketcap": {"configured": bool(os.getenv("COINMARKETCAP_API_KEY"))},
            "cryptopanic": {"configured": bool(os.getenv("CRYPTOPANIC_API_KEY"))},
            "dexscreener": {"configured": True, "note": "public endpoint ready for token-detail expansion"},
            "whale_alert": {"configured": bool(os.getenv("WHALE_ALERT_API_KEY"))},
        },
        "updated_at": _now_iso(),
    }
