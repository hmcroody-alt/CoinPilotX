#!/usr/bin/env python3
"""Measure representative PulseSoc routes with an authenticated Flask client."""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from scripts.site_functional_audit import ensure_smoke_accounts  # noqa: E402


ROUTES = (
    ("/dashboard", 500, 30),
    ("/pulse", 800, 80),
    ("/pulse/reels", 800, 80),
    ("/pulse/status", 800, 80),
    ("/pulse/messages", 800, 80),
    ("/pulse/live", 800, 80),
    ("/pulse/notifications", 800, 80),
    ("/pulse/alerts", 800, 80),
    ("/pulse/intelligence", 800, 80),
    ("/pulse/growth", 800, 80),
    ("/pulse/marketplace", 800, 80),
    ("/pulse/music", 800, 80),
    ("/pulse/profile", 800, 80),
    ("/pulse/premium", 1000, 80),
    ("/search", 800, 80),
    ("/admin/global-command", 1000, 80),
    ("/admin/intelligence", 1000, 100),
    ("/admin/calls", 1000, 100),
    ("/admin/emails", 1000, 100),
    ("/admin/performance", 1000, 100),
)


def emit(status: str, route: str, detail: str) -> None:
    print(f"{status}\t{route}\t{detail}")


def main() -> int:
    bot.init_db()
    user_id, admin_id = ensure_smoke_accounts()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["admin_user_id"] = admin_id

    registered = {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}
    failures = 0
    warnings = 0
    for route, latency_budget_ms, query_budget in ROUTES:
        if route not in registered:
            emit("SKIP", route, "route is not registered in this deployment")
            continue
        client.get(route)
        samples: list[float] = []
        max_queries = 0
        response_bytes = 0
        status_code = 0
        for _ in range(3):
            started = time.perf_counter()
            response = client.get(route)
            samples.append((time.perf_counter() - started) * 1000)
            status_code = response.status_code
            response_bytes = max(response_bytes, len(response.get_data()))
            max_queries = max(
                max_queries,
                int(response.headers.get("X-DB-Query-Count") or 0),
            )
        median_ms = statistics.median(samples)
        maximum_ms = max(samples)
        detail = (
            f"HTTP {status_code} median={median_ms:.1f}ms max={maximum_ms:.1f}ms "
            f"db={max_queries} bytes={response_bytes}"
        )
        if status_code >= 500 or median_ms > latency_budget_ms * 2 or max_queries > query_budget * 2:
            failures += 1
            emit("FAIL", route, detail)
        elif (
            median_ms > latency_budget_ms
            or max_queries > query_budget
            or response_bytes > 350_000
        ):
            warnings += 1
            emit("WARN", route, detail)
        else:
            emit("PASS", route, detail)

    print(f"SUMMARY\tfailures={failures}\twarnings={warnings}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
