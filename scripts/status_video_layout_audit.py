#!/usr/bin/env python3
"""Audit Status viewer video fills the available viewer space."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


expect(".pulse-status-story-shell" in CSS and "height: 100dvh" in CSS, "mobile Status viewer fills viewport")
expect(".pulse-status-story-media .pulse-media-wrap" in CSS, "Status media wrapper fills story media")
expect(".pulse-status-story-media .pulse-media-wrap video" in CSS, "Status videos have explicit sizing")
expect("object-fit: cover !important" in CSS, "Status video uses immersive cover fit")
expect(".pulse-status-story-close" in CSS and "z-index: 30" in CSS, "Close remains above full-size media")
print("status video layout audit ok")
