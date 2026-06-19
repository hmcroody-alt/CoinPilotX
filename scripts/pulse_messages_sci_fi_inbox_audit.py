#!/usr/bin/env python3
"""Audit the PulseSoc Messages sci-fi inbox redesign and preview sanitization."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main() -> None:
    expect("Search people, groups, messages..." in HTML, "search placeholder is production copy")
    expect("data-active-rail" in HTML and "function renderActiveRail" in JS, "active row is wired")
    for token in ['data-filter="all"', 'data-filter="direct"', 'data-filter="groups"', 'data-filter="rooms"', 'data-filter="unread"']:
        expect(token in HTML, f"Command Center filter exists: {token}")
    expect("data-ai-summary" in JS and "active-ai" in JS, "Pulse AI is compact and wired when enabled")
    expect("data-quick-call" not in JS, "row-level call/video buttons removed from renderer")
    expect(".conversation-quick-actions {\n  display: none !important;" in CSS, "old row quick actions are suppressed")
    expect("sanitizePreviewText" in JS and "containsLocalPath" in JS, "client preview sanitizer exists")
    expect("pulse_safe_message_preview" in (ROOT / "bot.py").read_text(encoding="utf-8"), "legacy serializer preview sanitizer exists")
    expect("_safe_preview" in (ROOT / "pulse_communications_v2/service.py").read_text(encoding="utf-8"), "v2 serializer preview sanitizer exists")

    import bot
    from pulse_communications_v2 import service

    path_preview = "/Users/hmcherie/Desktop/CoinPilotX/uploads/private/photo.png"
    expect(bot.pulse_safe_message_preview(path_preview, "image") == "Photo", "legacy image path preview becomes Photo")
    expect(bot.pulse_safe_message_preview(path_preview, "file") == "File", "legacy file path preview becomes File")
    expect(service._safe_preview(path_preview, "video") == "Video", "v2 video path preview becomes Video")
    expect("Users/hmcherie" not in service._safe_preview(path_preview, "text"), "v2 text path preview hides filesystem")
    legacy_payload = bot._pulse_message_payload({"id": 1, "conversation_id": 1, "sender_user_id": 2, "body": path_preview, "message_type": "image"}, 1)
    expect(legacy_payload["body"] == "Photo", "legacy thread body hides filesystem path")
    class DummyCursor:
        def execute(self, *args, **kwargs):
            return None
        def fetchall(self):
            return []
        def fetchone(self):
            return None
    v2_payload = service._message_payload(DummyCursor(), {"id": 1, "conversation_id": 1, "sender_user_id": 2, "body": path_preview, "message_type": "video"}, 1)
    expect(v2_payload["body"] == "Video", "v2 thread body hides filesystem path")
    print("pulse messages sci-fi inbox audit ok")


if __name__ == "__main__":
    main()
