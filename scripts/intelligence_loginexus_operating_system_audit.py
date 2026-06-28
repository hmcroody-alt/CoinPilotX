#!/usr/bin/env python3
"""Audit the PulseSoc Intelligence operating system wiring.

This audit intentionally keeps the internal design name out of rendered UI
checks while allowing internal filenames and reports to use it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-intelligence-os-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "intelligence-os-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "intelligence-os-audit-secret"
os.environ["SESSION_SECRET"] = "intelligence-os-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import backend_management_registry  # noqa: E402
from services import dashboard_intelligence_command_center  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402


EXPECTED_ACTIONS = {
    "scam-shield": "Protection Center",
    "scam-alerts": "Alert Center",
    "pulse-brain": "Open Pulse Brain",
    "ai-advisor": "Ask AI Advisor",
    "safety-scan": "Scan My Account",
    "smart-recommendations": "Explore Recommendations",
    "security-intelligence": "Review Security",
    "threat-intelligence": "Analyze Threats",
    "risk-assessment": "Assess Risk",
    "trust-intelligence": "Review Trust",
    "signal-intelligence": "Analyze Signals",
    "research-workspace": "Start Research",
    "feed-intelligence": "View Feed Intelligence",
    "prediction-center": "View Predictions",
    "pulse-heatmap": "Explore Heatmaps",
}

EXPECTED_ADMIN_SECTIONS = {
    "scam-intelligence",
    "alert-management",
    "pulse-brain",
    "ai-advisor",
    "safety-scanner",
    "recommendation-engine",
    "security-operations",
    "threat-intelligence",
    "risk-assessment",
    "trust-intelligence",
    "signal-intelligence",
    "research-engine",
    "feed-intelligence",
    "prediction-engine",
    "heatmap-engine",
    "audit",
}

EXPECTED_ROUTES = {
    "/dashboard/intelligence",
    "/dashboard/intelligence/<subsystem_key>",
    "/api/dashboard/intelligence/state",
    "/admin/intelligence-command-center",
    "/admin/intelligence-command-center/<section_key>",
}

SAFE_ADMIN_LINKS = {
    "/admin/scam-shield",
    "/admin/security",
    "/admin/notifications",
    "/admin/notification-delivery",
    "/admin/pulse-analytics",
    "/admin/ai-usage",
    "/admin/audit-logs",
    "/admin/pulse-moderation",
    "/admin/pulse-feed-health",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def ensure_user(cur, user_id: int, email: str, name: str, *, premium: bool = False) -> None:
    now = "2026-06-27T00:00:00"
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
    ensure_user(cur, 301, "intelligence-free@example.test", "Intelligence Free", premium=False)
    ensure_user(cur, 302, "intelligence-premium@example.test", "Intelligence Premium", premium=True)
    ensure_user(cur, 303, "intelligence-admin@example.test", "Intelligence Admin", premium=True)
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
        "user_id": 303,
        "email": "intelligence-admin@example.test",
        "name": "Intelligence Admin",
        "full_name": "Intelligence Admin",
        "display_name": "Intelligence Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-password-hash-not-used",
        "must_change_password": 0,
        "password_changed_at": "2026-06-27T00:00:00",
        "created_at": "2026-06-27T00:00:00",
        "updated_at": "2026-06-27T00:00:00",
    }
    insert_columns = [column for column in admin_values if column in admin_columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(admin_values[column] for column in insert_columns),
    )
    conn.commit()
    conn.close()


def client_for(user_id: int, *, admin_user_id: int | None = None):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        if admin_user_id is not None:
            sess["admin_user_id"] = admin_user_id
    return client


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def assert_no_sensitive_leak(text: str, context: str) -> None:
    lowered = text.lower()
    for secret_word in (
        "private_key",
        "database_url",
        "password_hash",
        "raw_token",
        "raw_push_token",
        "filesystem",
        "storage_path",
        "command_center_internal_token",
    ):
        assert_true(secret_word not in lowered, f"{context} does not expose {secret_word}")


def run() -> None:
    setup_data()
    rules = route_rules()
    for route in EXPECTED_ROUTES:
        assert_true(route in rules, f"{route} route is registered")
    for route in SAFE_ADMIN_LINKS:
        assert_true(route in rules, f"{route} linked admin support route exists")

    strict_states = dashboard_intelligence_command_center.STRICT_STATES
    for state in ("READY", "ACTION", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "COMING SOON", "ADMIN"):
        assert_true(state in strict_states, f"{state} strict state is supported")

    subsystem_keys = set(dashboard_intelligence_command_center.SUBSYSTEMS_BY_KEY)
    assert_true(set(EXPECTED_ACTIONS).issubset(subsystem_keys), "all user Intelligence subsystems are registered")
    for key, action in EXPECTED_ACTIONS.items():
        blueprint = dashboard_intelligence_command_center.SUBSYSTEMS_BY_KEY[key]
        assert_true(blueprint["action"] == action, f"{key} has contextual action label")
        assert_true(blueprint["action"] != "Open", f"{key} does not use generic Open")
        assert_true(blueprint["route"].startswith("/dashboard/intelligence/"), f"{key} uses dashboard Intelligence route")
        assert_true("LogiNexus" not in json.dumps(blueprint), f"{key} public blueprint keeps internal naming invisible")

    admin_keys = {section["key"] for section in dashboard_intelligence_command_center.INTELLIGENCE_SECTIONS}
    assert_true(EXPECTED_ADMIN_SECTIONS == admin_keys, "all backend Intelligence sections are represented")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=301")
        free_user = dict(cur.fetchone())
        state = dashboard_intelligence_command_center.build_intelligence_state(conn, free_user)
        assert_true("hub" in state and "cards" in state and "event_mesh" in state, "Intelligence state includes hub, cards, and event mesh")
        assert_true(len(state["cards"]) == len(EXPECTED_ACTIONS), "Intelligence state returns every user card")
        assert_true(isinstance(state["hub"].get("personalized_daily_brief"), str), "daily brief is a string")
        serialized_state = json.dumps(state)
        assert_true("LogiNexus" not in serialized_state, "state payload keeps internal naming invisible")
        assert_no_sensitive_leak(serialized_state, "state payload")

        for card in state["cards"]:
            key = card.get("key")
            assert_true(key in EXPECTED_ACTIONS, f"{key} card key is expected")
            assert_true(card.get("cta_label") == EXPECTED_ACTIONS[key], f"{key} card action is contextual")
            assert_true(card.get("state") in strict_states, f"{key} card state is strict")
            assert_true(card.get("state") != "ACTIVE", f"{key} card does not use ACTIVE")
            assert_true((card.get("route") or "").startswith("/dashboard/intelligence/"), f"{key} has real route")

        dashboard = pulse_dashboard_mission_control.build_mission_control_dashboard(conn, free_user)
        intelligence_categories = [category for category in dashboard.get("categories") or [] if category.get("name") == "Intelligence Center"]
        assert_true(intelligence_categories, "Mission Control includes Intelligence Center category")
        widgets = intelligence_categories[0].get("widgets") or []
        assert_true(len(widgets) >= 6, "Mission Control Intelligence widgets render")
        for widget in widgets:
            assert_true(widget.get("cta_label") != "Open", f"{widget.get('display_name')} avoids generic Open")
            assert_true(widget.get("status_label") != "ACTIVE", f"{widget.get('display_name')} avoids ACTIVE state")
            assert_true(widget.get("route") and widget.get("route") != "#", f"{widget.get('display_name')} has a real route")
    finally:
        conn.close()

    assert_true("intelligence" in backend_management_registry.REQUIRED_MODULES, "backend registry includes Intelligence module")
    assert_true("intelligence" in backend_management_registry.MODULE_OPERATING_BLUEPRINTS, "backend registry includes Intelligence operating blueprint")
    registry_routes = {
        feature.route
        for feature in backend_management_registry.FEATURES
        if feature.category == "intelligence"
    }
    for section in dashboard_intelligence_command_center.INTELLIGENCE_SECTIONS:
        assert_true(section["route"] in registry_routes, f"{section['key']} has backend registry route")

    free_client = client_for(301)
    for route in ("/dashboard/intelligence", "/api/dashboard/intelligence/state"):
        response = free_client.get(route)
        assert_true(response.status_code == 200, f"{route} loads for authenticated user")
        body = response.get_data(as_text=True)
        assert_true("LogiNexus" not in body, f"{route} keeps internal naming invisible")
        assert_no_sensitive_leak(body, route)
    for key in EXPECTED_ACTIONS:
        response = free_client.get(f"/dashboard/intelligence/{key}")
        assert_true(response.status_code == 200, f"/dashboard/intelligence/{key} loads")
        body = response.get_data(as_text=True)
        assert_true(EXPECTED_ACTIONS[key] in body, f"{key} renders contextual button")
        assert_true("LogiNexus" not in body, f"{key} keeps internal naming invisible")
        assert_no_sensitive_leak(body, key)

    non_admin = free_client.get("/admin/intelligence-command-center")
    assert_true(non_admin.status_code in {302, 401, 403}, "non-admin is blocked from Intelligence admin")

    admin_client = client_for(303, admin_user_id=1)
    admin_page = admin_client.get("/admin/intelligence-command-center")
    assert_true(admin_page.status_code == 200, "admin Intelligence command center loads")
    admin_body = admin_page.get_data(as_text=True)
    assert_true("Intelligence Command Center" in admin_body, "admin page renders title")
    assert_true("LogiNexus" not in admin_body, "admin page keeps internal naming invisible")
    assert_no_sensitive_leak(admin_body, "admin command center")
    for key in EXPECTED_ADMIN_SECTIONS:
        response = admin_client.get(f"/admin/intelligence-command-center/{key}")
        assert_true(response.status_code == 200, f"/admin/intelligence-command-center/{key} loads")
        body = response.get_data(as_text=True)
        assert_true("Launch Readiness" in body, f"{key} renders launch readiness")
        assert_true("LogiNexus" not in body, f"{key} keeps internal naming invisible")
        assert_no_sensitive_leak(body, key)

    print("PASS: Intelligence operating system audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
