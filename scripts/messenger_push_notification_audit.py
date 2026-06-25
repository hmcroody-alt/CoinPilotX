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
    require(bot, '"senderId": sender_id', "camelCase senderId payload")
    require(bot, 'mobile_deep_link = f"pulse://pulse/messages-v2?conversation={conversation_id}"', "legacy PulseSoc native deepLink")
    if '"channel_id": "pulse-messages-v2"' in bot or '"channelId": "pulse-messages-v2"' in bot:
        raise SystemExit("FAIL: legacy message paths must not hardcode Android message channel")
    require(bot, 'return redirect(f"/pulse/messages/{conversation_id}")', "V2 deep-link redirect")
    require(bot, 'deep_link = f"/pulse/messages/{conversation_id}"', "legacy PulseSoc message push URL")
    require(bot, '"web_url": f"https://pulsesoc.com{deep_link}"', "legacy PulseSoc web URL payload")
    require(bot, '"badge": unread_count', "legacy PulseSoc badge payload")
    require(comm_v2, '"conversationId": int(conversation_id)', "V2 conversationId payload")
    require(comm_v2, '"messageId": message_id', "V2 messageId payload")
    require(comm_v2, '"senderId": int(user_id)', "V2 senderId payload")
    if '"channel_id": "pulse-messages-v2"' in comm_v2 or '"channelId": "pulse-messages-v2"' in comm_v2:
        raise SystemExit("FAIL: V2 message path must not hardcode Android message channel")
    require(comm_v2, '"suppress_push": bool(policy.get("suppress_push"))', "V2 muted/active chat push suppression")
    require(comm_v2, 'def _message_preview_hidden', "V2 private preview helper")
    require(comm_v2, '"Open PulseSoc to view."', "V2 private preview generic body")
    require(comm_v2, 'deep_link = f"/pulse/messages/{int(conversation_id)}"', "V2 exact conversation deep link")
    require(comm_v2, 'mobile_deep_link = f"pulse://pulse/messages-v2?conversation={int(conversation_id)}"', "V2 native exact conversation deep link")
    require(comm_v2, '"badge": int(unread or 0)', "V2 chat badge payload")
    require(comm_v2, '"web_url": deep_link', "V2 web URL payload")
    require(notifications, 'suppress_push = bool((metadata or {}).get("suppress_push"))', "audited push suppression")
    require(notifications, '"Push suppressed by notification policy."', "push suppression delivery log")
    require(push, 'os.getenv("PUSH_MESSAGE_CHANNEL_ID", "pulse-messages-v2")', "Expo message channel fallback")
    require(push, 'PUSH_DEFAULT_SOUND', "configurable Expo sound")
    require(push, '"priority": "high"', "Expo high priority")
    require(push, '"ttl": 3600', "Expo TTL")
    require(push, 'f"/pulse/messages/{conversation_id}"', "push service PulseSoc conversation route")
    require(push, '"deepLink": preferred_deep_link', "push service native-preferred deepLink data")
    require(push, '"web_url": web_url', "push service web URL data")
    require(notifications, '"private_message": "chat_message"', "private message category mapping")
    for label, text in (("service worker", sw), ("service worker alias", sw_alias)):
        require(text, "data.web_url || data.url || data.target_url", f"{label} web click routing")
        require(text, "pulsesoc-message-${conversationId}", f"{label} conversation notification tag")
        require(text, "silent: payload.silent === true ? true : false", f"{label} explicit audible default")
        require(text, 'conversationId ? "Open Chat" : "Open Alerts"', f"{label} message action opens chat")
    for label, text in (("mobile push", mobile_push), ("native push", native_push)):
        require(text, "shouldPlaySound: true", f"{label} foreground sound")
        require(text, "AndroidImportance.HIGH", f"{label} high importance")
        require(text, '"pulse-messages-v2"', f"{label} versioned messages channel")
        require(text, "data.native_url || data.app_url || data.mobile_deep_link", f"{label} native deep link preference")
        require(text, "pulse://pulse/messages-v2?conversation=", f"{label} exact conversation fallback")
    notifications_js = (ROOT / "static" / "notifications.js").read_text()
    require(notifications_js, "count > STATE.lastChatUnread && !onSameConversation", "foreground chat alert off active conversation")
    require(notifications_js, "New PulseSoc message", "foreground chat alert title")
    print("PASS: Messenger push notifications include reliable dedicated message channel fallback, sound, private-preview policy, foreground chat alerts, recipient payloads, push suppression, and exact deep links.")


if __name__ == "__main__":
    main()
