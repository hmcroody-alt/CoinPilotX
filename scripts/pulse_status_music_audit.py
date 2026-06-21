#!/usr/bin/env python3
"""Audit Pulse Status approved music attachment wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    home_core = (ROOT / "static" / "js" / "pulse_home_core.js").read_text(encoding="utf-8")
    viewer = (ROOT / "static" / "js" / "pulse_status_viewer.js").read_text(encoding="utf-8")
    require("/api/pulse/status/music/search" in source, "Status music search exists")
    require("genre=genre" in source and "topic=topic" in source and "length=length" in source, "Status music search supports AI filters")
    require("music_not_approved" in source, "Status rejects unapproved music")
    require("pulse_attach_music_to_content(cur, content_type=\"status\"" in source, "Status snapshots approved music license")
    require("refreshed_music = music_service.attach_music_payload(music_track_id)" in source, "existing Status music metadata is rehydrated from the approved catalog")
    require("approved_matches = music_service.search_tracks(query=music_title, limit=8)" in source, "legacy Status music titles rehydrate from approved playable tracks")
    require("function statusShowMusicPanel" in home_core, "lightweight Home Status creator opens the approved music panel")
    require("function statusLoadMusic" in home_core and "/api/pulse/status/music/search" in home_core, "lightweight Status music picker loads approved tracks")
    require("data-status-select-track" in home_core and "statusAttachTrackFromButton" in home_core, "Status selected tracks attach to the draft")
    require("music_track_id: musicTrackId" in home_core, "Status publish sends selected approved music track")
    require("Choose music from the sound panel below" not in home_core, "Status music button no longer dead-ends with an instruction-only message")
    require("data-status-music-audio" in viewer and "musicUrl" in viewer, "viewer renders the approved Status audio source")
    require("viewerSoundMedia" in viewer and "music || video" in viewer, "attached music takes priority over embedded video audio")
    require("status-attached-music" in viewer, "video audio is muted while attached music plays")
    require("ensurePulseStatusViewerRuntime" in source, "Home loads the shared audio-capable viewer on demand")
    print("pulse status music audit ok")


if __name__ == "__main__":
    main()
