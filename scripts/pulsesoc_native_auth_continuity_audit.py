#!/usr/bin/env python3
"""Guard canonical production identity and native session-continuity contracts."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    backend = read("bot.py")
    auth_api = read("mobile-native/src/api/auth.ts")
    pulse_api = read("mobile-native/src/api/pulseApi.ts")
    auth = read("mobile-native/src/session/auth.ts")
    store = read("mobile-native/src/session/sessionStore.ts")
    app = read("mobile-native/App.tsx")
    login = read("mobile-native/src/screens/LoginScreen.tsx")
    recovery = read("mobile-native/src/screens/AccountRecoveryScreen.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require('pulseApi<SessionResponse>("/api/mobile/auth/login"' in auth_api, "native login does not use the production mobile auth route")
    require("load_account_by_email_or_username" in backend and 'session["account_user_id"] = user["user_id"]' in backend, "production login does not bind the canonical user ID")
    require('"user_id": int(user.get("user_id") or 0)' in backend, "mobile session payload does not return the canonical user ID")
    require("Number(session.user.user_id || 0) <= 0" in auth, "native sign-in does not reject a missing canonical user ID")
    require("create_account(" not in auth, "native sign-in path directly creates a user")
    require("expo-secure-store" not in store or "SecureStore" in store, "native session storage is not Keychain-backed")
    require("AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY" in store, "Keychain credentials are not device-bound")
    require("native_automatic_refresh" in pulse_api and "/api/mobile/auth/refresh" in pulse_api, "automatic refresh and retry are missing")
    require("mobile_security_sessions" in backend and "refresh_token_hash" in backend, "server-side refresh sessions are not hashed and tracked")
    require("refresh_token_reuse" in backend and "device_mismatch" in backend, "refresh replay/device mismatch defenses are missing")
    require("getCachedSessionUser" in auth and "getSessionCookie" in auth, "offline cached-session startup is missing")
    require("requestPasswordRecovery" in recovery and "resendEmailConfirmation" in recovery, "password recovery or email confirmation UI is missing")
    require("signOutEverywhere" in settings and '"/api/mobile/auth/logout-all"' in auth_api, "logout-all control is missing")
    require("registerPushDevice" in app and 'authState.status !== "signedIn"' in app, "push-token registration is not triggered after sign-in")
    require("authenticatedRedirectTarget" in app and "pendingQaRedirectTarget" in app, "deep-link restoration after authentication is missing")
    require("same account" in login.lower() or "existing pulsesoc account" in login.lower(), "login screen does not explain existing-account continuity")
    require("password_hash" not in auth_api + auth + store, "native client introduced a password database contract")
    require("CREATE TABLE" not in auth_api + auth + store, "native client introduced a separate identity database")
    require("revoke_all_mobile_security_sessions" in backend, "authoritative all-device revocation is missing")
    require("account_login_restriction_message" in backend, "restricted/deleted account enforcement is missing")

    if failures:
        print("Native authentication continuity audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: native auth reuses canonical production identity, Keychain session storage, refresh rotation, recovery, logout, push, and deep-link contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
