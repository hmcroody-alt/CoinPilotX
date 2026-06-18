#!/usr/bin/env python3
"""Audit Messenger push notification delivery wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"FAIL: missing {label}: {needle}")


def main() -> None:
    bot = (ROOT / "bot.py").read_text()
    push = (ROOT / "services" / "push_service.py").read_text()
    notifications = (ROOT / "services" / "notification_service.py").read_text()

    require(bot, 'channels=["in_app", "push"]', "legacy message push channel")
    require(bot, '"conversationId": conversation_id', "camelCase conversationId payload")
    require(bot, '"messageId": message_id', "camelCase messageId payload")
    require(bot, '"senderId": user["user_id"]', "camelCase senderId payload")
    require(bot, '"channel_id": "messages"', "messages notification channel payload")
    require(bot, 'return redirect(f"/pulse/messages/{conversation_id}")', "V2 deep-link redirect")
    require(push, 'channel_id = "messages"', "Expo messages channel selection")
    require(push, '"sound": "default"', "Expo sound")
    require(push, '"priority": "high"', "Expo high priority")
    require(push, '"ttl": 3600', "Expo TTL")
    require(notifications, '"private_message": "chat_message"', "private message category mapping")
    print("PASS: Messenger push notifications include sound, high-priority channel, recipient payload, and V2 deep links.")


if __name__ == "__main__":
    main()
