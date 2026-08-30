"""Canonical CoinGecko client — the single module that speaks to CoinGecko.

Every CoinGecko request in PulseSoc goes through here: auth resolution
(paid vs demo), User-Agent, timeouts, retry, 429 classification, an
optional shared TTL cache with single-flight locking, a plan-aware
rate-limit governor, and telemetry. Consumers (Pulse Briefings,
market_data, the crypto dashboard board in bot.py, intelligence
collectors, UNDX) never build CoinGecko URLs or headers themselves.

Auth contract (per docs.coingecko.com/reference/authentication):
- Paid plans call https://pro-api.coingecko.com/api/v3 with header
  x-cg-pro-api-key. The demo host rejects paid keys (error 10010).
- Demo keys call https://api.coingecko.com/api/v3 with x-cg-demo-api-key.
The header is chosen from the resolved hostname, so a single env override
(COINGECKO_API_BASE) flips both together and no combination can drift.
Default: pro host when COINGECKO_API_KEY is set, public demo host when not.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

PRO_BASE = "https://pro-api.coingecko.com/api/v3"
DEMO_BASE = "https://api.coingecko.com/api/v3"
USER_AGENT = "PulseSoc/1.0 (server; market-data; +https://pulsesoc.com)"

#: Ticker -> CoinGecko coin id, for the assets we must be able to resolve even
#: when the markets board is unreachable. A ticker is NOT an id: of the top 60
#: assets by market cap, 50 differ (BTC/bitcoin, XRP/ripple, AVAX/avalanche-2).
#: Guessing produces a 404 on /coins/{id}/... rather than an error we notice.
#:
#: Deliberately limited to durable majors. Everything else resolves from the
#: live board, which stays correct as listings change; this table only exists so
#: the assets users actually chart keep working while the board is degraded.
#: Never populate it from Coinbase fallback rows -- those carry a lowercased
#: ticker in the id field, which is exactly the collision this prevents.
COIN_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "USDT": "tether", "BNB": "binancecoin",
    "XRP": "ripple", "USDC": "usd-coin", "SOL": "solana", "TRX": "tron",
    "DOGE": "dogecoin", "ADA": "cardano", "LINK": "chainlink", "XLM": "stellar",
    "BCH": "bitcoin-cash", "LTC": "litecoin", "XMR": "monero", "ZEC": "zcash",
    "HBAR": "hedera-hashgraph", "UNI": "uniswap", "AVAX": "avalanche-2",
    "SHIB": "shiba-inu", "CRO": "crypto-com-chain", "DOT": "polkadot",
    "MNT": "mantle", "TAO": "bittensor", "ONDO": "ondo-finance", "ENA": "ethena",
    "WLD": "worldcoin-wld", "PAXG": "pax-gold", "XAUT": "tether-gold",
    "PYUSD": "paypal-usd", "HYPE": "hyperliquid", "LEO": "leo-token",
    "ATOM": "cosmos", "ETC": "ethereum-classic", "FIL": "filecoin",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism", "NEAR": "near",
    "INJ": "injective-protocol", "SUI": "sui", "SEI": "sei-network",
    "AAVE": "aave", "MKR": "maker", "ALGO": "algorand", "VET": "vechain",
}


def coin_id(symbol: str) -> str | None:
    """Authoritative CoinGecko id for a ticker, or None if we don't know one.

    None means "ask the board", not "use the ticker". Callers must not fall back
    to the raw symbol: /coins/btc/market_chart is a 404, and a 404 that arrives
    through a stale-serving cache looks like a slow chart rather than a bug.
    """
    return COIN_IDS.get((symbol or "").strip().upper())

DEFAULT_TIMEOUT = float(os.getenv("COINGECKO_TIMEOUT", "8"))
# The live /key probe identified the plan as Basic: 300 req/min, 100k monthly
# credits. Default keeps 10% headroom for bursts and other consumers of the
# same key; override via env on a plan change. Failed requests still count
# against the provider's per-minute limit, so the governor counts attempts,
# not successes.
RATE_LIMIT_PER_MIN = int(os.getenv("COINGECKO_RATE_LIMIT_PER_MIN", "270"))
STALE_MAX_SECONDS = int(os.getenv("COINGECKO_STALE_MAX", "1800"))

_LAT_WINDOW = 200

_TELEMETRY_LOCK = threading.Lock()
_TELEMETRY = {
    "cg_requests": 0,
    "cg_ok": 0,
    "cg_http_429": 0,
    "cg_http_errors": 0,
    "cg_timeouts": 0,
    "cg_network_errors": 0,
    "cg_governor_blocks": 0,
    "cg_cache_hits": 0,
    "cg_cache_misses": 0,
    "cg_stale_served": 0,
}
_LATENCIES: list[float] = []
_MINUTE_WINDOW: list[float] = []

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_FLIGHT_LOCKS: dict[str, threading.Lock] = {}


def api_key() -> str:
    return os.getenv("COINGECKO_API_KEY", "").strip()


def base_url() -> str:
    override = os.getenv("COINGECKO_API_BASE", "").strip().rstrip("/")
    if override:
        return override
    return PRO_BASE if api_key() else DEMO_BASE


def auth_header_name() -> str:
    """Header follows the resolved hostname so host+header can never split."""
    return "x-cg-pro-api-key" if "pro-api" in base_url() else "x-cg-demo-api-key"


def auth_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    key = api_key()
    if key:
        headers[auth_header_name()] = key
    return headers


def url(path: str) -> str:
    return base_url() + (path if path.startswith("/") else "/" + path)


def _bump(name: str, n: int = 1) -> None:
    with _TELEMETRY_LOCK:
        _TELEMETRY[name] = _TELEMETRY.get(name, 0) + n


def _record_latency(ms: float) -> None:
    with _TELEMETRY_LOCK:
        _LATENCIES.append(ms)
        if len(_LATENCIES) > _LAT_WINDOW:
            del _LATENCIES[: len(_LATENCIES) - _LAT_WINDOW]


def _governor_allows() -> bool:
    """Sliding 60s window over attempted requests. Plan-aware via env."""
    now = time.time()
    with _TELEMETRY_LOCK:
        while _MINUTE_WINDOW and now - _MINUTE_WINDOW[0] > 60:
            _MINUTE_WINDOW.pop(0)
        if len(_MINUTE_WINDOW) >= RATE_LIMIT_PER_MIN:
            _TELEMETRY["cg_governor_blocks"] += 1
            return False
        _MINUTE_WINDOW.append(now)
        return True


def telemetry_snapshot() -> dict[str, Any]:
    with _TELEMETRY_LOCK:
        snap: dict[str, Any] = dict(_TELEMETRY)
        lats = sorted(_LATENCIES)
        snap["cg_requests_last_minute"] = len(_MINUTE_WINDOW)
        snap["cg_rate_limit_per_min"] = RATE_LIMIT_PER_MIN
        snap["cg_latency_ms_p50"] = round(lats[len(lats) // 2], 1) if lats else None
        snap["cg_latency_ms_p95"] = round(lats[max(0, int(len(lats) * 0.95) - 1)], 1) if lats else None
        snap["cg_latency_samples"] = len(lats)
        total = snap["cg_cache_hits"] + snap["cg_cache_misses"]
        snap["cg_cache_hit_rate"] = round(snap["cg_cache_hits"] / total, 3) if total else None
        snap["cg_base"] = base_url()
        snap["cg_auth_header"] = auth_header_name() if api_key() else None
        # Presence only. The key itself must never reach a diagnostic payload.
        snap["cg_api_key_present"] = bool(api_key())
        snap["cg_last_error_code"] = _LAST_ERROR["code"]
        snap["cg_last_error_path"] = _LAST_ERROR["path"]
        return snap


#: Stage 16 error vocabulary. These are safe internal codes: they name the class
#: of fault without carrying URLs, params or headers, so they can be logged,
#: counted and surfaced in diagnostics without ever leaking the API key.
ERR_AUTH = "AUTH"                  # 401/403 -- bad key, or endpoint not on this plan
ERR_RATE_LIMIT = "RATE_LIMIT"      # 429
ERR_TIMEOUT = "TIMEOUT"
ERR_PROVIDER_5XX = "PROVIDER_5XX"
ERR_BAD_RESPONSE = "BAD_RESPONSE"  # 4xx other than auth/429, or unparseable body
ERR_UNKNOWN_ASSET = "UNKNOWN_ASSET"  # 404 on a /coins/{id} path
ERR_NETWORK = "NETWORK"

_LAST_ERROR: dict[str, Any] = {"code": None, "path": None, "at": None}


def _classify_status(status: int, path: str) -> str:
    if status in (401, 403):
        return ERR_AUTH
    if status == 429:
        return ERR_RATE_LIMIT
    if status == 404 and "/coins/" in path:
        return ERR_UNKNOWN_ASSET
    if status >= 500:
        return ERR_PROVIDER_5XX
    return ERR_BAD_RESPONSE


def _record_error(code: str, path: str) -> None:
    """Never logs params or headers -- the key travels in a header, and a
    diagnostic that echoes the request is how keys end up in log aggregators."""
    with _TELEMETRY_LOCK:
        _LAST_ERROR.update({"code": code, "path": path, "at": time.time()})
        bucket = f"cg_err_{code.lower()}"
        _TELEMETRY[bucket] = _TELEMETRY.get(bucket, 0) + 1
    logging.warning("COINGECKO_ERROR code=%s path=%s", code, path)


def last_error() -> dict[str, Any]:
    with _TELEMETRY_LOCK:
        return dict(_LAST_ERROR)


def get_json(path: str, params: dict[str, Any] | None = None, *,
             timeout: float | None = None, retries: int = 1) -> Any | None:
    """One CoinGecko GET. Returns parsed JSON or None (degrade, never raise).

    429 is classified and not retried (a retry inside the same window only
    deepens the throttle). Timeouts and 5xx get one bounded retry. Every failure
    is classified into the ERR_* vocabulary for telemetry; the return value stays
    None so callers keep their existing degrade paths.
    """
    if not _governor_allows():
        _record_error(ERR_RATE_LIMIT, path)
        return None
    timeout = timeout or DEFAULT_TIMEOUT
    attempt = 0
    while True:
        attempt += 1
        _bump("cg_requests")
        t0 = time.perf_counter()
        try:
            resp = requests.get(url(path), params=params or {}, headers=auth_headers(), timeout=timeout)
            _record_latency((time.perf_counter() - t0) * 1000)
            if resp.status_code == 429:
                _bump("cg_http_429")
                _record_error(ERR_RATE_LIMIT, path)
                return None
            if resp.status_code >= 500 and attempt <= retries:
                continue
            if resp.status_code >= 400:
                _bump("cg_http_errors")
                _record_error(_classify_status(resp.status_code, path), path)
                return None
            resp.raise_for_status()
            _bump("cg_ok")
            try:
                return resp.json()
            except ValueError:
                # 200 with a body that isn't JSON -- a proxy or status page,
                # not market data. Distinct from a provider error.
                _bump("cg_http_errors")
                _record_error(ERR_BAD_RESPONSE, path)
                return None
        except requests.Timeout:
            _record_latency((time.perf_counter() - t0) * 1000)
            _bump("cg_timeouts")
            if attempt <= retries:
                continue
            _record_error(ERR_TIMEOUT, path)
            return None
        except requests.HTTPError as exc:
            _bump("cg_http_errors")
            logging.warning("COINGECKO_HTTP_ERROR path=%s error=%s", path, str(exc)[:200])
            _record_error(ERR_BAD_RESPONSE, path)
            return None
        except Exception as exc:  # noqa: BLE001 - provider fault must degrade, not raise
            _bump("cg_network_errors")
            logging.warning("COINGECKO_NETWORK_ERROR path=%s error=%s", path, str(exc)[:200])
            if attempt <= retries:
                continue
            _record_error(ERR_NETWORK, path)
            return None


def _flight_lock(key: str) -> threading.Lock:
    with _CACHE_LOCK:
        lock = _FLIGHT_LOCKS.get(key)
        if lock is None:
            lock = _FLIGHT_LOCKS[key] = threading.Lock()
        return lock


def get_json_cached(path: str, params: dict[str, Any] | None = None, *,
                    ttl: int, cache_key: str | None = None,
                    timeout: float | None = None) -> Any | None:
    """Shared TTL cache with single-flight: N concurrent users -> 1 request.

    On provider failure, serves the last good value if younger than
    STALE_MAX_SECONDS, tagging dict payloads with stale=True so consumers
    can label or omit it — never present stale data as current.
    """
    value, _cached, _ms = get_json_cached_meta(
        path, params, ttl=ttl, cache_key=cache_key, timeout=timeout)
    return value


def get_json_cached_meta(path: str, params: dict[str, Any] | None = None, *,
                         ttl: int, cache_key: str | None = None,
                         timeout: float | None = None) -> tuple[Any | None, bool, int]:
    """``get_json_cached`` plus (value, served_from_cache, duration_ms).

    Callers that report per-source health need to distinguish "the provider
    answered in 600ms" from "the cache answered in 0ms"; without that a healthy
    cache reads as a suspiciously fast provider.
    """
    key = cache_key or f"{path}?{sorted((params or {}).items())!r}"
    started = time.perf_counter()

    def _elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.time() - entry["at"] < ttl:
            _bump("cg_cache_hits")
            return entry["value"], True, _elapsed()
    with _flight_lock(key):
        with _CACHE_LOCK:  # re-check after winning the flight lock
            entry = _CACHE.get(key)
            if entry and time.time() - entry["at"] < ttl:
                _bump("cg_cache_hits")
                return entry["value"], True, _elapsed()
        _bump("cg_cache_misses")
        value = get_json(path, params, timeout=timeout)
        if value is not None:
            with _CACHE_LOCK:
                _CACHE[key] = {"value": value, "at": time.time()}
            return value, False, _elapsed()
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry and time.time() - entry["at"] < STALE_MAX_SECONDS:
                _bump("cg_stale_served")
                stale = entry["value"]
                if isinstance(stale, dict):
                    stale = dict(stale)
                    stale["stale"] = True
                return stale, True, _elapsed()
        return None, False, _elapsed()


#: Fast-price TTL. Short enough to be "live", long enough that a crowd hitting
#: the crypto screen shares one upstream call.
SIMPLE_PRICE_TTL = int(os.getenv("COINGECKO_SIMPLE_PRICE_TTL", "45"))


def simple_price(coin_ids: list[str] | tuple[str, ...], *, vs_currency: str = "usd",
                 ttl: int | None = None, timeout: float | None = None) -> dict[str, Any]:
    """Canonical fast-price path: /simple/price for a batch of CoinGecko ids.

    Ids are deduplicated and sorted so that callers asking for the same basket
    in a different order share one cache entry and one upstream request --
    ["ethereum","bitcoin"] and ["bitcoin","ethereum"] must not be two calls.

    Returns {} rather than None so callers can index the result without a
    None-check; a missing asset is simply an absent key.
    """
    ids = sorted({str(c).strip().lower() for c in (coin_ids or []) if str(c).strip()})
    if not ids:
        return {}
    joined = ",".join(ids)
    data = get_json_cached(
        "/simple/price",
        {"ids": joined, "vs_currencies": vs_currency, "include_24hr_change": "true",
         "include_24hr_vol": "true", "include_market_cap": "true",
         "include_last_updated_at": "true"},
        ttl=SIMPLE_PRICE_TTL if ttl is None else ttl,
        cache_key=f"simple_price:{vs_currency}:{joined}",
        timeout=timeout,
    )
    return data if isinstance(data, dict) else {}


def simple_price_by_symbol(symbols: list[str] | tuple[str, ...], **kwargs) -> dict[str, Any]:
    """``simple_price`` keyed by ticker. Symbols with no known id are dropped
    rather than guessed -- see ``coin_id``."""
    pairs = [(s, coin_id(s)) for s in (symbols or [])]
    data = simple_price([cid for _s, cid in pairs if cid], **kwargs)
    return {s.strip().upper(): data[cid] for s, cid in pairs if cid and cid in data}
