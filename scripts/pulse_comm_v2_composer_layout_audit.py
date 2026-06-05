#!/usr/bin/env python3
"""Audit Communications V2 composer layout contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


composer_start = HTML.index('<form class="composer"')
composer_end = HTML.index("</form>", composer_start)
composer = HTML[composer_start:composer_end]
order = [
    "data-toggle-attachments",
    "data-message-input",
    "data-voice-start",
    'class="send-btn"',
]
positions = [composer.index(token) for token in order]
require(positions == sorted(positions), "composer order is attachment, input, mic, send")
require("grid-template-columns: 44px minmax(0, 1fr) 44px 48px" in CSS, "desktop composer uses full-width plus/input/mic/send layout")
require("grid-template-columns: 42px minmax(0, 1fr) 42px 46px" in CSS, "mobile composer uses compact plus/input/mic/send layout")
require("width: 100%" in CSS and "min-width: 0" in CSS, "message input can occupy available width")
require(".composer .send-btn" in CSS and "linear-gradient(135deg, #36e58f, #6edff6)" in CSS, "send button is aligned and visually primary")

print("pulse comm v2 composer layout audit ok")
