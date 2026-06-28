#!/usr/bin/env python3
"""Audit PulseSoc System Mission Control routing, privacy, and state wiring."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-system-mission-", suffix=".db", delete=False)
tmp_db.close()

os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "system-mission-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "system-mission-audit-secret"
os.environ["SESSION_SECRET"] = "system-mission-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["COMMAND_CENTER_ENABLED"] = "false"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import pulse_dashboard_mission_control, system_mission_control  # noqa: E402


EXPECTED_ROUTES = {
    "/dashboard/system",
    "/dashboard/system/<module_key>",
    "/api/dashboard/system/state",
    "/admin/system",
    "/admin/system/<module_key>",
}

FORBIDDEN_PUBLIC_STRINGS = (
    "LogiNexus",
    "LoGiNexus",
    "LOGINEXUS",
    "COMMAND_CENTER_INTERNAL_TOKEN",
    "APNS_PRIVATE_KEY",
    "FCM_PRIVATE_KEY",
    "VAPID_PRIVATE_KEY",
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "STRIPE_SECRET_KEY",
    "private_key",
    "raw_token",
    "password_hash",
    "filesystem",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def assert_no_forbidden(label: str, text: str) -> None:
    lowered = text.lower()
    for term in FORBIDDEN_PUBLIC_STRINGS:
        if term.lower() in lowered:
            fail(f"{label} exposed forbidden term: {term}")


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def seed() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-06-28T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (9801, "system_user", "System User", "system-user@example.test", now, 1, 1, 1, 1, "public"),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (9802, "system_admin", "System Admin", "system-admin@example.test", now, 1, 1, 1, 1, "public"),
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            email TEXT,
            name TEXT,
            full_name TEXT,
            role TEXT,
            status TEXT,
            password_hash TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(admin_users)")
    admin_columns = {row[1] for row in cur.fetchall()}
    admin_values = {
        "id": 98,
        "user_id": 9802,
        "email": "system-admin@example.test",
        "name": "System Admin",
        "full_name": "System Admin",
        "display_name": "System Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-only",
        "must_change_password": 0,
        "password_changed_at": now,
        "failed_login_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    insert_columns = [column for column in admin_values if column in admin_columns]
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({', '.join('?' for _ in insert_columns)})",
        tuple(admin_values[column] for column in insert_columns),
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            content TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            notification_type TEXT,
            read_at TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute("INSERT INTO posts (user_id, content, created_at) VALUES (?,?,?)", (9801, "mission signal", now))
    cur.execute("INSERT INTO notifications (user_id, notification_type, read_at, created_at) VALUES (?,?,?,?)", (9801, "system", "", now))
    conn.commit()
    conn.close()


def client_for(user_id: int | None = None, admin_id: int | None = None):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        if user_id is not None:
            sess["account_user_id"] = user_id
        if admin_id is not None:
            sess["admin_user_id"] = admin_id
    return client


def run() -> None:
    seed()
    rules = route_rules()
    missing = sorted(EXPECTED_ROUTES - rules)
    assert_true(not missing, f"missing expected routes: {missing}")

    assert_true(system_mission_control.STRICT_STATES >= {"READY", "WARNING", "OFFLINE", "BETA"}, "strict states include required labels")
    assert_true(len(system_mission_control.SYSTEM_MODULES) >= 10, "system modules registered")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        state = system_mission_control.build_system_state(conn)
        assert_true(state.get("privacy", {}).get("secrets_visible") is False, "state redacts secrets")
        assert_true(state.get("privacy", {}).get("device_secret_values_visible") is False, "state redacts device secret values")
        assert_true(any(module.get("key") == "messenger" for module in state.get("modules") or []), "messenger module present")
        payload = pulse_dashboard_mission_control.build_mission_control_dashboard(conn, {"user_id": 9801, "email": "system-user@example.test", "display_name": "System User", "is_pro": 1})
        system_widgets = [widget for category in payload.get("categories") or [] if category.get("name") == "System Status" for widget in category.get("widgets") or []]
        assert_true(system_widgets, "dashboard includes System Status widgets")
        assert_true(all((widget.get("route") or "").startswith("/dashboard/system") for widget in system_widgets), "system widgets route to mission control")
        assert_true(all(widget.get("cta_label") == "Review System" for widget in system_widgets), "system widgets have contextual CTA")
        assert_no_forbidden("mission payload", json.dumps(payload))
    finally:
        conn.close()

    public_client = client_for(9801)
    response = public_client.get("/dashboard/system")
    assert_true(response.status_code == 200, "user system mission control loads")
    html = response.get_data(as_text=True)
    assert_true("PulseSoc Mission Control" in html, "user mission control has safe public label")
    assert_no_forbidden("user system html", html)
    assert_true("private audit body must not render" not in html, "private messages are not rendered")

    module_response = public_client.get("/dashboard/system/messenger")
    assert_true(module_response.status_code == 200, "user system module loads")
    assert_no_forbidden("user module html", module_response.get_data(as_text=True))

    anon_admin = public_client.get("/admin/system")
    assert_true(anon_admin.status_code in {302, 401, 403}, "non-admin blocked from admin system")

    admin_client = client_for(9802, 98)
    admin_response = admin_client.get("/admin/system")
    assert_true(admin_response.status_code == 200, "admin system mission control loads")
    admin_html = admin_response.get_data(as_text=True)
    assert_true("System Mission Control" in admin_html, "admin mission control renders")
    assert_no_forbidden("admin system html", admin_html)

    admin_module = admin_client.get("/admin/system/messenger")
    assert_true(admin_module.status_code == 200, "admin system module loads")
    assert_no_forbidden("admin module html", admin_module.get_data(as_text=True))

    api_response = public_client.get("/api/dashboard/system/state")
    assert_true(api_response.status_code == 200, "system API loads")
    assert_no_forbidden("system api", json.dumps(api_response.get_json()))
    print("PASS: PulseSoc System Mission Control audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
