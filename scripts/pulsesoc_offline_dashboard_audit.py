#!/usr/bin/env python3
"""Regression audit for PulseSoc offline recovery, homepage routing, and dashboard modules."""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-offline-dashboard-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "offline-dashboard-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "offline-dashboard-audit-secret"
os.environ["SESSION_SECRET"] = "offline-dashboard-audit-secret"
os.environ["SESSION_COOKIE_SECURE"] = "1"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read(path: str) -> str:
    return (ROOT / path).read_text()


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
            901,
            "offline_dashboard_audit",
            "Offline Dashboard Audit",
            "Offline Dashboard Audit",
            "offline-dashboard@example.test",
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
            WHERE user_id=901
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
        sess["account_user_id"] = 901
    return client


def audit_routes() -> None:
    client = authenticated_client()
    root = client.get("/", follow_redirects=False)
    expect(root.status_code in {301, 302, 303, 307, 308}, "signed-in / redirects instead of rendering legacy homepage")
    expect(root.headers.get("Location", "").startswith("/pulse"), "signed-in / redirects to new PulseSoc Home")

    pulse = client.get("/pulse")
    expect(pulse.status_code == 200, "/pulse renders")
    pulse_body = pulse.get_data(as_text=True)
    expect("PulseSoc hit a temporary system issue" not in pulse_body, "/pulse does not show system issue")
    expect("PulseSoc is offline" not in pulse_body, "/pulse does not show offline fallback")

    dashboard = client.get("/dashboard")
    expect(dashboard.status_code == 200, "/dashboard renders")
    dashboard_body = dashboard.get_data(as_text=True)
    expect("Dashboard is reconnecting" not in dashboard_body, "dashboard does not use reconnecting fallback")
    expect("PulseSoc hit a temporary system issue" not in dashboard_body, "dashboard does not show global system issue")
    expect("Some dashboard modules are temporarily unavailable." not in dashboard_body, "normal dashboard render has no degraded modules")


def audit_offline_recovery_targets() -> None:
    offline = read("templates/offline.html")
    static_offline = read("static/offline.html")
    reset_route = read("bot.py")
    for label, source in {"offline template": offline, "static offline fallback": static_offline, "reset-pwa route": reset_route}.items():
        expect('location.href = "/"' not in source, f"{label} does not route recovery to legacy root")
        expect("/?reset_pwa=1" not in source, f"{label} does not reset into legacy root")
    expect("/pulse?offline_recovered=1" in offline, "offline recovery returns to PulseSoc Home")
    expect("/pulse?offline_recovered=1" in static_offline, "static offline recovery returns to PulseSoc Home")
    expect("/pulse?reset_pwa=1" in reset_route, "PWA reset returns to PulseSoc Home")


def audit_service_workers() -> None:
    for path in ("static/service-worker.js", "static/sw.js"):
        source = read(path)
        expect("coinplotx-cache-v20-pulse-offline-dashboard" in source, f"{path} cache version bumped")
        expect('const fallbackUrl = videoRoute ? "/pulse/videos" : "/pulse";' in source, f"{path} online fallback prefers PulseSoc Home")
        expect("/health?sw_recovery=" in source, f"{path} verifies server reachability before true offline fallback")
        expect('href="/pulse/videos"' not in source, f"{path} no hard-coded video fallback for every route")
        expect("Open PulseSoc Home" in source, f"{path} includes PulseSoc Home fallback")


def audit_language_safety() -> None:
    source = read("static/js/pulse_i18n.js")
    expect('return supported.has(base) ? base : "en";' in source, "language normalization falls back to English")
    expect('catch (error) {\n      return readCachedLanguage();\n    }' in source, "server language fetch failure falls back safely")
    expect('fetch("/api/i18n/missing"' in source, "missing translations are logged")
    expect(".catch(() => undefined)" in source, "missing translation logging never blocks boot")


def audit_mobile_offline_classification() -> None:
    for path in ("mobile/services/apiClient.ts", "mobile/pulse-react-native/services/apiClient.ts"):
        source = read(path)
        expect("pulseHealthReachable" in source, f"{path} verifies health before reporting offline")
        expect("/health?mobile_check=" in source, f"{path} checks health endpoint")
        expect("request_unreachable" in source, f"{path} treats reachable-server failures as request failures, not offline")
        expect("if (error instanceof PulseApiError) return false;" in source, f"{path} Pulse API errors are not offline")
    shell = read("mobile/pulse-react-native/App.tsx")
    expect("const PULSESOC_START_URL = PULSESOC_HOME_URL;" in shell, "native shell launches the new PulseSoc Home")
    expect("checkPulseSocReachable" in shell, "native shell verifies server reachability before offline state")
    expect("/pulse?offline_recovered=1" in shell, "native offline Retry returns to new PulseSoc Home")


def audit_postgres_dashboard_placeholders() -> None:
    targets = [
        "services/pulse_dashboard_mission_control.py",
        "services/dashboard_account_command_center.py",
        "services/dashboard_ads_command_center.py",
        "services/dashboard_network_command_center.py",
        "services/dashboard_creator_command_center.py",
        "services/dashboard_ai_command_center.py",
        "services/dashboard_crypto_command_center.py",
        "services/pulsesoc_dashboard_centers.py",
        "services/notification_service.py",
        "bot.py",
    ]
    pattern = re.compile(r"information_schema\.(?:tables|columns)[^\n]+\?")
    offenders = [path for path in targets if pattern.search(read(path))]
    expect(not offenders, f"PostgreSQL information_schema queries use %s placeholders: {offenders}")


def run() -> None:
    ensure_user()
    audit_routes()
    audit_offline_recovery_targets()
    audit_service_workers()
    audit_language_safety()
    audit_mobile_offline_classification()
    audit_postgres_dashboard_placeholders()
    print("PASS: PulseSoc offline/dashboard audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
