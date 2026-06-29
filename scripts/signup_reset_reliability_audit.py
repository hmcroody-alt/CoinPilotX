#!/usr/bin/env python3
"""Exercise signup/reset reliability without contacting production providers."""

from __future__ import annotations

import os
import logging
import secrets
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="pulsesoc-auth-reliability-")
DB_PATH = Path(TEMP_DIR.name) / "audit.db"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"
os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"
os.environ["BREVO_EMAIL_ENABLED"] = "0"
os.environ["FLASK_SECRET_KEY"] = "signup-reset-reliability-audit-only"
os.environ["SQLITE_BUSY_TIMEOUT_MS"] = "10000"
logging.basicConfig(level=logging.INFO)

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def scalar(sql, params=()):
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def seed_legacy_subscription_schema():
    """Reproduce the existing-db shape that broke signup before migration."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def table_columns(table):
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {str(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()


def register(email):
    client = bot.webhook_app.test_client()
    started = time.monotonic()
    response = client.post(
        "/api/mobile/auth/register",
        json={
            "full_name": "Reliability Audit",
            "username": f"reliability_{secrets.token_hex(5)}",
            "email": email,
            "password": "ReliabilityAudit!123",
            "age_confirmed": True,
        },
    )
    return response, time.monotonic() - started


def recover(email):
    client = bot.webhook_app.test_client()
    started = time.monotonic()
    response = client.post("/api/mobile/auth/recover", json={"email": email})
    return response, time.monotonic() - started


def main():
    seed_legacy_subscription_schema()
    bot.init_db()
    bot.dispatch_brevo_contact_sync_safe = lambda *_args, **_kwargs: {"ok": True, "status": "queued_for_audit"}
    subscription_columns = table_columns("subscriptions")
    for column in {"plan", "payment_type", "trial_start_date", "trial_end_date", "pro_expires_at"}:
        require(column in subscription_columns, f"legacy subscriptions table is migrated with {column}")

    # Any direct provider call from a request is a test failure.
    def forbidden_provider_call(*_args, **_kwargs):
        raise AssertionError("email provider was called inside the request path")

    bot.email_service_service.send_email = forbidden_provider_call
    emails = [f"signup-reset-{secrets.token_hex(6)}-{index}@example.com" for index in range(3)]
    for email in emails:
        response, elapsed = register(email)
        require(response.status_code == 200, f"signup returns 200 for {email}: status={response.status_code} body={response.get_data(as_text=True)[:300]}")
        require(elapsed < 5, f"signup returns without provider blocking ({elapsed:.3f}s)")
        payload = response.get_json() or {}
        require(payload.get("ok") is True, "signup completes even while providers are disabled")

        response, elapsed = recover(email)
        require(response.status_code == 200, "password reset request returns generic 200")
        require(elapsed < 5, f"password reset returns without provider blocking ({elapsed:.3f}s)")
        require("If an account exists" in ((response.get_json() or {}).get("message") or ""), "password reset does not reveal account existence")

        health = bot.webhook_app.test_client().get("/health")
        require(health.status_code == 200, "app remains responsive after signup and reset")

    require(scalar("SELECT COUNT(*) FROM users WHERE email LIKE 'signup-reset-%@example.com'") == len(emails), "all repeated signups commit")
    require(scalar("SELECT COUNT(*) FROM password_reset_tokens") >= len(emails), "reset tokens commit before email delivery")
    require(scalar("SELECT COUNT(*) FROM failed_email_queue WHERE email_type='welcome'") >= len(emails), "welcome emails are queued")
    require(scalar("SELECT COUNT(*) FROM failed_email_queue WHERE email_type='email_verification'") >= len(emails), "verification emails are queued")
    require(scalar("SELECT COUNT(*) FROM failed_email_queue WHERE email_type='password_reset'") >= len(emails), "password reset emails are queued")

    # Exercise notification + transactional push enqueue on the caller's cursor.
    user_id = int(scalar("SELECT user_id FROM users WHERE email=?", (emails[0],)))
    conn = db_service.connect()
    cur = conn.cursor()
    bot.notify_user(cur, user_id, "welcome", "Welcome", "Welcome to PulseSoc.", "/pulse", entity_type="account", entity_id=str(user_id))
    conn.commit()
    conn.close()
    require(scalar("SELECT COUNT(*) FROM pulse_notifications WHERE user_id=? AND type='welcome'", (user_id,)) == 1, "welcome notification commits safely")
    require(scalar("SELECT COUNT(*) FROM push_delivery_jobs WHERE user_id=?", (user_id,)) >= 1, "push delivery is queued without provider contact")

    before_jobs = int(scalar("SELECT COUNT(*) FROM failed_email_queue"))

    def failed_provider(*_args, **_kwargs):
        return {"ok": False, "status_code": 503, "error": "audit provider unavailable", "response": {}}

    first_batch = bot.process_email_delivery_jobs(limit=1, provider_send=failed_provider)
    require(first_batch.get("retry") == 1, "failed email moves to bounded backoff")
    require(int(scalar("SELECT COUNT(*) FROM failed_email_queue")) == before_jobs, "failed processing does not recursively create jobs")

    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("UPDATE failed_email_queue SET status='retry_ready', next_retry_at=?, max_attempts=1 WHERE id=(SELECT id FROM failed_email_queue WHERE retry_count=0 ORDER BY id LIMIT 1)", (bot.datetime.now().isoformat(),))
    conn.commit()
    conn.close()
    final_batch = bot.process_email_delivery_jobs(limit=1, provider_send=failed_provider)
    require(final_batch.get("dead_letter") == 1, "email retries stop at max_attempts")

    # Concurrent reset requests catch SQLite writer contention regressions.
    def concurrent_recover(_index):
        response, elapsed = recover(emails[0])
        return response.status_code, elapsed

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(concurrent_recover, range(12)))
    require(all(status == 200 for status, _elapsed in results), "concurrent reset requests produce no 500")
    require(max(elapsed for _status, elapsed in results) < 10, "SQLite busy timeout/WAL prevents lock stalls")
    require(scalar("PRAGMA journal_mode") == "wal", "SQLite audit database uses WAL mode")

    health = bot.webhook_app.test_client().get("/health")
    require(health.status_code == 200, "app remains responsive after repeated concurrent flows")
    print("signup/reset reliability audit ok")


if __name__ == "__main__":
    main()
