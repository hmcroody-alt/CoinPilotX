#!/usr/bin/env python3
"""Verify App Store signup works without collecting or requiring a phone."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


NATIVE_IOS_UA = "PulseSocNativeApp/1.0 (ios; com.pulsesoc.app)"


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    client = bot.webhook_app.test_client()
    response = client.get("/signup", headers={"User-Agent": NATIVE_IOS_UA})
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "native iOS signup page loads")
    require('name="email"' in html and 'name="email" type="email" autocomplete="email" placeholder="Email address" required' in html, "email is the explicit signup identifier")
    require('name="phone"' not in html, "signup does not collect a phone number")
    require('name="sms_opt_in"' not in html, "signup does not collect SMS consent")
    require("A phone number is not required" in html, "signup explains phone is optional after registration")

    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    require(bool(csrf_match), "signup exposes CSRF protection")

    captured = {}

    def fake_create_account(full_name, email, password, phone="", country="", email_opt_in=False, sms_opt_in=False, username="", age_confirmed=False):
        captured.update({
            "email": email,
            "phone": phone,
            "sms_opt_in": sms_opt_in,
            "username": username,
            "age_confirmed": age_confirmed,
        })
        return {
            "user_id": 991827,
            "email": email,
            "username": username,
            "full_name": full_name,
        }, ""

    with (
        patch.object(bot, "create_account", side_effect=fake_create_account),
        patch.object(bot, "send_signup_welcome_emails", return_value={}),
        patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "audit"}),
    ):
        submitted = client.post(
            "/signup",
            headers={"User-Agent": NATIVE_IOS_UA},
            data={
                "csrf_token": csrf_match.group(1),
                "full_name": "App Review Signup Audit",
                "username": "appreviewaudit",
                "email": "app-review-signup-audit@example.com",
                "password": "ReviewSafe123!",
                "country": "United States",
                "age_confirmed": "on",
                "terms_accepted": "on",
            },
        )

    require(submitted.status_code == 200, "email signup without phone completes")
    require(captured.get("email") == "app-review-signup-audit@example.com", "signup keeps the supplied email")
    require(captured.get("phone") == "", "signup passes an empty optional phone")
    require(captured.get("sms_opt_in") is False, "signup leaves SMS disabled")
    require("Account created. Check your email" in submitted.get_data(as_text=True), "signup reaches email confirmation state")

    api_captured = {}

    def fake_api_create(*args, **kwargs):
        api_captured["phone"] = args[3] if len(args) > 3 else kwargs.get("phone", "")
        return {"user_id": 991828, "email": args[1], "username": args[7]}, ""

    with (
        patch.object(bot, "create_account", side_effect=fake_api_create),
        patch.object(bot, "send_signup_welcome_emails", return_value={}),
        patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "audit"}),
    ):
        api_response = client.post(
            "/api/mobile/auth/register",
            headers={"User-Agent": NATIVE_IOS_UA},
            json={
                "full_name": "Native Signup Audit",
                "username": "nativesignupaudit",
                "email": "native-signup-audit@example.com",
                "password": "ReviewSafe123!",
                "age_confirmed": True,
            },
        )

    require(api_response.status_code == 200, "native signup API accepts no phone field")
    require(api_captured.get("phone") == "", "native signup API treats omitted phone as empty")
    require((api_response.get_json() or {}).get("requires_email_confirmation") is True, "native signup API reaches email confirmation")
    print("app_store_optional_phone_signup_audit: PASS")


if __name__ == "__main__":
    main()
