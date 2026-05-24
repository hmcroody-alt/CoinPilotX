#!/usr/bin/env python3
"""Verify active live viewer renders playback instead of a fake placeholder."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    user_id = int(row["user_id"]) if row else 0
    if not user_id:
        cur.execute(
            "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
            ("viewerplayback", "Viewer Playback", "viewer-playback@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,audio_tracks,video_tracks,updated_at)
        VALUES (?, 'Playback Live', 'Creator QA', 'live', 'live', 'play_key', 3, ?, ?, 'playaudit', 'https://live.coinpilotxai.app/hls/playaudit.m3u8', 'https://live.coinpilotxai.app/hls/playaudit.m3u8', 'pulse-webrtc-playaudit', 'stable', 1, 1, ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get(f"/pulse/live/{live_id}")
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "viewer page loads")
    require("data-live-player" in html and "live-public-video" in html, "active live renders public video player")
    require("Tap to unmute" in html, "viewer includes browser-safe audio unlock")
    require("live-ready-orb" not in html, "active live avoids fake avatar placeholder")
    require("live broadcast" not in html.lower(), "active live avoids fake broadcast placeholder text")
    print("live viewer playback audit ok")


if __name__ == "__main__":
    main()
