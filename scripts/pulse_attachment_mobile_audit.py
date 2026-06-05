#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["env(safe-area-inset-bottom)", ".attachment-sheet", "position: fixed", "border-radius: 22px 22px 0 0", ".composer"]:
        expect(token in CSS, f"mobile attachment UX includes {token}")
    print("pulse attachment mobile audit ok")

if __name__ == "__main__":
    main()
