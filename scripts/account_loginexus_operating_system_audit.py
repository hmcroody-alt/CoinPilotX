#!/usr/bin/env python3
"""Audit the internal account operating-system upgrade without exposing internal naming in UI."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-account-os-", suffix=".db", delete=False)
tmp_db.close()

os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "account-os-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "account-os-audit-secret"
os.environ["SESSION_SECRET"] = "account-os-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import dashboard_account_command_center  # noqa: E402


ALLOWED_STATES = {"READY", "ACTION", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "ADMIN"}
USER_ROUTES = (
    "/dashboard/account",
    "/dashboard/account/profile",
    "/dashboard/account/verification",
    "/dashboard/account/health",
    "/dashboard/account/security",
    "/dashboard/account/settings",
    "/dashboard/account/advanced-security",
    "/dashboard/account/identity-protection",
    "/dashboard/account/session-intelligence",
    "/dashboard/account/device-intelligence",
    "/dashboard/account/security-timeline",
    "/dashboard/account/threat-detection",
    "/dashboard/account/login-analytics",
)
ADMIN_ROUTES = (
    "/admin/account-command",
    "/admin/account-command/profile",
    "/admin/account-command/verification",
    "/admin/account-command/account-health",
    "/admin/account-command/security",
    "/admin/account-command/settings",
    "/admin/account-command/advanced-security",
    "/admin/account-command/identity-protection",
    "/admin/account-command/session-intelligence",
    "/admin/account-command/device-intelligence",
    "/admin/account-command/security-timeline",
    "/admin/account-command/threat-detection",
    "/admin/account-command/login-analytics",
    "/admin/account-command/audit",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def seed() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-06-27T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility, avatar_url)
        VALUES (301, 'account_os_user', 'Account OS User', 'account-os-user@example.test', ?, 1, 1, 0, 1, 'public', '/static/avatar.png')
        """,
        (now,),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (302, 'account_os_admin', 'Account OS Admin', 'account-os-admin@example.test', ?, 1, 1, 1, 1, 'public')
        """,
        (now,),
    )
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
    values = {
        "id": 11,
        "user_id": 302,
        "email": "account-os-admin@example.test",
        "name": "Account OS Admin",
        "full_name": "Account OS Admin",
        "display_name": "Account OS Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-password-hash-not-used",
        "must_change_password": 0,
        "password_changed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    columns = [column for column in values if column in admin_columns]
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    dashboard_account_command_center.ensure_schema(conn)
    dashboard_account_command_center.record_account_system_event(
        conn,
        user_id=301,
        subsystem_key="threat_detection",
        event_type="audit_warning",
        public_summary="Audit event created to verify threat routing.",
        severity="medium",
        source="account_os_audit",
    )
    conn.commit()
    conn.close()


def client_for(user_id: int, admin_id: int | None = None):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        if admin_id is not None:
            sess["admin_user_id"] = admin_id
    return client


def run() -> None:
    seed()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=301")
        state = dashboard_account_command_center.build_account_state(conn, dict(cur.fetchone()))
    finally:
        conn.close()
    assert_true("intelligence" in state, "Account Intelligence payload exists")
    assert_true(set(dashboard_account_command_center.ACCOUNT_SUBSYSTEM_MAP).issubset(set((state.get("subsystems") or {}).keys())), "all 12 account subsystems exist")
    for key, subsystem in (state.get("subsystems") or {}).items():
        assert_true(subsystem.get("state") in ALLOWED_STATES, f"{key} uses strict state label")
        assert_true(subsystem.get("cta_label") and subsystem.get("cta_label") != "Open", f"{key} has contextual CTA")
        for field in ("monitors", "protections", "recovery"):
            assert_true(field in subsystem, f"{key} includes {field} layer")
    serialized = json.dumps(state).lower()
    for forbidden in (
        "private_key",
        "database_url",
        "password_hash",
        "internal_note",
        "raw_token",
        "raw_push_token",
        "audited",
        "backend_managed",
        "admin_route",
        "admin_label",
        "/admin/",
    ):
        assert_true(forbidden not in serialized, f"state omits {forbidden}")

    user_client = client_for(301)
    admin_client = client_for(302, 11)
    for route in USER_ROUTES:
        response = user_client.get(route)
        assert_true(response.status_code == 200, f"user route {route} loads")
        html = response.get_data(as_text=True)
        assert_true("LogiNexus" not in html, f"user route {route} keeps internal term invisible")
        assert_true("Open</a>" not in html, f"user route {route} avoids generic open CTA")
    for route in ADMIN_ROUTES:
        response = admin_client.get(route)
        assert_true(response.status_code == 200, f"admin route {route} loads")
        html = response.get_data(as_text=True)
        assert_true("LogiNexus" not in html, f"admin route {route} keeps internal term invisible")
        assert_true("private_key" not in html.lower(), f"admin route {route} redacts secrets")
    denied = user_client.get("/admin/account-command/security")
    assert_true(denied.status_code in {302, 401, 403}, "non-admin blocked from backend account surface")
    print("PASS: Account operating system audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
