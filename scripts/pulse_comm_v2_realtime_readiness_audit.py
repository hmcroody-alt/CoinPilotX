#!/usr/bin/env python3
"""Audit that Communications V2 realtime readiness is documented honestly."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")
REPORT = (ROOT / "reports" / "pulse_comm_v2_realtime_readiness.md").read_text(encoding="utf-8")

assert "attachments/upload" in ROUTES
assert "voice/start" in ROUTES and "video/start" in ROUTES
assert "Voice and video are reserved for Phase 2." in ROUTES
assert "audio_voice_notes" in SERVICE
for heading in ["Voice Messages", "Media Sharing", "Audio And Video Calls", "Safe Implementation Order"]:
    assert heading in REPORT, f"missing readiness section: {heading}"
    print(f"PASS: {heading}")

print("pulse communications v2 realtime readiness audit ok")
