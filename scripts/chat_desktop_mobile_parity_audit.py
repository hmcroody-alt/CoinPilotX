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
    function_start = source.find("def pulse_communications_page")
    active_return = source.find("return pulse_social_shell(\"Pulse Communications\"", function_start)
    messenger_slice = source[function_start:active_return]
    required_functions = [
        "loadList",
        "openConversation",
        "renderMessages",
        "setComposer",
    ]
    for name in required_functions:
        expect(re.search(rf"function\s+{name}\s*\(", messenger_slice) is not None or re.search(rf"async\s+function\s+{name}\s*\(", messenger_slice) is not None, f"{name} exists")

    expect("/api/pulse/communications/conversations?type=direct" in messenger_slice, "direct loads use communications API")
    expect("/api/pulse/communications/rooms" in messenger_slice, "room loads use communications API")
    expect("/api/pulse/communications/groups" in messenger_slice, "group loads use communications API")
    expect("/api/pulse/communications/conversations/${encodeURIComponent(id)}/messages" in messenger_slice, "selected chat loads through unified route")
    expect("/api/pulse/communications/conversations/${encodeURIComponent(state.activeId)}/messages" in messenger_slice, "selected chat sends through unified route")
    expect("legacy-" in source and "legacy_dashboard" in source, "legacy Dashboard direct bridge remains wired")
    expect("Something needs attention. Please try again." not in messenger_slice, "messenger UI avoids generic dead error")
    expect("Loading messages..." in messenger_slice, "selected chat shows real loading state")
    expect("Messages could not load." in messenger_slice, "load failures report concrete message")
    print("chat desktop mobile parity audit ok")


if __name__ == "__main__":
    main()
