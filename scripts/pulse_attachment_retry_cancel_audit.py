#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["data-attachment-retry", "uploadAttachmentItem(item)", "removeAttachment", "client_message_id", "clearAttachmentQueue"]:
        expect(token in JS, f"retry/cancel includes {token}")
    print("pulse attachment retry cancel audit ok")

if __name__ == "__main__":
    main()
