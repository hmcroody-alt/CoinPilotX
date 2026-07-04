#!/usr/bin/env python3
"""Static audit for PulseSoc native Live viewer QA and hardening."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    screen = read("mobile-native/src/screens/LiveScreen.tsx")
    api = read("mobile-native/src/api/live.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    report = read("reports/pulsesoc_native_live_device_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in (
        "AppState",
        "playbackFailed",
        "setPlaybackFailed(true)",
        "navigateToHostProfile",
        "refreshLiveState(selected.id, \"manual\")",
        "listLiveChat(selected.id).then(setMessages)",
        "Open Live Web Viewer",
        "Go Live Web",
    ):
        require(token in screen, f"LiveScreen missing QA hardening token: {token}")

    for token in (
        "/api/pulse/live-now",
        "/api/pulse/live/${liveId}/state",
        "/api/pulse/live/${liveId}/join",
        "/api/pulse/live/${liveId}/chat",
        "/api/pulse/live/${liveId}/react",
    ):
        require(token in api, f"Live API wrapper must reuse existing backend route: {token}")

    for token in (
        "live_studio_web_fallback",
        "LiveDetail",
        "extractNumericQueryValue(normalized, \"live\")",
    ):
        require(token in routing, f"Live notification/deep-link routing missing: {token}")

    for forbidden in (
        "livekit/token",
        "browser-publish",
        "cohost/request",
        "/api/pulse/live/start",
    ):
        require(forbidden not in api, f"Native Live viewer QA must not add host/call flow: {forbidden}")

    for phrase in (
        "Real-device/simulator QA was not completed",
        "xcrun: error: unable to find utility \"simctl\"",
        "adb",
        "Hardening Completed",
        "foreground/background recovery",
        "playbackFailed",
        "Native Premium + Entitlements Foundation",
    ):
        require(phrase in report, f"Live device QA report missing required detail: {phrase}")

    for phrase in (
        "Live Viewer Device QA + Hardening",
        "reports/pulsesoc_native_live_device_qa.md",
        "scripts/pulsesoc_native_live_device_qa_audit.py",
        "Native Premium + Entitlements Foundation",
        "Risk: Medium.",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"Master native progress missing Live QA checkpoint or next recommendation: {phrase}")

    for path in ("templates", "static/js", "static/css", "mobile/pulse-react-native"):
        require(
            not any((ROOT / path).glob("**/*pulsesoc_native_live_device*")),
            f"Live device QA must not create production WebView artifacts under {path}",
        )

    print("PulseSoc native Live viewer device QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
