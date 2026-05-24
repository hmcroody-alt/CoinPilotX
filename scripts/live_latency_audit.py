#!/usr/bin/env python3
"""Audit Pulse Live playback/distribution latency metadata."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import live_archive_service, live_distribution_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    session = {
        "id": 12,
        "status": "live",
        "stream_uuid": "abc123",
        "hls_url": "https://live.coinpilotxai.app/hls/abc123.m3u8",
        "webrtc_room_id": "pulse-webrtc-abc123",
        "title": "Latency Audit",
    }
    playback = live_distribution_service.playback_manifest(session)
    require(playback["supports_hls"], "HLS playback is advertised")
    require(playback["supports_webrtc"], "WebRTC playback is advertised")
    require(playback["latency_mode"] == "low-latency", "low latency mode metadata exists")
    card = live_distribution_service.discovery_card(session, "Audit Creator")
    require(card["live_url"] == "/pulse/live/12", "live discovery card points to public viewer")
    replay = live_archive_service.replay_manifest(session, [{"body": "hello"}])
    require(replay["chat_replay_events"] == 1, "archive manifest carries chat replay count")
    print("live latency audit ok")


if __name__ == "__main__":
    main()
