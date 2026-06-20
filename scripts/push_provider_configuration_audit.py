#!/usr/bin/env python3
"""Audit the PulseSoc chat push provider configuration path without secrets."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.native_push_readiness import apns_readiness, fcm_readiness, normalize_private_key  # noqa: E402


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main() -> int:
    push = read("services/push_service.py")
    notifications = read("services/notification_service.py")
    worker_health = read("services/command_center_worker/health.py")
    worker_heartbeat = read("services/command_center_worker/heartbeat.py")
    bot = read("bot.py")

    require("https://exp.host/--/api/v2/push/send" in push, "Expo is the active native mobile push sender")
    require('provider = "expo" if _is_expo_token(endpoint, subscription) else "webpush"' in push, "Expo/WebPush provider state is inferred from registered token shape")
    require("VAPID_PUBLIC_KEY" in push and "VAPID_PRIVATE_KEY" in push, "Web/PWA push checks VAPID at send time")
    require("os.getenv(\"PUSH_DEFAULT_SOUND\") or \"default\"" in push, "Provider payload includes configured sound fallback")
    require("\"priority\": \"high\"" in push, "Provider payload uses high priority for chat pushes")
    require("PUSH_BADGE_ENABLED" in push, "Provider payload honors badge enablement")
    require("process_push_delivery_jobs" in worker_heartbeat, "Command Center Worker drains durable push jobs")
    require("process_expo_receipts" in worker_heartbeat, "Command Center Worker reconciles Expo receipts")
    require("native_push_readiness" in worker_health, "Worker health exposes APNs/FCM readiness without secrets")
    require("send_push_alert" in notifications and "enqueue_push" in notifications, "Notification service queues push jobs before provider calls")
    require("provider_summary" in bot and "Expo Receipt Checks" in bot, "Admin diagnostics show safe provider response and receipt status")
    require("VAPID push variables are not configured." in push, "WebPush missing config has an explicit skip reason")
    require("No active push subscription." in push, "Missing token has an explicit skip reason")
    require("DeviceNotRegistered" in push and "UPDATE user_device_tokens SET enabled=0" in push, "Invalid device tokens are deactivated")
    require("COMMAND_CENTER_INTERNAL_TOKEN" not in read("static/notifications.js"), "Browser notification code does not expose internal token")

    escaped_key = "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----"
    require("\n" in normalize_private_key(escaped_key), "Railway escaped private-key newlines normalize safely")
    apns = apns_readiness({})
    fcm = fcm_readiness({}, initialize_admin=False)
    require(apns.get("ready") is False and fcm.get("ready") is False, "Missing runtime APNs/FCM config reports not ready without crashing")

    print("push_provider_configuration_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"push_provider_configuration_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
