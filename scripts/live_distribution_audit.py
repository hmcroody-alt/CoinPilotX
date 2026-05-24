#!/usr/bin/env python3
"""Audit public Pulse Live distribution metadata."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import live_discovery_service, live_distribution_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    session = {
        "id": 21,
        "status": "live",
        "title": "Distribution Audit",
        "category": "Creator QA",
        "viewer_count": 9,
        "stream_uuid": "dist21",
        "hls_url": "https://live.coinpilotxai.app/hls/dist21.m3u8",
        "webrtc_room_id": "pulse-webrtc-dist21",
        "studio_url": "/pulse/live/studio/21",
    }
    manifest = live_distribution_service.playback_manifest(session)
    require(manifest["supports_hls"], "public playback exposes HLS")
    require(manifest["supports_webrtc"], "public playback exposes WebRTC room")
    require(manifest["fallback_mode"] == "ambient-ready-state", "viewer fallback avoids dead black screen")
    card = live_distribution_service.discovery_card(session, "Creator")
    require(card["playback"]["hls_url"].endswith(".m3u8"), "live discovery carries playback manifest")
    require(card["viewer_count"] == 9, "live discovery carries viewer count")
    social_card = live_discovery_service.live_card(session, "Creator")
    require({"pulse_feed", "creator_profile", "reels_live", "live_discovery", "notifications"}.issubset(set(social_card["surfaces"])), "live distributes to feed, profile, reels/live discovery, and notifications")
    require(social_card["autoplay_preview"], "live discovery supports autoplay preview when playback exists")
    print("live distribution audit ok")


if __name__ == "__main__":
    main()
