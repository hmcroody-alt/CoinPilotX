#!/usr/bin/env python3
"""Audit Pulse Communications V2 Twilio foundation."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    twilio = ROOT / "pulse_communications_v2" / "twilio_service.py"
    service = ROOT / "pulse_communications_v2" / "service.py"
    routes = ROOT / "pulse_communications_v2" / "routes.py"
    text = twilio.read_text()
    ast.parse(text)
    for name in ["send_sms_verification", "send_message_alert", "send_room_invite_alert", "send_security_alert"]:
        expect(name in text, f"Twilio method missing: {name}", failures)
    expect("COMM_V2_TWILIO_NOTIFICATIONS_ENABLED" in text, "Twilio enable flag missing", failures)
    expect("COMM_V2_TWILIO_DRY_RUN" in text, "Twilio dry-run flag missing", failures)
    expect("dry_run" in text, "Twilio dry-run behavior missing", failures)
    expect("/notifications/preview" in routes.read_text(), "Twilio preview route missing", failures)
    expect("twilio_notification_preview" in service.read_text(), "Twilio service integration missing", failures)
    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS pulse_comm_v2_twilio_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
