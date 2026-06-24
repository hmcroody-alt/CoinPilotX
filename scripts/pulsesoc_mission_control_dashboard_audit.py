#!/usr/bin/env python3
"""Audit PulseSoc Mission Control dashboard access and rendering."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-dashboard-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "dashboard-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "dashboard-audit-secret"
os.environ["SESSION_SECRET"] = "dashboard-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def ensure_user(cur, user_id: int, email: str, name: str, *, premium: bool = False) -> None:
    now = "2026-06-24T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro)
        VALUES (?, ?, ?, ?, ?, 1, 1, ?)
        """,
        (user_id, name.lower().replace(" ", "_"), name, email, now, 1 if premium else 0),
    )
    try:
        cur.execute("UPDATE users SET plan=?, subscription_status=?, email_verified=1 WHERE user_id=?", ("premium" if premium else "free", "active" if premium else "inactive", user_id))
    except Exception:
        pass


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 101, "free@example.test", "Free User", premium=False)
    ensure_user(cur, 102, "premium@example.test", "Premium User", premium=True)
    ensure_user(cur, 103, "admin@example.test", "Admin User", premium=True)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='admin_users'")
    if not cur.fetchone():
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY,
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
        "email": "admin@example.test",
        "name": "Admin User",
        "full_name": "Admin User",
        "display_name": "Admin User",
        "role": "owner",
        "status": "active",
        "created_at": "2026-06-24T00:00:00",
        "updated_at": "2026-06-24T00:00:00",
    }
    insert_columns = [column for column in admin_values if column in admin_columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(admin_values[column] for column in insert_columns),
    )
    for table in (
        "dashboard_widget_access_rules",
        "user_dashboard_widget_state",
        "dashboard_events",
        "user_dashboard_metrics",
        "creator_dashboard_metrics",
        "dashboard_recommendations",
        "dashboard_entitlements",
    ):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        assert_true(bool(cur.fetchone()), f"{table} table exists")
    conn.commit()
    conn.close()


def response_for(user_id: int, path: str = "/dashboard"):
    with bot.webhook_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["account_user_id"] = user_id
        return client.get(path)


def json_for(user_id: int):
    response = response_for(user_id, "/api/dashboard/mission-control")
    assert_true(response.status_code == 200, f"mission API returns 200 for {user_id}")
    return response.get_json()


def widget_names(payload: dict) -> set[str]:
    names = set()
    for category in payload.get("categories") or []:
        for widget in category.get("widgets") or []:
            names.add(widget.get("display_name"))
    return names


def run() -> None:
    setup_data()
    free_page = response_for(101)
    assert_true(free_page.status_code == 200, "free dashboard loads")
    free_html = free_page.get_data(as_text=True)
    assert_true("Mission Control" in free_html, "free dashboard renders Mission Control")
    assert_true("AI Insights" in free_html, "free users see locked premium widgets")
    assert_true("Unlock" in free_html, "free locked premium cards have unlock action")
    for forbidden in ("Audit Logs", "Blocked IPs", "Infrastructure Health", "Push Notification Health"):
        assert_true(forbidden not in free_html, f"free dashboard hides {forbidden}")

    premium_page = response_for(102)
    assert_true(premium_page.status_code == 200, "premium dashboard loads")
    premium_html = premium_page.get_data(as_text=True)
    assert_true("AI Insights" in premium_html, "premium dashboard includes AI Insights")
    assert_true("Premium User" in premium_html, "premium dashboard identifies user")
    for forbidden in ("Audit Logs", "Blocked IPs", "Infrastructure Health"):
        assert_true(forbidden not in premium_html, f"premium dashboard hides {forbidden}")

    admin_payload = json_for(103)
    admin_names = widget_names(admin_payload)
    assert_true("Audit Logs" in admin_names, "admin dashboard includes audit logs")
    assert_true("Infrastructure Health" in admin_names, "admin dashboard includes infrastructure health")

    free_payload = json_for(101)
    free_names = widget_names(free_payload)
    assert_true("AI Insights" in free_names, "free API includes locked AI Insights")
    assert_true("Audit Logs" not in free_names, "free API hides audit logs")
    assert_true("Blocked IPs" not in free_names, "free API hides blocked IPs")
    serialized = json.dumps(free_payload).lower()
    for secret_word in ("secret", "database_url", "private_key", "token", "filesystem"):
        assert_true(secret_word not in serialized, f"payload does not expose {secret_word}")
    for category in free_payload.get("categories") or []:
        for widget in category.get("widgets") or []:
            route = widget.get("route") or widget.get("cta_route")
            assert_true(route and route != "#", f"{widget.get('display_name')} has a real route")
    print("PASS: PulseSoc Mission Control dashboard audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
