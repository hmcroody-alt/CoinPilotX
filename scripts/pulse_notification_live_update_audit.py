#!/usr/bin/env python3
"""Audit Pulse notification badge and notification center live refresh."""

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
    require("pulse-topnav-messages" not in BOT and "data-header-notifications" in BOT and "pulse-bell-icon" in BOT, "header uses global bell notifications instead of duplicate messages")
    require("data-pulse-notification-list" in BOT, "notification center has a live refresh target")
    require("setBadgeNodes(\"[data-alert-unread], [data-notification-unread]\", alertCount)" in JS and "setBadgeNodes(\"[data-chat-unread]\", chatCount)" in JS, "notification JS updates all badge targets")
    require("refreshNotificationList" in JS and "/api/pulse/notifications?limit=12" in JS, "notification JS refreshes dropdown/page list")
    require("schedulePolling(30000)" in JS and "document.hidden ? 45000 : 30000" in JS, "visible pages use 30 second fallback polling and slow hidden tabs")
    require("notification_created" in JS and "message_notification" in JS, "notification JS listens for message notification events")
    require("notificationSectionsHtml" in JS and "Priority" in JS and "Earlier This Week" in JS, "notification JS renders grouped sections")
    require("/static/notifications.js?v=message-live-20260606" in TEMPLATE, "Messages V2 loads notification live updater")


if __name__ == "__main__":
    main()
