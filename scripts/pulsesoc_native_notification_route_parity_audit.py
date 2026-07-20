#!/usr/bin/env python3
"""Static contract gate for PulseSoc Native/WebView notification parity."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(label: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    web_notifications = read("static/notifications.js")
    backend = read("bot.py")
    notification_service = read("services/notification_service.py")
    native_push = read("mobile-native/src/api/push.ts")
    native_notifications = read("mobile-native/src/api/notifications.ts")
    native_routes = read("mobile-native/src/navigation/notificationRouting.ts")
    native_app = read("mobile-native/App.tsx")
    native_preferences = read("mobile-native/src/screens/NotificationPreferencesScreen.tsx")
    native_messenger = read("mobile-native/src/api/messenger.ts")
    native_feed = read("mobile-native/src/api/feed.ts")
    native_reels = read("mobile-native/src/api/reels.ts")
    native_status = read("mobile-native/src/api/status.ts")
    native_calls = read("mobile-native/src/api/calls.ts")

    shared_routes = {
        "push registration": (web_notifications, native_push, "/api/push/subscribe", "/api/push/subscribe"),
        "notification list": (backend, native_notifications, "/api/pulse/notifications", "/api/pulse/notifications"),
        "notification preferences": (web_notifications, native_notifications, "/api/pulse/notifications/preferences", "/api/pulse/notifications/preferences"),
        "message send": (read("static/js/pulse_messages_v2.js"), native_messenger, "/api/pulse/communications/v2", "/api/pulse/communications/v2"),
        "post reactions": (read("static/js/pulse_home_core.js"), native_feed, "/api/pulse/posts/${postId}/react", "/api/pulse/posts/${postId}/react"),
        "post comments": (read("static/js/pulse_home_core.js"), native_feed, "/api/pulse/posts/${postId}/comments", "/api/pulse/posts/${postId}/comments"),
        "reel reactions": (backend, native_reels, '/api/pulse/reels/<int:reel_id>/react', "/api/pulse/reels/${reelId}/react"),
        "reel comments": (backend, native_reels, '/api/pulse/reels/<int:reel_id>/comments', "/api/pulse/reels/${reelId}/comments"),
        "status reactions": (backend, native_status, '/api/pulse/status/<int:status_id>/react', "/api/pulse/status/${statusId}/react"),
        "status replies": (backend, native_status, '/api/pulse/status/<int:status_id>/reply', "/api/pulse/status/${statusId}/reply"),
        "call start": (read("pulse_communications_v2/routes.py"), native_calls, 'conversations/<path:conversation_ref>/voice/start', "/api/pulse/communications/v2/conversations/"),
    }
    for label, (web_or_backend, native, source_route, native_route) in shared_routes.items():
        require(f"{label}: production route missing from source of truth", source_route in web_or_backend, failures)
        require(f"{label}: native production route mismatch", native_route in native, failures)

    require("native must not create notifications through a side-channel", "/api/notifications/create" not in read_tree("mobile-native/src"), failures)
    require("push registration must use authenticated Pulse API client", 'pulseApi<PushRegistrationResult>("/api/push/subscribe"' in native_push, failures)
    require("push token refresh must run without prompting on foreground", "syncPushDeviceRegistration" in native_push and 'state === "active"' in native_app, failures)
    require("push installation identity must remain stable across token refresh", "PUSH_INSTALLATION_ID_KEY" in native_push and "installation_id" in native_push, failures)
    require("old Expo token must be revoked before refreshed token registration", 'reason: "token_refresh"' in native_push and "revokePushEndpoint" in native_push, failures)
    require("logout must revoke the saved production device association", "/api/push/unsubscribe" in native_push, failures)
    require("cold-start notification response restoration missing", "getLastNotificationResponseAsync" in native_routes, failures)
    require("signed-out notification taps must be deferred until auth", "pendingNotificationTarget" in native_app and "onDeferred" in native_app, failures)
    require("duplicate notification taps must be bounded", "lastNotificationResponseKey" in native_routes, failures)
    require("production route payload field is not handled", '"route"' in native_routes, failures)
    require("semantic conversation routing missing", '"conversation_id"' in native_routes, failures)
    require("semantic post routing missing", '"post_id"' in native_routes, failures)
    require("semantic Reel routing missing", '"reel_id"' in native_routes, failures)
    require("semantic call routing missing", '"call_id"' in native_routes, failures)
    require("Pulse native shorthand links are not normalized", "normalizeNativeShorthandPath" in native_routes, failures)
    require("preference UI must consume server category names", "prefData.categories" in native_preferences and "normalizeCategories" in native_preferences, failures)
    require("backend preference enforcement missing", "PULSE_TYPE_TO_CATEGORY" in notification_service and "_quiet_hours_active" in notification_service, failures)
    require("backend invalid-token handling missing", "invalid_ids" in read("services/push_service.py"), failures)

    if failures:
        print("PulseSoc native notification route parity audit: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PulseSoc native notification route parity audit: PASS")
    print(f"- shared action routes checked: {len(shared_routes)}")
    print("- production push registration and revocation routes preserved")
    print("- cold/background/auth-deferred notification routing covered")
    print("- server-authoritative preference categories covered")
    return 0


def read_tree(path: str) -> str:
    root = ROOT / path
    return "\n".join(
        file.read_text(encoding="utf-8", errors="ignore")
        for file in root.rglob("*")
        if file.is_file() and file.suffix in {".ts", ".tsx", ".js", ".jsx"}
    )


if __name__ == "__main__":
    raise SystemExit(main())
