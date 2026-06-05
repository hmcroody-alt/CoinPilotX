#!/usr/bin/env python3
"""Audit Communications V2 voice note recording and playback UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


for token in ["data-voice-start", "data-voice-pause", "data-voice-resume", "data-voice-stop", "data-voice-discard", "data-voice-timer", "data-voice-waveform"]:
    require(token in HTML, f"voice recorder includes {token}")
for token in ["Recording...", "Paused", "Ready to send"]:
    require(token in JS, f"voice recorder exposes state label {token}")
for token in ["data-voice-play", "data-voice-progress", "data-voice-speed", "1.5x", "2x", "data-voice-current", "data-voice-duration"]:
    require(token in JS, f"voice playback includes {token}")
require("button.dataset.playing" in JS and 'data-playing="true"' in CSS, "voice play/pause visual state is wired")
require("grid-template-columns: 38px minmax(110px, 1fr) auto auto" in CSS, "desktop voice bubble has play/waveform/duration/speed layout")
require("grid-template-columns: 36px minmax(88px, 1fr) auto" in CSS, "mobile voice bubble has compact layout")
require("voicePulse" in CSS, "recording indicator has subtle pulse animation")

print("pulse comm v2 voice note ui audit ok")
