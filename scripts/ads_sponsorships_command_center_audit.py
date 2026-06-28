#!/usr/bin/env python3
"""Audit the PulseSoc Ads & Sponsorships command center wiring."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-ads-command-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "ads-command-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "ads-command-audit-secret"
os.environ["SESSION_SECRET"] = "ads-command-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import dashboard_ads_command_center  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402


EXPECTED_USER_ROUTES = {
    "/dashboard/ads",
    "/dashboard/advertising",
    "/dashboard/ads/<subsystem_key>",
    "/api/dashboard/ads/state",
}

EXPECTED_ADMIN_ROUTES = {
    "/admin/ads-command-center",
    "/admin/ads-command-center/<section_key>",
}

EXPECTED_SUBSYSTEM_ACTIONS = {
    "sponsored-signals": "Inspect Signals",
    "manager": "Manage Campaigns",
    "campaign-builder": "Build Campaign",
    "signal-studio": "Create Sponsored Signal",
    "analytics": "Analyze Ads",
    "brand-deals": "Review Brand Deals",
    "creator-sponsorships": "Find Sponsorships",
    "revenue-intelligence": "Review Revenue",
    "audience-targeting": "Tune Audience",
    "conversion-tracking": "Track Conversions",
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
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def ensure_user(cur, user_id: int, email: str, name: str) -> None:
    now = "2026-06-28T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?, ?, ?, ?, ?, 1, 1, 0, 1, 'public')
        """,
        (user_id, name.lower().replace(" ", "_"), name, email, now),
    )


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 811, "ads-user@example.test", "Ads User")
    ensure_user(cur, 812, "ads-admin@example.test", "Ads Admin")
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
        "user_id": 812,
        "email": "ads-admin@example.test",
        "name": "Ads Admin",
        "full_name": "Ads Admin",
        "display_name": "Ads Admin",
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
        INSERT INTO pulse_ad_accounts
        (id, owner_user_id, business_name, business_email, business_website, business_type, status, verification_status, created_at, updated_at)
        VALUES (1, 811, 'Pulse Radio Sponsors', 'ads-user@example.test', 'https://pulsesoc.com/pulse/radio', 'internal', 'active', 'verified', '2026-06-28T00:00:00', '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_campaigns
        (id, ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents, lifetime_budget_cents, spent_cents, created_at, updated_at)
        VALUES (1, 1, 'Pulse Radio Awareness', 'pulse_radio', 'active', 'daily', 2500, 12500, 525, '2026-06-28T00:00:00', '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_creatives
        (id, ad_account_id, campaign_id, creative_type, title, body, destination_url, call_to_action, status, moderation_status, created_at, updated_at)
        VALUES (1, 1, 1, 'image', 'Pulse Radio', 'Live music and creator shows.', '/pulse/radio', 'Listen Now', 'active', 'approved', '2026-06-28T00:00:00', '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_wallets
        (account_id, currency, available_balance_cents, lifetime_funded_cents, lifetime_spent_cents, reserved_budget_cents, created_at, updated_at)
        VALUES (1, 'usd', 50000, 50000, 525, 2500, '2026-06-28T00:00:00', '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_ad_placements
        (placement_key, display_name, device_type, placement_type, is_active, max_frequency, created_at, updated_at)
        VALUES ('feed_inline', 'Feed Inline', 'all', 'feed', 1, 4, '2026-06-28T00:00:00', '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_impressions
        (campaign_id, creative_id, placement_key, viewer_user_id, session_id, device_type, viewport, rendered_at, visible_ms, viewable, created_at)
        VALUES (1, 1, 'feed_inline', 811, 'audit-session', 'desktop', 'wide', '2026-06-28T00:00:00', 1200, 1, '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_clicks
        (campaign_id, creative_id, placement_key, viewer_user_id, session_id, clicked_at, destination_url, created_at)
        VALUES (1, 1, 'feed_inline', 811, 'audit-session', '2026-06-28T00:00:00', '/pulse/radio', '2026-06-28T00:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_events
        (campaign_id, creative_id, event_type, metadata_json, created_at)
        VALUES (1, 1, 'conversion', '{}', '2026-06-28T00:00:00')
        """
    )
    conn.commit()
    conn.close()


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
    assert_true("/pulse/ads" in rules and "/pulse/advertise" in rules, "Advertiser portal routes are registered")
    assert_true("/admin/pulse-ads-review-board" in rules, "Ads review board route is registered")

    for state in ("READY", "ACTION REQUIRED", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "COMING SOON", "ADMIN"):
        assert_true(state in dashboard_ads_command_center.STRICT_STATES, f"{state} strict state is supported")

    assert_true(set(EXPECTED_SUBSYSTEM_ACTIONS).issubset(set(dashboard_ads_command_center.SUBSYSTEMS_BY_KEY)), "all user Ads subsystems are registered")
    for key, action in EXPECTED_SUBSYSTEM_ACTIONS.items():
        blueprint = dashboard_ads_command_center.SUBSYSTEMS_BY_KEY[key]
        assert_true(blueprint["action"] == action, f"{key} has contextual action label")
        assert_true(blueprint["action"] != "Open", f"{key} does not use generic Open")
        assert_true(blueprint["route"].startswith("/dashboard/ads/"), f"{key} uses dashboard Ads route")
        assert_no_forbidden_public_content(json.dumps(blueprint), f"{key} blueprint")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=811")
        user = dict(cur.fetchone())
        state = dashboard_ads_command_center.build_ads_state(conn, user)
        assert_true("hub" in state and "cards" in state and "automation_mesh" in state, "Ads state includes hub, cards, and automation mesh")
        assert_true(len(state["cards"]) == len(EXPECTED_SUBSYSTEM_ACTIONS), "Ads state returns every user subsystem")
        assert_true(state["metrics"]["accounts"] == 1, "owner account count is scoped")
        assert_true(state["metrics"]["impressions"] == 1, "owner impression count is scoped")
        assert_true(state["metrics"]["clicks"] == 1, "owner click count is scoped")
        assert_no_forbidden_public_content(json.dumps(state), "Ads state payload")

        dashboard = pulse_dashboard_mission_control.build_mission_control_dashboard(conn, user)
        ads_category = next((category for category in dashboard["categories"] if category["name"] == "Ads & Sponsorships"), None)
        assert_true(bool(ads_category), "Mission Control includes Ads & Sponsorships category")
        for widget in ads_category["widgets"]:
            assert_true(widget["route"].startswith("/dashboard/ads/"), f"{widget['widget_key']} routes to Ads dashboard")
            assert_true((widget.get("cta_label") or "") != "Open", f"{widget['widget_key']} has contextual CTA")
            assert_true(widget.get("status_label") != "COMING SOON" or widget.get("state"), f"{widget['widget_key']} state is backend driven")

        admin_state = dashboard_ads_command_center.build_admin_ads_state(conn)
        assert_true(len(admin_state["sections"]) >= 13, "Admin Ads sections are registered")
        assert_no_forbidden_public_content(json.dumps(admin_state), "Admin Ads state payload")
    finally:
        conn.close()

    user_client = client_for(811)
    for route in ["/api/dashboard/ads/state", "/dashboard/ads", "/dashboard/ads/manager", "/dashboard/ads/analytics", "/dashboard/ads/conversion-tracking"]:
        response = user_client.get(route)
        assert_true(response.status_code == 200, f"{route} returns 200 for authenticated user")
        text = response.get_data(as_text=True)
        assert_no_forbidden_public_content(text, route)
        assert_true("LogiNexus" not in text and "LoGiNexus" not in text, f"{route} keeps internal name invisible")

    non_admin = user_client.get("/admin/ads-command-center")
    assert_true(non_admin.status_code in {302, 401, 403}, "non-admin cannot access Ads admin command center")

    admin_client = client_for(812, admin_id=1)
    for route in ["/admin/ads-command-center", "/admin/ads-command-center/review-board", "/admin/ads-command-center/delivery-engine", "/admin/ads-command-center/audit"]:
        response = admin_client.get(route)
        assert_true(response.status_code == 200, f"{route} returns 200 for owner admin")
        assert_no_forbidden_public_content(response.get_data(as_text=True), route)

    print("PASS: Ads & Sponsorships command center routes, states, permissions, and redaction checks passed.")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            os.unlink(tmp_db.name)
        except OSError:
            pass
