#!/usr/bin/env python3
"""Audit Communications V2 typing indicators."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")

for token in ["comm_v2_typing", "set_typing", "typing_state", "expires_at"]:
    assert token in SERVICE
    print(f"PASS: {token}")
for token in ["/typing", "typing_indicator"]:
    assert token in ROUTES
    print(f"PASS: {token}")
for token in ["typingSummary", "others are typing", "sendTypingIndicator"]:
    assert token in JS
    print(f"PASS: {token}")

print("pulse typing indicator audit ok")
