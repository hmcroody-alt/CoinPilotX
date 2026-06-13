#!/usr/bin/env python3
"""Audit Brevo IP-block account delivery recovery paths."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
EMAIL_SERVICE = (ROOT / "services/email_service.py").read_text(encoding="utf-8")
MOBILE_SCREEN = (ROOT / "mobile/pulse-react-native/screens/auth/EmailConfirmationPendingScreen.tsx").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    timestamp = datetime.now(UTC).timestamp()
    email = f"brevo-audit-{timestamp}@example.com"
    new_email = f"brevo-audit-new-{timestamp}@example.com"

    original_welcome = bot.send_signup_welcome_emails
    original_confirmation = bot.send_account_confirmation_email
    bot.send_signup_welcome_emails = lambda user: {"new_user": False}
    bot.send_account_confirmation_email = lambda user, source="signup": {
        "ok": False,
        "trace_id": "BREVOAUDIT",
        "message": "Brevo rejected the request because the Railway server IP is not authorized in Brevo.",
    }
    try:
        client = bot.webhook_app.test_client()
        response = client.post("/api/mobile/auth/register", json={
            "full_name": "Brevo Audit",
            "username": f"brevoaudit{int(timestamp)}",
            "email": email,
            "password": "CorrectHorse123!",
            "age_confirmed": True,
            "email_opt_in": True,
        })
        data = response.get_json() or {}
        require(response.status_code == 200, "mobile signup returns success when Brevo blocks verification delivery")
        require(data.get("ok") is True and data.get("requires_email_confirmation") is True, "signup stays pending confirmation")
        require(data.get("email_delivery_failed") is True, "signup response flags email delivery failure")
        require("Account created successfully but verification email could not be delivered" in data.get("message", ""), "signup response uses required delivery-failed copy")

        duplicate = client.post("/api/mobile/auth/register", json={
            "full_name": "Brevo Audit Duplicate",
            "username": f"brevoauditdup{int(timestamp)}",
            "email": email,
            "password": "CorrectHorse123!",
            "age_confirmed": True,
            "email_opt_in": True,
        })
        duplicate_data = duplicate.get_json() or {}
        require(duplicate.status_code == 200, "duplicate unverified signup does not return account-already-exists error")
        require("Account created successfully but verification email could not be delivered" in duplicate_data.get("message", ""), "duplicate unverified signup shows delivery recovery copy")

        change_bad_password = client.post("/api/mobile/auth/change-confirmation-email", json={
            "old_email": email,
            "new_email": new_email,
            "password": "wrong-password",
        })
        require(change_bad_password.status_code == 401, "change email requires current account password")

        change = client.post("/api/mobile/auth/change-confirmation-email", json={
            "old_email": email,
            "new_email": new_email,
            "password": "CorrectHorse123!",
        })
        change_data = change.get_json() or {}
        require(change.status_code == 202, "change email succeeds even when follow-up delivery is blocked")
        require(change_data.get("ok") is False, "change email reports delivery still blocked for resend visibility")
        conn = db_service.connect()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT email, email_verified FROM users WHERE lower(email)=lower(?)", (new_email,))
        changed_user = dict(cur.fetchone() or {})
        conn.close()
        require(changed_user.get("email") == new_email and int(changed_user.get("email_verified") or 0) == 0, "unverified account email is updated and remains unverified")
    finally:
        bot.send_signup_welcome_emails = original_welcome
        bot.send_account_confirmation_email = original_confirmation

    require("BREVO_FROM_EMAIL" in EMAIL_SERVICE and "BREVO_SENDER" in EMAIL_SERVICE, "Brevo sender aliases are supported")
    require("brevo_unauthorized_ip" in EMAIL_SERVICE and "ip blocked" in EMAIL_SERVICE.lower(), "Brevo IP-block errors are classified")
    require("/admin/emails/resend-welcome" in BOT and "Resend Welcome Email" in BOT, "admin can resend welcome emails")
    require("Resend Verification Email" in BOT, "admin can resend verification emails")
    require("/api/admin/email/outbound-ip" in BOT and "Check Railway Outbound IP" in BOT, "admin can inspect runtime outbound IP")
    require("Blocked Emails" in BOT and "Pending Verification Emails" in BOT and "Successful Deliveries" in BOT, "admin dashboard shows required email metrics")
    require("/api/mobile/auth/change-confirmation-email" in BOT, "mobile change confirmation email endpoint exists")
    require("Change Email Address" in MOBILE_SCREEN, "mobile pending confirmation screen exposes change email action")
    print("brevo ip block delivery audit ok")


if __name__ == "__main__":
    main()
