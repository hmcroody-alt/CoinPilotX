"""CryptoMarketProvider abstraction for Pulse Briefings.

Normalizes provider payloads into PulseSoc-owned schemas so nothing outside
this module sees vendor-specific fields. Primary: CoinGecko (already the
platform provider — services/market_data.py). Fallback: Coinbase public
ticker (majors only). Every fact carries provider + observed_at/retrieved_at
so staleness is detectable downstream.

A shared TTL cache with single-flight locking guarantees N users -> 1
provider call per window, never N calls.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

COINGECKO_BASE = os.getenv("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3")
_CG_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
COINBASE_TICKER = "https://api.exchange.coinbase.com/products/{symbol}-USD/ticker"

OVERVIEW_TTL_SECONDS = int(os.getenv("BRIEFING_MARKET_OVERVIEW_TTL", "180"))
MOVERS_TTL_SECONDS = int(os.getenv("BRIEFING_MARKET_MOVERS_TTL", "240"))
TRENDING_TTL_SECONDS = int(os.getenv("BRIEFING_MARKET_TRENDING_TTL", "300"))
STALE_MAX_SECONDS = int(os.getenv("BRIEFING_MARKET_STALE_MAX", "1800"))
REQUEST_TIMEOUT = int(os.getenv("BRIEFING_MARKET_TIMEOUT", "8"))

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_FLIGHT_LOCKS: dict[str, threading.Lock] = {}
_METRICS = {"crypto_cache_hits": 0, "crypto_cache_misses": 0, "crypto_provider_errors": 0, "crypto_provider_429": 0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def metrics_snapshot() -> dict[str, int]:
    return dict(_METRICS)


def _flight_lock(key: str) -> threading.Lock:
    with _CACHE_LOCK:
        lock = _FLIGHT_LOCKS.get(key)
        if lock is None:
            lock = _FLIGHT_LOCKS[key] = threading.Lock()
        return lock


def _cached(key: str, ttl: int, loader):
    """TTL cache with single-flight: concurrent callers share one fetch."""
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.time() - entry["at"] < ttl:
            _METRICS["crypto_cache_hits"] += 1
            return entry["value"]
    lock = _flight_lock(key)
    with lock:
        with _CACHE_LOCK:  # re-check after winning the flight lock
            entry = _CACHE.get(key)
            if entry and time.time() - entry["at"] < ttl:
                _METRICS["crypto_cache_hits"] += 1
                return entry["value"]
        _METRICS["crypto_cache_misses"] += 1
        value = loader()
        if value is not None:
            with _CACHE_LOCK:
                _CACHE[key] = {"value": value, "at": time.time()}
            return value
        with _CACHE_LOCK:  # loader failed: serve stale if within hard bound
            entry = _CACHE.get(key)
            if entry and time.time() - entry["at"] < STALE_MAX_SECONDS:
                stale = dict(entry["value"]) if isinstance(entry["value"], dict) else entry["value"]
                if isinstance(stale, dict):
                    stale["stale"] = True
                return stale
        return None


def _cg_get(path: str, params: dict[str, Any] | None = None):
    headers = {"Accept": "application/json"}
    if _CG_KEY:
        headers["x-cg-demo-api-key"] = _CG_KEY
    try:
        resp = requests.get(f"{COINGECKO_BASE}{path}", params=params or {}, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            _METRICS["crypto_provider_429"] += 1
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - provider fault must degrade, not raise
        _METRICS["crypto_provider_errors"] += 1
        logging.warning("BRIEFING_CRYPTO_PROVIDER_ERROR path=%s error=%s", path, str(exc)[:200])
        return None


def _asset(row: dict[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "name": row.get("name") or "",
        "price": float(row.get("current_price") or 0),
        "market_cap": float(row.get("market_cap") or 0),
        "volume_24h": float(row.get("total_volume") or 0),
        "change_1h": row.get("price_change_percentage_1h_in_currency"),
        "change_24h": row.get("price_change_percentage_24h_in_currency", row.get("price_change_percentage_24h")),
        "change_7d": row.get("price_change_percentage_7d_in_currency"),
        "rank": row.get("market_cap_rank"),
        "provider": "coingecko",
        "observed_at": observed_at,
        "retrieved_at": observed_at,
    }


def _load_overview() -> dict[str, Any] | None:
    observed_at = _now_iso()
    markets = _cg_get("/coins/markets", {
        "vs_currency": "usd", "order": "market_cap_desc", "per_page": 50, "page": 1,
        "price_change_percentage": "1h,24h,7d",
    })
    if not isinstance(markets, list) or not markets:
        return _coinbase_fallback_overview()
    assets = [_asset(r, observed_at) for r in markets if isinstance(r, dict)]
    by_symbol = {a["symbol"]: a for a in assets}
    top10 = assets[:10]
    positive = sum(1 for a in top10 if (a["change_24h"] or 0) > 0)
    g = _cg_get("/global")
    gdata = (g or {}).get("data") or {}
    total_cap = float((gdata.get("total_market_cap") or {}).get("usd") or 0)
    total_vol = float((gdata.get("total_volume") or {}).get("usd") or 0)
    dominance = float((gdata.get("market_cap_percentage") or {}).get("btc") or 0)
    cap_change = gdata.get("market_cap_change_percentage_24h_usd")
    changes = [a["change_24h"] for a in top10 if a["change_24h"] is not None]
    avg_move = sum(abs(c) for c in changes) / len(changes) if changes else 0.0
    direction = "up" if positive >= 7 else "down" if positive <= 3 else "mixed"
    return {
        "generated_at": observed_at,
        "provider": "coingecko",
        "stale": False,
        "btc": by_symbol.get("BTC"),
        "eth": by_symbol.get("ETH"),
        "assets": assets,
        "total_market_cap": total_cap,
        "total_volume_24h": total_vol,
        "market_cap_change_24h_pct": cap_change,
        "btc_dominance": dominance,
        "market_direction": direction,
        "breadth_positive_top10": positive,
        "volatility_avg_abs_24h": round(avg_move, 2),
    }


def _coinbase_fallback_overview() -> dict[str, Any] | None:
    observed_at = _now_iso()
    assets = []
    for symbol in ("BTC", "ETH", "SOL"):
        try:
            resp = requests.get(COINBASE_TICKER.format(symbol=symbol), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            t = resp.json()
            assets.append({
                "symbol": symbol, "name": symbol, "price": float(t.get("price") or 0),
                "market_cap": 0.0, "volume_24h": float(t.get("volume") or 0),
                "change_1h": None, "change_24h": None, "change_7d": None, "rank": None,
                "provider": "coinbase", "observed_at": observed_at, "retrieved_at": observed_at,
            })
        except Exception:  # noqa: BLE001
            _METRICS["crypto_provider_errors"] += 1
    if not assets:
        return None
    by_symbol = {a["symbol"]: a for a in assets}
    return {
        "generated_at": observed_at, "provider": "coinbase", "stale": False,
        "btc": by_symbol.get("BTC"), "eth": by_symbol.get("ETH"), "assets": assets,
        "total_market_cap": 0.0, "total_volume_24h": 0.0, "market_cap_change_24h_pct": None,
        "btc_dominance": 0.0, "market_direction": "unknown",
        "breadth_positive_top10": None, "volatility_avg_abs_24h": None,
    }


def get_market_overview() -> dict[str, Any] | None:
    """Canonical shared market snapshot; one provider call feeds all users."""
    return _cached("overview", OVERVIEW_TTL_SECONDS, _load_overview)


def get_top_movers(limit: int = 3) -> dict[str, list[dict[str, Any]]]:
    overview = get_market_overview() or {}
    assets = [a for a in overview.get("assets") or [] if a.get("change_24h") is not None]
    ranked = sorted(assets, key=lambda a: a["change_24h"], reverse=True)
    return {"gainers": ranked[:limit], "losers": list(reversed(ranked[-limit:])) if ranked else []}


def get_trending() -> list[dict[str, Any]]:
    def _load():
        data = _cg_get("/search/trending")
        coins = (data or {}).get("coins") or []
        observed_at = _now_iso()
        return [{
            "symbol": str((c.get("item") or {}).get("symbol") or "").upper(),
            "name": (c.get("item") or {}).get("name") or "",
            "rank": (c.get("item") or {}).get("market_cap_rank"),
            "provider": "coingecko", "observed_at": observed_at,
        } for c in coins[:5]]
    return _cached("trending", TRENDING_TTL_SECONDS, _load) or []


def get_watchlist_snapshots(symbols: list[str]) -> list[dict[str, Any]]:
    """Shared-by-symbol: reads from the cached overview, no per-user calls."""
    wanted = {s.strip().upper() for s in symbols if s and s.strip()}
    if not wanted:
        return []
    overview = get_market_overview() or {}
    return [a for a in overview.get("assets") or [] if a["symbol"] in wanted]


def is_stale(fact: dict[str, Any] | None, max_age_seconds: int = STALE_MAX_SECONDS) -> bool:
    if not fact:
        return True
    if fact.get("stale"):
        return True
    raw = str(fact.get("observed_at") or fact.get("generated_at") or "")
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - observed).total_seconds() > max_age_seconds
