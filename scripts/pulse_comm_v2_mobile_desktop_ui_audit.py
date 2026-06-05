#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["data-thread-search", "data-thread-mute", "data-thread-more", "thread-avatar", "comm-details"]:
        expect(token in HTML, f"chat header/details includes {token}")
    for token in ["grid-template-columns: 44px 44px minmax(0, 1fr) 48px", "height: 100dvh", "env(safe-area-inset-bottom)", "message-stack", "min-height: 58px"]:
        expect(token in CSS, f"responsive chat UI includes {token}")
    print("pulse comm v2 mobile desktop ui audit ok")

if __name__ == "__main__":
    main()
