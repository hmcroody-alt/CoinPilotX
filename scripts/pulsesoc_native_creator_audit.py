#!/usr/bin/env python3
"""Static audit for PulseSoc native Creator Studio foundation."""

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
    api = read("mobile-native/src/api/creator.ts")
    screen = read("mobile-native/src/screens/CreatorStudioScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    status = read("mobile-native/src/screens/StatusScreen.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    report = read("reports/pulsesoc_native_creator_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in (
        "/api/dashboard/creator/state",
        "/api/pulse/creator-ai/${tool}",
        "/api/dashboard/content-planner/item",
        "readJsonCache",
        "writeJsonCache",
        "mobile_native_creator_studio",
        "openCreatorWebFallback",
    ):
        require(token in api, f"Creator API wrapper missing required reuse token: {token}")

    for token in (
        "getCreatorState",
        "getPremiumStatus",
        "runCreatorAiTool",
        "saveContentPlannerItem",
        "Feed Composer",
        "Status Creator",
        "Live Studio Web",
        "Creator AI",
        "Open Full Web Creator Studio",
    ):
        require(token in screen, f"Creator Studio screen missing required behavior: {token}")

    for forbidden in (
        "creator_score =",
        "grant_entitlement",
        "revoke_entitlement",
        "stripe.",
        "createCheckout",
        "payout",
        "wallet_balance =",
        "moderation_status =",
        "processing_status =",
    ):
        require(forbidden not in api, f"Creator API must not duplicate backend logic: {forbidden}")
        require(forbidden not in screen, f"Creator screen must not duplicate backend logic: {forbidden}")

    require("CreatorStudioScreen" in app_nav and '<Stack.Screen name="CreatorStudio"' in app_nav, "App navigator missing CreatorStudio route")
    require("CreatorStudio: undefined" in types, "RootStackParamList missing CreatorStudio route")
    require("openComposer?: boolean" in types and "openCreator?: boolean" in types, "Composer route params missing")
    require('path: "pulse/creator-studio"' in linking, "Deep-link config missing Creator Studio path")
    require('normalized.startsWith("/pulse/creator-studio")' in routing and 'normalized.startsWith("/pulse/creator/dashboard")' in routing, "Notification routing missing creator routes")
    require("route.params?.openComposer" in home and "setComposerOpen(true)" in home, "Home screen missing composer route hook")
    require("route.params?.openCreator" in status and "setCreatorOpen(true)" in status, "Status screen missing creator route hook")
    require('navigation.navigate("CreatorStudio")' in settings, "Settings missing Creator Studio entry point")

    for phrase in (
        "The backend remains authoritative",
        "GET /api/dashboard/creator/state",
        "POST /api/pulse/creator-ai/<tool>",
        "POST /api/dashboard/content-planner/item",
        "Not device-verified",
        "Native Growth Center Foundation",
        "Risk: Medium",
    ):
        require(phrase in report, f"Creator progress report missing required detail: {phrase}")

    for phrase in (
        "Creator Studio Foundation",
        "reports/pulsesoc_native_creator_progress.md",
        "scripts/pulsesoc_native_creator_audit.py",
        "Native Growth Center Foundation",
        "GET /api/pulse/growth",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"Master progress missing Creator checkpoint or next recommendation: {phrase}")

    for path in ("templates", "static/js", "static/css", "mobile/pulse-react-native"):
        require(
            not any((ROOT / path).glob("**/*pulsesoc_native_creator*")),
            f"Creator native mission must not create production WebView artifacts under {path}",
        )

    print("PulseSoc native Creator Studio foundation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
