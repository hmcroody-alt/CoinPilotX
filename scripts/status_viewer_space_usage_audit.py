#!/usr/bin/env python3
"""Audit Pulse Status viewer avoids reserved black metadata space."""

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
    require("bottom: max(18px, calc(14px + env(safe-area-inset-bottom)))" in CSS, "metadata overlays sit above safe area without reserving a panel")
    require("position: fixed" in CSS and "bottom: max(16px, env(safe-area-inset-bottom))" in CSS, "reply input appears as focused overlay")
    require("object-fit: cover !important" in CSS, "video fills the media stage")
    require("max-width: none !important" in CSS and "max-height: none !important" in CSS, "legacy media caps are removed inside viewer")
    viewer_css = CSS[CSS.find(".pulse-status-story-viewer"):CSS.find(".pulse-status-card.style-glass")]
    require("radial-gradient(circle at 20% 8%" in viewer_css and ".pulse-status-story-media::before" in viewer_css, "viewer uses ambient gradients instead of a plain black metadata area")
    require("data-status-story-actions" in BOT or "pulse-status-story-actions" in BOT, "overlay action controls are present")
    require("Like</button><button type=\"button\" data-status-story-comment" in BOT, "home viewer action row is compact in markup")
    require("Like</button>" in BOT and "data-status-viewer-mute" in BOT, "dedicated viewer action controls remain wired")


if __name__ == "__main__":
    main()
