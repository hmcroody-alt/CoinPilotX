#!/usr/bin/env python3
"""Audit Status viewer close button behavior and layering."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


expect("clearInterval(statusViewerTimer)" in BOT, "Close clears status progress timer")
expect("video,audio" in BOT and "media.pause()" in BOT, "Close pauses active media")
expect("history.back()" in BOT and "location.href='/pulse'" in BOT, "Close has history and /pulse fallback")
expect("e.stopPropagation();closeStatusViewer()" in BOT, "Close tap cannot fall through to overlays")
expect(".pulse-status-story-close" in CSS and "z-index: 30" in CSS, "Close button is above media overlays")
expect("touch-action: manipulation" in CSS, "Close button is mobile tap optimized")
print("status viewer close button audit ok")
