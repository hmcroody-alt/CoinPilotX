#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    expect("multiple" in HTML, "file inputs support multi-select")
    for token in ["state.maxAttachments", "moveAttachment", "data-attachment-move", "media_ids: [...mediaIds"]:
        expect(token in JS, f"multi attachment includes {token}")
    print("pulse multi attachment audit ok")

if __name__ == "__main__":
    main()
