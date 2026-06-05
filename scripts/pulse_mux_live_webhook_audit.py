#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["/api/pulse/live/mux/webhook", "video.live_stream.connected", "video.live_stream.disconnected", "is_live=CASE", "mux_live_status"]:
        expect(token in BOT, f"mux live webhook includes {token}")
    print("pulse mux live webhook audit ok")

if __name__ == "__main__":
    main()
