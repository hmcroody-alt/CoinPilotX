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
    for token in ["presenceLabel", "presenceClass", "presence-online", "presence-away", "presence-offline"]:
        expect(token in JS + CSS, f"presence visual includes {token}")
    print("pulse comm v2 presence audit ok")

if __name__ == "__main__":
    main()
