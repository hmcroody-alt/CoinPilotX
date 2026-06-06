#!/usr/bin/env python3
"""Audit immersive Pulse Status viewer layout contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    compact = "".join(CSS.split())
    require("height: min(96dvh, 940px)" in CSS, "desktop story frame consumes nearly all viewer height")
    require("height: 100dvh" in CSS and "min-height: 100dvh" in CSS, "mobile story frame fills viewport")
    require(".pulse-status-story-shell::before" in CSS, "viewer has ambient Pulse background layer")
    require(".pulse-status-story-media::before" in CSS, "media stage has ambient glow for unused aspect space")
    require(".pulse-status-story-footer" in CSS and "left: 16px" in CSS and "right: 92px" in CSS, "author metadata is compact lower-left overlay")
    require(".pulse-status-story-actions" in CSS and "top: 28%" in CSS and "right: 14px" in CSS, "actions float as side overlay controls")
    require("[data-status-story-count]" in CSS and "[data-status-viewer-count]" in CSS, "views render as compact overlay badges")
    require("data-status-story-prev" in BOT and "data-status-story-next" in BOT and "data-status-story-close" in BOT, "home story viewer keeps navigation controls")
    require("data-status-viewer-prev" in BOT and "data-status-viewer-next" in BOT and "data-status-viewer-close" in BOT, "Status page viewer keeps navigation controls")
    require("button.hidden=false" in BOT, "Sound control stays available after unmuting")
    require("padding:42px16px16px" not in compact and "linear-gradient(180deg,transparent,rgba(2,6,23,.88))" not in compact, "old large bottom metadata panel styling is removed")


if __name__ == "__main__":
    main()
