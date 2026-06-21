#!/usr/bin/env python3
"""Audit Pulse shared music picker and AI suggestion APIs."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token, label in [
        ("/api/pulse/music/search", "shared music search API exists"),
        ("/api/pulse/music/ai-suggest", "AI music suggestion API exists"),
        ("pulseMusicPicker", "composer music picker exists"),
        ("data-composer-music-search", "picker search form exists"),
        ("mood", "mood filter exists"),
        ("genre", "genre filter exists"),
        ("length", "duration filter exists"),
        ("data-select-composer-track", "track select control exists"),
        ("music_track_id:composerMusicTrackId", "composer sends selected music track"),
        ("data-use-library-music=\"reel\"", "public music library wires Use in Reel"),
        ("data-use-library-music=\"video\"", "public music library wires Use in Video"),
        ("data-use-library-music=\"status\"", "public music library wires Use in Status"),
        ("pulseSelectedMusicTrackId", "music library persists selected track for handoff"),
        ("adoptIncomingReelMusic", "Reels consumes selected library music"),
        ("adoptIncomingStatusMusic", "Status consumes selected library music"),
        ("composerMusicAutofocus", "Video composer opens when music is handed off"),
        ("proof verified", "picker shows license proof status"),
    ]:
        require(token in source, label)
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 1
    response = client.post("/api/pulse/music/ai-suggest", json={"mood": "cinematic", "topic": "market lesson", "length": 30})
    payload = response.get_json() or {}
    require(response.status_code == 200 and payload.get("ok") and payload.get("items"), "AI music suggestion returns approved tracks", response.get_data(as_text=True)[:300])
    require(all(item.get("is_creator_safe") for item in payload.get("items") or []), "AI suggestions are creator-safe")
    print("pulse music picker audit ok")


if __name__ == "__main__":
    main()
