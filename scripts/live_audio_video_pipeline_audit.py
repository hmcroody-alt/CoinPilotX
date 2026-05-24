#!/usr/bin/env python3
"""Verify browser camera/microphone publish state is persisted for viewers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, live_feed_service  # noqa: E402


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
            ("liveavaudit", "Live AV Audit", "live-av-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,updated_at)
        VALUES (?, 'Audio Video Live', 'Creator QA', 'publishing', 'publishing', 'av_key', 0, ?, ?, 'avaudit', 'https://live.coinpilotxai.app/hls/avaudit.m3u8', 'https://live.coinpilotxai.app/hls/avaudit.m3u8', 'pulse-webrtc-avaudit', 'starting', ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    post_id = live_feed_service.ensure_live_feed_post(
        cur,
        user_id=user_id,
        live_id=live_id,
        title="Audio Video Live",
        category="Creator QA",
        playback_url="https://live.coinpilotxai.app/hls/avaudit.m3u8",
    )
    cur.execute("UPDATE pulse_live_sessions SET feed_post_id=? WHERE id=?", (post_id, live_id))
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    publish = client.post(f"/api/pulse/live/{live_id}/browser-publish", json={"audio_tracks": 1, "video_tracks": 1}).get_json() or {}
    require(publish.get("ok"), "browser publish endpoint accepts real audio and video tracks")
    state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(state.get("status") == "live", "published media moves session into live state")
    require(state.get("publish_state") == "live", "publish state machine is live")
    require(state.get("playback", {}).get("supports_hls"), "viewer state exposes playable HLS")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT audio_tracks, video_tracks FROM pulse_live_sessions WHERE id=?", (live_id,))
    row = dict(cur.fetchone() or {})
    conn.close()
    require(int(row.get("audio_tracks") or 0) == 1, "audio track count persisted")
    require(int(row.get("video_tracks") or 0) == 1, "video track count persisted")
    print("live audio video pipeline audit ok")


if __name__ == "__main__":
    main()
