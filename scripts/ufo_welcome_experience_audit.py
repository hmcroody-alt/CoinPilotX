#!/usr/bin/env python3
"""Audit the PulseSoc UFO welcome experience wiring and cooldown behavior."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-ufo-welcome-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "ufo-welcome-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "ufo-welcome-audit-secret"
os.environ["SESSION_SECRET"] = "ufo-welcome-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"
os.environ["PULSESOC_WELCOME_APP_VERSION"] = "audit.1"

import bot  # noqa: E402


USER_ID = 982701


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="ignore")


def ensure_user() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-01T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
            (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, preferred_language)
        VALUES (?, ?, ?, ?, ?, 1, 1, 1, 'en')
        """,
        (USER_ID, "ufo_welcome_audit", "Roody Audit", "ufo-welcome-audit@example.test", now),
    )
    for sql in (
        "UPDATE users SET email_verified=1 WHERE user_id=?",
        "UPDATE users SET account_status='active' WHERE user_id=?",
        "UPDATE users SET access_enabled=1 WHERE user_id=?",
        "UPDATE users SET login_enabled=1 WHERE user_id=?",
    ):
        try:
            cur.execute(sql, (USER_ID,))
        except Exception:
            pass
    conn.commit()
    conn.close()


def authenticated_client(reason: str = ""):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = USER_ID
        sess.permanent = True
        if reason:
            sess["pulse_welcome_reason"] = reason
    return client


def set_setting(key: str, value: str) -> None:
    conn = bot.db()
    cur = conn.cursor()
    bot.ensure_pulse_welcome_schema(cur, conn)
    cur.execute(
        """
        INSERT INTO user_settings (user_id, setting_key, setting_value, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at
        """,
        (USER_ID, key, value, "2026-07-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def audit_static() -> None:
    source = read("bot.py")
    home_js = read("static/js/pulse_home_core.js")
    home_css = read("static/css/pulse_home_os.css")
    i18n = read("static/js/pulse_i18n.js")
    settings = read("services/dashboard_account_command_center.py")

    for endpoint in ("/api/pulse/welcome-state", "/api/pulse/welcome-dismiss", "/api/pulse/welcome-trigger"):
        expect(endpoint in source, f"{endpoint} endpoint exists")
    expect("CREATE TABLE IF NOT EXISTS user_welcome_events" in source, "server-side welcome event table exists")
    expect("PULSESOC_WELCOME_COOLDOWN_DAYS" in source and "pulse_welcome_latest_any_event" in source, "server-side cooldown exists")
    for welcome_type in ("first_login", "welcome_back", "session_return", "version_update", "manual"):
        expect(welcome_type in source, f"{welcome_type} welcome type exists")
    expect("data-ufo-welcome-overlay" in source, "Home shell includes UFO overlay component")
    expect("UfoWelcomeOverlay" in home_js, "Home JS includes reusable UfoWelcomeOverlay component")
    expect("/api/pulse/welcome-state" in home_js and "/api/pulse/welcome-dismiss" in home_js, "Home JS calls welcome APIs")
    expect("load(true).finally" in home_js, "welcome overlay does not block initial feed render")
    expect("PulseShell?.haptics?.impact" in home_js and "welcome_sound" in home_js, "sound and haptic settings are respected")
    expect("prefers-reduced-motion: reduce" in home_css and "data-pulseshell-performance=\"reduced-motion\"" in home_css, "reduced-motion CSS exists")
    expect("overlay.remove()" in home_js and "sessionStorage.setItem" in home_js, "overlay cleanup and local anti-flicker cache exist")
    expect("welcome_experience" in settings and "welcome_sound" in settings and "welcome_haptics" in settings, "settings registry supports welcome controls")
    expect("welcome.welcome_back.title" in i18n and "languagePattern" in i18n, "welcome copy is translation-keyed and language extensibility remains")
    response_block = source[source.find("def api_pulse_welcome_state"):source.find("@webhook_app.route(\"/api/pulse/welcome-dismiss")]
    for sensitive in ("email", "phone", "session_token", "refresh_token", "admin_status"):
        expect(sensitive not in response_block, f"welcome response does not expose {sensitive}")


def audit_runtime() -> None:
    ensure_user()
    client = authenticated_client("first_login")
    first = client.get("/api/pulse/welcome-state").get_json() or {}
    expect(first.get("ok") is True and first.get("should_show") is True, "first login welcome is shown")
    expect(first.get("welcome_type") == "first_login", "first login welcome type is selected")
    expect("email" not in first and "phone" not in first and "session" not in first, "welcome payload omits sensitive fields")
    event_id = int(first.get("event_id") or 0)
    expect(event_id > 0, "welcome-state claims an event")

    repeat = client.get("/api/pulse/welcome-state").get_json() or {}
    expect(repeat.get("should_show") is False and repeat.get("reason") == "cooldown_active", "refresh does not show another welcome immediately")

    dismissed = client.post("/api/pulse/welcome-dismiss", json={"event_id": event_id, "welcome_type": "first_login"}).get_json() or {}
    expect(dismissed.get("ok") is True and dismissed.get("dismissed") is True, "dismiss endpoint records idempotent dismissal")

    set_setting("welcome_experience", "false")
    disabled_client = authenticated_client("welcome_back")
    disabled = disabled_client.get("/api/pulse/welcome-state").get_json() or {}
    expect(disabled.get("should_show") is False and disabled.get("reason") == "user_disabled", "user setting disables welcome")

    anonymous = bot.webhook_app.test_client().post("/api/pulse/welcome-trigger", json={"target_user_id": USER_ID})
    expect(anonymous.status_code == 403, "manual trigger is protected")


def run() -> None:
    audit_static()
    audit_runtime()
    print("UFO welcome experience audit passed.")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
