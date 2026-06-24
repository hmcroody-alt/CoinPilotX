#!/usr/bin/env python3
"""Audit PulseSoc Mission Control V2 registry, schema, and access rules."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-dashboard-v2-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "dashboard-v2-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "dashboard-v2-audit-secret"
os.environ["SESSION_SECRET"] = "dashboard-v2-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402


REQUIRED_DASHBOARD_TABLES = {
    "dashboard_categories",
    "dashboard_modules",
    "dashboard_permissions",
    "dashboard_visibility",
    "dashboard_usage",
    "dashboard_audit_logs",
}

REQUIRED_AD_TABLES = {
    "ads",
    "ad_campaigns",
    "ad_creatives",
    "ad_videos",
    "ad_images",
    "ad_impressions",
    "ad_clicks",
    "ad_revenue",
    "advertisers",
    "ad_targeting",
    "sponsorships",
    "brand_deals",
}

REQUIRED_CATEGORIES = {
    "Account Command Center",
    "Pulse Network",
    "Creator Studio",
    "Intelligence Center",
    "Economy & Earnings",
    "Pulse Radio & Media",
    "Ads & Sponsorships",
    "PulseSoc AI",
    "System Status",
}

SAFE_STATUSES = {"PRODUCTION_READY", "ACTIVE", "BETA", "PARTIAL", "COMING_SOON"}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def table_names(cur: sqlite3.Cursor) -> set[str]:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {str(row[0]) for row in cur.fetchall()}


def ensure_user(cur: sqlite3.Cursor, user_id: int, email: str, name: str, *, premium: bool = False) -> None:
    now = "2026-06-24T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro)
        VALUES (?, ?, ?, ?, ?, 1, 1, ?)
        """,
        (user_id, name.lower().replace(" ", "_"), name, email, now, 1 if premium else 0),
    )
    try:
        cur.execute(
            "UPDATE users SET plan=?, subscription_status=?, email_verified=1 WHERE user_id=?",
            ("premium" if premium else "free", "active" if premium else "inactive", user_id),
        )
    except Exception:
        pass


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 201, "free-v2@example.test", "Free V2", premium=False)
    ensure_user(cur, 202, "premium-v2@example.test", "Premium V2", premium=True)
    ensure_user(cur, 203, "admin-v2@example.test", "Admin V2", premium=True)
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
        "email": "admin-v2@example.test",
        "name": "Admin V2",
        "full_name": "Admin V2",
        "display_name": "Admin V2",
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
    conn.commit()
    conn.close()


def payload_for(user_id: int) -> dict:
    with bot.webhook_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["account_user_id"] = user_id
        response = client.get("/api/dashboard/mission-control")
    assert_true(response.status_code == 200, f"mission control API loads for user {user_id}")
    return response.get_json() or {}


def widget_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for category in payload.get("categories") or []:
        for widget in category.get("widgets") or []:
            names.add(str(widget.get("display_name") or ""))
    return names


def categories(payload: dict) -> set[str]:
    return {str(category.get("name") or "") for category in payload.get("categories") or []}


def all_widgets(payload: dict) -> list[dict]:
    widgets: list[dict] = []
    for category in payload.get("categories") or []:
        widgets.extend(category.get("widgets") or [])
    return widgets


def run() -> None:
    setup_data()
    conn = bot.db()
    cur = conn.cursor()
    existing_tables = table_names(cur)
    missing_dashboard = REQUIRED_DASHBOARD_TABLES - existing_tables
    missing_ads = REQUIRED_AD_TABLES - existing_tables
    assert_true(not missing_dashboard, f"dashboard V2 tables exist: {sorted(missing_dashboard)}")
    assert_true(not missing_ads, f"ads readiness tables exist: {sorted(missing_ads)}")
    conn.close()

    registry = pulse_dashboard_mission_control.registry_rows()
    registry_categories = {row["category"] for row in registry}
    assert_true(REQUIRED_CATEGORIES.issubset(registry_categories), "registry includes all V2 categories")
    assert_true(len(registry) >= 90, "registry contains expanded V2 module inventory")
    for row in registry:
        assert_true(row.get("route") and row.get("route") != "#", f"{row.get('widget_key')} has a real route")
        assert_true(row.get("status") in SAFE_STATUSES, f"{row.get('widget_key')} has safe status")
        assert_true(isinstance(row.get("tables"), list), f"{row.get('widget_key')} has table lineage list")
        assert_true(isinstance(row.get("dependencies"), list), f"{row.get('widget_key')} has dependencies list")

    free_payload = payload_for(201)
    premium_payload = payload_for(202)
    admin_payload = payload_for(203)

    assert_true(REQUIRED_CATEGORIES - {"Admin / Moderator Only"} <= categories(free_payload), "free user sees non-admin V2 categories")
    assert_true("Ads Manager" in widget_names(free_payload), "free user sees locked ads upgrade path")
    assert_true("UNDX" in widget_names(free_payload), "free user sees locked AI upgrade path")
    assert_true("Audit Logs" not in widget_names(free_payload), "free user cannot see admin audit logs")
    assert_true("Infrastructure Health" not in widget_names(premium_payload), "premium non-admin cannot see infrastructure health")
    assert_true("Infrastructure Health" in widget_names(admin_payload), "admin can see infrastructure health")

    locked = [widget for widget in all_widgets(free_payload) if widget.get("access") == "locked"]
    assert_true(locked, "free payload has locked premium cards")
    assert_true(all(widget.get("cta_route") for widget in locked), "locked cards have upgrade/action route")
    assert_true(all(widget.get("status_label") for widget in all_widgets(free_payload)), "widgets expose maturity label")

    serialized = json.dumps(free_payload).lower()
    for secret_word in ("database_url", "private_key", "command_center_internal_token", "secret_key", "filesystem"):
        assert_true(secret_word not in serialized, f"payload does not expose {secret_word}")
    print("PASS: PulseSoc Mission Control V2 audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
