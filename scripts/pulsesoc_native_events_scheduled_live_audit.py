#!/usr/bin/env python3
"""Audit the PulseSoc Native Events + Scheduled Live gateway foundation."""

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
        "mobile-native/src/api/events.ts",
        "mobile-native/src/screens/EventsScreen.tsx",
        "mobile-native/src/navigation/AppNavigator.tsx",
        "mobile-native/src/navigation/linking.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "mobile-native/src/navigation/types.ts",
        "mobile-native/src/screens/SearchScreen.tsx",
        "mobile-native/src/screens/SettingsScreen.tsx",
        "reports/pulsesoc_native_events_scheduled_live_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]

    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    events_api = read("mobile-native/src/api/events.ts")
    events_screen = read("mobile-native/src/screens/EventsScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    types = read("mobile-native/src/navigation/types.ts")
    search = read("mobile-native/src/screens/SearchScreen.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    report = read("reports/pulsesoc_native_events_scheduled_live_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "listLiveNow",
        "loadCachedLiveDiscovery",
        "eventItemsFromLive",
        "openEventsWebFallback",
        "/pulse/live/schedule",
        "/pulse/live/events/create",
    ]:
        require(token in events_api, f"events API missing {token}", failures)

    for token in [
        "Scheduled Live Gateway",
        "Join or Watch",
        "Schedule Web",
        "Studio Web",
        "Backend authority preserved",
    ]:
        require(token in events_screen, f"Events screen missing {token}", failures)

    for token in ["EventsScreen", "EventDetail", "LiveScheduleGateway", "LiveEventCreateGateway"]:
        require(token in app_nav, f"App navigator missing {token}", failures)

    for token in [
        'path: "pulse/events"',
        'path: "pulse/events/:eventId"',
        'LiveScheduleGateway: "pulse/live/schedule"',
        'LiveEventCreateGateway: "pulse/live/events/create"',
    ]:
        require(token in linking, f"linking missing {token}", failures)

    for token in [
        'navigate("Events"',
        'navigate("EventDetail"',
        'navigate("LiveScheduleGateway"',
        'navigate("LiveEventCreateGateway"',
    ]:
        require(token in routing, f"notification routing missing {token}", failures)

    for token in ["Events:", "EventDetail:", "LiveScheduleGateway:", "LiveEventCreateGateway:"]:
        require(token in types, f"route types missing {token}", failures)

    require("EventsGatewayShortcut" in search, "Search Events tab shortcut missing", failures)
    require('navigation.navigate("Events"' in settings, "Settings Events entry missing", failures)
    require("Native Events + Scheduled Live Gateway Foundation" in report, "feature progress report missing title", failures)
    require("Native Content Planner + Scheduled Publishing Gateway Foundation" in report, "feature report missing next recommendation", failures)
    require("Native Events + Scheduled Live Gateway Foundation" in progress, "master progress missing Events section", failures)
    require("LogiNexus" not in events_api + "\n" + events_screen + "\n" + search + "\n" + settings, "internal LogiNexus name leaked into user-facing native source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native events scheduled live audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
