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
    expect("data-typing-pill" in HTML, "typing pill exists")
    for token in ["renderTypingPill", "typingSummary", "data-typing-state", "typing_indicator"]:
        expect(token in JS + HTML, f"typing behavior includes {token}")
    expect(".typing-pill" in CSS and "is-visible" in CSS, "typing fade CSS exists")
    print("pulse comm v2 typing audit ok")

if __name__ == "__main__":
    main()
