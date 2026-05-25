#!/usr/bin/env python3
"""Audit creator-safe Pulse music discovery and Status attachment contracts."""

from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import music_service  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def ensure_user() -> int:
    user_id = 971000 + int(time.time()) % 10000
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            f"music_audit_{user_id}",
            "Music Audit",
            f"music-audit-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    status = music_service.provider_status()
    require(status.get("ok") is True, "music provider status is available")
    require("original_pulse_sound" in status.get("providers", []), "original Pulse sounds are available as safe fallback")
    tracks = music_service.search_tracks("pulse", limit=4)
    require(tracks and all(track.get("is_creator_safe") for track in tracks), "music search returns creator-safe tracks")
    payload = music_service.attach_music_payload(tracks[0]["id"])
    require(payload.get("track_id") and payload.get("waveform"), "music attachment payload includes waveform metadata")

    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    search = client.get("/api/pulse/status/music/search?q=pulse")
    search_payload = search.get_json() or {}
    require(search.status_code == 200 and search_payload.get("ok") and search_payload.get("items"), "music search endpoint returns tracks", search.get_data(as_text=True)[:300])
    created = client.post(
        "/api/pulse/status",
        json={
            "status_type": "music",
            "body": "Music status audit",
            "music_track_id": tracks[0]["id"],
            "visibility": "public",
        },
    )
    created_payload = created.get_json() or {}
    require(created.status_code == 200 and created_payload.get("ok"), "music status publishes", created.get_data(as_text=True)[:300])
    status_item = created_payload.get("status") or {}
    require((status_item.get("music") or {}).get("track_id") == tracks[0]["id"], "published status returns attached music metadata")
    print("music system audit ok")


if __name__ == "__main__":
    main()
