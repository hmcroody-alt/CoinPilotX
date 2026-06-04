#!/usr/bin/env python3
"""Audit Communications V2 presence foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")

for token in ["comm_v2_presence", "comm_v2_user_settings", "presence_privacy", "heartbeat", "conversation_presence"]:
    assert token in SERVICE, f"missing presence service token: {token}"
    print(f"PASS: {token}")
for token in ["/presence/heartbeat", "/presence", "service.conversation_presence"]:
    assert token in ROUTES, f"missing presence route token: {token}"
    print(f"PASS: {token}")
for token in ["sendPresenceHeartbeat", "loadPresence", "Active now", "Last seen"]:
    assert token in JS, f"missing presence client token: {token}"
    print(f"PASS: {token}")

print("pulse presence audit ok")
