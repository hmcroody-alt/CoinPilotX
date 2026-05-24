#!/usr/bin/env python3
"""Audit Pulse Live replay/VOD lifecycle helpers."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import live_archive_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    live = {"id": 42, "title": "Replay Audit", "status": "live", "audience": "public", "thumbnail_url": "https://cdn.coinpilotx.app/live/replay.jpg"}
    active = live_archive_service.replay_manifest(live, [{"body": "first"}, {"body": "second"}])
    require(active["status"] == "recording", "active live session reports recording replay lifecycle")
    require(active["chat_replay_events"] == 2, "chat replay event count is preserved")
    ended = live_archive_service.replay_manifest({**live, "status": "ended", "ended_at": "2026-05-24T00:00:00"}, [])
    require(ended["status"] == "ready", "ended live session can become replay-ready")
    payload = live_archive_service.publish_replay_payload({**live, "duration_seconds": 1800}, peak_viewers=21, engagement=74)
    require(payload["visibility"] == "public", "replay publish payload preserves public visibility")
    require(payload["peak_viewers"] == 21 and payload["engagement_score"] == 74, "replay payload preserves performance metadata")
    require({"publish_replay", "save_private", "clip_highlights", "convert_to_reels", "download_mp4", "delete_replay"}.issubset(set(live_archive_service.post_live_actions())), "post-live workflow exposes replay actions")
    print("live replay audit ok")


if __name__ == "__main__":
    main()
