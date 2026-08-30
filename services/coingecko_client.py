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
        return snap


def get_json(path: str, params: dict[str, Any] | None = None, *,
             timeout: float | None = None, retries: int = 1) -> Any | None:
    """One CoinGecko GET. Returns parsed JSON or None (degrade, never raise).

    429 is classified and not retried (a retry inside the same window only
    deepens the throttle). Timeouts and 5xx get one bounded retry.
    """
    if not _governor_allows():
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
                return None
            if resp.status_code >= 500 and attempt <= retries:
                continue
            resp.raise_for_status()
            _bump("cg_ok")
            return resp.json()
        except requests.Timeout:
            _record_latency((time.perf_counter() - t0) * 1000)
            _bump("cg_timeouts")
            if attempt <= retries:
                continue
            return None
        except requests.HTTPError as exc:
            _bump("cg_http_errors")
            logging.warning("COINGECKO_HTTP_ERROR path=%s error=%s", path, str(exc)[:200])
            return None
        except Exception as exc:  # noqa: BLE001 - provider fault must degrade, not raise
            _bump("cg_network_errors")
            logging.warning("COINGECKO_NETWORK_ERROR path=%s error=%s", path, str(exc)[:200])
            if attempt <= retries:
                continue
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
    key = cache_key or f"{path}?{sorted((params or {}).items())!r}"
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.time() - entry["at"] < ttl:
            _bump("cg_cache_hits")
            return entry["value"]
    with _flight_lock(key):
        with _CACHE_LOCK:  # re-check after winning the flight lock
            entry = _CACHE.get(key)
            if entry and time.time() - entry["at"] < ttl:
                _bump("cg_cache_hits")
                return entry["value"]
        _bump("cg_cache_misses")
        value = get_json(path, params, timeout=timeout)
        if value is not None:
            with _CACHE_LOCK:
                _CACHE[key] = {"value": value, "at": time.time()}
            return value
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry and time.time() - entry["at"] < STALE_MAX_SECONDS:
                _bump("cg_stale_served")
                stale = entry["value"]
                if isinstance(stale, dict):
                    stale = dict(stale)
                    stale["stale"] = True
                return stale
        return None
