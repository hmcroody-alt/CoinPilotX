#!/usr/bin/env python3
"""Consolidated audit for Pulse Live discovery, transport, replay, audio, scenes, and multistreaming."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

LIVE_AUDITS = [
    "live_stream_audit.py",
    "live_distribution_audit.py",
    "live_feed_insertion_audit.py",
    "live_feed_integration_audit.py",
    "live_media_transport_audit.py",
    "live_audio_video_pipeline_audit.py",
    "live_viewer_playback_audit.py",
    "live_replay_audit.py",
    "live_multistream_audit.py",
    "live_restream_audit.py",
    "live_platform_destination_audit.py",
    "live_post_broadcast_options_audit.py",
    "live_audio_audit.py",
    "live_scene_audit.py",
    "live_chat_audit.py",
    "live_mobile_audit.py",
    "live_stream_health_audit.py",
]


def run(script: str):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="")
        raise AssertionError(f"{script} failed")


def main():
    for script in LIVE_AUDITS:
        run(script)
    print("live pipeline audit ok")


if __name__ == "__main__":
    main()
