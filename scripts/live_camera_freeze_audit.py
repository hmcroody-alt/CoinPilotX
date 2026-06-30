#!/usr/bin/env python3
"""Audit mobile Live camera freeze self-healing safeguards."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_FILES = [
    ROOT / "static/js/pulse_live_studio_runtime.js",
    ROOT / "static/js/pulse_live_studio.js",
]
BOT = ROOT / "bot.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def audit_live_file(path: Path) -> None:
    source = read(path)
    name = path.relative_to(ROOT).as_posix()
    require("createLiveHealthManager" in source, f"{name} defines Live Health Manager")
    require("live_video_freeze_detected" in source and "freezeThresholdMs" in source, f"{name} detects frozen video")
    require("requestVideoFrameCallback" in source, f"{name} uses requestVideoFrameCallback for rendered frame health")
    require("collectVideoSenderStats" in source and ".getStats" in source, f"{name} has getStats fallback for publisher video")
    require('addEventListener?.("ended"' in source, f"{name} handles video track ended events")
    require('addEventListener?.("mute"' in source and 'addEventListener?.("unmute"' in source, f"{name} handles track muted/unmuted events")
    require("createVideoOnlyTracks" in source and "audio: false" in source, f"{name} reacquires video without duplicating audio")
    require("republishVideoTracks" in source and "unpublishLocalTracksByKind" in source, f"{name} has video-only republish path")
    require("browser_camera_recovery" in source and "browser_camera_reconnect" in source, f"{name} escalates from video recovery to room reconnect only after failure")
    require("LiveHostPublisher" in source and "__PulseSocLiveHostPublisher" in source, f"{name} enforces single camera owner rule")
    require("pauseCompetingLiveMedia" in source, f"{name} pauses competing media when host live starts")
    require("pagehide" in source and "cleanupPublisher" in source, f"{name} cleans up on live end/pagehide")
    for event_name in ("visibilitychange", "pageshow", "focus", "orientationchange", "resize"):
        require(event_name in source, f"{name} handles mobile lifecycle event {event_name}")
    require("TrackMuted" in source and "TrackUnmuted" in source and "LocalTrackUnpublished" in source, f"{name} monitors LiveKit track lifecycle")
    require("live_video_recovery_success" in source and "live_video_recovery_failed" in source, f"{name} logs recovery outcomes")
    get_user_media_calls = len(re.findall(r"\bgetUserMedia\s*\(", source))
    require(get_user_media_calls <= 1, f"{name} avoids duplicate direct getUserMedia publisher calls")


def audit_backend() -> None:
    source = read(BOT)
    required_events = [
        "live_camera_started",
        "live_video_frame_seen",
        "live_video_freeze_detected",
        "live_video_recovery_started",
        "live_video_track_reacquired",
        "live_video_republished",
        "live_video_recovery_success",
        "live_video_recovery_failed",
        "live_room_reconnect_started",
        "live_room_reconnect_success",
    ]
    require("@webhook_app.route(\"/api/pulse/live/<int:live_id>/debug-event\"" in source, "host-only live debug endpoint exists")
    require("pulse_live_safe_debug_payload" in source, "live debug payload sanitizer exists")
    require('"secret", "token", "stream_key"' in source, "debug sanitizer redacts secret-like fields")
    for event_name in required_events:
        require(event_name in source, f"backend accepts {event_name}")


def main() -> int:
    for path in LIVE_FILES:
        audit_live_file(path)
    audit_backend()
    print("live camera freeze audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
