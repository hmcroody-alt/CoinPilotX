#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_live_studio.css").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["live-mobile-controls", "@media (max-width: 680px)", "env(safe-area-inset-bottom)"]:
        expect(token in CSS + BOT, f"live mobile UX includes {token}")
    print("pulse mux live mobile audit ok")

if __name__ == "__main__":
    main()
