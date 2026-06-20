#!/usr/bin/env python3
"""PulseSoc API latency and payload budget audit."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from scripts.site_functional_audit import ensure_smoke_accounts  # noqa: E402


ENDPOINTS = [
    ("/api/pulse/feed?limit=8", 1200, 220_000, 55),
    ("/api/pulse/feed?tab=trending&limit=8", 1400, 220_000, 65),
    ("/api/pulse/videos?limit=8", 1200, 180_000, 60),
    ("/api/pulse/notifications/unread-count", 500, 20_000, 20),
    ("/api/pulse/notifications?limit=8", 900, 120_000, 35),
    ("/api/pulse/communications/conversations?limit=12", 1200, 180_000, 160),
    ("/api/pulse/profile/me", 900, 120_000, 35),
]


def main() -> int:
    bot.init_db()
    user_id, _admin_id = ensure_smoke_accounts()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    failures: list[str] = []
    for path, ms_budget, size_budget, query_budget in ENDPOINTS:
        started = time.perf_counter()
        response = client.get(path)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        size = len(response.get_data())
        queries = int(response.headers.get("X-DB-Query-Count") or 0)
        ok = response.status_code < 500 and elapsed_ms <= ms_budget and size <= size_budget and queries <= query_budget
        status = "PASS" if ok else "FAIL"
        print(f"{status}\t{path}\t{elapsed_ms}ms size={size} db={queries} budget={ms_budget}ms/{size_budget}B/{query_budget}q")
        if not ok:
            failures.append(path)
    if failures:
        raise SystemExit("api latency audit failed:\n- " + "\n- ".join(failures))
    print("api latency audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
