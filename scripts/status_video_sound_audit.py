#!/usr/bin/env python3
"""Audit Status viewer video sound behavior."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
STATUS_VIEWER = (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


expect("playStatusViewerVideo" in BOT, "Status viewer has dedicated playback handler")
expect("window.PulseMediaRenderer?.soundEnabled?.()" in BOT, "Status viewer reads shared Pulse sound preference")
expect("status-autoplay-fallback" in BOT, "Status viewer falls back to muted autoplay per attempt")
expect("setSoundEnabled?.(!nextMuted)" in BOT, "Status sound toggle persists shared preference")
expect("controls autoplay muted playsinline" not in STATUS_VIEWER, "Status fallback markup is not forced muted")
expect("data-status-viewer-mute" in BOT and "Tap for sound" in BOT, "Status sound affordance exists only when needed")
print("status video sound audit ok")
