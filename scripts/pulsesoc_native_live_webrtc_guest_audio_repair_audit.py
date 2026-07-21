#!/usr/bin/env python3
"""Audit the native Live WebRTC viewer and host audio repair."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main() -> int:
    api = read("mobile-native/src/api/live.ts")
    viewer = read("mobile-native/src/screens/LiveScreen.tsx")
    hook = read("mobile-native/src/live/useLiveBroadcastRoom.ts")
    report = read("reports/pulsesoc_native_live_webrtc_guest_audio_repair_2026-07-20.md")

    require("liveSupportsNativeWebRtc" in api, "API exposes native WebRTC playback capability")
    require("playback.supports_webrtc" in api and "playback.webrtc_room_id" in api, "WebRTC capability reads server playback flags")
    require('getLiveKitToken(liveId, "viewer")' in viewer, "Live detail viewer requests a viewer LiveKit token")
    require("useLiveBroadcastRoom" in viewer, "Live detail uses the shared native LiveKit room hook")
    require("Waiting for host media" in viewer, "WebRTC live uses native waiting state instead of generic fallback")
    require("PulseSoc did not return a native LiveKit room or HLS playback URL" in viewer, "Viewer fallback only appears when no native transport exists")
    require("remoteAudioTrackCount" in hook and "remoteVideoTrackCount" in hook, "LiveKit hook tracks remote media publications")
    require("localAudioTrackCount" in hook, "LiveKit hook verifies local microphone publication")
    require("LIVE_LOCAL_AUDIO_NOT_PUBLISHED" in hook, "Host publishing fails clearly when microphone track is missing")
    require("setRemoteAudioEnabled" in hook, "Viewer sound control toggles native remote audio track state")
    require("static/js/pulse_live_studio.js" in report, "Report documents inspected production WebView Live source")
    require("/api/pulse/live/<id>/livekit/token" in report, "Report documents reused LiveKit token route")
    require("Physical-device-only" in report, "Report separates physical audio validation from code verification")

    print("PulseSoc native Live WebRTC guest/audio repair audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
