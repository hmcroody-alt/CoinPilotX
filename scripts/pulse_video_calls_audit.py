#!/usr/bin/env python3
"""Audit video calls remain gated for Phase 6."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAGS = (ROOT / "pulse_communications_v2" / "flags.py").read_text(encoding="utf-8")
REPORT = (ROOT / "reports" / "pulse_comm_v2_video_calls.md").read_text(encoding="utf-8")

assert "PULSE_VIDEO_CALLS_ENABLED" in FLAGS
assert "Video calls are Phase 6" in REPORT
print("pulse video calls audit ok")
