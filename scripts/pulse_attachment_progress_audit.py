#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ['status = "uploading"', 'status = "uploaded"', 'status = "failed"', "progress", "data-attachment-retry", "data-attachment-remove"]:
        expect(token in JS, f"attachment progress includes {token}")
    expect("progress" in CSS and "[data-state=" in CSS, "attachment progress CSS exists")
    print("pulse attachment progress audit ok")

if __name__ == "__main__":
    main()
