#!/usr/bin/env python3
"""End-to-end audit for Home feed video routing and online fallback behavior."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def client_for(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["user_id"] = user_id
    return client


def main() -> None:
    bot.init_db()
    owner_id = 99412001
    viewer_id = 99412002
    now = datetime.utcnow().isoformat(timespec="seconds")
    media_url = "/static/uploads/pulse_media/2026/06/02/pulse-video-4817676b5fb4084f.webm"
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for user_id, username in ((owner_id, "feed_video_route_owner"), (viewer_id, "feed_video_route_viewer")):
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username, f"{username}@example.com", now),
        )
    cur.execute("DELETE FROM pulse_videos WHERE source_type='feed_video' AND owner_user_id=?", (owner_id,))
    cur.execute("DELETE FROM pulse_posts WHERE user_id=?", (owner_id,))
    cur.execute("DELETE FROM chat_media_uploads WHERE uploader_user_id=? AND context_type='routing_audit'", (owner_id,))
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, original_filename, stored_filename, media_url, playback_url,
         thumbnail_url, media_type, mime_type, moderation_status, processing_status, width, height, created_at, updated_at)
        VALUES (?, 'routing_audit', 'routing-audit.webm', 'routing-audit.webm', ?, ?, '', 'video', 'video/webm',
                'approved', 'ready', 1080, 1920, ?, ?)
        """,
        (owner_id, media_url, media_url, now, now),
    )
    media_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    created = pulse_feed_engine.create_post(
        owner_id,
        body="Routing audit portrait video",
        post_type="video",
        title="Feed video routing audit",
        visibility="public",
        media_ids=[media_id],
        enqueue_background=False,
    )
    post_id = int(created.get("post_id") or 0)
    require(post_id > 0, "video post created")

    client = client_for(viewer_id)
    response = client.get("/api/pulse/feed?tab=for_you&limit=40")
    require(response.status_code == 200, "feed API returns 200")
    posts = response.get_json().get("posts") or []
    post = next((item for item in posts if int(item.get("id") or 0) == post_id), None)
    require(bool(post), "seeded video appears in Home feed payload")
    route = str(post.get("video_permalink") or "")
    require(route.startswith("/pulse/videos/") and route.rsplit("/", 1)[-1].isdigit(), "feed emits validated internal video route")
    require(all(item.get("video_permalink") == route for item in post.get("media") or [] if item.get("media_type") == "video"), "video media carries the same watch route")

    detail = client.get(route)
    html = detail.get_data(as_text=True)
    require(detail.status_code == 200, "generated video detail route returns 200")
    require("pulse-video-detail-page" in html and "Related videos" in html, "watch page includes player and related videos")
    require("PulseSoc is offline" not in html and "You are offline" not in html, "online watch response never renders offline fallback")

    root_sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    static_sw = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")
    for source in (root_sw, static_sw):
        require("onlineNavigationError" in source, "service worker has an online navigation error response")
        require("offline ? offlineResponse() : onlineNavigationError(url.pathname)" in source, "offline fallback requires confirmed offline state")

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_videos WHERE source_type='feed_video' AND source_id=?", (str(post_id),))
    cur.execute("DELETE FROM pulse_posts WHERE id=?", (post_id,))
    cur.execute("DELETE FROM chat_media_uploads WHERE id=?", (media_id,))
    conn.commit()
    conn.close()
    print("pulse feed video routing audit ok")


if __name__ == "__main__":
    main()
