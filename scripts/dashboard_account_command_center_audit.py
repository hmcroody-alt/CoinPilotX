#!/usr/bin/env python3
"""Audit Dashboard Account Command Center wiring and security shape."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-account-command-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "account-command-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "account-command-audit-secret"
os.environ["SESSION_SECRET"] = "account-command-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import dashboard_account_command_center  # noqa: E402


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def ensure_user(cur, user_id: int, email: str, name: str, *, premium: bool = False) -> None:
    now = "2026-06-26T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?, ?, ?, ?, ?, 1, 1, ?, 1, 'public')
        """,
        (user_id, name.lower().replace(" ", "_"), name, email, now, 1 if premium else 0),
    )
    try:
        cur.execute(
            "UPDATE users SET plan=?, subscription_status=?, avatar_url=?, updated_at=? WHERE user_id=?",
            ("premium" if premium else "free", "active" if premium else "inactive", "/static/avatar.png", now, user_id),
        )
    except Exception:
        pass


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 201, "free-account@example.test", "Free Account", premium=False)
    ensure_user(cur, 202, "premium-account@example.test", "Premium Account", premium=True)
    ensure_user(cur, 203, "admin-account@example.test", "Admin Account", premium=True)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            email TEXT,
            name TEXT,
            role TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(admin_users)")
    admin_columns = {row[1] for row in cur.fetchall()}
    admin_values = {
        "id": 1,
        "user_id": 203,
        "email": "admin-account@example.test",
        "name": "Admin Account",
        "full_name": "Admin Account",
        "display_name": "Admin Account",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-password-hash-not-used",
        "must_change_password": 0,
        "password_changed_at": "2026-06-26T00:00:00",
        "created_at": "2026-06-26T00:00:00",
        "updated_at": "2026-06-26T00:00:00",
    }
    insert_columns = [column for column in admin_values if column in admin_columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(admin_values[column] for column in insert_columns),
    )
    dashboard_account_command_center.ensure_schema(conn)
    conn.commit()
    conn.close()


def client_for(user_id: int, *, admin_user_id: int | None = None):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        if admin_user_id is not None:
            sess["admin_user_id"] = admin_user_id
    return client


def run() -> None:
    setup_data()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        dashboard_account_command_center.ensure_schema(conn)
        cur = conn.cursor()
        for table in (
            "profile_audit_logs",
            "verification_documents",
            "account_health_events",
            "account_strikes",
            "account_warnings",
            "account_restrictions",
            "security_login_events",
            "security_devices",
            "active_sessions",
            "user_settings",
            "account_audit_logs",
        ):
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            assert_true(bool(cur.fetchone()), f"{table} table exists")

        cur.execute("SELECT * FROM users WHERE user_id=201")
        state = dashboard_account_command_center.build_account_state(conn, dict(cur.fetchone()))
        assert_true(state["profile"]["state"] == "ON", "profile state is backend derived")
        assert_true(state["verification"]["state"] == "ACTION", "verification starts as action")
        assert_true("settings" in state and state["settings"]["status"] == "server_managed", "settings are server managed")

        result = dashboard_account_command_center.submit_verification_request(conn, 201, "identity", {"public_note": "please review"})
        assert_true(result["status"] == "submitted", "verification request submitted")
        review = dashboard_account_command_center.admin_decide_verification(conn, result["request_id"], 203, "approved", "Approved in audit")
        assert_true(review["status"] == "approved", "admin decision works")

        settings = dashboard_account_command_center.update_settings(conn, 201, {"profile_visibility": "private", "ads_personalization": "false"}, 201)
        assert_true(settings["settings"]["profile_visibility"] == "private", "settings save")
        try:
            dashboard_account_command_center.update_settings(conn, 201, {"profile_visibility": "everybody"}, 201)
            fail("invalid setting should fail")
        except ValueError:
            pass
    finally:
        conn.close()

    free_client = client_for(201)
    page = free_client.get("/dashboard")
    assert_true(page.status_code == 200, "dashboard loads for account user")
    html = page.get_data(as_text=True)
    assert_true("/dashboard/account/profile" in html, "dashboard links account profile route")
    assert_true("LOCK" in html, "locked premium account items are visible to free users")

    state_response = free_client.get("/api/dashboard/account/state")
    assert_true(state_response.status_code == 200, "account state API loads")
    payload = state_response.get_json() or {}
    serialized = json.dumps(payload).lower()
    for secret_word in ("private_key", "database_url", "storage_path", "token", "password_hash", "internal_note"):
        assert_true(secret_word not in serialized, f"state payload does not expose {secret_word}")

    bad_username = free_client.post("/api/pulse/profile/update", json={"display_name": "Free Account", "username": "admin", "bio": ""})
    assert_true(bad_username.status_code == 400, "reserved username rejected")
    good_profile = free_client.post("/api/pulse/profile/update", json={"display_name": "Free Account", "username": "free_account_new", "bio": "Hello PulseSoc", "profile_visibility": "public"})
    assert_true(good_profile.status_code == 200, "profile update works")

    non_admin = free_client.get("/admin/account-command")
    assert_true(non_admin.status_code in {302, 401, 403}, "non-admin cannot access account admin")

    admin_client = client_for(203, admin_user_id=1)
    admin_page = admin_client.get("/admin/account-command")
    assert_true(admin_page.status_code == 200, "admin account command loads")
    assert_true("Account Command Center" in admin_page.get_data(as_text=True), "admin account page renders")

    print("PASS: Dashboard Account Command Center audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
