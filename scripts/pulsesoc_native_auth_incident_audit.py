#!/usr/bin/env python3
"""Regression checks for the native existing-account login incident."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

database_file = tempfile.NamedTemporaryFile(prefix="pulsesoc-native-auth-incident-", suffix=".db", delete=False)
database_file.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{database_file.name}"
os.environ["SECRET_KEY"] = "native-auth-incident-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "native-auth-incident-audit-secret"
os.environ["SESSION_SECRET"] = "native-auth-incident-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402


USER_ID = 703
EMAIL = "existing-native-auth-audit@example.test"
USERNAME = "existing_native_auth_audit"
PASSWORD = "ExistingAccountAudit!703"


def seed_existing_account() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-13T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
            (user_id, username, display_name, full_name, email, password_hash,
             email_verified, account_status, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?)
        """,
        (USER_ID, USERNAME, "Existing Auth Audit", "Existing Auth Audit", EMAIL, generate_password_hash(PASSWORD), now, now, now),
    )
    conn.commit()
    conn.close()


def user_count() -> int:
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    row = cur.fetchone()
    conn.close()
    return int(row[0] or 0)


def post_login(identifier: str, password: str):
    client = bot.webhook_app.test_client()
    started = time.monotonic()
    response = client.post(
        "/api/mobile/auth/login",
        json={"identifier": identifier, "password": password},
        headers={"User-Agent": "PulseSocNativeApp/auth-incident-audit"},
    )
    return client, response, time.monotonic() - started


def run() -> None:
    seed_existing_account()
    before = user_count()

    _, rejected, rejected_seconds = post_login(EMAIL, "incorrect-password")
    rejected_payload = rejected.get_json() or {}
    assert rejected.status_code == 401, rejected_payload
    assert rejected_seconds < 3, f"invalid login stalled for {rejected_seconds:.3f}s"
    assert "incorrect" in str(rejected_payload.get("message") or "").lower(), rejected_payload

    client, accepted, accepted_seconds = post_login(USERNAME, PASSWORD)
    accepted_payload = accepted.get_json() or {}
    assert accepted.status_code == 200, accepted_payload
    assert accepted_seconds < 3, f"valid login stalled for {accepted_seconds:.3f}s"
    assert accepted_payload.get("authenticated") is True, accepted_payload
    assert int((accepted_payload.get("user") or {}).get("user_id") or 0) == USER_ID, accepted_payload
    assert str(accepted_payload.get("refresh_token") or "").startswith("psr_"), "refresh credential missing"

    original_refresh = str(accepted_payload.get("refresh_token") or "")
    refreshed = client.post(
        "/api/mobile/auth/refresh",
        json={"refresh_token": original_refresh, "source": "native-auth-incident-audit"},
        headers={"User-Agent": "PulseSocNativeApp/auth-incident-audit"},
    )
    refreshed_payload = refreshed.get_json() or {}
    assert refreshed.status_code == 200, refreshed_payload
    assert int((refreshed_payload.get("user") or {}).get("user_id") or 0) == USER_ID, refreshed_payload
    assert str(refreshed_payload.get("refresh_token") or "").startswith("psr_"), refreshed_payload
    assert str(refreshed_payload.get("refresh_token") or "") != original_refresh, "refresh credential did not rotate"

    restored = client.get("/api/mobile/auth/session")
    restored_payload = restored.get_json() or {}
    assert restored.status_code == 200, restored_payload
    assert int((restored_payload.get("user") or {}).get("user_id") or 0) == USER_ID, restored_payload
    assert user_count() == before, "native login created a duplicate user"

    print(
        "PASS: native auth rejects promptly, preserves canonical user_id, "
        "rotates refresh credentials, restores the canonical session, and creates no duplicate user"
    )


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            os.unlink(database_file.name)
        except FileNotFoundError:
            pass
