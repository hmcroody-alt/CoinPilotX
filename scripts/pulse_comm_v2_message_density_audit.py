#!/usr/bin/env python3
"""Audit compact Communications V2 message bubbles."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


require("sender?.display_name" in JS, "message bubbles render sender names")
require("shortTime(item.created_at)" in JS, "message bubbles render timestamps")
require("delivery_status" in JS, "own message bubbles render delivery/read status")
require("item.is_edited" in JS, "edited label is available")
require("reply_preview" in JS and "data-jump-message" in JS, "reply preview is available")
require("reaction-summary" in JS and "data-reaction-menu" in JS, "reactions and message menu are available")
require("padding: 7px 10px 6px" in CSS, "message bubble padding is compact")
require("max-width: min(620px, 70%)" in CSS, "desktop message width is readable, not giant")
require("max-width: 84%" in CSS, "mobile message width leaves breathing room")
require("font-size: 14px" in CSS, "message text uses compact readable size")

print("pulse comm v2 message density audit ok")
