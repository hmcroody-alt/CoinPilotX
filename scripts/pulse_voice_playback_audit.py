#!/usr/bin/env python3
"""Audit Communications V2 voice playback controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "pulse_messages_v2.css").read_text(encoding="utf-8")

for needle in [
    "voiceAttachmentHtml",
    "data-voice-play",
    "data-voice-progress",
    "data-voice-speed",
    "data-voice-audio",
    "toggleVoicePlayback",
    "setVoicePlaybackSpeed",
    "bindVoiceAudio",
    "1.5",
    "2",
]:
    assert needle in JS, f"missing playback logic {needle}"

for needle in [".voice-message", ".voice-message-controls", ".voice-waveform", "progress"]:
    assert needle in CSS, f"missing playback style {needle}"

print("pulse voice playback audit ok")
