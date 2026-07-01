#!/usr/bin/env python3
"""Regression audit for dashboard availability and mobile login restore."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-dashboard-mobile-incident-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "dashboard-mobile-incident-secret"
os.environ["FLASK_SECRET_KEY"] = "dashboard-mobile-incident-secret"
os.environ["SESSION_SECRET"] = "dashboard-mobile-incident-secret"
os.environ["SESSION_COOKIE_SECURE"] = "1"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"
os.environ["PULSESOC_REFRESH_REUSE_GRACE_SECONDS"] = "180"

import bot  # noqa: E402


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


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
             onboarding_complete, alerts_enabled, is_pro, email_verified, preferred_language)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 1, 'en')
        """,
        (
            501,
            "incident_user",
            "Incident User",
            "Incident User",
            "incident@example.test",
            generate_password_hash("Password123!"),
            now,
        ),
    )
    try:
        cur.execute(
            """
            UPDATE users
            SET account_status='active', access_enabled=1, login_enabled=1, plan='premium',
                subscription_status='active', updated_at=?
            WHERE user_id=501
            """,
            (now,),
        )
    except Exception:
        pass
    conn.commit()
    conn.close()


def authenticated_client():
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 501
    return client


def assert_no_system_issue(response, route: str) -> str:
    body = response.get_data(as_text=True)
    expect(response.status_code != 500, f"{route} must not return HTTP 500")
    expect("PulseSoc hit a temporary system issue" not in body, f"{route} must not show the global system issue page")
    return body


def audit_dashboard_routes() -> None:
    client = authenticated_client()
    for route in ("/dashboard", "/pulse"):
        response = client.get(route)
        assert_no_system_issue(response, route)
        expect(response.status_code in {200, 302}, f"{route} returns a recoverable status")

    for route in ("/api/dashboard", "/api/dashboard/mission-control", "/api/dashboard/account/state", "/api/mobile/auth/session"):
        response = client.get(route)
        expect(response.status_code == 200, f"{route} returns 200")
        payload = response.get_json() or {}
        expect(payload.get("ok") is not False, f"{route} returns a successful payload")
        if route == "/api/dashboard/account/state":
            expect(bool(payload.get("account")), "account state API returns account payload")
        if route == "/api/mobile/auth/session":
            expect(payload.get("authenticated") is True, "mobile auth session sees existing account session")


def audit_dashboard_recovery_page() -> None:
    client = authenticated_client()
    original = bot.pulse_dashboard_mission_control.build_mission_control_dashboard

    def broken_dashboard(*_args, **_kwargs):
        raise RuntimeError("forced dashboard audit failure")

    bot.pulse_dashboard_mission_control.build_mission_control_dashboard = broken_dashboard
    try:
        response = client.get("/dashboard")
    finally:
        bot.pulse_dashboard_mission_control.build_mission_control_dashboard = original
    body = assert_no_system_issue(response, "/dashboard recovery")
    expect(response.status_code == 200, "dashboard recovery returns 200")
    expect("Dashboard is reconnecting" in body, "dashboard recovery explains the fallback")
    expect("Retry Dashboard" in body, "dashboard recovery includes retry button")
    expect("/pulse?source=dashboard_recovery" in body, "dashboard recovery routes users back to PulseSoc")


def audit_mobile_login_and_refresh() -> None:
    client = bot.webhook_app.test_client()
    login = client.post(
        "/api/mobile/auth/login",
        json={"identifier": "incident@example.test", "password": "Password123!", "preferred_language": "es"},
        headers={"User-Agent": "PulseSocNativeApp/1.0 (ios; incident-audit)"},
    )
    expect(login.status_code == 200, "mobile login returns 200")
    payload = login.get_json() or {}
    expect(payload.get("authenticated") is True, "mobile login authenticates")
    refresh_token = payload.get("refresh_token") or ""
    expect(refresh_token.startswith("psr_"), "mobile login returns a refresh token")
    set_cookie = "\n".join(login.headers.getlist("Set-Cookie"))
    expect("HttpOnly" in set_cookie, "mobile login cookies are HttpOnly")
    expect("Secure" in set_cookie, "mobile login cookies are Secure in production mode")
    expect("SameSite=Lax" in set_cookie, "mobile login cookies use SameSite=Lax")

    session_response = client.get("/api/mobile/auth/session", headers={"User-Agent": "PulseSocNativeApp/1.0 (ios; incident-audit)"})
    expect(session_response.status_code == 200, "mobile session endpoint returns 200 after login")
    expect((session_response.get_json() or {}).get("authenticated") is True, "mobile session remains authenticated after login")

    first_refresh = client.post(
        "/api/mobile/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"User-Agent": "PulseSocNativeApp/1.0 (ios; incident-audit)"},
    )
    expect(first_refresh.status_code == 200, "first explicit mobile refresh returns 200")
    expect((first_refresh.get_json() or {}).get("refresh_token"), "first refresh rotates refresh token")

    grace_refresh = client.post(
        "/api/mobile/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"User-Agent": "PulseSocNativeApp/1.0 (ios; incident-audit)"},
    )
    expect(grace_refresh.status_code == 200, "same-device rotated refresh grace prevents false logout")
    expect((grace_refresh.get_json() or {}).get("authenticated") is True, "grace refresh remains authenticated")


def audit_mobile_client_cookie_parsing() -> None:
    required_files = [
        ROOT / "mobile/services/apiClient.ts",
        ROOT / "mobile/pulse-react-native/services/apiClient.ts",
        ROOT / "mobile/pulse-react-native/src/api/client.ts",
    ]
    for path in required_files:
        source = path.read_text()
        expect("mergeSessionCookies" in source, f"{path} merges Set-Cookie into Cookie header pairs")
        expect("split(/,(?=\\s*[^=;,\\s]+=)/)" in source, f"{path} handles combined Set-Cookie headers")
        expect("Max-Age=0" not in source, f"{path} does not keep deletion attributes as cookies")

    for path in [ROOT / "mobile/store/authStore.ts", ROOT / "mobile/pulse-react-native/store/authStore.ts"]:
        source = path.read_text()
        expect("if (!session.authenticated && refreshToken)" in source, f"{path} falls back to refresh token when cookie session is stale")


def run() -> None:
    ensure_user()
    audit_dashboard_routes()
    audit_dashboard_recovery_page()
    audit_mobile_login_and_refresh()
    audit_mobile_client_cookie_parsing()
    print("PASS: dashboard/mobile login incident audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
