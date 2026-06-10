#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["mux_live_service.playback_url(live.get(\"mux_playback_id\")", "Stream has not started yet", "Start Browser Live", "data-live-player"]:
        expect(token in BOT, f"viewer truth includes {token}")
    expect("connect OBS/RTMP before viewers see live video" not in BOT, "viewer no longer says OBS is required before playback")
    print("pulse mux live viewer audit ok")

if __name__ == "__main__":
    main()
