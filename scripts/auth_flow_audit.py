#!/usr/bin/env python3
"""Auth flow regression checks for reset/login messaging."""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("csrf token missing")
    return match.group(1)


def make_user(email: str, password: str, *, confirmed: bool) -> int:
    bot.init_db()
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE lower(email)=lower(?)", (email,))
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified, account_status, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (f"auth_audit_{secrets.token_hex(4)}", "Auth Audit", "Auth Audit", email, bot.generate_password_hash(password), 1 if confirmed else 0, now, now, now),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def main() -> None:
    confirmed_email = f"auth-confirmed-{secrets.token_hex(5)}@example.com"
    unconfirmed_email = f"auth-unconfirmed-{secrets.token_hex(5)}@example.com"
    password = "AuthAuditPass!123"
    make_user(confirmed_email, password, confirmed=True)
    make_user(unconfirmed_email, password, confirmed=False)

    client = bot.webhook_app.test_client()
    ok = client.post("/api/mobile/auth/login", json={"identifier": confirmed_email.upper(), "password": password})
    assert ok.status_code == 200, ok.get_data(as_text=True)

    bad = client.post("/api/mobile/auth/login", json={"identifier": confirmed_email, "password": "WrongPass!123"})
    assert bad.status_code == 401, bad.get_data(as_text=True)

    blocked = client.post("/api/mobile/auth/login", json={"identifier": unconfirmed_email, "password": password})
    data = blocked.get_json() or {}
    assert blocked.status_code == 403, blocked.get_data(as_text=True)
    assert data.get("error") == "email_not_confirmed", data
    assert "confirm your email" in (data.get("message") or "").lower(), data

    web_client = bot.webhook_app.test_client()
    page = web_client.get("/login").get_data(as_text=True)
    web_blocked = web_client.post("/login", data={"csrf_token": csrf(page), "email": unconfirmed_email, "password": password})
    body = web_blocked.get_data(as_text=True)
    assert web_blocked.status_code == 200, web_blocked.status_code
    assert "Please confirm your email before logging in." in body, body[:500]
    assert "Email or password is incorrect." not in body, body[:500]

    print("auth flow audit ok")


if __name__ == "__main__":
    main()
