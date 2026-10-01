#!/usr/bin/env python3
"""Verify active Live watching resolves to the canonical Reels viewer."""

from __future__ import annotations

from datetime import datetime, timezone
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
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,audio_tracks,video_tracks,mux_live_status,mux_playback_id,updated_at)
        VALUES (?, 'Playback Live', 'Creator QA', 'live', 'mux_live', 'play_key', 3, ?, ?, 'playaudit', 'https://stream.mux.com/playbackaudit.m3u8', 'https://stream.mux.com/playbackaudit.m3u8', 'pulse-webrtc-playaudit', 'stable', 1, 1, 'active', 'playbackaudit', ?)
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
    require(response.status_code == 302, "legacy live viewer route redirects")
    require(response.location == f"/pulse/reels?live={live_id}", "legacy live viewer redirects to Reels live target")

    reels_page = client.get(f"/pulse/reels?live={live_id}")
    require(reels_page.status_code == 200, "canonical Reels live viewer page loads")
    html = reels_page.get_data(as_text=True)
    require("PulseSoc Reels" in html and "pulse_reels_experience.css" in html, "Reels viewer shell renders")

    feed = client.get(f"/api/pulse/reels/feed?tab=live&live={live_id}")
    payload = feed.get_json() or {}
    reels = payload.get("reels") or (payload.get("data") or {}).get("reels") or []
    focused = next((item for item in reels if int(item.get("live_session_id") or 0) == live_id), None)
    require(feed.status_code == 200 and payload.get("ok") is True, "Reels live feed API loads")
    require(focused is not None, "focused live session appears in Reels feed")
    media = (focused.get("media") or [{}])[0]
    require(focused.get("post_type") == "live" or focused.get("content_type") == "live", "focused item is typed as live")
    require((focused.get("live") or {}).get("live_url") == f"/pulse/reels?live={live_id}", "live item keeps Reels watch URL")
    require(str(media.get("playback_url") or "").endswith(".m3u8"), "live item exposes HLS playback")
    require(media.get("has_audio") is True, "live item declares host audio-capable playback")
    require(media.get("mime_type") == "application/vnd.apple.mpegurl", "live item exposes native HLS MIME type")
    require("live-ready-orb" not in html, "Reels live viewer avoids fake avatar placeholder")
    print("live viewer playback audit ok")


if __name__ == "__main__":
    main()
