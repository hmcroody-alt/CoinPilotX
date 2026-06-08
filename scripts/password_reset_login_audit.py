#!/usr/bin/env python3
"""Verify reset passwords are accepted by web and mobile login."""

from __future__ import annotations

import re
import secrets
import sys
from datetime import timedelta
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


def make_user(email: str, password: str, *, confirmed: bool = True) -> int:
    bot.init_db()
    now = bot.datetime.now().isoformat()
    username = f"audit_{secrets.token_hex(4)}"
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE lower(email)=lower(?)", (email,))
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified, account_status, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (username, "Password Reset Audit", "Password Reset Audit", email, bot.generate_password_hash(password), 1 if confirmed else 0, now, now, now),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def latest_reset_token(user_id: int) -> str:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT token FROM password_reset_tokens WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise AssertionError("reset token was not created")
    return row[0]


def assert_mobile_login(client, email: str, password: str, expected_status: int, expected_auth: bool = False):
    res = client.post("/api/mobile/auth/login", json={"identifier": email, "password": password})
    assert res.status_code == expected_status, (res.status_code, res.get_data(as_text=True))
    data = res.get_json() or {}
    assert bool(data.get("authenticated")) is expected_auth, data
    return data


def main() -> None:
    email = f"reset-audit-{secrets.token_hex(5)}@example.com"
    old_password = "OldAuditPass!123"
    new_password = "NewAuditPass!456"
    user_id = make_user(email, old_password, confirmed=True)

    user, created_token = bot.create_password_reset(email)
    assert user and int(user["user_id"]) == user_id, "reset token created for wrong user"
    token = created_token or latest_reset_token(user_id)

    client = bot.webhook_app.test_client()
    reset = client.post("/api/mobile/auth/reset-password", json={"token": token, "password": new_password})
    assert reset.status_code == 200, reset.get_data(as_text=True)

    reused = client.post("/api/mobile/auth/reset-password", json={"token": token, "password": "ReusePass!789"})
    assert reused.status_code == 400, reused.get_data(as_text=True)

    assert_mobile_login(client, email, old_password, 401, False)
    assert_mobile_login(client, email, new_password, 200, True)

    # Web login must accept the same saved hash.
    web_client = bot.webhook_app.test_client()
    login_page = web_client.get("/login").get_data(as_text=True)
    res = web_client.post("/login", data={"csrf_token": csrf(login_page), "email": email, "password": new_password})
    assert res.status_code in {302, 303}, (res.status_code, res.get_data(as_text=True)[:500])

    # Web reset path must also save a login-valid hash.
    second_email = f"reset-web-audit-{secrets.token_hex(5)}@example.com"
    second_user_id = make_user(second_email, old_password, confirmed=True)
    _, web_token = bot.create_password_reset(second_email)
    reset_page = web_client.get(f"/reset-password/{web_token}").get_data(as_text=True)
    web_reset = web_client.post(
        f"/reset-password/{web_token}",
        data={"csrf_token": csrf(reset_page), "token": web_token, "password": new_password},
        follow_redirects=False,
    )
    assert web_reset.status_code in {302, 303}, web_reset.get_data(as_text=True)[:500]
    assert second_user_id > 0
    assert_mobile_login(bot.webhook_app.test_client(), second_email, new_password, 200, True)

    # Expired tokens fail safely.
    expired_email = f"reset-expired-audit-{secrets.token_hex(5)}@example.com"
    expired_user_id = make_user(expired_email, old_password, confirmed=True)
    expired_token = secrets.token_urlsafe(32)
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO password_reset_tokens (user_id, token, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (expired_user_id, expired_token, (bot.datetime.now() - timedelta(minutes=1)).isoformat(), bot.datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    expired = client.post("/api/mobile/auth/reset-password", json={"token": expired_token, "password": new_password})
    assert expired.status_code == 400, expired.get_data(as_text=True)

    print("password reset login audit ok")


if __name__ == "__main__":
    main()
