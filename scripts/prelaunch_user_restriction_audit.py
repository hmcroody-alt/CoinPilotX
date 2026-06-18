#!/usr/bin/env python3
"""Audit the owner-only pre-launch User 20 restriction workflow."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import bot  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


FAILURES: list[str] = []


def require(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}")
        FAILURES.append(label)


def row_count(cur: sqlite3.Cursor, table: str, user_col: str, user_id: int) -> int:
    if not bot.table_exists(cur, table):
        return 0
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {user_col}=?", (user_id,))
    return int((cur.fetchone() or [0])[0] or 0)


def main() -> int:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("restricted login message is exact", bot.PRELAUNCH_RESTRICTED_LOGIN_MESSAGE in source)
    require("prelaunch endpoint is owner-only", "/api/admin/users/<int:user_id>/prelaunch-restrict" in source and "require_owner_api()" in source)
    require("workflow verifies expected email", "Expected email does not match the account record." in source)
    require("workflow blocks protected accounts", "prelaunch_restriction_protected_account_reasons" in source)
    require("workflow sends exact email subject", "PulseSoc Early Access Update" in source)
    require("workflow verifies delivery before restricting", "delivery_verified" in source and "Account was not restricted" in source)
    require("access flags are migrated", "(\"access_enabled\", \"INTEGER DEFAULT 1\")" in source and "(\"login_enabled\", \"INTEGER DEFAULT 1\")" in source)

    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=20 LIMIT 1")
    if cur.fetchone():
        conn.close()
        raise SystemExit("Refusing to run fixture audit because local User ID 20 already exists.")

    now = datetime.utcnow().isoformat(timespec="seconds")
    owner_email = "owner-prelaunch-audit@example.test"
    user_email = "nathannorth2005@gmail.com"
    cur.execute("DELETE FROM admin_users WHERE email=?", (owner_email,))
    cur.execute(
        "INSERT INTO admin_users (full_name, email, password_hash, role, status, created_at, updated_at) VALUES (?, ?, ?, 'owner', 'active', ?, ?)",
        ("Owner Audit", owner_email, generate_password_hash("OwnerAudit!23456"), now, now),
    )
    admin_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO users
        (user_id, username, display_name, full_name, email, password_hash, email_verified,
         account_status, access_enabled, login_enabled, is_super_user, plan, subscription_plan,
         subscription_status, is_pro, created_at, updated_at)
        VALUES (20, 'Nathan', 'Nathan', 'Nathan', ?, ?, 1, 'active', 1, 1, 0, 'pro', 'pro', 'trialing', 1, ?, ?)
        """,
        (user_email, generate_password_hash("NathanAudit!23456"), now, now),
    )
    content_before = {
        "posts": row_count(cur, "pulse_posts", "user_id", 20),
        "media": row_count(cur, "chat_media_uploads", "uploader_user_id", 20),
        "messages": row_count(cur, "pulse_messages", "sender_user_id", 20),
    }
    conn.commit()
    conn.close()

    original_send = bot.email_service_service.send_email

    def fake_send_email(to_email, subject, html_body, **kwargs):
        return {
            "ok": True,
            "status_code": 201,
            "message_id": "<prelaunch-audit-message@smtp-relay.mailin.fr>",
            "response": {"messageId": "<prelaunch-audit-message@smtp-relay.mailin.fr>"},
        }

    bot.email_service_service.send_email = fake_send_email
    try:
        client = bot.webhook_app.test_client()
        with client.session_transaction() as session:
            session["admin_user_id"] = admin_id
        response = client.post(
            "/api/admin/users/20/prelaunch-restrict",
            json={"expected_email": user_email, "confirm": "RESTRICT_USER_20_PRELAUNCH"},
        )
        data = response.get_json() or {}
        require("restriction endpoint succeeds after verified email", response.status_code == 200 and data.get("ok") is True)
        require("provider message id is returned", data.get("provider_message_id") == "<prelaunch-audit-message@smtp-relay.mailin.fr>")
        require("trace id is returned", bool(data.get("trace_id")))
    finally:
        bot.email_service_service.send_email = original_send

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT account_status, access_enabled, login_enabled, restricted_reason FROM users WHERE user_id=20")
    user = dict(cur.fetchone() or {})
    require("account status is restricted", user.get("account_status") == "restricted")
    require("access and login are disabled", int(user.get("access_enabled") or 0) == 0 and int(user.get("login_enabled") or 0) == 0)
    require("restriction reason is preserved", user.get("restricted_reason") == "Pre-launch access restriction")
    cur.execute("SELECT status, provider_message_id, trace_id FROM email_logs WHERE user_id=20 AND email_type='prelaunch_access_restriction' ORDER BY id DESC LIMIT 1")
    email_log = dict(cur.fetchone() or {})
    require("email log is written", email_log.get("status") == "sent_brevo" and bool(email_log.get("trace_id")))
    cur.execute("SELECT action, details FROM admin_user_actions WHERE target_user_id=20 ORDER BY id DESC LIMIT 5")
    actions = [dict(row) for row in cur.fetchall()]
    require("audit action is written", any(row.get("action") == "Restrict Account" for row in actions))
    content_after = {
        "posts": row_count(cur, "pulse_posts", "user_id", 20),
        "media": row_count(cur, "chat_media_uploads", "uploader_user_id", 20),
        "messages": row_count(cur, "pulse_messages", "sender_user_id", 20),
    }
    require("content counts are preserved", content_before == content_after)

    login_client = bot.webhook_app.test_client()
    with login_client.session_transaction() as session:
        session["csrf_token"] = "audit-csrf"
    login = login_client.post("/login", data={"csrf_token": "audit-csrf", "email": user_email, "password": "NathanAudit!23456"})
    require("restricted web login returns 403", login.status_code == 403 and bot.PRELAUNCH_RESTRICTED_LOGIN_MESSAGE in login.get_data(as_text=True))
    mobile = login_client.post("/api/mobile/auth/login", json={"email": user_email, "password": "NathanAudit!23456"})
    require("restricted mobile login returns 403", mobile.status_code == 403 and (mobile.get_json() or {}).get("message") == bot.PRELAUNCH_RESTRICTED_LOGIN_MESSAGE)
    with bot.webhook_app.test_request_context("/admin/users?filter=restricted"):
        payload = bot.admin_users_payload()
    require("restricted filter contains user", any(int(row.get("user_id") or 0) == 20 for row in payload.get("users") or []))

    cur.execute("DELETE FROM email_logs WHERE user_id=20 AND email_type='prelaunch_access_restriction'")
    cur.execute("DELETE FROM admin_user_actions WHERE target_user_id=20")
    cur.execute("DELETE FROM admin_audit_logs WHERE target_type='user' AND target_id='20' AND action LIKE 'prelaunch_%'")
    cur.execute("DELETE FROM admin_activity_logs WHERE target_type='user' AND target_id='20' AND action LIKE 'prelaunch_%'")
    cur.execute("DELETE FROM users WHERE user_id=20")
    cur.execute("DELETE FROM admin_users WHERE id=?", (admin_id,))
    conn.commit()
    conn.close()

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("prelaunch user restriction audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
