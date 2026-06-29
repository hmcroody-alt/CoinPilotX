#!/usr/bin/env python3
"""Audit PulseSoc notification push enqueue health for login-style writes."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")
os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


REPORT = ROOT / "reports" / "push_notification_audit.json"
USER_ID = 9_950_781


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "PUSH_TRACE" in message or "PULSE_LEGACY_PUSH_DELIVERY_FAILED" in message:
            self.records.append(message)


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.details: dict = {}

    def check(self, condition: bool, name: str, details: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(condition), "details": details})
        print(("PASS " if condition else "FAIL ") + name + (f": {details}" if details else ""))
        if not condition:
            raise AssertionError(f"{name}: {details}")

    def write(self) -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps({"ok": all(item["ok"] for item in self.checks), "checks": self.checks, "details": self.details}, indent=2),
            encoding="utf-8",
        )


def setup_user() -> None:
    bot.init_db()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, account_status, email_verified)
        VALUES (?, 'push_enqueue_audit', 'Push Enqueue Audit', 'push-enqueue-audit@example.test', ?, 1, 'active', 1)
        """,
        (USER_ID, now),
    )
    cur.execute("UPDATE users SET account_status='active', email_verified=1, onboarding_complete=1 WHERE user_id=?", (USER_ID,))
    for table in ("push_delivery_jobs", "pulse_notification_deliveries", "pulse_notifications"):
        try:
            cur.execute(f"DELETE FROM {table} WHERE user_id=?", (USER_ID,))
        except Exception:
            pass
    conn.commit()
    conn.close()


def main() -> int:
    audit = Audit()
    setup_user()
    capture = Capture()
    logging.getLogger().addHandler(capture)
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        cur.execute("UPDATE users SET last_login_at=?, last_seen_at=? WHERE user_id=?", (now, now, USER_ID))
        bot.notify_user(
            cur,
            USER_ID,
            "account_login",
            "New login to PulseSoc",
            "Your PulseSoc account was accessed successfully.",
            "/pulse/settings/notifications",
            actor_user_id=USER_ID,
            entity_type="account",
            entity_id=str(USER_ID),
            metadata={"audit": "push_notification_audit"},
        )
        bot.notify_user(
            cur,
            USER_ID,
            "new_device",
            "Device activity detected",
            "PulseSoc recorded a browser/device login for your account.",
            "/pulse/settings/notifications",
            actor_user_id=USER_ID,
            entity_type="account",
            entity_id=str(USER_ID),
            metadata={"audit": "push_notification_audit", "event": "device_login"},
        )
        cur.execute("SELECT COUNT(*) AS total FROM push_delivery_jobs WHERE user_id=?", (USER_ID,))
        jobs_before_commit = int((cur.fetchone() or {"total": 0})["total"])
        conn.commit()
    finally:
        conn.close()
        logging.getLogger().removeHandler(capture)

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    notifications = int((cur.execute("SELECT COUNT(*) AS total FROM pulse_notifications WHERE user_id=?", (USER_ID,)).fetchone() or {"total": 0})["total"])
    deliveries = [dict(row) for row in cur.execute("SELECT channel, provider, status, error_message FROM pulse_notification_deliveries WHERE user_id=? ORDER BY id", (USER_ID,)).fetchall()]
    jobs = [dict(row) for row in cur.execute("SELECT push_type, status, attempts, max_attempts, trace_id FROM push_delivery_jobs WHERE user_id=? ORDER BY id", (USER_ID,)).fetchall()]
    conn.close()

    failed_logs = [line for line in capture.records if "push_job_enqueue_failed" in line]
    audit.details = {
        "root_cause_before_fix": "Legacy notify_user wrote notification rows on one SQLite transaction, then opened a second writer through send_push_alert/enqueue_push before the first transaction committed. That can raise sqlite OperationalError: database is locked.",
        "fix": "Legacy notify_user now queues async push jobs with push_service.enqueue_push_with_cursor on the caller cursor, so notification rows and push jobs commit together.",
        "jobs_before_commit": jobs_before_commit,
        "deliveries": deliveries,
        "jobs": jobs,
        "push_trace_records": capture.records,
    }
    audit.check(notifications == 2, "Login-style notifications are stored in-app", str(notifications))
    audit.check(jobs_before_commit == 2, "Push jobs queue inside the caller transaction before commit", str(jobs_before_commit))
    audit.check(len(jobs) == 2 and all(job.get("status") == "pending" for job in jobs), "Push jobs remain durable pending work", str(jobs))
    audit.check(any(row.get("channel") == "in_app" for row in deliveries), "In-app delivery rows are written", str(deliveries))
    audit.check(any(row.get("channel") == "push" and row.get("status") == "queued" for row in deliveries), "Push delivery rows record queued state", str(deliveries))
    audit.check(not failed_logs, "No push_job_enqueue_failed trace is emitted", str(failed_logs))
    audit.write()
    print(f"report={REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
