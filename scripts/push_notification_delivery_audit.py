#!/usr/bin/env python3
"""Audit native/PWA push registration and message delivery wiring."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    bot = read("bot.py")
    push = read("services/push_service.py")
    notifications = read("services/notification_service.py")
    comm = read("pulse_communications_v2/service.py")

    for route in ("/api/push/subscribe", "/api/push/register-device", "/api/push/unsubscribe", "/api/push/revoke-device", "/api/push/status"):
        require(route in bot, f"missing push route {route}")
    for column in ("user_device_tokens", "platform", "device_id", "push_token", "push_provider", "revoked_at"):
        require(column in bot, f"bot.py missing device token schema field {column}")
        require(column in push or column == "revoked_at", f"push_service.py missing device token persistence field {column}")

    require("_ensure_user_device_tokens" in push, "push_service must create fallback device token schema")
    require("push_delivery_jobs" in bot and "def enqueue_push" in push and "def process_push_delivery_jobs" in push, "push delivery must use durable async queue")
    require("process_push_delivery_jobs" in read("services/command_center_worker/heartbeat.py"), "worker heartbeat must process queued push jobs")
    require("endpoint_hash" in push and "push_token" in push, "push_service must store tokens but log only hashes")
    require("send_push_alert" in comm, "message side effects must call push delivery")
    require("suppress_push" in comm and "blocked" in comm and "muted" in comm, "message push policy must suppress blocked/muted/active chat")
    require("pulse://pulse/messages-v2?conversation=" in comm, "mobile deep link must target exact conversation")
    require('"channelId": "pulse-messages-v2"' in comm, "message push must use fresh audible Android channel")
    require('"badge": int(unread or 0)' in comm, "message push must include chat badge count")
    require("/pulse/messages/" in comm, "web deep link must target exact conversation")
    require("privacy_preview_hidden" in comm, "push payload must respect private preview mode")
    require("UPDATE user_device_tokens SET enabled=0" in notifications, "unsubscribe must revoke mirrored device tokens")
    require("PUSH_TRACE stage=send_push_complete" in comm, "message push completion must be trace logged")
    require("push_service._async_push_enabled()" in notifications and "push_service.enqueue_push" in notifications, "notification service must queue pushes before provider fallback")

    forbidden_log_tokens = ["subscription_json)[:1200]", "push_token)[:", "COMMAND_CENTER_INTERNAL_TOKEN"]
    for token in forbidden_log_tokens:
        require(token not in comm + push + bot, f"unsafe token logging marker found: {token}")

    print("push_notification_delivery_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"push_notification_delivery_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
