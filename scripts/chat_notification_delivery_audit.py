#!/usr/bin/env python3
"""Static safety gate for the PulseSoc message-to-notification delivery chain."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
NOTIFICATIONS = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
PUSH = (ROOT / "services" / "push_service.py").read_text(encoding="utf-8")
WORKER = (ROOT / "services" / "command_center_worker" / "app.py").read_text(encoding="utf-8")
MOBILE_PUSH = (ROOT / "mobile" / "pulse-react-native" / "services" / "push.ts").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def block(source, start, end):
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


finalizer = block(BOT, "def pulse_finalize_message_delivery", "PULSE_VISIBLE_MESSAGE_FILTER")
require("create_pulse_notification(" in finalizer, "post-commit finalizer creates the in-app notification")
require("enqueue_message_event(" in finalizer, "post-commit finalizer dispatches the Command Center message event")
require("idempotency_key=f\"chat-message-{message_id}-{target_id}\"" in finalizer, "Command Center dispatch is idempotent per message and recipient")
require('"recipient_ids": [target_id]' in finalizer, "realtime event is isolated to the intended recipient")
require('"push_type": "chat_message"' in finalizer, "push payload is classified as chat_message")
require('entity_id=str(message_id)' in finalizer, "chat notification deduplication is message-specific")
require('"suppress_push": suppress_push' in finalizer and '"conversation_muted"' in BOT, "muted conversations keep unread state while suppressing noisy push")
require('"badge": unread_count' in finalizer, "push payload carries the chat unread badge")
require("pulse://pulse/messages-v2?conversation=" in finalizer, "push payload carries the registered native conversation deep link")
require("https://pulsesoc.com" in finalizer, "push payload carries an HTTPS fallback")
require("notification_service.create_pulse_notification" not in block(BOT, "def pulse_send_conversation_message", "def pulse_finalize_message_delivery"), "message transaction does not perform cross-connection notification delivery")
require("stage=message_notification_failed" in finalizer, "post-commit delivery failure cannot roll back or misreport a saved message")

send_call_count = BOT.count("pulse_send_conversation_message(") - 1
finalize_call_count = BOT.count("pulse_finalize_message_delivery(") - 2
require(send_call_count == 4, "all four canonical send call sites are known to the audit")
require(finalize_call_count >= send_call_count, "every canonical send call site invokes post-commit delivery")
require(BOT.count("pulse_finalize_legacy_message_delivery(") >= 5, "legacy and compatibility sends bridge into canonical delivery")

require("send_push_alert(" in NOTIFICATIONS and "enqueue_push(" in NOTIFICATIONS, "notification service enqueues durable push delivery")
require('"sound": os.getenv("PUSH_DEFAULT_SOUND") or "default"' in PUSH, "native push includes configured sound")
require('"priority": "high"' in PUSH, "chat push uses high provider priority")
require("process_push_delivery_jobs" in PUSH and "dead_letter" in PUSH, "push worker has retry and dead-letter processing")
require("@require_internal_auth" in WORKER and '"/internal/command-center/messages/event"' in WORKER, "Command Center message endpoint requires internal authentication")
require("COMMAND_CENTER_INTERNAL_TOKEN" not in finalizer, "browser-facing delivery code never reads or exposes the internal token")
require("(?:conversation|conversation_id|conversationId)" in MOBILE_PUSH, "native foreground suppression recognizes every supported conversation query key")

print("chat_notification_delivery_audit: PASS")
