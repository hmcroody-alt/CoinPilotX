#!/usr/bin/env python3
"""Audit the PulseSoc AI command center wiring."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-ai-command-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "ai-command-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "ai-command-audit-secret"
os.environ["SESSION_SECRET"] = "ai-command-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import dashboard_ai_command_center  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402


EXPECTED_USER_ROUTES = {
    "/dashboard/ai",
    "/dashboard/pulsesoc-ai",
    "/dashboard/ai/<subsystem_key>",
    "/api/dashboard/ai/state",
}

EXPECTED_ADMIN_ROUTES = {
    "/admin/ai-command-center",
    "/admin/ai-command-center/<section_key>",
}

EXPECTED_SUBSYSTEM_ACTIONS = {
    "undx": "Enter UNDX",
    "assistant": "Open Companion",
    "research": "Start Research",
    "creative-studio": "Create Content",
    "visual-engine": "Open Visual Engine",
    "music-studio": "Open Music Studio",
    "video-studio": "Open Video Studio",
    "mission-control": "Open AI Mission Control",
}

EXPECTED_ADMIN_SECTIONS = {
    "undx-core",
    "adaptive-companion",
    "research-lab",
    "creative-studio",
    "visual-engine",
    "music-studio",
    "video-studio",
    "mission-control",
    "knowledge-graph",
    "agent-council",
    "memory-engine",
    "automation-queue",
    "scientific-engine",
    "world-model",
    "audit",
}

FORBIDDEN_PUBLIC_STRINGS = (
    "LogiNexus",
    "LoGiNexus",
    "private_key",
    "database_url",
    "raw_token",
    "storage_key",
    "provider_session_id",
    "checkout_url",
    "command_center_internal_token",
    "OPENAI_API_KEY",
    "api_key",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def ensure_user(cur, user_id: int, email: str, name: str, *, is_pro: int = 1) -> None:
    now = "2026-06-28T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?, ?, ?, ?, ?, 1, 1, ?, 1, 'public')
        """,
        (user_id, name.lower().replace(" ", "_"), name, email, now, is_pro),
    )


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 831, "ai-user@example.test", "AI User")
    ensure_user(cur, 832, "ai-admin@example.test", "AI Admin")
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
    columns = {row[1] for row in cur.fetchall()}
    admin_values = {
        "id": 1,
        "user_id": 832,
        "email": "ai-admin@example.test",
        "name": "AI Admin",
        "full_name": "AI Admin",
        "display_name": "AI Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "not-used",
        "must_change_password": 0,
        "password_changed_at": "2026-06-28T00:00:00",
        "created_at": "2026-06-28T00:00:00",
        "updated_at": "2026-06-28T00:00:00",
    }
    insert_columns = [column for column in admin_values if column in columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(admin_values[column] for column in insert_columns),
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            title TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            conversation_id INTEGER,
            role TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_analyses (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            analysis_type TEXT,
            status TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_recommendations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            recommendation_type TEXT,
            status TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_action_requests (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            action_type TEXT,
            status TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS command_center_ai_events (
            id INTEGER PRIMARY KEY,
            event_id TEXT,
            user_id INTEGER,
            ai_task_type TEXT,
            status TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute("INSERT INTO ai_conversations (user_id, title, created_at) VALUES (831, 'Mission', '2026-06-28T00:00:00')")
    cur.execute("INSERT INTO ai_messages (user_id, conversation_id, role, created_at) VALUES (831, 1, 'assistant', '2026-06-28T00:00:00')")
    insert_table_row(
        cur,
        "ai_analyses",
        {
            "user_id": 831,
            "analysis_type": "research",
            "type": "research",
            "status": "complete",
            "created_at": "2026-06-28T00:00:00",
        },
    )
    insert_table_row(
        cur,
        "ai_recommendations",
        {
            "user_id": 831,
            "recommendation_type": "daily_brief",
            "type": "daily_brief",
            "status": "ready",
            "created_at": "2026-06-28T00:00:00",
        },
    )
    insert_table_row(
        cur,
        "ai_action_requests",
        {
            "user_id": 831,
            "action_type": "creative_draft",
            "type": "creative_draft",
            "status": "created",
            "created_at": "2026-06-28T00:00:00",
        },
    )
    insert_table_row(
        cur,
        "command_center_ai_events",
        {
            "event_id": "evt-ai-1",
            "user_id": 831,
            "ai_task_type": "summary",
            "type": "summary",
            "status": "queued",
            "created_at": "2026-06-28T00:00:00",
        },
    )
    conn.commit()
    conn.close()


def insert_table_row(cur, table: str, values: dict[str, object]) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cur.fetchall()}
    insert_columns = [column for column in values if column in columns]
    if not insert_columns:
        return
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT INTO {table} ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )


def client_for(user_id: int, *, admin_id: int | None = None):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        if admin_id is not None:
            sess["admin_user_id"] = admin_id
    return client


def assert_no_forbidden_public_content(text: str, context: str) -> None:
    lowered = text.lower()
    for item in FORBIDDEN_PUBLIC_STRINGS:
        assert_true(item.lower() not in lowered, f"{context} does not expose {item}")


def run() -> None:
    setup_data()
    rules = route_rules()
    for route in EXPECTED_USER_ROUTES | EXPECTED_ADMIN_ROUTES:
        assert_true(route in rules, f"{route} route is registered")
    for route in ("/pulse/premium/undx", "/pulse/assistant", "/pulse/premium/intelligence"):
        assert_true(route in rules, f"{route} support route is registered")

    for state in ("READY", "ACTION REQUIRED", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "COMING SOON", "ADMIN"):
        assert_true(state in dashboard_ai_command_center.STRICT_STATES, f"{state} strict state is supported")

    assert_true(set(EXPECTED_SUBSYSTEM_ACTIONS).issubset(set(dashboard_ai_command_center.SUBSYSTEMS_BY_KEY)), "all PulseSoc AI subsystems are registered")
    for key, action in EXPECTED_SUBSYSTEM_ACTIONS.items():
        blueprint = dashboard_ai_command_center.SUBSYSTEMS_BY_KEY[key]
        assert_true(blueprint["action"] == action, f"{key} has contextual action label")
        assert_true(blueprint["action"] != "Open", f"{key} does not use generic Open")
        assert_true(blueprint["route"].startswith("/dashboard/ai/"), f"{key} uses dashboard AI route")
        assert_no_forbidden_public_content(json.dumps(blueprint), f"{key} blueprint")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=831")
        user = dict(cur.fetchone())
        state = dashboard_ai_command_center.build_ai_state(conn, user)
        assert_true("hub" in state and "cards" in state and "automation_mesh" in state, "AI state includes hub, cards, and automation mesh")
        assert_true(len(state["cards"]) == len(EXPECTED_SUBSYSTEM_ACTIONS), "AI state returns every user subsystem")
        assert_true(state["metrics"]["ai_conversations"] == 1, "owner conversation count is scoped")
        assert_true(state["metrics"]["ai_messages"] == 1, "owner message signal count is scoped")
        assert_true(state["metrics"]["pending_events"] == 1, "owner queued AI event count is scoped")
        assert_true(state["privacy"]["raw_prompts_visible"] is False, "raw prompts are hidden")
        assert_no_forbidden_public_content(json.dumps(state), "AI state payload")

        dashboard = pulse_dashboard_mission_control.build_mission_control_dashboard(conn, user)
        ai_category = next((category for category in dashboard["categories"] if category["name"] == "PulseSoc AI"), None)
        assert_true(bool(ai_category), "Mission Control includes PulseSoc AI category")
        for widget in ai_category["widgets"]:
            assert_true(widget["route"].startswith("/dashboard/ai/"), f"{widget['widget_key']} routes to AI dashboard")
            assert_true((widget.get("cta_label") or "") != "Open", f"{widget['widget_key']} has contextual CTA")

        admin_state = dashboard_ai_command_center.build_admin_ai_state(conn)
        admin_keys = {section["key"] for section in admin_state["sections"]}
        assert_true(EXPECTED_ADMIN_SECTIONS.issubset(admin_keys), "Admin AI sections are registered")
        assert_true(admin_state["privacy"]["provider_credentials_visible"] is False, "provider credentials are hidden")
        assert_no_forbidden_public_content(json.dumps(admin_state), "Admin AI state payload")
    finally:
        conn.close()

    user_client = client_for(831)
    admin_client = client_for(832, admin_id=1)
    for route in ("/dashboard/ai", "/dashboard/pulsesoc-ai", "/dashboard/ai/undx", "/dashboard/ai/mission-control"):
        response = user_client.get(route)
        assert_true(response.status_code == 200, f"{route} loads for authenticated user")
        assert_no_forbidden_public_content(response.get_data(as_text=True), f"{route} HTML")
    response = user_client.get("/api/dashboard/ai/state")
    assert_true(response.status_code == 200, "AI API loads for authenticated user")
    assert_no_forbidden_public_content(response.get_data(as_text=True), "AI API response")

    denied = user_client.get("/admin/ai-command-center")
    assert_true(denied.status_code in {302, 403}, "non-admin cannot access AI admin command center")
    for route in ("/admin/ai-command-center", "/admin/ai-command-center/undx-core", "/admin/ai-command-center/mission-control"):
        response = admin_client.get(route)
        assert_true(response.status_code == 200, f"{route} loads for admin")
        assert_no_forbidden_public_content(response.get_data(as_text=True), f"{route} HTML")

    print(json.dumps({"ok": True, "audited": "pulsesoc_ai_command_center", "routes": len(EXPECTED_USER_ROUTES | EXPECTED_ADMIN_ROUTES)}, indent=2))


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            os.unlink(tmp_db.name)
        except OSError:
            pass
