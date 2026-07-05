#!/usr/bin/env python3
"""Audit the PulseSoc Native simulator-only authenticated QA path."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read(path: str) -> str:
    file_path = ROOT / path
    require(file_path.exists(), f"missing required file: {path}")
    return file_path.read_text(encoding="utf-8")


def require_terms(label: str, text: str, terms: list[str]) -> None:
    for term in terms:
        require(term in text, f"{label} missing required term: {term}")


def main() -> int:
    report = read("reports/pulsesoc_native_simulator_auth_qa_path.md")
    qa_auth = read("mobile-native/src/session/qaSimulatorAuth.ts")
    app = read("mobile-native/App.tsx")
    auth = read("mobile-native/src/session/auth.ts")
    progress = read("reports/pulsesoc_native_progress.md")
    camera_qa = read("reports/pulsesoc_native_camera_studio_ios_simulator_qa.md")
    bot = read("bot.py")

    require_terms(
        "simulator auth QA report",
        report,
        [
            "# PulseSoc Native Reliable Authenticated Simulator Input Path",
            "pulsesoc://qa/simulator-login",
            "__DEV__",
            "127.0.0.1",
            "localhost",
            "/api/mobile/auth/login",
            "Does not weaken production auth",
            "Do not move to Native LiveKit calls",
            "Status: implemented and simulator-verified for authenticated route access",
            "Feed/photo mode",
            "Reel/video mode",
            "Session restore passed",
            "Still not verified",
        ],
    )

    require_terms(
        "QA auth helper",
        qa_auth,
        [
            "isQaSimulatorAuthEnabled",
            "__DEV__",
            "Platform.OS !== \"web\"",
            "isLocalApiBaseUrl(PULSE_API_BASE_URL)",
            "parsed.hostname !== \"qa\"",
            "parsed.pathname !== \"/simulator-login\"",
            "signIn(identifier.trim(), password)",
            "cameraRouteFromQaUrl",
        ],
    )

    require_terms(
        "App QA hook",
        app,
        [
            "tryHandleQaSimulatorAuthUrl",
            "Linking.getInitialURL()",
            "Linking.addEventListener(\"url\"",
            "pendingQaCameraRoute",
            "navigationRef.navigate(\"CameraStudio\"",
        ],
    )

    require("/api/mobile/auth/login" in read("mobile-native/src/api/auth.ts"), "native signIn still uses existing mobile login API")
    require("setSessionCookie" in auth and "logout()" in auth, "native auth still uses existing session storage/logout")
    require("simulator-login" not in bot, "production backend must not expose simulator-login QA route")
    require("/api/mobile/auth/qa" not in bot, "production backend must not expose QA auth endpoint")
    require("Native Simulator Authenticated QA Path" in progress, "progress report must record simulator auth QA path")
    require("Native Camera Studio Authenticated Simulator QA Through QA Deep Link" in progress, "progress report must record authenticated simulator QA pass")
    require("touch/media automation" in progress, "progress report must name the remaining touch/media automation blocker")
    require("QA-only simulator deep link" in camera_qa, "Camera Studio simulator QA report must record QA auth path")
    require("Authenticated Simulator QA Through QA Deep Link" in camera_qa, "Camera Studio report must record rerun through QA deep link")
    require("Feed/photo mode" in camera_qa and "Reel/video mode" in camera_qa, "Camera Studio report must record destination verification")
    require("Gallery picker selection" in camera_qa, "Camera Studio report must keep gallery selection unverified")

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_camera_engine.js",
        "static/css/pulse_camera_engine.css",
    ]
    for path in forbidden_paths:
        file_path = ROOT / path
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        require("simulator-login" not in text, f"QA simulator auth leaked into production WebView path: {path}")

    print("PulseSoc native simulator authenticated QA path audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
