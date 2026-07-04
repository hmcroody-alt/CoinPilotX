#!/usr/bin/env python3
"""Audit PulseSoc Native Camera Studio iOS simulator QA documentation."""

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
    report = read("reports/pulsesoc_native_camera_studio_ios_simulator_qa.md")
    device_report = read("reports/pulsesoc_native_camera_studio_device_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    app = read("mobile-native/App.tsx")
    camera_screen = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")

    require_terms(
        "iOS simulator QA report",
        report,
        [
            "# PulseSoc Native Camera Studio iOS Simulator QA",
            "com.pulsesoc.nativeapp",
            "iPhone 17 Pro",
            "7B3BEEBC-6135-497D-91CD-A3E70C927D56",
            "iOS 26.5",
            "npx expo run:ios --device 7B3BEEBC-6135-497D-91CD-A3E70C927D56 --no-bundler",
            "Build Succeeded",
            "17/17 checks passed",
            "Signed-out Camera Studio deep link",
            "Scoped Blocker Fixed",
            "route-mismatch warning",
            "authState.status === \"signedIn\"",
            "Physical Device QA",
            "Do not move to Native LiveKit calls yet",
            "authenticated Camera Studio",
        ],
    )

    require_terms(
        "device QA report",
        device_report,
        [
            "com.pulsesoc.nativeapp",
            "Auth-gate relaunch verified",
            "installed development build",
            "authenticated Camera Studio",
            "physical iPhone",
            "physical Android",
            "Do not move to Native LiveKit calls yet",
        ],
    )

    require_terms(
        "progress report",
        progress,
        [
            "Native Camera Studio iOS Simulator QA Through Installed Dev Build",
            "QA-safe authenticated PulseSoc credentials",
            "physical iPhone and Android Camera Studio QA",
            "before moving to Native LiveKit calls",
            "Signed-out Camera Studio deep links",
        ],
    )

    require('linking={authState.status === "signedIn" ? linking : undefined}' in app, "protected linking is gated by signed-in auth state")
    require("CameraStudio" in linking and "pulse/camera/:mode?" in linking, "camera deep-link route remains registered for signed-in users")
    require("CameraView" in camera_screen and "useCameraPermissions" in camera_screen, "Camera Studio still uses native camera primitives")
    require("useMicrophonePermissions" in camera_screen, "Camera Studio still uses microphone permissions")
    require("chooseFromGallery" in camera_screen, "Camera Studio still exposes gallery fallback")

    forbidden = [
        "bot.py",
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_camera_engine.js",
        "static/css/pulse_camera_engine.css",
    ]
    for path in forbidden:
        file_path = ROOT / path
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        require(
            "PulseSoc Native Camera Studio iOS Simulator QA" not in text,
            f"native iOS simulator QA report leaked into production WebView path: {path}",
        )

    print("PulseSoc native Camera Studio iOS simulator QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
