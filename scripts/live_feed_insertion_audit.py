#!/usr/bin/env python3
"""Verify live sessions become first-class Pulse feed posts."""

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


def ensure_user(cur) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    if row:
        return int(row["user_id"])
    cur.execute(
        "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
        ("livefeedaudit", "Live Feed Audit", "live-feed-audit@example.com", now, now),
    )
    return int(cur.lastrowid)


def main():
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    user_id = ensure_user(cur)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,updated_at)
        VALUES (?, 'Feed Insertion Live', 'Creator QA', 'live', 'live', 'feed_key', 7, ?, ?, 'feedinsert', 'https://live.coinpilotxai.app/hls/feedinsert.m3u8', 'https://live.coinpilotxai.app/hls/feedinsert.m3u8', 'pulse-webrtc-feedinsert', 'stable', ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    post_id = live_feed_service.ensure_live_feed_post(
        cur,
        user_id=user_id,
        live_id=live_id,
        title="Feed Insertion Live",
        category="Creator QA",
        playback_url="https://live.coinpilotxai.app/hls/feedinsert.m3u8",
        viewer_count=7,
    )
    cur.execute("UPDATE pulse_live_sessions SET feed_post_id=? WHERE id=?", (post_id, live_id))
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    feed = client.get("/api/pulse/feed?feed=for_you&limit=30").get_json() or {}
    posts = feed.get("items") or feed.get("posts") or []
    live_posts = [post for post in posts if int(post.get("id") or 0) == post_id]
    require(feed.get("ok"), "Pulse feed API returns ok")
    require(live_posts, "active live session appears in main Pulse feed")
    require(live_posts[0].get("post_type") == "live", "live feed post is typed as live")
    require((live_posts[0].get("live") or {}).get("live_session_id") == live_id, "live feed payload carries session id")
    require((live_posts[0].get("live") or {}).get("playback_url", "").endswith(".m3u8"), "live feed payload carries playback URL")
    print("live feed insertion audit ok")


if __name__ == "__main__":
    main()
