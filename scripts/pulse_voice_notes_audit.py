#!/usr/bin/env python3
"""Aggregate Phase 2 voice notes audit."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = (ROOT / "reports" / "pulse_comm_v2_voice_notes.md").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")

for needle in [
    "Phase 2 delivers voice notes only",
    "MediaRecorder",
    "startVoiceRecording",
    "uploadVoiceDraft",
    "voice_note",
    "COMM_V2_VOICE_MAX_SECONDS",
]:
    assert needle in REPORT + JS + SERVICE, needle

print("pulse voice notes audit ok")
