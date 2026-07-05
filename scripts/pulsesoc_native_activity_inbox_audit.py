#!/usr/bin/env python3
"""Audit the PulseSoc Native Activity Inbox foundation."""

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
        "mobile-native/src/api/activity.ts",
        "mobile-native/src/screens/ActivityInboxScreen.tsx",
        "reports/pulsesoc_native_activity_inbox_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for file_path in required_files:
        require((ROOT / file_path).exists(), f"missing {file_path}", failures)

    activity_api = read("mobile-native/src/api/activity.ts")
    activity_screen = read("mobile-native/src/screens/ActivityInboxScreen.tsx")
    notifications_api = read("mobile-native/src/api/notifications.ts")
    nav_types = read("mobile-native/src/navigation/types.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    report = read("reports/pulsesoc_native_activity_inbox_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "listNotifications",
        "getNotificationBadgeCounts",
        "markNotificationRead",
        "markAllNotificationsRead",
        "deleteNotification",
        "resolveNotificationTarget",
        "listConversations",
        "getActiveCalls",
        "serverAuthoritative",
    ]:
        require(token in activity_api, f"activity API missing {token}", failures)

    for token in [
        "Activity Inbox",
        "Unified signal layer",
        "Mark read",
        "Preferences",
        "Synchronizing activity graph",
        "server-authoritative routing",
    ]:
        require(token in activity_screen, f"activity screen missing {token}", failures)

    for token in [
        "messages",
        "calls",
        "social",
        "safety",
        "verification",
        "marketplace",
        "creator_growth",
        "intelligence_alerts",
    ]:
        require(token in activity_api, f"category missing {token}", failures)
    require("activityCategories" in activity_screen, "activity screen does not reuse shared category metadata", failures)

    require("markAllNotificationsRead(category" in notifications_api, "notification API missing category-scoped read-all wrapper", failures)
    require("ActivityInbox" in nav_types, "navigation types missing ActivityInbox", failures)
    require("ActivityInboxScreen" in app_nav, "app navigator missing ActivityInboxScreen", failures)
    require('name="Notifications" component={ActivityInboxScreen}' in app_nav, "Notifications tab does not use ActivityInboxScreen", failures)

    for token in [
        "pulse/activity/:category?",
        "activityRouteTarget",
        "/pulse/inbox",
        "/dashboard/activity",
        "/dashboard/inbox",
        'navigation.navigate("ActivityInbox"',
    ]:
        require(token in linking + notification_routing + settings, f"routing/entry missing {token}", failures)

    for token in [
        "GET /api/pulse/notifications",
        "GET /api/pulse/messages/conversations",
        "GET /api/calls/active",
        "Native grouping is display-only",
        "Push notification tap routing must still be verified on physical devices",
        "Native Activity Inbox practical QA hardening",
    ]:
        require(token in report, f"progress report missing {token}", failures)

    require("Native Notifications + Inbox + Activity Graph Unification" in progress, "master progress missing completed Activity Inbox feature", failures)
    require("LogiNexus" not in activity_api + "\n" + activity_screen, "internal LogiNexus name leaked to user-facing Activity Inbox code", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native activity inbox audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
