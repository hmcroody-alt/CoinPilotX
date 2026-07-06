#!/usr/bin/env python3
"""Audit the PulseSoc Native Content Planner gateway foundation."""

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
        "mobile-native/src/api/creator.ts",
        "mobile-native/src/screens/ContentPlannerScreen.tsx",
        "mobile-native/src/screens/CreatorStudioScreen.tsx",
        "mobile-native/src/navigation/AppNavigator.tsx",
        "mobile-native/src/navigation/linking.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "mobile-native/src/navigation/types.ts",
        "mobile-native/src/screens/SettingsScreen.tsx",
        "reports/pulsesoc_native_content_planner_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    creator_api = read("mobile-native/src/api/creator.ts")
    planner = read("mobile-native/src/screens/ContentPlannerScreen.tsx")
    creator_screen = read("mobile-native/src/screens/CreatorStudioScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    types = read("mobile-native/src/navigation/types.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    report = read("reports/pulsesoc_native_content_planner_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/dashboard/content-planner/item",
        "scheduled_at",
        "links_validated",
        "final_preview_reviewed",
        "plannerWebRoute",
    ]:
        require(token in creator_api, f"creator API missing {token}", failures)

    for token in [
        "Content Planner",
        "Scheduled Publishing",
        "Save Scheduled Draft",
        "Publish safety",
        "openCreatorWebFallback(plannerWebRoute",
    ]:
        require(token in planner, f"Content Planner screen missing {token}", failures)

    for token in [
        'navigation.navigate("ContentPlanner", { mode: "planner"',
        'navigation.navigate("ContentPlanner", { mode: "scheduler"',
    ]:
        require(token in creator_screen, f"Creator Studio missing native planner entry {token}", failures)

    for token in [
        "ContentPlanner",
        "ContentPlannerWeb",
        "ContentPlannerPulseAlias",
        "PostScheduler",
        "PostSchedulerPulseAlias",
        "DraftStudio",
        "DraftStudioPulseAlias",
    ]:
        require(token in app_nav, f"navigator missing {token}", failures)
        require(token in types, f"route types missing {token}", failures)

    for token in [
        'path: "pulse/content-planner"',
        'path: "dashboard/creator/content-planner"',
        'ContentPlannerPulseAlias: "pulse/dashboard/content-planner"',
        'PostScheduler: "dashboard/creator/post-scheduler"',
        'PostSchedulerPulseAlias: "pulse/dashboard/post-scheduler"',
        'DraftStudio: "dashboard/creator/draft-studio"',
        'DraftStudioPulseAlias: "pulse/dashboard/draft-studio"',
    ]:
        require(token in linking, f"linking missing {token}", failures)

    require("contentPlannerTarget" in routing, "notification routing missing content planner target helper", failures)
    require('navigation.navigate("ContentPlanner"' in settings, "Settings missing Content Planner entry", failures)
    require("Native Content Planner + Scheduled Publishing Gateway Foundation" in report, "feature report missing title", failures)
    require("Native Courses + Learning Gateway Foundation" in report, "feature report missing next recommendation", failures)
    require("Native Content Planner + Scheduled Publishing Gateway Foundation" in progress, "master progress missing Content Planner section", failures)
    require("LogiNexus" not in creator_api + "\n" + planner + "\n" + creator_screen + "\n" + settings, "internal LogiNexus name leaked into user-facing native source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native content planner audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
