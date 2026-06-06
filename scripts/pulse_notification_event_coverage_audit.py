#!/usr/bin/env python3
"""Audit Pulse notification event wiring for production readiness."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
SERVICE = ROOT / "services" / "notification_service.py"


REQUIRED_EVENTS = {
    "follow": ["follow"],
    "follow request accepted": ["follow_accept"],
    "mention": ["mention"],
    "comment": ["comment"],
    "reply": ["reply"],
    "like": ["like"],
    "share": ["share"],
    "save": ["save"],
    "new message": ["message"],
    "group invite": ["group_invite"],
    "room invite": ["room_invite"],
    "voice message": ["voice_message"],
    "status reaction": ["status_reaction"],
    "status mention": ["status_mention"],
    "video like": ["video_like"],
    "video comment": ["video_comment"],
    "video mention": ["video_mention"],
    "reel like": ["reel_like"],
    "reel comment": ["reel_comment"],
    "reel mention": ["reel_mention"],
    "live started": ["live_started"],
    "live invite": ["live_invite"],
    "replay available": ["replay_available", "live_replay_ready"],
    "subscription renewal": ["subscription_renewal", "premium_alert"],
    "payment success": ["payment_success", "purchase", "premium_alert"],
    "payment failure": ["payment_failure", "premium_alert"],
    "new login": ["account_login"],
    "new device": ["new_device"],
    "password changed": ["security_alert", "password_changed"],
    "email changed": ["security_alert", "email_changed"],
    "roast challenge": ["roast_battle_invite"],
    "challenge accepted": ["roast_battle_result"],
    "battle result": ["roast_battle_result"],
}


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Missing file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    bot = read(BOT)
    service = read(SERVICE)
    combined = bot + "\n" + service
    failures = []

    for label, tokens in REQUIRED_EVENTS.items():
        if not any(token in combined for token in tokens):
            failures.append(f"Missing required event token for {label}: one of {tokens}")

    required_routes = [
        "/api/pulse/notifications",
        "/api/pulse/notifications/unread-count",
        "/api/pulse/videos/<int:video_id>/react",
        "/api/pulse/videos/<int:video_id>/comments",
        "/api/pulse/messages/<int:conversation_id>/send",
    ]
    for route in required_routes:
        if route not in bot:
            failures.append(f"Missing route: {route}")

    required_fragments = [
        "INSERT INTO pulse_notifications",
        "INSERT INTO pulse_notification_deliveries",
        "delivery_status",
        "deep_link",
        "pulse_notify_mentions",
        "pulse_notify_followers",
        "create_pulse_notification",
        "send_user_alert",
    ]
    for fragment in required_fragments:
        if fragment not in combined:
            failures.append(f"Missing implementation fragment: {fragment}")

    notify_user_body = re.search(r"def notify_user\([\s\S]+?\n\ndef ", bot)
    if not notify_user_body or "pulse_notification_deliveries" not in notify_user_body.group(0):
        failures.append("notify_user must write pulse_notification_deliveries.")

    send_user_alert_body = re.search(r"def send_user_alert\([\s\S]+?\n\ndef ", service)
    if not send_user_alert_body or "create_pulse_notification" not in send_user_alert_body.group(0):
        failures.append("send_user_alert must mirror legacy alerts into pulse_notifications.")

    if failures:
        print("Pulse notification event coverage audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Pulse notification event coverage audit passed.")
    print(f"Checked {len(REQUIRED_EVENTS)} required event groups.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
