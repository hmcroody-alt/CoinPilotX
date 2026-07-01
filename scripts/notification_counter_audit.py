#!/usr/bin/env python3
"""Audit independent PulseSoc notification and message counters."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
JS = (ROOT / "static" / "notifications.js").read_text(encoding="utf-8")


def block(text: str, start: str, end: str = "\ndef ") -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i:] if j < 0 else text[i:j]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    counts_block = block(SERVICE, "def pulse_badge_counts")
    read_one_block = block(SERVICE, "def mark_pulse_read")
    read_all_block = block(SERVICE, "def mark_all_pulse_read")
    seen_block = block(BOT, "def api_pulse_messages_seen")
    list_block = block(SERVICE, "def list_pulse_notifications")
    page_block = block(BOT, "def pulse_notifications_page")

    require('"alert_unread_count": alert_count' in counts_block, "alert count must be returned separately", failures)
    require('"chat_unread_count": chat_count' in counts_block, "chat count must be returned separately", failures)
    require("NOT ({_message_notification_where_clause()})" in counts_block, "alert count must exclude message-like notifications", failures)
    require("pulse_conversation_participants" in counts_block and "comm_v2_participants" in counts_block, "chat count must use message participant unread state", failures)

    require("UPDATE pulse_notifications" in read_one_block and "pulse_conversation_participants" not in read_one_block, "single notification read must not touch message counters", failures)
    require("UPDATE pulse_notifications" in read_all_block and "pulse_conversation_participants" not in read_all_block, "read-all notifications must not touch message counters", failures)
    require("pulse_mark_conversation_read" in seen_block and "UPDATE pulse_notifications" not in seen_block, "message seen must not clear notification rows", failures)

    require('"messages"' not in re.search(r"allowed_filters = \{([^}]+)\}", page_block, flags=re.S).group(1), "notification center filters must not include Messages", failures)
    require('"priority"' in list_block and '"crypto"' in list_block and '"marketplace"' in list_block and '"system"' in list_block, "notification listing missing requested global filters", failures)

    require('setBadgeNodes("[data-alert-unread], [data-notification-unread]", alertCount)' in JS, "alert badge selector must stay independent", failures)
    require('setBadgeNodes("[data-chat-unread]", chatCount)' in JS, "chat badge selector must stay independent", failures)
    require("lastAlertUnread" in JS and "lastChatUnread" in JS, "browser state must track alert/chat unread independently", failures)
    require("isChat" in JS and "message_notification" in JS, "browser realtime handler must branch chat events separately", failures)

    if failures:
        print("notification counter audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("notification counter audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
