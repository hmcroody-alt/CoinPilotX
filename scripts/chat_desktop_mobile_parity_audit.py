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
    required_functions = [
        "loadConversationMessages",
        "loadRoomMessages",
        "loadGroupMessages",
        "sendPrivateMessage",
        "sendRoomMessage",
        "sendGroupMessage",
        "retryMessageLoad",
        "reconnectSocket",
        "fallbackPollMessages",
    ]
    for name in required_functions:
        expect(re.search(rf"function\s+{name}\s*\(", source) is not None or re.search(rf"async\s+function\s+{name}\s*\(", source) is not None, f"{name} exists")

    expect('/api/pulse/messages/${Number(state.activePulseConversation || 0)}/send' in source, "private/group sends use selected conversation id")
    expect("/api/pulse/chatrooms/${encodeURIComponent(state.activeRoomId)}/messages" in source, "room sends use selected room id")
    expect("/api/pulse/messages/${Number(conversationId)}/messages?limit=80" in source, "private/group loads use canonical message endpoint")
    expect("/api/pulse/chatrooms/${encodeURIComponent(roomId)}/messages?limit=80" in source, "room loads use canonical room endpoint")
    expect('fallbackPollMessages();' in source[source.find("state.live.onerror"):source.find("state.live.onerror") + 500], "websocket failure triggers HTTP fallback polling")
    expect('window.addEventListener("online", () => { state.offline = false; setNetworkStatus("syncing"); reconnectSocket(); flushPendingMessages(); });' in source, "online recovery reconnects and polls")
    messenger_slice = source[source.find("data-unified-messenger"):]
    expect("Something needs attention. Please try again." not in messenger_slice, "messenger UI avoids generic dead error")
    expect("Messages could not load." in messenger_slice, "messenger selected-thread failure is specific")
    expect("Message loading failed. Retry is ready." in messenger_slice, "retry copy reflects real retry action")
    print("chat desktop mobile parity audit ok")


if __name__ == "__main__":
    main()
