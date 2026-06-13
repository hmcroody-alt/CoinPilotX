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
    require("StartRoomCompositeEgress" in BOT, "backend tries room composite egress first")
    require("StartParticipantEgress" in BOT, "backend keeps host participant egress fallback")
    require(BOT.find('strategy="room_composite"') < BOT.find('strategy="participant_fallback"'), "room composite is preferred before participant fallback")
    require("pulse_livekit_post_egress" in BOT, "backend captures sanitized LiveKit egress failures")
    require("pulse_livekit_egress_resource_exhausted" in BOT, "backend detects LiveKit egress quota exhaustion")
    require("participant_error" in BOT and "room_composite_error" in BOT, "backend returns safe egress rejection diagnostics")
    require("rtmps?://[^\\s\\\"'<>]+" in BOT and "[redacted]" in BOT, "backend redacts RTMP destinations from egress errors")
    require("roomRecord" in BOT, "egress token includes roomRecord permission")
    require("pulse_livekit_mux_destination" in BOT and "MUX_RTMP_INGEST_URL" in BOT, "backend builds Mux RTMP destination safely")
    require("pulse_livekit_wait_for_host_tracks" in BOT and "ListParticipants" in BOT, "backend verifies LiveKit host tracks before egress")
    require("waiting_for_tracks" in BOT and "retryable" in BOT, "backend returns retryable wait state instead of early egress")
    require("browser_live_egress" in BOT, "browser publish records LiveKit egress state")
    require("browser_live_livekit_direct" in BOT, "browser publish has LiveKit direct fallback state")
    require("egress_quota_exhausted" in BOT, "browser publish persists quota fallback status")
    require("livekit_direct" in JS, "viewer handles LiveKit direct playback without fake HLS")
    require('"requires_rtmp_encoder": False' in BOT, "browser publish no longer requires OBS encoder")
    require("livekit-client.umd.js" in BOT, "Studio loads LiveKit browser client")
    require("connectLiveKitRoom" in JS and "publishToLiveKit" in JS, "Studio publishes browser tracks to LiveKit")
    require("waitForLiveKitConnected" in JS and "livekitConnectPromise" in JS, "Studio waits for connected state and prevents duplicate joins")
    require("livekitPublishPromise" in JS and "livekitPublishComplete" in JS, "Studio prevents duplicate local track publishing")
    require("backend_waiting_for_tracks" in JS and "retry_after_ms" in JS, "Studio retries egress start while LiveKit publishes settle")
    require("createLocalTracks" in JS, "Studio uses LiveKit local camera/mic tracks")
    require("Browser Live is publishing through LiveKit and forwarding to Mux" in BOT + JS, "Studio copy reflects real bridge")
    require("Camera remains local until forwarding recovers" not in JS, "Studio never regresses to local-only egress failure copy")
    require("Camera preview ready" not in BOT, "old face-covering camera overlay copy removed")
    require(".is-camera-active .live-preview-stage .live-ready-state" in CSS, "active camera hides ready overlay")
    require("data-secret-live-value" in BOT, "stream key is masked by default in Studio")
    print("pulse livekit mux bridge audit ok")


if __name__ == "__main__":
    main()
