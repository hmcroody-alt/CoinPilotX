#!/usr/bin/env python3
"""Ensure mobile and desktop Messenger share the same reliable chat data layer."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main() -> None:
    source = (ROOT / "bot.py").read_text()
    function_start = source.find("def pulse_dashboard_messenger_page")
    active_return = source.find("return pulse_social_shell(\"Pulse Messenger\", \"Stable Dashboard", function_start)
    messenger_slice = source[function_start:active_return]
    required_functions = [
        "loadConversations",
        "loadRooms",
        "loadGroups",
        "openConversation",
        "openRoom",
        "pollActiveConversation",
    ]
    for name in required_functions:
        expect(re.search(rf"function\s+{name}\s*\(", messenger_slice) is not None or re.search(rf"async\s+function\s+{name}\s*\(", messenger_slice) is not None, f"{name} exists")

    expect('/api/messages/${state.activeConversationId}/send' in messenger_slice, "all sends use Dashboard-compatible selected conversation id")
    expect("/api/chat-room/${encodeURIComponent(roomId)}/messages?limit=80" in messenger_slice, "room loads use stable room bridge")
    expect("/api/chat/threads" in messenger_slice and "/api/pulse/messages/conversations" in messenger_slice, "desktop and mobile share same direct chat list loader")
    expect("setInterval(pollActiveConversation, 1800)" in messenger_slice, "HTTP polling keeps messages fresh")
    expect("document.addEventListener(\"visibilitychange\"" in messenger_slice, "visibility recovery polls active conversation")
    expect("Something needs attention. Please try again." not in messenger_slice, "messenger UI avoids generic dead error")
    expect("Loading messages..." in messenger_slice, "selected chat shows real loading state")
    expect("Unable to load messages." in messenger_slice, "load failures report concrete message")
    print("chat desktop mobile parity audit ok")


if __name__ == "__main__":
    main()
