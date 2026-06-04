#!/usr/bin/env python3
"""Audit Phase 2 voice notes remain gated and documented."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAGS = (ROOT / "pulse_communications_v2" / "flags.py").read_text(encoding="utf-8")
REPORT = (ROOT / "reports" / "pulse_comm_v2_voice_notes.md").read_text(encoding="utf-8")

assert "PULSE_VOICE_NOTES_ENABLED" in FLAGS
assert "Voice notes are Phase 2" in REPORT
print("pulse voice notes audit ok")
