#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "pulse_communications_v2/service.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["_validate_attachment_upload", "blocked_attachment_type", "attachment_size_exceeded", "too_many_attachments", "virus_scan", "moderation_scan"]:
        expect(token in SERVICE, f"server attachment security includes {token}")
    for token in ["blocked", "uploadLimits", "validateAttachment"]:
        expect(token in JS, f"client attachment validation includes {token}")
    print("pulse attachment security audit ok")

if __name__ == "__main__":
    main()
