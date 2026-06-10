#!/usr/bin/env python3
"""Guard PulseSoc Browser Live -> LiveKit -> Mux bridge behavior."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_live_studio.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_live_studio.css").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    require("pulse_livekit_start_mux_egress" in BOT, "backend has LiveKit egress starter")
    require("StartRoomCompositeEgress" in BOT, "backend calls LiveKit room composite egress")
    require("roomRecord" in BOT, "egress token includes roomRecord permission")
    require("pulse_livekit_mux_destination" in BOT and "MUX_RTMP_INGEST_URL" in BOT, "backend builds Mux RTMP destination safely")
    require("browser_live_egress" in BOT, "browser publish records LiveKit egress state")
    require('"requires_rtmp_encoder": False' in BOT, "browser publish no longer requires OBS encoder")
    require("livekit-client.umd.js" in BOT, "Studio loads LiveKit browser client")
    require("connectLiveKitRoom" in JS and "publishToLiveKit" in JS, "Studio publishes browser tracks to LiveKit")
    require("createLocalTracks" in JS, "Studio uses LiveKit local camera/mic tracks")
    require("Browser Live is publishing through LiveKit and forwarding to Mux" in BOT + JS, "Studio copy reflects real bridge")
    require("Camera preview ready" not in BOT, "old face-covering camera overlay copy removed")
    require(".is-camera-active .live-preview-stage .live-ready-state" in CSS, "active camera hides ready overlay")
    require("data-secret-live-value" in BOT, "stream key is masked by default in Studio")
    print("pulse livekit mux bridge audit ok")


if __name__ == "__main__":
    main()
