#!/usr/bin/env python3
"""Benchmark the crypto market provider from a production-like network.

The build sandbox cannot reach api.coingecko.com, so the Stage 1 latency
baseline must be captured where the workers actually run (Railway):

    python3 scripts/briefings/benchmark_crypto_provider.py

Reports p50/p95 latency, error/429 rates, and field depth per endpoint.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import coingecko_client  # noqa: E402

# Host and auth come from the canonical client. This script used to default to
# the public host and unconditionally send x-cg-demo-api-key, so benchmarking
# with the real paid key measured a 401 against the wrong host -- the exact
# pro/demo split the canonical client exists to make impossible. A benchmark
# that does not exercise the production configuration is measuring fiction.
BASE = coingecko_client.base_url()
KEY = coingecko_client.api_key()
N = int(os.getenv("BENCH_SAMPLES", "10"))

ENDPOINTS = {
    "simple_price_btc_eth": ("/simple/price", {"ids": "bitcoin,ethereum", "vs_currencies": "usd", "include_24hr_change": "true"}),
    "markets_top50_1h24h7d": ("/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 50, "page": 1, "price_change_percentage": "1h,24h,7d"}),
    "global": ("/global", {}),
    "trending": ("/search/trending", {}),
}


def bench(name: str, path: str, params: dict) -> dict:
    # Header name is derived from the resolved host, never hardcoded.
    headers = coingecko_client.auth_headers()
    lat, errors, throttled = [], 0, 0
    sample = None
    for _ in range(N):
        start = time.time()
        try:
            resp = requests.get(f"{BASE}{path}", params=params, headers=headers, timeout=10)
            if resp.status_code == 429:
                throttled += 1
                continue
            resp.raise_for_status()
            sample = resp.json()
            lat.append((time.time() - start) * 1000)
        except Exception:  # noqa: BLE001
            errors += 1
        time.sleep(0.5)
    lat.sort()
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None
    return {
        "endpoint": name, "samples_ok": len(lat), "errors": errors, "throttled_429": throttled,
        "p50_ms": round(statistics.median(lat)) if lat else None,
        "p95_ms": round(p95) if p95 else None,
        "min_ms": round(lat[0]) if lat else None, "max_ms": round(lat[-1]) if lat else None,
        "fields_seen": sorted((sample[0] if isinstance(sample, list) and sample else sample or {}).keys())[:20]
        if isinstance(sample, (list, dict)) else [],
    }


def main() -> int:
    results = [bench(name, path, params) for name, (path, params) in ENDPOINTS.items()]
    print(json.dumps({
        "provider": "coingecko", "base": BASE, "keyed": bool(KEY),
        "auth_header": coingecko_client.auth_header_name() if KEY else None,
        "results": results,
    }, indent=2))
    return 0 if any(r["samples_ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
