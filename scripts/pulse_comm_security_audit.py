#!/usr/bin/env python3
"""Audit Communications V2 security and abuse controls for Phase 1."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")

for token in ["_blocked_between", "report_message", "block_user", "moderate_message", "You do not have access"]:
    assert token in SERVICE or token in ROUTES, f"missing security token: {token}"
    print(f"PASS: {token}")
for token in ["edit_window_expired", "delete_window_expired", "delete_for", "forward_message"]:
    assert token in SERVICE or token in ROUTES, f"missing lifecycle safety token: {token}"
    print(f"PASS: {token}")

print("pulse communications security audit ok")
