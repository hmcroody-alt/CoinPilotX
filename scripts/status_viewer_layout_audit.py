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
    require("Content-first PulseSoc Status viewer" in CSS, "viewer declares content-first design contract")
    require(".pulse-status-story-shell::before" in CSS, "viewer has subtle ambient Pulse background layer")
    require(".pulse-status-story-media::before" in CSS, "media stage has ambient glow for unused aspect space")
    require(".pulse-status-story-footer" in CSS and "bottom: auto !important" in CSS and "border-radius: 999px" in CSS, "author metadata is compact top capsule")
    require(".pulse-status-story-actions" in CSS and "opacity: 0" in CSS and "is-ui-visible" in CSS, "actions fade away until the user reveals them")
    require("[data-status-story-count]" in CSS and "[data-status-viewer-count]" in CSS and "display: none !important" in CSS, "analytics stay hidden from the clean viewer chrome")
    require("data-status-story-prev" in BOT and "data-status-story-next" in BOT and "data-status-story-close" in BOT, "home story viewer keeps navigation controls")
    require("data-status-viewer-prev" in BOT and "data-status-viewer-next" in BOT and "data-status-viewer-close" in BOT, "Status page viewer keeps navigation controls")
    require("display: none !important" in CSS and ".pulse-status-story-nav" in CSS, "visible next/back buttons are hidden")
    require("button.hidden=false" in BOT, "Sound control stays available after unmuting")
    require("revealStatusChrome" in (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8"), "viewer auto-hides chrome and reveals it on interaction")
    require("status-content-first-20260621a" in BOT, "status CSS/JS cache keys are bumped")
    require("padding:42px16px16px" not in compact and "linear-gradient(180deg,transparent,rgba(2,6,23,.88))" not in compact, "old large bottom metadata panel styling is removed")


if __name__ == "__main__":
    main()
