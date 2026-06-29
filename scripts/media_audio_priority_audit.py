#!/usr/bin/env python3
"""Audit PulseSoc attached-audio priority wiring across media surfaces."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "media_audio_priority_audit.json"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def main() -> int:
    bot = read("bot.py")
    renderer = read("static/js/pulse_media_renderer.js")
    status_viewer = read("static/js/pulse_status_viewer.js")
    feed = read("services/pulse_feed_engine.py")
    checks: list[dict] = []

    required_api_fields = [
        "audio_id",
        "attached_audio_url",
        "audio_title",
        "audio_artist",
        "audio_duration",
        "audio_start_time",
        "audio_volume",
        "original_audio_muted",
    ]
    for field in required_api_fields:
        require(checks, f"api field {field}", field in bot and field in feed, f"{field} is emitted by backend payloads")

    require(checks, "shared renderer detects attached audio", "function hasAttachedAudio" in renderer and "data-attached-audio-url" in renderer, "shared renderer reads attached audio metadata")
    require(checks, "original video audio forced muted", "forceOriginalAudioMuted" in renderer and "video.volume = 0" in renderer, "attached audio forces video muted and volume zero")
    require(checks, "attached audio sync events", all(token in renderer for token in ["seeking", "seeked", "timeupdate", "ended", "pauseAttachedAudio"]), "attached audio is synced to video lifecycle")
    require(checks, "mute controls attached audio", "setAttachedAudioMuted" in renderer and "hasAttachedAudio(video)" in renderer, "sound toggles use attached audio when present")
    require(checks, "no duplicate post audio element playback", "data-post-music-audio" not in bot and "playAttachedAudio" in bot, "post music control uses shared attached-audio player")

    require(checks, "reels pass attached audio metadata", "attachedAudioUrl" in bot and "original_audio_muted:!!attachedAudioUrl" in bot, "Reels renderMedia receives attached audio metadata")
    require(checks, "reels managed playback uses shared priority", "bindAttachedAudioPriority" in bot and "setAttachedAudioMuted" in bot and "reels-attached" in bot, "managed Reel player uses shared attached-audio controls")
    require(checks, "reel save-time priority persisted", "start_seconds=sound_start" in bot and "volume=audio_volume" in bot and "original_audio_muted=1" in bot, "Reel create/manage persists muted original audio rule")

    require(checks, "status attached audio gated", "hasAttachedAudio" in status_viewer and "forceOriginalAudioMuted" in status_viewer, "Status viewer routes attached audio through shared priority")
    require(checks, "status original video muted", "video.volume = 0" in status_viewer and "status-attached-music" in status_viewer, "Status video original audio is muted when music exists")

    require(checks, "feed media enriched with music", "_media_with_attached_music" in feed and "attached_audio_url" in feed, "feed media carries attached audio metadata")
    require(checks, "database stores priority fields", all(token in bot for token in ["original_audio_muted", "audio_start_time", "audio_volume"]), "content music table has priority fields")

    ok = all(item["ok"] for item in checks)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"ok": ok, "checks": checks}, indent=2), encoding="utf-8")
    for item in checks:
        print(("PASS" if item["ok"] else "FAIL") + f" {item['name']}: {item['detail']}")
    print(f"report={REPORT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
