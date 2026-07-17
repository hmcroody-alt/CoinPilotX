#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    pulse_api = read("mobile-native/src/api/pulseApi.ts")
    session_store = read("mobile-native/src/session/sessionStore.ts")
    backend = read("bot.py")

    require("Promise.all([getSessionCookie(), getSessionEnvelope()])" in pulse_api, "native requests must load cookie and session envelope together", failures)
    require("envelope?.refreshToken && (!envelope.accessToken || envelope.accessTokenExpiresAt <= Date.now() + 5000)" in pulse_api, "native must proactively refresh missing or near-expiry access tokens", failures)
    require("[cookie, envelope] = await Promise.all([getSessionCookie(), getSessionEnvelope()])" in pulse_api, "native must reload credentials after proactive refresh", failures)
    require('headers.set("Authorization", `Bearer ${envelope.accessToken}`)' in pulse_api, "native requests must send the existing mobile access token as Bearer auth", failures)
    require("envelope.accessTokenExpiresAt > Date.now() + 5000" in pulse_api, "native must not send expired access tokens", failures)
    require("refreshNativeSession(cookie || \"\")" in pulse_api, "native refresh recovery must remain available after 401", failures)

    require("accessToken: string;" in session_store, "session envelope must retain access token", failures)
    require("refreshToken: string;" in session_store, "session envelope must retain refresh token", failures)

    require("def account_user_id_from_mobile_access_token():" in backend, "backend must resolve account identity from mobile access tokens", failures)
    require('request.headers.get("Authorization")' in backend, "backend must inspect Authorization header", failures)
    require("hmac.compare_digest(signature, expected_signature)" in backend, "backend must verify mobile access token signature", failures)
    require("mobile_token_hash(access_token)" in backend, "backend must verify the access token against mobile_security_sessions", failures)
    require("COALESCE(access_expires_at,'')>=?" in backend, "backend must reject expired persisted access-token rows", failures)
    require("session.get(\"account_user_id\") or account_user_id_from_mobile_access_token() or restore_account_from_persistent_cookie()" in backend, "access-token auth must precede persistent-cookie refresh fallback", failures)

    if failures:
        print("PulseSoc native auth immediate-logout audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native auth immediate-logout audit passed.")
    print("Validated native Bearer access-token reuse, backend token verification, and refresh-cookie fallback ordering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
