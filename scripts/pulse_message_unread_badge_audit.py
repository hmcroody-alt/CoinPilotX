#!/usr/bin/env python3
"""Audit unread badge and conversation preview updates for Communications V2."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text()
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text()


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    require("unread_count=CASE WHEN user_id=? THEN 0 ELSE COALESCE(unread_count,0)+1 END" in SERVICE, "send path increments receiver unread count")
    require('"chat_unread_count": int(unread or 0)' in SERVICE, "realtime payload includes authoritative receiver chat unread count")
    require('"chat_unread_count": int(payload.get("chat_unread_count") or 0)' in SERVICE, "all receiver realtime events preserve chat unread count")
    require('"conversation": _side_effect_conversation_payload' in SERVICE, "realtime payload includes updated conversation metadata")
    require("mergeRealtimeConversation" in JS, "client merges live conversation metadata")
    require("last_message_preview" in JS, "client updates conversation preview from incoming message")
    require("unread_count: activeConversation ? 0" in JS, "active conversation stays read while inactive conversations show unread")
    require("updateNotificationBadges" in JS, "client refreshes global notification badge from live payload")
    require("pollRealtime" in JS and "scheduleRealtimePoll" in JS, "chat list has realtime polling fallback")


if __name__ == "__main__":
    main()
