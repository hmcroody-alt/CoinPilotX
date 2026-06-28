#!/usr/bin/env python3
"""Verify user dashboards do not expose admin-only operating details."""

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

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-dashboard-boundary-", suffix=".db", delete=False)
tmp_db.close()

os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "dashboard-boundary-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "dashboard-boundary-audit-secret"
os.environ["SESSION_SECRET"] = "dashboard-boundary-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["COMMAND_CENTER_ENABLED"] = "false"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402


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
    "/dashboard/network",
    "/dashboard/network/notifications",
    "/dashboard/network/messages",
    "/dashboard/network/friends",
    "/dashboard/network/followers",
    "/dashboard/network/groups",
    "/dashboard/network/delivery-intelligence",
    "/dashboard/network/push-delivery",
    "/dashboard/network/network-security",
    "/dashboard/creator",
    "/dashboard/creator/posts",
    "/dashboard/creator/reels",
    "/dashboard/creator/videos",
    "/dashboard/creator/statuses",
    "/dashboard/creator/live-studio",
    "/dashboard/intelligence",
    "/dashboard/intelligence/scam-shield",
    "/dashboard/intelligence/trust-intelligence",
    "/dashboard/intelligence/safety-scan",
    "/dashboard/economy",
    "/dashboard/economy/wallet",
    "/dashboard/economy/payouts",
    "/dashboard/economy/marketplace",
)

API_ROUTES = (
    "/api/dashboard/account/state",
    "/api/dashboard/network/state",
    "/api/dashboard/creator/state",
    "/api/dashboard/intelligence/state",
    "/api/dashboard/economy/state",
)

ADMIN_ROUTES = (
    "/admin/account-command",
    "/admin/network-command-center",
    "/admin/creator-command-center",
    "/admin/intelligence-command-center",
    "/admin/economy-command-center",
)

FORBIDDEN_USER_TERMS = (
    "LogiNexus",
    "LoGiNexus",
    "/admin/",
    "admin_route",
    "admin_label",
    "backend_managed",
    '"audited"',
    "Backend Layer",
    "Command Layer",
    "Audit Layer",
    "Backend-managed",
    "admin-only",
    "moderator-only",
    "provider health",
    "provider response",
    "provider failures",
    "missing-token",
    "raw push token",
    "Stripe customer",
    "Stripe subscription",
    "LiveKit",
    "Mux",
    "push failures",
    "failed push",
    "worker state",
    "COMMAND_CENTER_INTERNAL_TOKEN",
    "APNS_PRIVATE_KEY",
    "VAPID_PRIVATE_KEY",
    "DATABASE_URL",
)

FORBIDDEN_ADMIN_SECRET_TERMS = (
    "COMMAND_CENTER_INTERNAL_TOKEN",
    "APNS_PRIVATE_KEY",
    "VAPID_PRIVATE_KEY",
    "FCM_PRIVATE_KEY",
    "DATABASE_URL",
    "password_hash",
    "private_key",
    "secret_key",
    "raw_push_token",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_status(label: str, response, expected: set[int]) -> None:
    if response.status_code not in expected:
        body = response.get_data(as_text=True)[:500]
        fail(f"{label} returned {response.status_code}, expected {sorted(expected)}: {body}")


def assert_no_terms(label: str, text: str, terms: tuple[str, ...]) -> None:
    lowered = text.lower()
    for term in terms:
        if term.lower() in lowered:
            fail(f"{label} exposed forbidden term: {term}")


def seed() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-06-27T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (9101, "boundary_user", "Boundary User", "boundary-user@example.test", now, 1, 1, 0, 1, "public"),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (9102, "boundary_admin", "Boundary Admin", "boundary-admin@example.test", now, 1, 1, 1, 1, "public"),
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
    values = {
        "id": 91,
        "user_id": 9102,
        "email": "boundary-admin@example.test",
        "name": "Boundary Admin",
        "full_name": "Boundary Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-only",
        "must_change_password": 0,
        "failed_login_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    columns = [column for column in values if column in admin_columns]
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
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
    anonymous = bot.webhook_app.test_client()
    user_client = client_for(9101)
    admin_client = client_for(9102, 91)

    for route in USER_ROUTES:
        assert_status(f"anonymous {route}", anonymous.get(route), {302})
        response = user_client.get(route)
        assert_status(f"user {route}", response, {200})
        assert_no_terms(route, response.get_data(as_text=True), FORBIDDEN_USER_TERMS)

    for route in API_ROUTES:
        assert_status(f"anonymous {route}", anonymous.get(route), {401})
        response = user_client.get(route)
        assert_status(f"user {route}", response, {200})
        payload = json.dumps(response.get_json() or {}, sort_keys=True)
        assert_no_terms(route, payload, FORBIDDEN_USER_TERMS)

    for route in ADMIN_ROUTES:
        assert_status(f"user denied {route}", user_client.get(route), {302, 401, 403})
        response = admin_client.get(route)
        assert_status(f"admin {route}", response, {200})
        assert_no_terms(f"admin {route}", response.get_data(as_text=True), FORBIDDEN_ADMIN_SECRET_TERMS)

    print("PASS: dashboard user/admin boundary audit passed")
    print(f"user_routes={len(USER_ROUTES)} api_routes={len(API_ROUTES)} admin_routes={len(ADMIN_ROUTES)}")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
