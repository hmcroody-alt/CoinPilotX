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

# --- Monthly credit budget -------------------------------------------------
# The per-minute governor above guards the WRONG limit. Basic allows 300
# req/min but only 100,000 credits/month, which is a sustained average of
# 2.31 req/min -- 130x tighter. Running flat out at the governor's 270/min
# exhausts the entire month in 6.2 hours without the governor ever objecting.
# This section closes that gap.
#
# Thresholds are cumulative month-to-date spend, not rates:
#   < WARN        normal      full speed
#   >= WARN       warning     observe only; faster budget refresh
#   >= HIGH       high        stretch cache TTLs (fewer upstream refreshes)
#   >= PROTECT    protective  stretch harder AND refuse non-essential refreshes
#
# Protective mode is a brake, never a kill switch. Essential reads still go
# out, cached values still serve, the Coinbase fallback still answers, and
# nothing is ever fabricated. A soft budget threshold must not take the crypto
# product offline.
MONTHLY_CREDIT = int(os.getenv("COINGECKO_MONTHLY_CREDIT", "100000"))
BUDGET_TARGET = int(os.getenv("COINGECKO_BUDGET_TARGET", "80000"))
BUDGET_WARN = int(os.getenv("COINGECKO_BUDGET_WARN", "50000"))
BUDGET_HIGH = int(os.getenv("COINGECKO_BUDGET_HIGH", "75000"))
BUDGET_PROTECT = int(os.getenv("COINGECKO_BUDGET_PROTECT", "90000"))
BUDGET_GUARD_ENABLED = os.getenv("COINGECKO_BUDGET_GUARD", "1").strip().lower() not in (
    "0", "false", "no", "off")
#: Cache TTLs are multiplied by these once spend crosses HIGH / PROTECT. This is
#: the cheapest lever available: it cuts upstream refreshes without touching a
#: single call site and without degrading what users see beyond freshness.
HIGH_TTL_MULTIPLIER = float(os.getenv("COINGECKO_HIGH_TTL_MULTIPLIER", "2"))
PROTECTIVE_TTL_MULTIPLIER = float(os.getenv("COINGECKO_PROTECTIVE_TTL_MULTIPLIER", "4"))

#: /key is the only account-wide view of spend -- per-process counters cannot
#: see the other processes, and every process here has a private cache. It is
#: also not free: measured empirically at ~0.5 credits per call. Polling it
#: every 10 minutes from 4 processes would cost ~17k credits/month, i.e. the
#: guard would become 17% of the budget it exists to protect. So: poll rarely
#: when spend is low, more often as risk rises, and estimate in between from a
#: local attempt counter.
BUDGET_REFRESH_SECONDS = {
    "normal": int(os.getenv("COINGECKO_BUDGET_REFRESH", "1800")),
    "warning": 900,
    "high": 600,
    "protective": 300,
}
#: A failed /key read must not turn into a retry storm against a provider that
#: is already unhappy.
BUDGET_RETRY_SECONDS = 300
KEY_PATH = "/key"

#: Requests we will not refuse even in protective mode. /simple/price is the
#: fast-price path behind alert evaluation and live portfolio valuation -- the
#: reads where a stale number has real consequences. Board and chart refreshes
#: are not on this list: they degrade gracefully to cache or Coinbase.
ESSENTIAL_PATH_PREFIXES = ("/simple/price",)

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
    "cg_budget_throttles": 0,
    "cg_budget_refreshes": 0,
}
_LATENCIES: list[float] = []
_MINUTE_WINDOW: list[float] = []

_BUDGET_LOCK = threading.Lock()
_BUDGET_REFRESH_LOCK = threading.Lock()
#: ``used``/``credit`` are the provider's account-wide truth as of ``at``.
#: ``local_calls`` is what THIS process has attempted since that reading. The
#: other processes are invisible until the next refresh, so the between-refresh
#: estimate is a LOWER bound on fleet spend -- it can arrive at a threshold
#: late, never early. That is precisely why the refresh interval tightens as
#: spend rises: the window in which we can be wrong shrinks as being wrong
#: starts to matter.
_BUDGET: dict[str, Any] = {
    "used": None,
    "credit": None,
    "at": 0.0,
    "last_attempt": 0.0,
    "local_calls": 0,
    "error": None,
}

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


def estimated_month_calls() -> int | None:
    """Best estimate of account-wide month-to-date spend, or None if unknown.

    Provider truth plus this process's attempts since that truth was read.
    None means we have never had a successful /key read, which is a reason to
    log, not a reason to stop serving.
    """
    with _BUDGET_LOCK:
        if _BUDGET["used"] is None:
            return None
        return int(_BUDGET["used"]) + int(_BUDGET["local_calls"])


def budget_state() -> str:
    """normal | warning | high | protective.

    Unknown spend reports ``normal``. Failing open is deliberate: the guard
    exists to stretch caches, and treating a provider hiccup as an emergency
    would degrade the product for a reason that has nothing to do with the
    budget. The floor is modelled at ~16% of plan, so an unknown reading is
    overwhelmingly likely to be a safe one.
    """
    if not BUDGET_GUARD_ENABLED:
        return "normal"
    used = estimated_month_calls()
    if used is None:
        return "normal"
    if used >= BUDGET_PROTECT:
        return "protective"
    if used >= BUDGET_HIGH:
        return "high"
    if used >= BUDGET_WARN:
        return "warning"
    return "normal"


def _fetch_key_usage() -> None:
    """Refresh account-wide spend from /key. Costs ~0.5 credits; call rarely."""
    data = get_json(KEY_PATH, retries=0)
    now = time.time()
    if not isinstance(data, dict):
        with _BUDGET_LOCK:
            _BUDGET["last_attempt"] = now
            _BUDGET["error"] = (last_error() or {}).get("code") or "NO_DATA"
        return
    used = data.get("current_total_monthly_calls")
    credit = data.get("monthly_call_credit")
    if not isinstance(used, (int, float)):
        with _BUDGET_LOCK:
            _BUDGET["last_attempt"] = now
            _BUDGET["error"] = "BAD_RESPONSE"
        return
    with _BUDGET_LOCK:
        _BUDGET["used"] = int(used)
        _BUDGET["credit"] = int(credit) if isinstance(credit, (int, float)) else MONTHLY_CREDIT
        _BUDGET["at"] = now
        _BUDGET["last_attempt"] = now
        _BUDGET["local_calls"] = 0
        _BUDGET["error"] = None
    _bump("cg_budget_refreshes")


def _maybe_refresh_budget() -> None:
    """Refresh spend if the current reading is older than the state's interval.

    Non-blocking: exactly one thread pays the ~300ms, everyone else proceeds on
    the previous reading rather than queueing behind a provider call.
    """
    if not BUDGET_GUARD_ENABLED or not api_key():
        return
    state = budget_state()
    with _BUDGET_LOCK:
        last = _BUDGET["last_attempt"]
        stale_read = _BUDGET["used"] is None
    interval = BUDGET_RETRY_SECONDS if stale_read else BUDGET_REFRESH_SECONDS.get(state, 1800)
    if time.time() - last < interval:
        return
    if not _BUDGET_REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        _fetch_key_usage()
    finally:
        _BUDGET_REFRESH_LOCK.release()


def _is_essential(path: str, essential: bool | None) -> bool:
    if essential is not None:
        return essential
    return any(path.startswith(p) for p in ESSENTIAL_PATH_PREFIXES)


def _budget_allows(path: str, essential: bool | None) -> bool:
    """Refuse only non-essential upstream refreshes, and only in protective mode.

    Refusal returns the caller to its normal degrade path: cached value, marked
    stale if old, Coinbase fallback, or omission. Never a fabricated number.
    """
    if path == KEY_PATH:  # the guard's own probe must not be gated by the guard
        return True
    if not BUDGET_GUARD_ENABLED:
        return True
    _maybe_refresh_budget()
    if budget_state() != "protective" or _is_essential(path, essential):
        return True
    _bump("cg_budget_throttles")
    return False


def effective_ttl(ttl: int) -> int:
    """Stretch cache windows as spend rises. Freshness only -- never staleness:
    STALE_MAX_SECONDS is untouched, so nothing ages past its serve limit."""
    if not BUDGET_GUARD_ENABLED:
        return ttl
    state = budget_state()
    if state == "protective":
        return int(ttl * PROTECTIVE_TTL_MULTIPLIER)
    if state == "high":
        return int(ttl * HIGH_TTL_MULTIPLIER)
    return ttl


def budget_snapshot() -> dict[str, Any]:
    """Operator view of the monthly credit position. Contains no secrets."""
    with _BUDGET_LOCK:
        used = _BUDGET["used"]
        credit = _BUDGET["credit"] or MONTHLY_CREDIT
        at = _BUDGET["at"]
        local = _BUDGET["local_calls"]
        err = _BUDGET["error"]
    estimated = None if used is None else int(used) + int(local)
    with _TELEMETRY_LOCK:
        tele = dict(_TELEMETRY)
    return {
        "state": budget_state(),
        "current_month_calls": estimated,
        "provider_reported_calls": used,
        "local_calls_since_refresh": local,
        "monthly_credit": credit,
        "estimated_remaining": None if estimated is None else max(0, credit - estimated),
        "target": BUDGET_TARGET,
        "thresholds": {"warning": BUDGET_WARN, "high": BUDGET_HIGH,
                       "protective": BUDGET_PROTECT},
        "reading_age_seconds": None if not at else int(time.time() - at),
        "last_refresh_error": err,
        "ttl_multiplier": effective_ttl(100) / 100.0,
        "cache_hits": tele["cg_cache_hits"],
        "provider_requests": tele["cg_requests"],
        "429s": tele["cg_http_429"],
        "budget_throttles": tele["cg_budget_throttles"],
        "guard_enabled": BUDGET_GUARD_ENABLED,
    }


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
    # Outside the telemetry lock: budget_snapshot takes it too, and the whole
    # point of a diagnostics endpoint is that it cannot wedge the process.
    budget = budget_snapshot()
    snap["cg_budget_state"] = budget["state"]
    snap["cg_current_month_calls"] = budget["current_month_calls"]
    snap["cg_estimated_remaining"] = budget["estimated_remaining"]
    snap["cg_monthly_credit"] = budget["monthly_credit"]
    snap["cg_budget_reading_age_seconds"] = budget["reading_age_seconds"]
    snap["cg_budget_ttl_multiplier"] = budget["ttl_multiplier"]
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
             timeout: float | None = None, retries: int = 1,
             essential: bool | None = None) -> Any | None:
    """One CoinGecko GET. Returns parsed JSON or None (degrade, never raise).

    429 is classified and not retried (a retry inside the same window only
    deepens the throttle). Timeouts and 5xx get one bounded retry. Every failure
    is classified into the ERR_* vocabulary for telemetry; the return value stays
    None so callers keep their existing degrade paths.

    ``essential`` overrides the path-prefix default for the monthly credit
    guard. Non-essential requests are refused in protective mode; essential ones
    always go out. Two independent limits are enforced here -- the per-minute
    governor and the monthly budget -- because the plan has two, and the monthly
    one is the tighter of the pair by two orders of magnitude.
    """
    if not _budget_allows(path, essential):
        return None
    if not _governor_allows():
        _record_error(ERR_RATE_LIMIT, path)
        return None
    timeout = timeout or DEFAULT_TIMEOUT
    attempt = 0
    while True:
        attempt += 1
        _bump("cg_requests")
        with _BUDGET_LOCK:
            _BUDGET["local_calls"] += 1
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
                    timeout: float | None = None,
                    essential: bool | None = None) -> Any | None:
    """Shared TTL cache with single-flight: N concurrent users -> 1 request.

    On provider failure, serves the last good value if younger than
    STALE_MAX_SECONDS, tagging dict payloads with stale=True so consumers
    can label or omit it — never present stale data as current.
    """
    value, _cached, _ms = get_json_cached_meta(
        path, params, ttl=ttl, cache_key=cache_key, timeout=timeout,
        essential=essential)
    return value


def get_json_cached_meta(path: str, params: dict[str, Any] | None = None, *,
                         ttl: int, cache_key: str | None = None,
                         timeout: float | None = None,
                         essential: bool | None = None) -> tuple[Any | None, bool, int]:
    """``get_json_cached`` plus (value, served_from_cache, duration_ms).

    Callers that report per-source health need to distinguish "the provider
    answered in 600ms" from "the cache answered in 0ms"; without that a healthy
    cache reads as a suspiciously fast provider.
    """
    key = cache_key or f"{path}?{sorted((params or {}).items())!r}"
    started = time.perf_counter()
    # Budget pressure widens every cache window at once. Applied here rather
    # than at call sites so a single threshold crossing reduces upstream load
    # across the whole product without a deploy.
    ttl = effective_ttl(ttl)

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
        value = get_json(path, params, timeout=timeout, essential=essential)
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
