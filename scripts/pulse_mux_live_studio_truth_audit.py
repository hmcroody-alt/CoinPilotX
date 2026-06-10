#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_live_studio.js").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    forbidden = ["Browser camera preview is local only", "Browser camera preview only — use RTMP/OBS to go live", "0 kbps and 0 FPS are expected until an RTMP encoder connects to Mux", "Browser Live Mode requires a real WebRTC/WHIP or RTMP bridge and is not active"]
    for token in forbidden:
        expect(token not in BOT, f"old preview-only/OBS copy removed: {token}")
    for token in ["Browser Live is publishing through LiveKit and forwarding to Mux", "LiveKit room", "Check Mux Status", "data-copy-live-value", "data-secret-live-value"]:
        expect(token in BOT, f"live studio truthful UI includes {token}")
    for token in ["checkMuxStatus", "scheduleMuxPolling", "copyLiveValue", "connectLiveKitRoom", "publishToLiveKit"]:
        expect(token in JS, f"live studio JS includes {token}")
    print("pulse mux live studio truth audit ok")

if __name__ == "__main__":
    main()
