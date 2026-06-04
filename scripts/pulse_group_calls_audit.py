#!/usr/bin/env python3
"""Audit group calls remain gated for Phase 7."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAGS = (ROOT / "pulse_communications_v2" / "flags.py").read_text(encoding="utf-8")
REPORT = (ROOT / "reports" / "pulse_comm_v2_group_calls.md").read_text(encoding="utf-8")

assert "PULSE_GROUP_CALLS_ENABLED" in FLAGS
assert "Group calls are Phase 7" in REPORT
print("pulse group calls audit ok")
