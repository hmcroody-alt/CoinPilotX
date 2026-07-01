#!/usr/bin/env python3
"""Audit PulseSoc dashboard module recovery and Account Command real payloads."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-dashboard-recovery-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "dashboard-recovery-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "dashboard-recovery-audit-secret"
os.environ["SESSION_SECRET"] = "dashboard-recovery-audit-secret"
os.environ["SESSION_COOKIE_SECURE"] = "1"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import dashboard_account_command_center, pulse_dashboard_mission_control  # noqa: E402


ACCOUNT_KEYS = {
    "profile",
    "verification",
    "account_health",
    "security",
    "settings",
    "advanced_security",
    "identity_protection",
    "session_intelligence",
    "device_intelligence",
    "security_timeline",
    "threat_detection",
    "login_analytics",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def ensure_user() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-01T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
            (user_id, username, display_name, full_name, email, password_hash, signup_time,
             onboarding_complete, alerts_enabled, is_pro, email_verified, preferred_language,
             plan, premium_status, subscription_status, account_status, access_enabled, login_enabled, avatar_url, bio)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 1, 'en', 'premium', 'active', 'active', 'active', 1, 1, ?, ?)
        """,
        (
            941,
            "dashboard_recovery_audit",
            "Dashboard Recovery Audit",
            "Dashboard Recovery Audit",
            "dashboard-recovery@example.test",
            generate_password_hash("Password123!"),
            now,
            "/static/avatar.png",
            "Recovery audit account.",
        ),
    )
    conn.commit()
    conn.close()


def authenticated_client():
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 941
    return client


def audit_legacy_schema_recovery() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            email TEXT,
            email_verified INTEGER DEFAULT 1
        )
        """
    )
    cur.execute(
        "INSERT INTO users (user_id, username, display_name, email, email_verified) VALUES (1, 'legacy_user', 'Legacy User', 'legacy@example.test', 1)"
    )
    cur.execute("CREATE TABLE verification_requests (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT)")
    cur.execute("INSERT INTO verification_requests (id, user_id, status) VALUES (7, 1, 'submitted')")
    cur.execute("CREATE TABLE account_audit_logs (id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT)")
    conn.commit()

    dashboard_account_command_center.ensure_schema(conn)
    user = dict(conn.execute("SELECT * FROM users WHERE user_id=1").fetchone())
    state = dashboard_account_command_center.build_account_state(conn, user)
    subsystems = state.get("subsystems") or {}
    expect(ACCOUNT_KEYS.issubset(subsystems.keys()), "legacy-shaped schema still builds all account modules")
    expect(all((subsystems[key] or {}).get("available", True) is not False for key in ACCOUNT_KEYS), "account modules return real payloads, not unavailable cards")
    expect((subsystems["settings"].get("metrics") or {}).get("settings", {}).get("language") == "en", "missing language column falls back to English")
    expect(subsystems["verification"].get("status") == "submitted", "legacy verification row still powers verification module")
    conn.close()


def audit_dashboard_route() -> None:
    client = authenticated_client()
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    expect(response.status_code == 200, "/dashboard returns 200")
    expect("Some dashboard modules are temporarily unavailable." not in body, "dashboard does not show degraded module banner")
    expect("PulseSoc is offline" not in body, "dashboard never renders offline fallback")
    expect("UNAVAILABLE" not in body, "normal dashboard cards are not globally unavailable")
    for key in ACCOUNT_KEYS:
        route = dashboard_account_command_center.ACCOUNT_SUBSYSTEM_MAP[key]["route"]
        expect(route in body, f"dashboard links {route}")
        route_response = client.get(route)
        route_body = route_response.get_data(as_text=True)
        expect(route_response.status_code == 200, f"{route} returns 200")
        expect("This dashboard module is temporarily unavailable." not in route_body, f"{route} renders real module content")
        expect("LogiNexus" not in route_body, f"{route} keeps internal framework naming private")


def audit_registry_and_logging() -> None:
    registered = {item["key"] for item in dashboard_account_command_center.ACCOUNT_SUBSYSTEMS}
    expect(ACCOUNT_KEYS == registered, "all 12 Account Command Center modules are registered")
    dashboard_source = read("services/pulse_dashboard_mission_control.py")
    for needle in ("module_id=", "module_name=", "route=/dashboard", "user_id=", "error_message=", "data_step=", "language="):
        expect(needle in dashboard_source, f"degraded module logging includes {needle}")
    offline_source = read("scripts/pulsesoc_offline_dashboard_audit.py")
    expect("checkPulseSocReachable" in offline_source, "offline audit covers native health-gated retry")
    expect("coinplotx-cache-v20-pulse-offline-dashboard" in offline_source, "offline audit requires current service worker cache")


def run() -> None:
    ensure_user()
    audit_legacy_schema_recovery()
    audit_dashboard_route()
    audit_registry_and_logging()
    print("PASS: Dashboard modules recovery audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
