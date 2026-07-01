#!/usr/bin/env python3
"""Lock PulseSoc header Notifications vs bottom Messages separation."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    mobile_headers = re.findall(r'<nav class="mobile-topbar".*?</nav>', BOT, flags=re.S)
    desktop_top = BOT[BOT.find("def pulse_desktop_top_nav_html"):BOT.find("def pulse_desktop_left_rail_html")]

    require(mobile_headers, "mobile PulseSoc header snippets were not found", failures)
    for index, header in enumerate(mobile_headers, start=1):
        require("/pulse/notifications" in header, f"mobile header {index} missing Notifications route", failures)
        require("data-header-notifications" in header, f"mobile header {index} missing header notification marker", failures)
        require("PULSE_NOTIFICATION_BELL_ICON" in header or "__NOTIFICATION_BELL_ICON__" in header or "pulse-bell-icon" in header, f"mobile header {index} does not render bell icon", failures)
        require("pulse-alert-radar" not in header, f"mobile header {index} reintroduced old radar icon", failures)
        require("data-alert-unread" in header and "data-notification-unread" in header, f"mobile header {index} missing notification unread badge", failures)
        require("/pulse/messages" not in header, f"mobile header {index} reintroduced top Messages link", failures)
        require("data-chat-unread" not in header, f"mobile header {index} reintroduced chat badge in header", failures)
        require("pulse-topnav-messages" not in header, f"mobile header {index} reintroduced pulse-topnav-messages", failures)

    require("data-header-notifications" in desktop_top, "desktop header missing Notifications marker", failures)
    require("PULSE_NOTIFICATION_BELL_ICON" in desktop_top or "pulse-bell-icon" in desktop_top, "desktop header does not render bell icon", failures)
    require("pulse-alert-radar" not in desktop_top, "desktop header reintroduced old radar icon", failures)
    require("data-alert-unread" in desktop_top, "desktop header missing notification unread badge", failures)
    require('("Messages", "/pulse/messages")' not in desktop_top, "desktop header reintroduced Messages navigation item", failures)
    require("pulse-topnav-live" not in desktop_top, "desktop header reintroduced top Live action", failures)
    require("pulse-create-strong" not in desktop_top, "desktop header reintroduced top Create action", failures)
    require("pulse-topnav-messages" not in desktop_top, "desktop header reintroduced top Messages icon", failures)

    require("Messages\", \"/pulse/messages\"" in BOT or "Messages', '/pulse/messages'" in BOT or '"Messages", "/pulse/messages"' in BOT, "bottom/destination Messages route missing", failures)
    require("data-chat-unread" in BOT, "Messages destination badge missing data-chat-unread", failures)
    require("data-dock-action='{clean_html(action)}'" in BOT and 'action == "messages"' in BOT, "bottom dock Messages action missing", failures)

    if failures:
        print("header notification regression audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("header notification regression audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
