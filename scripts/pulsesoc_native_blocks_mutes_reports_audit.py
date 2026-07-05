#!/usr/bin/env python3
"""Audit the PulseSoc Native Safety Hub foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "mobile-native/src/api/safety.ts",
        "mobile-native/src/screens/SafetyHubScreen.tsx",
        "reports/pulsesoc_native_blocks_mutes_reports_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for file_path in required_files:
        require((ROOT / file_path).exists(), f"missing {file_path}", failures)

    safety_api = read("mobile-native/src/api/safety.ts")
    safety_screen = read("mobile-native/src/screens/SafetyHubScreen.tsx")
    nav_types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    trust = read("mobile-native/src/screens/TrustSafetyScreen.tsx")
    account_health = read("mobile-native/src/screens/AccountHealthAppealsScreen.tsx")
    profile_header = read("mobile-native/src/components/ProfileHeader.tsx")
    profile_screen = read("mobile-native/src/screens/ProfileScreen.tsx")
    messenger = read("mobile-native/src/screens/MessengerScreen.tsx")
    report = read("reports/pulsesoc_native_blocks_mutes_reports_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/dashboard/network/state",
        "reportPulseTarget",
        "blockPulseUser",
        "recordMuteHandoff",
        "recordUnblockHandoff",
        "openSafetyWebFallback",
    ]:
        require(token in safety_api, f"safety API missing {token}", failures)

    for token in [
        "Safety Hub",
        "Block user",
        "Mute management",
        "Create report",
        "Authority boundary",
        "server-authoritative",
        "Protected safety controls",
    ]:
        require(token in safety_screen, f"safety screen missing {token}", failures)

    for token in ["SafetyHub", "SafetyWebHub"]:
        require(token in nav_types, f"navigation types missing {token}", failures)
        require(token in app_nav, f"app navigator missing {token}", failures)

    for token in [
        "pulse/safety/:section?",
        "dashboard/network/:section?",
        "safetyRouteTarget",
        "/dashboard/network/network-security",
        "/dashboard/network/blocks-mutes",
    ]:
        require(token in linking + notification_routing, f"deep link routing missing {token}", failures)

    for source_name, source in [
        ("Settings", settings),
        ("TrustSafety", trust),
        ("AccountHealth", account_health),
        ("ProfileHeader", profile_header + profile_screen),
        ("Messenger", messenger),
    ]:
        require("SafetyHub" in source or "Safety Hub" in source, f"{source_name} entry point missing Safety Hub", failures)

    for token in [
        "POST /api/pulse/report",
        "POST /api/pulse/block",
        "GET /api/dashboard/network/state",
        "No user-safe native JSON endpoint currently exposes unblock",
        "Native Notifications + Inbox + Activity Graph Unification",
    ]:
        require(token in report, f"progress report missing {token}", failures)

    require("Native Blocks, Mutes, and Report Management Foundation" in progress, "master progress missing completed feature", failures)
    require("Native Notifications + Inbox + Activity Graph Unification" in progress, "master progress missing next recommendation", failures)

    user_facing_sources = safety_api + "\n" + safety_screen
    require("LogiNexus" not in user_facing_sources, "internal LogiNexus name leaked to user-facing native safety code", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native blocks/mutes/reports audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
