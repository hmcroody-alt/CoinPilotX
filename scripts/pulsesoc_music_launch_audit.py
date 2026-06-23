#!/usr/bin/env python3
"""Audit PulseSoc Music launch foundation contracts."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import music_service  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    service = (ROOT / "services" / "music_service.py").read_text(encoding="utf-8")

    for token, label in [
        ('@webhook_app.route("/pulse/music"', "PulseSoc Music page exists"),
        ("Music Upload Portal", "upload portal is visible"),
        ("I confirm that I own this music or have the legal right to upload it.", "rights confirmation copy exists"),
        ("/api/pulse/music/upload", "music upload endpoint exists"),
        ("/api/pulse/music/<int:track_id>/report", "report song endpoint exists"),
        ("/api/admin/pulse/music/<int:track_id>/remove", "admin remove endpoint exists"),
        ("/api/pulse/music/artist/<int:artist_user_id>", "artist profile endpoint exists"),
        ("pulse_music_events", "music analytics event table exists"),
        ("play_count", "play analytics counter exists"),
        ("reel_use_count", "Reels use analytics counter exists"),
        ("video_use_count", "Videos use analytics counter exists"),
        ("save_count", "save analytics counter exists"),
        ("share_count", "share analytics counter exists"),
        ("PulseSoc Music", "navigation links include PulseSoc Music"),
        ("data-composer-music", "Feed composer can add music"),
        ("Add Music", "creator surfaces expose Add Music"),
        ("pulseComposerMusicTrackId", "library can hand off music to video composer"),
        ("data-use-library-music=\"reel\"", "library exposes wired Use in Reel handoff"),
        ("data-use-library-music=\"video\"", "library exposes wired Use in Video handoff"),
        ("data-use-library-music=\"status\"", "library exposes wired Use in Status handoff"),
        ("pulseReelsPendingMusicTrackId", "library can hand off music to Reels"),
        ("adoptIncomingReelMusic", "Reels page consumes music handoff"),
        ("pulseStatusPendingMusicTrackId", "library can hand off music to Status"),
        ("adoptIncomingStatusMusic", "Status creator consumes music handoff"),
        ('@webhook_app.route("/api/pulse/music/radio"', "Pulse Radio approved-pool endpoint exists"),
        ("music_service.radio_tracks", "Pulse Radio uses the approved music service pool"),
        ("pulseVideoPendingMusicLabel", "video composer preserves selected music label"),
        ("soundUploadRights", "Reels upload confirms rights"),
        ("rights_confirmed", "rights acceptance is stored"),
    ]:
        require(token in source, label)

    for token, label in [
        ("language", "music catalog indexes language"),
        ("cover_art_url", "music catalog exposes cover art"),
        ("public_track", "single-track public payload helper exists"),
        ("artist_user_id", "artist ownership is exposed"),
        ("def radio_tracks(", "Pulse Radio approved-pool helper exists"),
        ("random.SystemRandom", "Pulse Radio server shuffle is enabled"),
        ("_safe_track(track) and _playable_track(track)", "Pulse Radio only returns safe playable tracks"),
    ]:
        require(token in service, label)

    conn = bot.db()
    cur = conn.cursor()
    for table in ["pulse_audio_tracks", "pulse_content_music", "pulse_music_reports", "pulse_music_events"]:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        require(cur.fetchone(), f"{table} table exists")
    cur.execute("PRAGMA table_info(pulse_audio_tracks)")
    columns = {row[1] for row in cur.fetchall()}
    for column in [
        "language",
        "description",
        "cover_art_url",
        "rights_confirmed",
        "play_count",
        "reel_use_count",
        "video_use_count",
        "save_count",
        "share_count",
        "removed_at",
        "admin_review_notes",
    ]:
        require(column in columns, f"pulse_audio_tracks.{column} exists")
    conn.close()

    tracks = music_service.search_tracks(query="pulse", limit=8)
    require(tracks, "catalog returns launch-safe music")
    require(all(track.get("is_creator_safe") for track in tracks), "catalog only returns creator-safe tracks")
    require(music_service.attach_music_payload(tracks[0]["id"]).get("track_id"), "music can be prepared for content attachment")
    radio_tracks = music_service.radio_tracks(limit=40)
    require(radio_tracks, "Pulse Radio returns approved playable music")
    require(
        all(track.get("is_creator_safe") and track.get("audio_url") for track in radio_tracks),
        "Pulse Radio catalog only returns playable creator-safe tracks",
    )
    require(
        all(track.get("radio_ready") for track in radio_tracks),
        "Pulse Radio marks approved tracks as radio ready",
    )
    print("PulseSoc Music launch audit ok")


if __name__ == "__main__":
    main()
