#!/usr/bin/env python3
"""Audit PulseSoc immersive video detail, direct reactions, and related videos."""

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


FAILURES: list[str] = []


def require(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}")
        FAILURES.append(label)


def client_for(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        sess["user_id"] = user_id
    return client


def main() -> int:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("direct video reaction table is initialized", "CREATE TABLE IF NOT EXISTS pulse_video_reactions" in source)
    require("direct video comment table is initialized", "CREATE TABLE IF NOT EXISTS pulse_video_comments" in source)
    require("video detail preserves original aspect ratio by default", "--video-aspect" in source and "object-fit:contain!important" in source)
    require("video detail offers explicit fill mode", "data-video-fit-toggle" in source and ".video-detail-player.is-fill video" in source and "object-fit:cover!important" in source and "setProperty('object-fit',fill?'cover':'contain','important')" in source)
    require("video detail fullscreen control is wired", "data-video-fullscreen" in source and "requestFullscreen" in source)
    require("related videos render thumbnail cards", "data-related-video-card" in source and "related-thumb" in source)
    require("placeholder related video title removed", "v.get('title') or 'PulseSoc Video'" not in source)
    require("direct video reactions are persisted", "INSERT INTO pulse_video_reactions" in source and "ON CONFLICT(video_id, user_id)" in source)
    require("direct video comments are persisted", "INSERT INTO pulse_video_comments" in source)
    require("direct video reposts are wired to backend", '@webhook_app.route("/api/pulse/videos/<int:video_id>/repost"' in source and "pulse_video_reposted" in source)
    require("video API visibility checks remain", "video.get(\"visibility\") != \"public\"" in source)
    require("private Pulse Insights are owner/admin gated", "can_view_insights" in source and "data-video-private-insights" in source)

    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(timespec="seconds")
    viewer_id = 99351001
    owner_id = 99351002
    admin_id = 99351003
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, email, created_at) VALUES (?, ?, ?, ?)", (viewer_id, "video_audit_viewer", "video-audit-viewer@example.com", now))
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, email, created_at) VALUES (?, ?, ?, ?)", (owner_id, "video_audit_owner", "video-audit-owner@example.com", now))
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, email, is_super_user, created_at) VALUES (?, ?, ?, 1, ?)", (admin_id, "video_audit_admin", "video-audit-admin@example.com", now))
    cur.execute("UPDATE users SET is_super_user=1 WHERE user_id=?", (admin_id,))
    cur.execute("DELETE FROM pulse_video_reactions WHERE user_id IN (?, ?, ?)", (viewer_id, owner_id, admin_id))
    cur.execute("DELETE FROM pulse_video_comments WHERE user_id IN (?, ?, ?)", (viewer_id, owner_id, admin_id))
    cur.execute("DELETE FROM pulse_posts WHERE user_id IN (?, ?, ?) AND title IN ('Direct audit video', 'Feed source audit video')", (viewer_id, owner_id, admin_id))
    cur.execute("DELETE FROM pulse_videos WHERE source_type='audit_direct_video'")
    cur.execute(
        """
        INSERT INTO pulse_videos
        (owner_user_id, source_type, source_id, title, description, thumbnail_url, media_url, playback_url, processing_status, mux_status, duration_seconds, visibility, status, created_at, updated_at)
        VALUES (?, 'audit_direct_video', 'primary', 'Direct audit video', 'Direct video body', '/static/audit-thumb.jpg', 'https://cdn.example.com/audit.mp4', 'https://cdn.example.com/audit.mp4', 'ready', 'ready', 42, 'public', 'active', ?, ?)
        """,
        (owner_id, now, now),
    )
    video_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_videos
        (owner_user_id, source_type, source_id, title, description, thumbnail_url, media_url, playback_url, processing_status, mux_status, duration_seconds, visibility, status, created_at, updated_at)
        VALUES (?, 'audit_direct_video', 'related', 'Related audit video', 'Related video body', '', 'https://cdn.example.com/related.mp4', 'https://cdn.example.com/related.mp4', 'ready', 'ready', 33, 'public', 'active', ?, ?)
        """,
        (owner_id, now, now),
    )
    conn.commit()
    conn.close()

    client = client_for(viewer_id)
    first = client.post(f"/api/pulse/videos/{video_id}/react", json={"reaction_type": "like"})
    require("direct video reaction endpoint accepts first reaction", first.status_code == 200 and first.get_json().get("removed") is False)
    second = client.post(f"/api/pulse/videos/{video_id}/react", json={"reaction_type": "like"})
    require("direct video reaction endpoint removes duplicate reaction", second.status_code == 200 and second.get_json().get("removed") is True)
    comment = client.post(f"/api/pulse/videos/{video_id}/comments", json={"body": "Audit direct video comment"})
    require("direct video comment endpoint persists comment", comment.status_code == 201 and comment.get_json().get("comment_id"))
    repost = client.post(f"/api/pulse/videos/{video_id}/repost", json={})
    repost_data = repost.get_json() or {}
    require("direct video repost endpoint creates a real post", repost.status_code == 200 and repost_data.get("post_id"))
    page = client.get(f"/pulse/videos/{video_id}")
    html = page.get_data(as_text=True)
    require("video detail page loads direct video", page.status_code == 200 and "Direct audit video" in html)
    require("video detail page includes real related card", "Related audit video" in html and "related-thumb" in html)
    require("video detail page avoids fake placeholders", "PulseSoc Video</span>" not in html and "Untitled Video" not in html)
    require("public viewer cannot see private insights", "data-video-private-insights" not in html and "Pulse Insights" not in html)

    owner_html = client_for(owner_id).get(f"/pulse/videos/{video_id}").get_data(as_text=True)
    require("owner can see private insights", "data-video-private-insights" in owner_html and "Pulse Insights" in owner_html)
    admin_html = client_for(admin_id).get(f"/pulse/videos/{video_id}").get_data(as_text=True)
    require("admin can see private insights", "data-video-private-insights" in admin_html and "Pulse Insights" in admin_html)

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_video_reactions WHERE video_id IN (SELECT id FROM pulse_videos WHERE source_type='audit_direct_video')")
    cur.execute("DELETE FROM pulse_video_comments WHERE video_id IN (SELECT id FROM pulse_videos WHERE source_type='audit_direct_video')")
    cur.execute("DELETE FROM pulse_posts WHERE user_id IN (?, ?, ?) AND title IN ('Direct audit video', 'Feed source audit video')", (viewer_id, owner_id, admin_id))
    cur.execute("DELETE FROM pulse_videos WHERE source_type='audit_direct_video'")
    conn.commit()
    conn.close()

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse immersive video experience audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
