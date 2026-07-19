#!/usr/bin/env python3
"""Audit PulseSoc native alert activation against the existing backend pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "pulsesoc_native_alert_activation_audit.json"


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def check(condition: bool, name: str, detail: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "pass" if condition else "fail",
        "detail": detail,
    }


def require_all(results: list[dict[str, str]]) -> None:
    failures = [item for item in results if item["status"] != "pass"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({"ok": not failures, "checks": results}, indent=2) + "\n", encoding="utf-8")
    if failures:
        raise SystemExit("PulseSoc native alert activation audit failed:\n- " + "\n- ".join(f"{item['name']}: {item['detail']}" for item in failures))


def main() -> int:
    bot = read("bot.py")
    notification_service = read("services/notification_service.py")
    notification_os = read("services/pulsesoc_notification_system.py")
    native_push = read("mobile-native/src/api/push.ts")
    native_notifications = read("mobile-native/src/api/notifications.ts")
    native_activity = read("mobile-native/src/api/activity.ts")
    native_alerts = read("mobile-native/src/api/alerts.ts")
    native_auth = read("mobile-native/src/session/auth.ts")
    app_navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    global_navigation = read("mobile-native/src/navigation/GlobalNavigation.tsx")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    report = read("reports/pulsesoc_native_alert_activation_report.md")

    combined_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "mobile-native" / "src").rglob("*.ts*"))
        if path.is_file()
    )
    hardcoded_99_locations = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "mobile-native" / "src").rglob("*.ts*"))
        if '"99+"' in path.read_text(encoding="utf-8") and path.name != "GlobalNavigation.tsx"
    ]

    required_backend_routes = [
        '"/api/pulse/notifications"',
        '"/api/pulse/notifications/unread-count"',
        '"/api/pulse/notifications/<int:notification_id>/resolve"',
        '"/api/pulse/notifications/<int:notification_id>/read"',
        '"/api/pulse/notifications/read-all"',
        '"/api/pulse/notifications/<int:notification_id>"',
        '"/api/pulse/notifications/preferences"',
        '"/api/push/subscribe"',
        '"/api/push/unsubscribe"',
        '"/api/pulse/sync/events"',
    ]
    deep_link_markers = [
        ("/pulse/messages", r"\/pulse\/messages"),
        ("/pulse/calls", r"\/pulse\/calls"),
        ("/pulse/live", r"\/pulse\/live"),
        ("/pulse/reels", r"\/pulse\/reels"),
        ("/pulse/status", r"\/pulse\/status"),
        ("/pulse/post", r"\/pulse\/post"),
        ("/dashboard/crypto/alerts", r"\/dashboard\/crypto\/alerts"),
        ("/account/security", r"\/account\/security"),
        ("/pulse/marketplace", r"\/pulse\/marketplace"),
        ("/pulse/purchases", r"\/pulse\/purchases"),
        ("/pulse/premium", r"\/pulse\/premium"),
        ("/pulse/notifications", r"\/pulse\/notifications"),
    ]
    activity_categories = [
        "messages",
        "calls",
        "social",
        "safety",
        "verification",
        "marketplace",
        "creator_growth",
        "intelligence_alerts",
    ]

    results = [
        check(all(route in bot for route in required_backend_routes), "backend canonical routes", "existing notification, push, preference, and sync routes are present"),
        check("preserve_preferences" in bot and "preferences_preserved" in bot, "device cleanup preserves preferences", "push unsubscribe supports logout cleanup without globally disabling push"),
        check("ensure_user_notification_defaults" in notification_os and "backfill_notification_defaults" in notification_os, "default preference provisioning", "notification OS provisions missing default rows idempotently"),
        check("muted_user_ids" in notification_os and "muted_conversation_ids" in notification_os and "_quiet_hours_active" in notification_os, "respect quiet hours and mutes", "notification rules enforce quiet hours and muted actors/conversations"),
        check("notification_delivery_jobs" in notification_os and "dedupe_key" in notification_os and "schedule_delivery_processing" in notification_os, "queued delivery pipeline", "backend creates deduped delivery jobs instead of native-only pushes"),
        check("PULSE_NOTIFICATION_CATEGORIES" in notification_service and "PULSE_TYPE_TO_CATEGORY" in notification_service, "legacy category mapping retained", "existing legacy notification categories remain available"),
        check('"/api/push/subscribe"' in native_push and '"/api/push/unsubscribe"' in native_push, "native push route reuse", "native registration and cleanup use existing backend push routes"),
        check("Notifications.getExpoPushTokenAsync" in native_push and "Notifications.getDevicePushTokenAsync" in native_push, "Expo plus native push tokens", "native sends Expo token and APNs/FCM token metadata when available"),
        check("SecureStore" in native_push and "PUSH_REGISTRATION_CACHE_KEY" in native_push, "push registration cache", "native caches registration for logout/account-switch cleanup"),
        check("unregisterPushDevice" in native_auth and native_auth.find("unregisterPushDevice") < native_auth.find("logout().catch"), "logout cleanup order", "native revokes this device before clearing the authenticated session"),
        check(all(endpoint in native_notifications for endpoint in ["/api/pulse/notifications", "/api/pulse/notifications/unread-count", "/read", "/read-all", "/resolve", "/preferences"]), "native notification API reuse", "native notification client uses canonical list/read/delete/resolve/preferences routes"),
        check("totalUnreadCount" in native_notifications and "alertUnreadCount" in native_notifications and "chatUnreadCount" in native_notifications, "canonical badge helper split", "native separates total, alert, and chat counts from backend fields"),
        check("totalUnreadCount(counts)" in app_navigator and "Notifications.setBadgeCountAsync(activity)" in app_navigator, "OS badge uses total unread", "app shell drives OS badge from canonical total unread counts"),
        check("listNotifications" in native_activity and "getNotificationBadgeCounts" in native_activity and "resolveNotificationTarget" in native_activity, "activity inbox backend source", "activity inbox is derived from canonical notifications, messages, and calls"),
        check("readJsonCache" in native_activity and "writeJsonCache" in native_activity, "activity cache", "activity inbox has offline cache without creating native-only alert records"),
        check(all(category in native_activity for category in activity_categories), "activity category coverage", "required native alert categories are present"),
        check('"/api/pulse/sync/events"' in event_sync and "registerSyncInvalidation" in event_sync and "notifications" in event_sync and "activity" in event_sync, "realtime sync reuse", "native uses backend sync events for notification/activity invalidation"),
        check(
            all(plain in notification_routing or escaped in notification_routing for plain, escaped in deep_link_markers),
            "deep link routing coverage",
            "native routes canonical backend notification targets",
        ),
        check("pulsesoc://" in linking and "https://pulsesoc.com" in linking and "ActivityInbox" in linking and "AlertManagement" in linking, "linking config coverage", "native linking accepts canonical app/web schemes for alerts"),
        check("/api/crypto/alerts" in native_alerts and "/api/alerts/events" in native_alerts and "/api/alerts/channel-readiness" in native_alerts, "crypto alert backend reuse", "native alert management uses existing crypto/alert endpoints"),
        check("formatBadge" in global_navigation and '"99+"' in global_navigation and not hardcoded_99_locations, "no static 99+ badges", "99+ appears only in the shared formatter, not as static UI state"),
        check("/api/native/notifications" not in bot and "/api/native/alerts" not in bot, "no duplicate native alert backend routes", "backend does not expose native-only notification endpoints"),
        check("CREATE TABLE native_notifications" not in combined_native and "native_alerts" not in combined_native, "no native-only alert storage", "native source does not define separate alert tables or IDs"),
        check("Implementation matrix" in report and "Production evidence" in report and "Not proven in this shell" in report, "authoritative report present", "report contains matrix and honest verification boundary"),
    ]
    require_all(results)
    print(f"PulseSoc native alert activation audit passed: {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
