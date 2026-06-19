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
    comm_v2 = (ROOT / "pulse_communications_v2" / "service.py").read_text()
    sw = (ROOT / "static" / "service-worker.js").read_text()
    sw_alias = (ROOT / "static" / "sw.js").read_text()
    mobile_push = (ROOT / "mobile" / "services" / "push.ts").read_text()
    native_push = (ROOT / "mobile" / "pulse-react-native" / "src" / "notifications" / "notifications.ts").read_text()

    require(bot, 'channels=["in_app", "push"]', "legacy message push channel")
    require(bot, '"conversationId": conversation_id', "camelCase conversationId payload")
    require(bot, '"messageId": message_id', "camelCase messageId payload")
    require(bot, '"senderId": user["user_id"]', "camelCase senderId payload")
    require(bot, '"channel_id": "messages"', "messages notification channel payload")
    require(bot, 'return redirect(f"/pulse/messages/{conversation_id}")', "V2 deep-link redirect")
    require(bot, '"url": f"/pulse/messages/{conversation_id}"', "legacy PulseSoc message push URL")
    require(bot, '"deepLink": f"/pulse/messages/{conversation_id}"', "legacy PulseSoc deepLink")
    require(comm_v2, '"conversationId": int(conversation_id)', "V2 conversationId payload")
    require(comm_v2, '"messageId": message_id', "V2 messageId payload")
    require(comm_v2, '"senderId": int(user_id)', "V2 senderId payload")
    require(comm_v2, '"channel_id": "messages"', "V2 messages channel payload")
    require(comm_v2, '"suppress_push": bool(policy.get("suppress_push"))', "V2 muted/active chat push suppression")
    require(comm_v2, 'def _message_preview_hidden', "V2 private preview helper")
    require(comm_v2, '"Open PulseSoc to view."', "V2 private preview generic body")
    require(comm_v2, 'deep_link = f"/pulse/messages/{int(conversation_id)}"', "V2 exact conversation deep link")
    require(notifications, 'suppress_push = bool((metadata or {}).get("suppress_push"))', "audited push suppression")
    require(notifications, '"Push suppressed by notification policy."', "push suppression delivery log")
    require(push, 'channel_id = "messages"', "Expo messages channel selection")
    require(push, '"sound": "default"', "Expo sound")
    require(push, '"priority": "high"', "Expo high priority")
    require(push, '"ttl": 3600', "Expo TTL")
    require(push, 'f"/pulse/messages/{conversation_id}"', "push service PulseSoc conversation route")
    require(push, '"deepLink": deep_link or url', "push service deepLink data")
    require(notifications, '"private_message": "chat_message"', "private message category mapping")
    for label, text in (("service worker", sw), ("service worker alias", sw_alias)):
        require(text, "data.deepLink || data.deep_link || data.url", f"{label} deepLink click routing")
        require(text, "pulsesoc-message-${conversationId}", f"{label} conversation notification tag")
        require(text, "silent: payload.silent === true ? true : false", f"{label} explicit audible default")
    for label, text in (("mobile push", mobile_push), ("native push", native_push)):
        require(text, "shouldPlaySound: true", f"{label} foreground sound")
        require(text, "AndroidImportance.HIGH", f"{label} high importance")
        require(text, '"messages"', f"{label} messages channel")
        require(text, "data.deepLink || data.deep_link || data.url", f"{label} deep link preference")
        require(text, "`/pulse/messages/${conversationId}`", f"{label} exact conversation fallback")
    print("PASS: Messenger push notifications include sound, high-priority message channel, private-preview policy, recipient payloads, push suppression, and exact deep links.")


if __name__ == "__main__":
    main()
