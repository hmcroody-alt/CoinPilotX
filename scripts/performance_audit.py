#!/usr/bin/env python3
"""Performance audit for CoinPilotXAI/Pulse.

Measures representative routes, response sizes, DB query counts exposed by the
request middleware, static payload sizes, polling intervals, and index coverage.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from scripts.site_functional_audit import ensure_smoke_accounts  # noqa: E402


ROUTES = [
    "/dashboard",
    "/pulse",
    "/pulse/reels",
    "/pulse/messages",
    "/pulse/spaces",
    "/pulse/marketplace",
    "/pulse/profile",
    "/pulse/premium",
    "/pulse/premium/undx",
    "/admin/global-command",
    "/admin/performance",
]

STATIC_LIMITS = {
    ".js": 300 * 1024,
    ".css": 160 * 1024,
    ".html": 250 * 1024,
}

EXPECTED_INDEX_HINTS = [
    "message", "conversation", "notification", "pulse_posts", "pulse_reels",
    "marketplace", "teacher", "merchant", "performance_traces", "background_jobs",
]


def emit(status, item, detail):
    print(f"{status}\t{item}\t{detail}")


def static_asset_audit():
    warnings = 0
    for root in [ROOT / "static", ROOT / "templates"]:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            limit = STATIC_LIMITS.get(path.suffix.lower())
            if not limit:
                continue
            size = path.stat().st_size
            rel = path.relative_to(ROOT)
            if size > limit:
                warnings += 1
                emit("WARN", str(rel), f"large payload {size} bytes > budget {limit}")
            else:
                emit("PASS", str(rel), f"{size} bytes")
    return warnings


def polling_audit():
    warnings = 0
    patterns = []
    for path in list((ROOT / "templates").glob("*.html")) + list((ROOT / "static").rglob("*.js")):
        text = path.read_text(errors="ignore")
        for match in re.finditer(r"setInterval\s*\([^,]+,\s*(\d+)", text):
            ms = int(match.group(1))
            rel = path.relative_to(ROOT)
            status = "WARN" if ms < 15000 else "PASS"
            if status == "WARN":
                warnings += 1
            patterns.append((status, str(rel), ms))
    for status, rel, ms in patterns:
        emit(status, rel, f"poll interval {ms}ms")
    if not patterns:
        emit("PASS", "polling", "no setInterval loops found")
    return warnings


def index_audit():
    conn = bot.db()
    cur = conn.cursor()
    names = []
    if bot.db_service.IS_POSTGRES:
        cur.execute("SELECT indexname AS name FROM pg_indexes WHERE schemaname='public'")
        names = [row[0] for row in cur.fetchall()]
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        names = [row[0] for row in cur.fetchall()]
    conn.close()
    joined = " ".join(names)
    missing = [hint for hint in EXPECTED_INDEX_HINTS if hint not in joined]
    if missing:
        emit("WARN", "indexes", f"missing expected hints: {', '.join(missing)}")
        return len(missing)
    emit("PASS", "indexes", f"{len(names)} indexes discovered")
    return 0


def route_audit():
    failures = 0
    warnings = 0
    user_id, admin_id = ensure_smoke_accounts()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        sess["admin_user_id"] = admin_id
    for route in ROUTES:
        start = time.perf_counter()
        res = client.get(route)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        size = len(res.get_data())
        db_queries = int(res.headers.get("X-DB-Query-Count") or 0)
        status = res.status_code
        if status >= 500:
            failures += 1
            emit("FAIL", route, f"HTTP {status} {elapsed_ms}ms db={db_queries} size={size}")
        elif elapsed_ms > 1500 or db_queries >= 100:
            failures += 1
            emit("FAIL", route, f"critical {elapsed_ms}ms db={db_queries} size={size}")
        elif elapsed_ms > 800 or db_queries >= 50 or size > 300_000:
            warnings += 1
            emit("WARN", route, f"{elapsed_ms}ms db={db_queries} size={size} trace={res.headers.get('X-Trace-Id')}")
        else:
            emit("PASS", route, f"{elapsed_ms}ms db={db_queries} size={size}")
    return failures, warnings


def media_path_audit():
    status = bot.media_storage.storage_status()
    if status.get("provider") in {"r2", "s3"} and not status.get("configured"):
        emit("WARN", "media storage", "R2/S3 selected but required env vars are missing")
        return 1
    emit("PASS", "media storage", str(status))
    return 0


def main():
    bot.init_db()
    failures, warnings = route_audit()
    warnings += static_asset_audit()
    warnings += polling_audit()
    warnings += index_audit()
    warnings += media_path_audit()
    print(f"SUMMARY\tfailures={failures}\twarnings={warnings}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
