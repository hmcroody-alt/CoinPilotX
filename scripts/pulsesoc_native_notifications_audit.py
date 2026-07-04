#!/usr/bin/env python3
"""Static audit for the PulseSoc native Notifications foundation."""

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
    report = read("reports/pulsesoc_native_notifications_progress.md")
    api = read("mobile-native/src/api/notifications.ts")
    push = read("mobile-native/src/api/push.ts")
    center = read("mobile-native/src/screens/NotificationCenterScreen.tsx")
    preferences = read("mobile-native/src/screens/NotificationPreferencesScreen.tsx")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    app = read("mobile-native/App.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "No native-only notification business rules were introduced",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"report must document reuse/safety/device truth: {phrase}")

    for route in (
        "/api/pulse/notifications",
        "/api/pulse/notifications/unread-count",
        "/api/pulse/notifications/${notificationId}/read",
        "/api/pulse/notifications/read-all",
        "/api/pulse/notifications/${notificationId}",
        "/api/pulse/notifications/${notificationId}/resolve",
        "/api/pulse/notifications/preferences",
        "/api/notification-preferences",
    ):
        require(route in api, f"notifications API must reuse backend route: {route}")

    for token in (
        "listNotifications",
        "getNotificationBadgeCounts",
        "markNotificationRead",
        "markAllNotificationsRead",
        "deleteNotification",
        "resolveNotificationTarget",
        "getNotificationPreferences",
        "updateNotificationPreferences",
        "getNotificationExperience",
        "updateNotificationExperience",
        "unreadCount",
    ):
        require(token in api, f"notifications API helper missing: {token}")

    require("/api/push/subscribe" in push, "push registration must reuse existing backend endpoint")
    require("getPushPermissionState" in push, "push permission state helper must exist")
    require("Push registration requires a physical device." in push, "push no-device fallback must remain explicit")
    require("Push permission was not granted." in push, "push denied fallback must remain explicit")

    for token in (
        "FlatList",
        "RefreshControl",
        "listNotifications",
        "getNotificationBadgeCounts",
        "markNotificationRead",
        "markAllNotificationsRead",
        "deleteNotification",
        "resolveNotificationTarget",
        "routeNotificationTarget",
        "AppState.addEventListener",
    ):
        require(token in center, f"notification center behavior missing: {token}")

    for token in (
        "getNotificationPreferences",
        "updateNotificationPreferences",
        "getNotificationExperience",
        "updateNotificationExperience",
        "registerPushDevice",
        "getPushPermissionState",
        "security",
        "in_app",
        "push",
        "email",
        "sms",
    ):
        require(token in preferences, f"notification preferences behavior missing: {token}")

    for token in (
        "Notifications.addNotificationReceivedListener",
        "Notifications.setBadgeCountAsync",
        "AppState.addEventListener",
        "tabBarBadge",
        "NotificationCenter",
        "NotificationPreferences",
    ):
        require(token in navigator, f"badge/navigation behavior missing: {token}")

    for token in (
        "setupNotificationResponseRouting",
        "addNotificationResponseReceivedListener",
        "routeNotificationData",
        "routeNotificationTarget",
        "/pulse/messages",
        "/pulse/profile",
        "/pulse/notifications",
        "Linking.openURL",
    ):
        require(token in routing, f"notification tap routing missing: {token}")

    require("navigationRef" in app and "setupNotificationResponseRouting" in app, "app must register notification response routing")
    require("pulse/notifications" in linking and "pulse/settings/notifications" in linking, "linking must include notification routes")
    require("NotificationPreferences" in settings, "settings must expose native notification preferences")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Notifications must not introduce WebView")

    print("PulseSoc native Notifications audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
