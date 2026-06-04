#!/usr/bin/env python3
"""Audit audio calls remain gated for Phase 5."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAGS = (ROOT / "pulse_communications_v2" / "flags.py").read_text(encoding="utf-8")
REPORT = (ROOT / "reports" / "pulse_comm_v2_audio_calls.md").read_text(encoding="utf-8")

assert "PULSE_AUDIO_CALLS_ENABLED" in FLAGS
assert "Audio calls are Phase 5" in REPORT
print("pulse audio calls audit ok")
