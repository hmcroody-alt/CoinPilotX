#!/usr/bin/env python3
"""Audit Communications V2 read receipt privacy and states."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")

for token in ["comm_v2_read_receipts", "read_receipts_enabled", "_read_receipts_allowed", "delivered_at", "seen_at"]:
    assert token in SERVICE, f"missing read receipt token: {token}"
    print(f"PASS: {token}")
for token in ["delivery_status", "sent", "delivered", "seen"]:
    assert token in JS or token in SERVICE, f"missing state token: {token}"
    print(f"PASS: {token}")

print("pulse read receipts audit ok")
