#!/usr/bin/env python3
"""Audit the PulseSoc user/admin Network Command Center routes."""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STATE_LABELS = {
    "READY",
    "ACTION REQUIRED",
    "REVIEW",
    "WARNING",
    "SYNCING",
    "OFFLINE",
    "LIMITED",
    "PREMIUM",
    "ADMIN",
    "BETA",
    "COMING SOON",
    "PRODUCTION READY",
}

NETWORK_ROUTE_KEYS = [
    "notifications",
    "messages",
    "friends",
    "followers",
    "groups",
    "status-activity",
    "community-activity",
    "network-health",
    "delivery-intelligence",
    "notification-intelligence",
    "relationship-intelligence",
    "connection-analytics",
    "audience-mapping",
    "growth-signals",
    "delivery-matrix",
    "network-security",
    "community-intelligence",
    "creator-reach",
    "connection-recovery",
]

ADMIN_ROUTE_KEYS = [
    "notifications",
    "messenger",
    "friends",
    "followers",
    "groups",
    "status-activity",
    "community-activity",
    "network-health",
    "delivery-intelligence",
    "notification-intelligence",
    "relationship-intelligence",
    "connection-analytics",
    "audience-mapping",
    "growth-signals",
    "delivery-matrix",
    "network-security",
    "community-intelligence",
    "creator-reach",
    "connection-recovery",
    "blocks-mutes",
    "bans",
    "push-delivery",
    "message-health",
    "audit",
]


def configure_env() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="pulsesoc-network-command-"))
    db_path = temp_dir / "audit.db"
    os.environ.setdefault("COINPILOTX_DISABLE_LOCAL_ENV", "1")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.setdefault("SECRET_KEY", "network-command-audit-secret")
    os.environ.setdefault("SESSION_SECRET", "network-command-audit-session")
    os.environ.setdefault("COMMAND_CENTER_ENABLED", "false")
    os.environ.setdefault("PULSE_AI_ENABLED", "false")
    return db_path


def seed_data(bot_module) -> None:
    bot_module.init_db()
    conn = bot_module.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-06-27T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (9001, "networkqa", "Network QA", "networkqa@example.com", now, 1, 1, 0),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO admin_users
        (id, full_name, email, password_hash, role, status, created_at, updated_at, must_change_password, failed_login_count)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (1, "Network Admin", "network-admin@example.com", "audit-only", "owner", "active", now, now, 0, 0),
    )
    conn.commit()
    conn.close()


def assert_status(label: str, response, expected: set[int]) -> None:
    if response.status_code not in expected:
        body = response.get_data(as_text=True)[:500]
        raise AssertionError(f"{label} returned {response.status_code}, expected {sorted(expected)}: {body}")


def assert_internal_admin_links_resolve(client, route: str, html: str) -> None:
    """Catch dead admin links exposed from Network Command Center surfaces."""
    hrefs = set(re.findall(r"href=['\"](/admin/[^'\"#?]+)", html))
    for href in sorted(hrefs):
        if href in {"/admin/logout"} or href.startswith("/admin/api/"):
            continue
        response = client.get(href)
        if response.status_code == 404 or response.status_code >= 500:
            body = response.get_data(as_text=True)[:500]
            raise AssertionError(f"{route} exposed broken admin link {href}: {response.status_code} {body}")


def main() -> int:
    configure_env()
    import bot  # noqa: WPS433

    seed_data(bot)
    client = bot.webhook_app.test_client()

    user_routes = ["/dashboard/network"] + [f"/dashboard/network/{key}" for key in NETWORK_ROUTE_KEYS]
    admin_routes = ["/admin/network-command-center"] + [f"/admin/network-command-center/{key}" for key in ADMIN_ROUTE_KEYS]

    for route in user_routes:
        assert_status(f"unauthenticated {route}", client.get(route), {302})
    assert_status("unauthenticated network api", client.get("/api/dashboard/network/state"), {401})
    for route in admin_routes:
        assert_status(f"non-admin {route}", client.get(route), {302})

    with client.session_transaction() as sess:
        sess["account_user_id"] = 9001

    for route in user_routes:
        response = client.get(route)
        assert_status(f"user {route}", response, {200})
        text = response.get_data(as_text=True)
        if "Network Command" not in text and "PulseSoc Network" not in text:
            raise AssertionError(f"{route} did not render Network command content")
        if "LogiNexus" in text:
            raise AssertionError(f"{route} leaked internal LogiNexus terminology")
        if ">Open</a>" in text or ">ON<" in text:
            raise AssertionError(f"{route} rendered a generic Open button or ON state")

    state_response = client.get("/api/dashboard/network/state")
    assert_status("authenticated network api", state_response, {200})
    state_json = state_response.get_json() or {}
    network = state_json.get("network") or {}
    privacy = (network.get("privacy") or {})
    for key in ("message_body_redacted", "raw_device_registrations_redacted", "reporter_identity_hidden", "device_private_data_hidden"):
        if privacy.get(key) is not True:
            raise AssertionError(f"privacy flag missing or false: {key}")
    intelligence = network.get("intelligence") or {}
    for key in ("network_health", "relationship_score", "audience_score", "delivery_score", "community_score", "recommended_next_actions"):
        if key not in intelligence:
            raise AssertionError(f"network intelligence missing {key}")
    subsystems = network.get("subsystems") or {}
    expected_subsystems = {key.replace("-", "_") for key in NETWORK_ROUTE_KEYS}
    expected_subsystems.discard("messages")
    expected_subsystems.add("messenger")
    missing = sorted(expected_subsystems - set(subsystems))
    if missing:
        raise AssertionError(f"missing network subsystems: {missing}")
    for key, subsystem in subsystems.items():
        state = subsystem.get("state")
        if state not in STATE_LABELS:
            raise AssertionError(f"invalid state for {key}: {state}")
        action = str(subsystem.get("action") or subsystem.get("cta_label") or "")
        if action.strip() == "Open":
            raise AssertionError(f"generic Open action for {key}")
        for required in ("intelligence", "automation", "protection", "recovery"):
            if not subsystem.get(required):
                raise AssertionError(f"{key} missing {required} layer")

    with client.session_transaction() as sess:
        sess["admin_user_id"] = 1

    for route in admin_routes:
        response = client.get(route)
        assert_status(f"admin {route}", response, {200})
        text = response.get_data(as_text=True)
        if "Network Command Center" not in text and "Network" not in text:
            raise AssertionError(f"{route} did not render admin Network command content")
        if "LogiNexus" in text:
            raise AssertionError(f"{route} leaked internal LogiNexus terminology")
        if ">Open</a>" in text or ">ON<" in text:
            raise AssertionError(f"{route} rendered a generic Open button or ON state")
        forbidden_terms = ("DATABASE_URL", "COMMAND_CENTER_INTERNAL_TOKEN", "APNS_PRIVATE_KEY", "VAPID_PRIVATE_KEY")
        if any(term in text for term in forbidden_terms):
            raise AssertionError(f"{route} exposed forbidden diagnostic text")
        assert_internal_admin_links_resolve(client, route, text)

    print("network_command_center_audit: PASS")
    print(f"user_routes={len(user_routes)} admin_routes={len(admin_routes)} privacy_flags=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
