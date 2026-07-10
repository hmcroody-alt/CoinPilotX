#!/usr/bin/env python3
"""Audit the native simulator QA auth bootstrap guardrails."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(f"Missing {label}: {needle}")


def main() -> int:
    failures: list[str] = []
    qa_auth = ROOT / "mobile-native" / "src" / "session" / "qaSimulatorAuth.ts"
    login = ROOT / "mobile-native" / "src" / "screens" / "LoginScreen.tsx"

    qa_text = qa_auth.read_text()
    login_text = login.read_text()

    require(qa_text, "EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN", "explicit simulator auto-login env gate", failures)
    require(qa_text, "isQaSimulatorAuthEnabled() &&", "local API + dev gate composition", failures)
    require(qa_text, "api/mobile/auth/register", "existing mobile auth registration endpoint", failures)
    require(qa_text, "native_simulator_qa_auto_login", "QA-only source marker", failures)
    require(qa_text, "setSessionCookie", "existing session cookie persistence", failures)
    require(qa_text, "password", "runtime password payload", failures)
    require(login_text, "createQaSimulatorLocalSession", "LoginScreen simulator bootstrap integration", failures)
    require(login_text, "tryHandleQaSimulatorAuthUrl", "LoginScreen QA deep-link listener", failures)
    require(login_text, 'testID="login-identifier"', "login identifier QA selector", failures)
    require(login_text, 'testID="login-password"', "login password QA selector", failures)
    require(login_text, 'testID="login-submit"', "login submit QA selector", failures)

    forbidden = [
        "QA!2026",
        "SellerInventoryQA",
        "OwnerNativeQA",
        "password was exposed",
    ]
    for needle in forbidden:
        if needle in qa_text or needle in login_text:
            failures.append(f"Forbidden credential-like text found: {needle}")

    if failures:
        print("PulseSoc native simulator QA auth audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native simulator QA auth audit passed")
    print("- dev-only, local-API-only, explicit-env simulator auth bootstrap is present")
    print("- existing mobile auth registration and session-cookie paths are reused")
    print("- login screen has QA-addressable selectors and consumes QA auth URLs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
