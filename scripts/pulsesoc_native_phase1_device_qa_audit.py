#!/usr/bin/env python3
"""Audit the Phase 1 native device QA report and reuse-first guardrails."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pulsesoc_native_phase1_device_qa.md"
NATIVE = ROOT / "mobile-native"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    report = REPORT.read_text(encoding="utf-8")
    package = read("mobile-native/package.json")
    lockfile = read("mobile-native/package-lock.json")
    app_json = read("mobile-native/app.json")
    config = read("mobile-native/src/api/config.ts")
    pulse_api = read("mobile-native/src/api/pulse.ts")
    auth_api = read("mobile-native/src/api/auth.ts")
    push_api = read("mobile-native/src/api/push.ts")
    foundation_audit = read("scripts/pulsesoc_native_app_foundation_audit.py")

    require("Reuse-First Migration Rule" in report, "report includes reuse-first migration rule")
    for phrase in [
        "new client for the existing PulseSoc platform",
        "Do not copy DOM, HTML, CSS, or browser-only code directly",
        "Rebuild only the native UI/device layer",
        "Inspect the existing web/backend implementation",
    ]:
        require(phrase in report, f"report includes reuse guardrail: {phrase}")

    for test_name in [
        "App opens",
        "API base URL loads correctly",
        "Login screen works",
        "Signup screen works",
        "Session restore after close/reopen",
        "Logout works",
        "Denied push permission does not break app",
        "Accepted push permission registers safely",
        "Mission Control loads",
        "Messenger list loads",
        "Basic chat send works",
        "Pulse AI chat works",
        "Profile loads",
        "Settings loads",
    ]:
        require(test_name in report, f"report includes QA matrix item: {test_name}")

    require("simctl" in report and "adb" in report, "report records simulator/device tooling status")
    require("Not verified on device/simulator" in report, "report honestly marks blocked device checks")
    require("No production WebView/mobile shell paths were changed" in report, "report records WebView production safety")
    require("https://pulsesoc.com" in report and "pulseApiBaseUrl" in report, "report records API base URL evidence")
    require('{"authenticated":false,"ok":true,"user":null}' in report, "report records safe logged-out session response")

    require('"expo-status-bar"' in package and '"expo-status-bar"' in lockfile, "status bar dependency remains locked")
    require("com.pulsesoc.nativeapp" in app_json, "valid native app bundle/package id is retained")
    require("normalizeApiBaseUrl" in config and "https://pulsesoc.com" in config, "API base URL normalization remains in source")
    require("Push permission was not granted." in push_api and "Push registration requires a physical device." in push_api, "push denial/no-device fallbacks remain in source")
    require("catch (error)" in push_api and "ok: false" in push_api, "push registration catches failures safely")

    for route in [
        "/api/mobile/auth/session",
        "/api/mobile/auth/login",
        "/api/mobile/auth/register",
        "/api/mobile/auth/logout",
    ]:
        require(route in auth_api, f"auth API wrapper reuses existing route {route}")

    for route in [
        "/api/dashboard/mission-control",
        "/api/pulse/messages/conversations",
        "/api/pulse/assistant/chat",
        "/api/pulse/profile/me",
    ]:
        require(route in pulse_api, f"Pulse API wrapper reuses existing route {route}")

    implementation_files = [
        NATIVE / "App.tsx",
        NATIVE / "index.ts",
        NATIVE / "app.json",
        NATIVE / "package.json",
        *sorted((NATIVE / "src").rglob("*.ts")),
        *sorted((NATIVE / "src").rglob("*.tsx")),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in implementation_files)
    require("WebView" not in combined and "react-native-webview" not in combined.lower(), "native implementation does not use WebView")
    require("package-lock.json" in foundation_audit, "foundation audit validates lockfile")

    print("PulseSoc native phase 1 device QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
