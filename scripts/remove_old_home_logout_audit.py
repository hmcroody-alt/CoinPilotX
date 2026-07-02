#!/usr/bin/env python3
"""Audit removal of the legacy PulseSoc home and global logout reachability."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-old-home-logout-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "old-home-logout-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "old-home-logout-audit-secret"
os.environ["SESSION_SECRET"] = "old-home-logout-audit-secret"
os.environ["SESSION_COOKIE_SECURE"] = "1"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402


FORBIDDEN_HOME_STRINGS = (
    "Global PulseSoc Feed",
    "PulseSoc Universe / Network Is Alive",
    "Post questions, scam warnings, ideas, and creator updates. New approved posts appear immediately.",
    "signals mapped",
    "Explore Live Network",
    "<summary>Learn more</summary>",
)


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
            (user_id, username, display_name, full_name, email, password_hash, signup_time,
             onboarding_complete, alerts_enabled, is_pro, email_verified, preferred_language)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 1, ?)
        """,
        (
            97201,
            "old_home_logout_audit",
            "Old Home Logout Audit",
            "Old Home Logout Audit",
            "old-home-logout@example.test",
            generate_password_hash("Password123!"),
            now,
            "sw-ke",
        ),
    )
    try:
        cur.execute(
            """
            UPDATE users
            SET account_status='active', access_enabled=1, login_enabled=1,
                subscription_status='active', updated_at=?, last_login_at=NULL
            WHERE user_id=97201
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
        sess["account_user_id"] = 97201
        sess.permanent = True
    return client


def audit_source_removal() -> None:
    source = read("bot.py")
    home_core = read("static/js/pulse_home_core.js")
    for path, text in {"bot.py": source, "static/js/pulse_home_core.js": home_core}.items():
        for old in FORBIDDEN_HOME_STRINGS:
            expect(old not in text, f"legacy home string removed from {path}: {old}")
    expect("PulseSoc Home OS" in source, "new PulseSoc Home hero is present")
    expect("data-current-pulsesoc-home" in source, "new home is marked as the current home")
    expect("pulse_home_core.js?v=remove-old-home-logout-20260701" in source, "Home core asset cache key is bumped")
    expect("legacy_pulsesoc_home_redirect" in source, "legacy home redirect route exists")
    expect("is_legacy_pulsesoc_home_target" in source, "post-login legacy target sanitizer exists")
    expect("PERSISTENT_SESSION_DAYS = max(3650" in source, "web session is long-lived by default")
    expect("MOBILE_REFRESH_TOKEN_TTL_SECONDS" in source and "3650" in source, "mobile refresh tokens are long-lived by default")
    expect("revoke_mobile_refresh_token(refresh_token, \"user_logout\")" in source, "logout revokes refresh token")
    expect("password_changed" in source and "refresh_token_reuse" in source and "device_mismatch" in source, "long-lived sessions still revoke on risk events")


def audit_routes() -> None:
    client = authenticated_client()
    root = client.get("/", follow_redirects=False)
    expect(root.status_code in {301, 302, 303, 307, 308}, "signed-in root redirects")
    expect(root.headers.get("Location", "").startswith("/pulse"), "signed-in root redirects to current PulseSoc Home")

    pulse = client.get("/pulse")
    expect(pulse.status_code == 200, "/pulse renders")
    body = pulse.get_data(as_text=True)
    for old in FORBIDDEN_HOME_STRINGS:
        expect(old not in body, f"/pulse does not render old string: {old}")
    expect("PulseSoc Home" in body and "PulseSoc Home OS" in body, "/pulse renders current Home")
    expect("href=\"/logout\"" in body, "/pulse exposes logout link")
    expect("drawer-logout" in body, "mobile drawer exposes direct logout")
    expect("pulse-topnav-profile-panel" in body, "desktop profile dropdown exposes account menu")
    expect("href='/dashboard'" in body or 'href=\"/dashboard\"' in body, "Dashboard is reachable from Home")

    for path in ("/home", "/feed", "/pulse/home", "/pulse/legacy-home", "/pulse/home-legacy", "/pulse/old-home", "/pulse/legacy"):
        response = client.get(path, follow_redirects=False)
        expect(response.status_code in {301, 302, 303, 307, 308}, f"{path} redirects")
        expect(response.headers.get("Location", "").startswith("/pulse"), f"{path} redirects to current Home")

    dashboard = client.get("/dashboard")
    dashboard_body = dashboard.get_data(as_text=True)
    expect(dashboard.status_code == 200, "/dashboard renders")
    expect('href="/logout"' in dashboard_body and "Log Out" in dashboard_body, "dashboard exposes logout")


def audit_redirect_sanitizer() -> None:
    for target in ("/home", "/feed", "/pulse/home", "/pulse?legacy=1", "/pulse?old_home=1"):
        with bot.webhook_app.test_request_context(f"/login?next={target}"):
            expect(bot.safe_redirect_target("pulse_page") == "/pulse", f"login next target {target} is normalized to /pulse")
    with bot.webhook_app.test_request_context("/login?next=/pulse/messages"):
        expect(bot.safe_redirect_target("pulse_page") == "/pulse/messages", "valid non-legacy next target is preserved")


def audit_logout() -> None:
    client = authenticated_client()
    response = client.get("/logout", follow_redirects=False)
    expect(response.status_code in {301, 302, 303, 307, 308}, "logout redirects")
    expect(response.headers.get("Location", "").endswith("/login") or response.headers.get("Location", "") == "/login", "logout redirects to login")
    cookie_header = "\n".join(response.headers.getlist("Set-Cookie"))
    expect(bot.PERSISTENT_SESSION_COOKIE in cookie_header and "Expires=Thu, 01 Jan 1970" in cookie_header, "logout clears persistent cookie")
    protected = client.get("/pulse", follow_redirects=False)
    expect(protected.status_code in {301, 302, 303, 307, 308}, "back/protected route after logout requires login")


def audit_service_worker_and_i18n() -> None:
    for path in ("static/sw.js", "static/service-worker.js"):
        source = read(path)
        expect("coinplotx-cache-v21-remove-old-home-logout" in source, f"{path} cache version bumped")
        expect("/pulse?offline_recovered=1" in source, f"{path} offline Retry targets current Home")
        for old in ("Global PulseSoc Feed", "PulseSoc Universe / Network Is Alive", "Explore Live Network"):
            expect(old not in source, f"{path} does not cache legacy copy: {old}")
    i18n = read("static/js/pulse_i18n.js")
    expect("languagePattern" in i18n, "client accepts valid BCP-47-style language codes")
    expect("document.documentElement.dir" in i18n, "client updates text direction")
    expect("translationFallback" in i18n, "client marks fallback translation state")
    expect("api/i18n/missing" in i18n, "client logs missing translations")
    expect(bot.normalize_preferred_language("sw-KE") == "sw-ke", "backend stores regional languages")
    expect(bot.normalize_preferred_language("yo") == "yo", "backend accepts broad supported languages")
    expect(bot.normalize_preferred_language("zz-test") == "zz-test", "backend accepts future valid language tags")


def audit_css() -> None:
    desktop_css = read("static/css/pulse_desktop_feed.css")
    home_css = read("static/css/pulse_home_os.css")
    for name, css in {"desktop css": desktop_css, "home css": home_css}.items():
        expect("pulse-topnav-profile-panel" in css, f"{name} styles desktop profile dropdown")
        expect("z-index: 10090" in css, f"{name} keeps profile dropdown in front")
        expect("drawer-logout" in css, f"{name} styles drawer logout")


def run() -> None:
    ensure_user()
    audit_source_removal()
    audit_routes()
    audit_redirect_sanitizer()
    audit_logout()
    audit_service_worker_and_i18n()
    audit_css()
    print("Old homepage removal, logout reachability, persistent session, and i18n audit passed.")


if __name__ == "__main__":
    run()
