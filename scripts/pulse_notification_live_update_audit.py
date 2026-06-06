#!/usr/bin/env python3
"""Audit Pulse notification badge, dropdown, and notification center live refresh."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text()
JS = (ROOT / "static" / "notifications.js").read_text()
TEMPLATE = (ROOT / "templates" / "pulse_messages_v2.html").read_text()


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    require("data-notification-unread" in BOT, "Pulse shell exposes unread badge target")
    require("data-notification-list='dropdown'" in BOT, "desktop dropdown has a live notification list target")
    require("data-pulse-notification-list" in BOT, "notification center has a live refresh target")
    require("setBadges(count)" in JS, "notification JS updates all badge targets")
    require("refreshNotificationList" in JS and "/api/pulse/notifications?limit=12" in JS, "notification JS refreshes dropdown/page list")
    require("schedulePolling(12000)" in JS and "document.hidden ? 45000 : 12000" in JS, "visible pages use 12 second fallback polling and slow hidden tabs")
    require("notification_created" in JS and "message_notification" in JS, "notification JS listens for message notification events")
    require("/static/notifications.js?v=message-live-20260606" in TEMPLATE, "Messages V2 loads notification live updater")


if __name__ == "__main__":
    main()
