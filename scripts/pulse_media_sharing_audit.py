#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["data-attachment-sheet", "Camera", "Photo", "Video", "File", "Voice", "data-attachment-preview"]:
        expect(token in HTML, f"attachment UI includes {token}")
    for token in ["addAttachmentFiles", "uploadAttachmentQueue", "clearAttachmentQueue", "client_message_id"]:
        expect(token in JS, f"media sharing JS includes {token}")
    expect(".attachment-sheet" in CSS and ".attachment-preview-card" in CSS, "media sharing CSS exists")
    print("pulse media sharing audit ok")

if __name__ == "__main__":
    main()
