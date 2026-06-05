#!/usr/bin/env python3
"""Audit Communications V2 chat header polish."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


for token in ["data-thread-avatar", "data-thread-title", "data-thread-subtitle", "data-thread-search", "data-toggle-details", "data-thread-more"]:
    require(token in HTML, f"thread header includes {token}")
require("data-thread-mute" in HTML, "mute action remains available for future menu wiring")
require("[data-thread-mute]" in CSS and "display: none" in CSS, "future call/video/mute-style icon is not visually cluttering header")
require("presenceLabel(threadPresence)" in JS, "header shows presence/last seen")
require("thread-avatar presence-" in JS, "header avatar reflects presence state")
require("grid-template-columns: auto 44px minmax(0, 1fr) auto" in CSS, "desktop header has avatar/name/actions layout")
require("grid-template-columns: 38px 40px minmax(0, 1fr) auto" in CSS, "mobile header stays compact")

print("pulse comm v2 header audit ok")
