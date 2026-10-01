#!/usr/bin/env python3
"""Audit PulseSoc mobile video detail layout, owner menu, and real related media."""

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
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def main() -> int:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    source_checks = {
        "route hides boxed mobile shell chrome": "body:has(.pulse-video-detail-page) .wrap>section.card:first-of-type" in source,
        "floating create button hidden on detail route": "body:has(.pulse-video-detail-page) .pulse-fab" in source,
        "creator details moved to drawer": "data-video-owner-menu" in source and "videoOwnerSheet" in source and "video-creator-drawer-head" in source,
        "top inline owner actions removed": "<div class='actions'>{source_link}{owner_controls}</div>" not in source,
        "compact reaction row uses icon buttons": "video-detail-action" in source and "<span>❤️</span>" in source and "<span>💬</span>" in source and "video-detail-stats{{display:none!important}}" in source,
        "fit fill controls are wired": "data-video-fit-toggle" in source and "data-video-fullscreen" in source and "object-fit:contain!important" in source and ".video-player-top-controls button{{pointer-events:auto}}" in source,
        "video repost action is connected": '@webhook_app.route("/api/pulse/videos/<int:video_id>/repost"' in source and "detailApi('/api/pulse/videos/'+videoId+'/repost'" in source,
        "private insights are server gated": "can_view_insights" in source and "data-video-private-insights" in source,
        "related media uses actual video fallback": "related_thumb_markup" in source and "<video muted playsinline preload='metadata'" in source,
        "mobile bottom nav alignment scoped": "body:has(.pulse-video-detail-page) .mobile-bottom-nav" in source,
        "delete still requires confirmation": "confirm('Delete this video? This cannot be undone.')" in source,
    }
    for label, condition in source_checks.items():
        require(label, condition)

    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    owner_id = 99352002
    viewer_id = 99352003
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, email, created_at) VALUES (?, ?, ?, ?)", (owner_id, "mobile_video_owner", "mobile-video-owner@example.com", now))
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, email, created_at) VALUES (?, ?, ?, ?)", (viewer_id, "mobile_video_viewer", "mobile-video-viewer@example.com", now))
    cur.execute("DELETE FROM pulse_videos WHERE source_type='mobile_detail_audit'")
    cur.execute(
        """
        INSERT INTO pulse_videos
        (owner_user_id, source_type, source_id, title, description, thumbnail_url, media_url, playback_url, processing_status, mux_status, duration_seconds, visibility, status, created_at, updated_at)
        VALUES (?, 'mobile_detail_audit', 'primary', 'Mobile detail audit video', 'Portrait layout contract', '', 'https://cdn.example.com/audit-primary.mp4', 'https://cdn.example.com/audit-primary.mp4', 'ready', 'ready', 18, 'public', 'active', ?, ?)
        """,
        (owner_id, now, now),
    )
    video_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_videos
        (owner_user_id, source_type, source_id, title, description, thumbnail_url, media_url, playback_url, processing_status, mux_status, duration_seconds, visibility, status, created_at, updated_at)
        VALUES (?, 'mobile_detail_audit', 'related', 'Real related audit clip', 'Related media contract', '', 'https://cdn.example.com/audit-related.mp4', 'https://cdn.example.com/audit-related.mp4', 'ready', 'ready', 25, 'public', 'active', ?, ?)
        """,
        (owner_id, now, now),
    )
    conn.commit()
    conn.close()

    owner_page = client_for(owner_id).get(f"/pulse/videos/{video_id}")
    owner_html = owner_page.get_data(as_text=True)
    require("owner detail route returns 200", owner_page.status_code == 200)
    require("owner sees three-dot menu", "<button class='video-detail-more' type='button' data-video-owner-menu" in owner_html)
    require("owner actions are in creator drawer", "videoOwnerSheet" in owner_html and "video-creator-drawer-head" in owner_html and "class='danger' type='button' data-video-delete" in owner_html)
    require("detail page avoids old PulseSoc Video fallback", "PulseSoc Video" not in owner_html and "Untitled Video" not in owner_html)
    require("related card includes real media preview", "Real related audit clip" in owner_html and "<video muted playsinline preload='metadata'" in owner_html)
    require("compact reaction row renders without stat cards", "data-video-like" in owner_html and "data-video-share" in owner_html and "<span class='video-detail-stat'" not in owner_html)
    require("owner sees private Pulse Insights", "data-video-private-insights" in owner_html and "Pulse Insights" in owner_html)
    require("owner bio is not exposed in compact creator row", "Portrait layout contract ·" not in owner_html)

    viewer_page = client_for(viewer_id).get(f"/pulse/videos/{video_id}")
    viewer_html = viewer_page.get_data(as_text=True)
    require("viewer detail route returns 200", viewer_page.status_code == 200)
    require("viewer can open creator drawer", "data-video-owner-menu" in viewer_html and "video-creator-drawer-head" in viewer_html)
    require(
        "viewer cannot see owner controls",
        "class='danger' type='button' data-video-delete" not in viewer_html
        and "type='button' data-video-edit" not in viewer_html,
    )
    require("viewer cannot see private Pulse Insights", "data-video-private-insights" not in viewer_html and "Pulse Insights" not in viewer_html)

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_videos WHERE source_type='mobile_detail_audit'")
    conn.commit()
    conn.close()

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse mobile video detail page audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
