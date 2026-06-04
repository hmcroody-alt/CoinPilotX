#!/usr/bin/env python3
"""Audit media sharing foundation and Phase 3 boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
REPORT = (ROOT / "reports" / "pulse_comm_v2_media_sharing.md").read_text(encoding="utf-8")

assert "attachments/upload" in ROUTES
assert "Phase 3 remains" in REPORT
print("pulse media sharing audit ok")
