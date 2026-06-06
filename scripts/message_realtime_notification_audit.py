#!/usr/bin/env python3
"""Audit Communications V2 realtime notification delivery wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text()
ROUTES = (ROOT / "pulse_communications_v2" / "routes.py").read_text()
MESSAGES_JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text()
NOTIFICATIONS_JS = (ROOT / "static" / "notifications.js").read_text()


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    require("create_pulse_notification(" in SERVICE, "message send creates Pulse notification")
    require('f"comm_v2:user:{int(payload[\'recipient_user_id\'])}"' in SERVICE, "receiver-scoped Communications V2 event is published")
    require('"message_notification"' in SERVICE, "message_notification event type is emitted")
    require("recipient_message = _side_effect_message_payload" in SERVICE, "receiver gets message payload from receiver perspective")
    require('f"{API_PREFIX}/realtime"' in ROUTES and "poll_realtime_events" in ROUTES, "authenticated realtime polling route exists")
    require("comm_v2:user:{int(user_id)}" in SERVICE and "pulse:user:{int(user_id)}" in SERVICE, "poll endpoint reads user-specific channels")
    require("handleRealtimeEvent" in MESSAGES_JS and "message_notification" in MESSAGES_JS, "Messages V2 handles live message notification events")
    require("appendRealtimeMessage" in MESSAGES_JS, "active conversation can append incoming messages without refresh")
    require("BroadcastChannel(\"pulse-comm-v2\")" in MESSAGES_JS, "Messages V2 mirrors live events across tabs")
    require("handleLiveNotification" in NOTIFICATIONS_JS and "BroadcastChannel(\"pulse-notifications\")" in NOTIFICATIONS_JS, "global notification UI handles live events and cross-tab refresh")


if __name__ == "__main__":
    main()
