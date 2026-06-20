#!/usr/bin/env python3
"""Guard PulseSoc hot-path requests against intermittent request stalls."""

from __future__ import annotations

import re
import sys
import time
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def source_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def ensure_user():
    conn = bot.db()
    cur = conn.cursor()
    user_id = 940129
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                user_id,
                "pulse_hot_path_audit",
                "Pulse Hot Path Audit",
                "pulse-hot-path-audit@example.test",
                bot.datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    conn.commit()
    conn.close()
    return user_id


def timed_get(client, path):
    start = time.perf_counter()
    response = client.get(path)
    return response, (time.perf_counter() - start) * 1000


def main():
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    db_source = (ROOT / "services/db.py").read_text(encoding="utf-8")

    capture_block = source_between(bot_source, "def capture_referral_and_run_trial_maintenance", "def basic_abuse_guard")
    expect("run_trial_maintenance_async()" in capture_block, "trial maintenance is dispatched asynchronously")
    expect(not re.search(r"(?<!async\.)\brun_trial_maintenance\(\)", capture_block), "request hook does not run trial maintenance inline")

    visitor_block = source_between(bot_source, "def log_visitor_request", "def record_pulsesoc_presence_activity")
    expect('"/api/"' in visitor_block, "visitor request logger skips high-frequency API calls")

    presence_block = source_between(bot_source, "def record_pulsesoc_presence_activity", "def enforce_admin_first_password_change")
    expect("dispatch_presence_activity_async" in presence_block, "presence hook dispatches asynchronously")
    expect("enqueue_presence_event" not in presence_block, "presence hook does not wait on worker network calls")
    expect("record_local_presence_activity" not in presence_block, "presence hook does not write presence inline")

    perf_block = source_between(bot_source, "def add_pwa_headers", "def enforce_https")
    expect("PERFORMANCE_LOG_ALL" in perf_block, "normal request performance logging is gated")
    expect('if level != "info" or PERFORMANCE_LOG_ALL' in perf_block, "info-level PERF_REQUEST logs are opt-in")

    init_block = source_between(bot_source, "def init_db", "def _init_db_impl")
    expect("INIT_DB_REQUEST_SKIPPED_ONCE" in init_block, "init-db request skip logs only once")
    expect('logging.warning(\n            "INIT_DB_REQUEST_SKIPPED' not in init_block, "init-db request skip warning flood removed")

    expect("timeout=_sqlite_timeout_seconds()" in db_source, "SQLite connections use busy timeout")
    expect("PRAGMA busy_timeout" in db_source, "SQLite busy timeout pragma is applied")
    expect("PRAGMA journal_mode=WAL" in db_source, "SQLite WAL is enabled for local concurrency")
    expect("PRAGMA synchronous=NORMAL" in db_source, "SQLite synchronous mode avoids local write stalls")

    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = ensure_user()

    paths = [
        "/pulse/settings/notifications",
        "/pulse/messages",
        "/pulse",
        "/api/push/status",
        "/api/pulse/badge-counts",
    ]
    for path in paths:
        samples = []
        for _ in range(3):
            response, elapsed_ms = timed_get(client, path)
            samples.append(elapsed_ms)
            expect(response.status_code < 500, f"{path} avoids server errors", response.get_data(as_text=True)[:300])
            expect("X-Response-Time-Ms" in response.headers, f"{path} exposes response timing")
        avg_ms = sum(samples) / len(samples)
        expect(max(samples) < 1200, f"{path} max hot-path latency stays bounded ({max(samples):.0f}ms)")
        expect(avg_ms < 450, f"{path} average hot-path latency stays bounded ({avg_ms:.0f}ms)")

    sqlite_connect = source_between(db_source, "def connect()", "@contextmanager")
    expect(not re.search(r"sqlite3\.connect\([^)]*timeout\s*=\s*0", sqlite_connect), "SQLite timeout is not disabled")
    print("pulse hot path performance audit ok")


if __name__ == "__main__":
    main()
