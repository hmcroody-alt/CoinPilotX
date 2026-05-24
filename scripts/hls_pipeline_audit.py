#!/usr/bin/env python3
"""Audit Pulse Live HLS/LL-HLS pipeline readiness."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import live_distribution_service, live_stream_engine  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    setup = live_stream_engine.start_stream(1, "HLS Audit", "Creator QA")
    require(setup["hls_url"].endswith(".m3u8"), "stream setup creates HLS manifest URL")
    require("rtmp" in setup["rtmp_url"], "stream setup creates RTMP ingest URL")
    require("webrtc" in setup["webrtc_room_id"], "stream setup creates WebRTC room id")
    manifest = live_distribution_service.playback_manifest({"id": 1, **setup, "status": "live"})
    require(manifest["latency_mode"] == "low-latency", "distribution manifest advertises low latency playback")
    require(manifest["supports_hls"], "distribution manifest supports HLS")
    print("hls pipeline audit ok")


if __name__ == "__main__":
    main()
