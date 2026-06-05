#!/usr/bin/env python3
"""Audit Communications V2 voice recording UI and client flow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates" / "pulse_messages_v2.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")

for needle in [
    "data-voice-start",
    "data-voice-panel",
    "data-voice-pause",
    "data-voice-resume",
    "data-voice-stop",
    "data-voice-discard",
    "data-voice-preview",
]:
    assert needle in TEMPLATE, f"missing template control {needle}"

for needle in [
    "navigator.mediaDevices",
    "MediaRecorder",
    "startVoiceRecording",
    "pauseVoiceRecording",
    "resumeVoiceRecording",
    "stopVoiceRecording",
    "finalizeVoiceRecording",
    "Microphone permission was denied",
]:
    assert needle in JS, f"missing recorder logic {needle}"

print("pulse voice recording audit ok")
