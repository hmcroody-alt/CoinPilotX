#!/usr/bin/env python3
"""Audit persistent PulseSoc login and language preference wiring."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8", errors="ignore")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"{label}: missing {needle!r}")


def main() -> int:
    bot = read("bot.py")
    dashboard_settings = read("services/dashboard_account_command_center.py")
    notification_service = read("services/notification_service.py")
    i18n = read("static/js/pulse_i18n.js")

    mobile_auth = read("mobile/services/auth.ts")
    mobile_store = read("mobile/store/authStore.ts")
    mobile_secure = read("mobile/services/secureSession.ts")
    rn_auth = read("mobile/pulse-react-native/services/auth.ts")
    rn_store = read("mobile/pulse-react-native/store/authStore.ts")
    rn_secure = read("mobile/pulse-react-native/services/secureSession.ts")

    checks = [
        (bot, "PERSISTENT_SESSION_COOKIE", "persistent refresh cookie constant"),
        (bot, "PERMANENT_SESSION_LIFETIME=timedelta(days=PERSISTENT_SESSION_DAYS)", "long-lived Flask session config"),
        (bot, "set_persistent_session_cookie", "httpOnly persistent cookie setter"),
        (bot, "httponly=True", "httpOnly refresh cookie"),
        (bot, "secure=COINPILOTX_SESSION_COOKIE_SECURE", "secure refresh cookie flag"),
        (bot, "rotate_mobile_refresh_token", "refresh token rotation"),
        (bot, "refresh_token_reuse", "refresh token reuse detection"),
        (bot, "device_mismatch", "suspicious device mismatch detection"),
        (bot, "revoke_all_mobile_security_sessions", "sign out all devices backend support"),
        (bot, "/api/account/sessions/revoke-all", "sign out all devices API route"),
        (bot, "preferred_language TEXT DEFAULT 'en'", "durable preferred language schema"),
        (bot, "api_account_language", "language preference API"),
        (bot, "pulse_i18n.js", "global i18n script injection"),
        (bot, "preferred_language_for_user", "notification language lookup"),
        (dashboard_settings, "UPDATE users SET preferred_language", "settings language sync to users"),
        (notification_service, "_preferred_language_for_user", "central notification language metadata"),
        (i18n, "PulseI18n", "global i18n bridge"),
        (i18n, "api/account/language", "i18n server preference fetch/save"),
        (i18n, "api/i18n/missing", "missing translation logging"),
        (mobile_auth, "refreshMobileSession(refreshToken", "mobile refresh sends stored token"),
        (mobile_auth, "logoutMobileSession(refreshToken", "mobile logout revokes stored token"),
        (mobile_store, "getPreferredLanguage", "mobile auth sends cached language"),
        (mobile_store, "setRefreshToken(session.refresh_token)", "mobile always stores refresh token"),
        (mobile_secure, "PREFERRED_LANGUAGE_KEY", "mobile language SecureStore cache"),
        (rn_auth, "refreshMobileSession(refreshToken", "RN refresh sends stored token"),
        (rn_auth, "logoutMobileSession(refreshToken", "RN logout revokes stored token"),
        (rn_store, "getPreferredLanguage", "RN auth sends cached language"),
        (rn_store, "setRefreshToken(session.refresh_token)", "RN always stores refresh token"),
        (rn_secure, "PREFERRED_LANGUAGE_KEY", "RN language SecureStore cache"),
    ]

    for text, needle, label in checks:
        require(text, needle, label)

    forbidden_mobile_patterns = [
        "if (remember && session.refresh_token) await setRefreshToken(session.refresh_token)",
        "refreshMobileSession();",
        "logoutMobileSession().catch",
    ]
    combined_mobile = "\n".join([mobile_auth, mobile_store, rn_auth, rn_store])
    for pattern in forbidden_mobile_patterns:
        if pattern in combined_mobile:
            raise AssertionError(f"mobile persistence regression: found {pattern!r}")

    print("Persistent login and language preference audit passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"Persistent login/language audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
