#!/usr/bin/env python3
"""Inspect PulseSoc queue depth and bounded-retry worker readiness."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


QUEUE_TABLES = (
    "push_delivery_jobs",
    "notification_delivery_jobs",
    "intelligence_delivery_jobs",
    "alert_delivery_jobs",
    "background_jobs",
    "failed_email_queue",
    "sms_delivery_logs",
)


def emit(status: str, item: str, detail: str) -> None:
    print(f"{status}\t{item}\t{detail}")


def columns_for(cur, table: str) -> set[str]:
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}
    return {str(row[1]) for row in cur.execute(f"PRAGMA table_info('{table}')").fetchall()}


def main() -> int:
    bot.init_db()
    failures = 0
    warnings = 0
    conn = db_service.connect()
    cur = conn.cursor()
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {str(row[0]) for row in cur.fetchall()}

    for table in QUEUE_TABLES:
        if table not in tables:
            emit("SKIP", table, "queue table not present")
            continue
        columns = columns_for(cur, table)
        if "status" not in columns:
            emit("PASS", table, "log table present; no status queue column")
            continue
        cur.execute(
            f"SELECT COALESCE(status,'unknown'), COUNT(*) FROM {table} "
            "GROUP BY COALESCE(status,'unknown')"
        )
        statuses = {str(row[0]): int(row[1]) for row in cur.fetchall()}
        pending = sum(
            count
            for status, count in statuses.items()
            if status.lower() in {
                "pending",
                "queued",
                "ready",
                "processing",
                "retry",
                "retry_ready",
                "scheduled",
            }
        )
        dead = sum(
            count
            for status, count in statuses.items()
            if status.lower() in {"failed", "dead_letter", "error"}
        )
        detail = f"pending={pending} failed_or_dead={dead} statuses={statuses}"
        if pending > 10_000 or dead > 1_000:
            failures += 1
            emit("FAIL", table, detail)
        elif pending > 1_000 or dead > 100:
            warnings += 1
            emit("WARN", table, detail)
        else:
            emit("PASS", table, detail)
    conn.close()

    source_checks = {
        "durable push retry/dead-letter": (
            ROOT / "services/push_service.py",
            ("max_attempts", "next_retry_at", "dead_letter", "idempotency_key"),
        ),
        "worker heartbeat visibility": (
            ROOT / "services/command_center_worker/heartbeat.py",
            ("PUSH_JOBS", "EMAIL_JOBS", "dead_letter"),
        ),
        "alert worker heartbeat": (
            ROOT / "services/alert_engine.py",
            ("record_worker_heartbeat", "evaluate_all_active_alerts"),
        ),
        "intelligence delivery queue": (
            ROOT / "services/pulsesoc_intelligence_engine.py",
            ("intelligence_delivery_jobs", "delivery_status"),
        ),
    }
    for label, (path, markers) in source_checks.items():
        text = path.read_text(errors="ignore") if path.exists() else ""
        missing = [marker for marker in markers if marker not in text]
        if missing:
            failures += 1
            emit("FAIL", label, f"missing: {', '.join(missing)}")
        else:
            emit("PASS", label, "bounded queue controls present")

    print(f"SUMMARY\tfailures={failures}\twarnings={warnings}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
