#!/usr/bin/env python3
"""Static audit for the PulseSoc native Live viewer foundation."""

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
    api = read("mobile-native/src/api/live.ts")
    screen = read("mobile-native/src/screens/LiveScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    report = read("reports/pulsesoc_native_live_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in (
        "/api/pulse/live-now",
        "/api/pulse/live/${liveId}/state",
        "/api/pulse/live/${liveId}/join",
        "/api/pulse/live/${liveId}/chat",
        "/api/pulse/live/${liveId}/react",
        "readJsonCache",
        "writeJsonCache",
        "openLiveWebFallback",
    ):
        require(token in api, f"Live API wrapper missing existing backend contract or shared cache token: {token}")

    for token in (
        "LiveScreen",
        "listLiveNow",
        "getLiveState",
        "joinLive",
        "sendLiveChat",
        "reactToLive",
        "Video",
        "Open Live Web Viewer",
        "Go Live Web",
        "Scheduled Live/events will appear here when the existing API returns them to native.",
    ):
        require(token in screen, f"Live screen missing required viewer/discovery behavior: {token}")

    require("Tabs.Screen name=\"Live\"" in app_nav, "Live tab must be registered")
    require("Stack.Screen name=\"LiveDetail\"" in app_nav, "Live detail route must be registered")
    require("Live: undefined" in types, "Live tab type missing")
    require("LiveDetail: { liveId: number" in types, "Live detail route type missing")
    require("Live: \"pulse/live\"" in linking, "Live deep link config missing")
    require("path: \"pulse/live/:liveId\"" in linking, "Live detail deep link config missing")

    for token in (
        "live_studio_web_fallback",
        "LiveDetail",
        "extractNumericQueryValue(normalized, \"live\")",
        "screen: \"Live\"",
    ):
        require(token in routing, f"Notification/deep-link routing missing Live support: {token}")

    for phrase in (
        "does not build native Go Live",
        "safe web fallback",
        "viewer-leave endpoint was not found",
        "Device-Only Behavior Not Verified",
        "Native Live Viewer Device QA + Hardening",
    ):
        require(phrase in report, f"Live progress report missing required honesty/scope phrase: {phrase}")

    for phrase in (
        "Live Discovery + Live Viewer Foundation",
        "reports/pulsesoc_native_live_progress.md",
        "scripts/pulsesoc_native_live_audit.py",
        "Native Live Viewer Device QA + Hardening",
        "Risk: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"Master native progress report missing Live completion/next recommendation: {phrase}")

    production_paths = [
        "templates",
        "static/js",
        "static/css",
        "mobile/pulse-react-native",
    ]
    for path in production_paths:
        require(not any((ROOT / path).glob("**/*pulsesoc_native_live*")), f"Live native audit should not create production WebView artifacts under {path}")

    require("livekit/token" not in api, "Native viewer must not request host LiveKit token flow")
    require("browser-publish" not in api, "Native viewer must not call browser publish/host flow")
    require("cohost/request" not in api, "Native viewer must not implement co-hosting in this phase")

    print("PulseSoc native Live viewer audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
