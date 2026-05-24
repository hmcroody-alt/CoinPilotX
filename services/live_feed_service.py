"""Bind Pulse Live sessions to first-class Pulse feed posts."""

from __future__ import annotations

import json
from datetime import datetime


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _clean(value: str, limit: int) -> str:
    return str(value or "").replace("<", " ").replace(">", " ").strip()[:limit]


def _row_dict(row) -> dict:
    if not row:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def ensure_live_feed_post(cur, *, user_id: int, live_id: int, title: str, category: str, playback_url: str = "", preview_url: str = "", viewer_count: int = 0) -> int:
    """Create or refresh the Pulse feed post that represents an active live."""
    now = _now()
    cur.execute("SELECT public_player_id FROM arena_profiles WHERE user_id=? LIMIT 1", (int(user_id),))
    profile = _row_dict(cur.fetchone())
    cur.execute("SELECT id FROM pulse_posts WHERE live_session_id=? AND deleted_at IS NULL LIMIT 1", (int(live_id),))
    existing = _row_dict(cur.fetchone())
    body = f"{_clean(title, 140)} is live now. Join the broadcast, chat, and react in realtime."
    tags = ["live", "pulse-live", _clean(category, 40).lower().replace(" ", "-")]
    if existing.get("id"):
        post_id = int(existing["id"])
        cur.execute(
            """
            UPDATE pulse_posts
            SET post_type='live', title=?, body=?, visibility='public', moderation_status='approved',
                live_session_id=?, live_status='live', live_viewer_count=?, playback_url=?, preview_url=?,
                status='published', updated_at=?
            WHERE id=?
            """,
            (_clean(title, 160), body, int(live_id), int(viewer_count or 0), playback_url or "", preview_url or "", now, post_id),
        )
        return post_id
    cur.execute(
        """
        INSERT INTO pulse_posts
        (user_id, public_player_id, post_type, body, media_ids_json, title, tags_json, visibility,
         moderation_status, ai_summary, ai_tags_json, sentiment, risk_score, engagement_score,
         live_session_id, live_status, live_viewer_count, playback_url, preview_url, status, created_at, updated_at)
        VALUES (?, ?, 'live', ?, '[]', ?, ?, 'public', 'approved', ?, ?, 'excited', 0, 75,
                ?, 'live', ?, ?, ?, 'published', ?, ?)
        """,
        (
            int(user_id),
            profile.get("public_player_id"),
            body,
            _clean(title, 160),
            json.dumps(tags),
            f"Live now: {_clean(title, 160)}",
            json.dumps(tags),
            int(live_id),
            int(viewer_count or 0),
            playback_url or "",
            preview_url or "",
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def mark_live_feed_ended(cur, *, live_id: int, replay_url: str = "", viewer_count: int = 0) -> int:
    """Convert the live feed post to ended/replay state."""
    now = _now()
    cur.execute("SELECT id, title FROM pulse_posts WHERE live_session_id=? AND deleted_at IS NULL LIMIT 1", (int(live_id),))
    row = _row_dict(cur.fetchone())
    if not row:
        return 0
    post_id = int(row.get("id") or 0)
    status = "archived" if replay_url else "ended"
    cur.execute(
        """
        UPDATE pulse_posts
        SET live_status=?, live_viewer_count=?, replay_url=?, playback_url=COALESCE(NULLIF(?, ''), playback_url),
            body=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            int(viewer_count or 0),
            replay_url or "",
            replay_url or "",
            f"{row.get('title') or 'Pulse Live'} has ended." + (" Watch the replay." if replay_url else ""),
            now,
            post_id,
        ),
    )
    return post_id


def live_post_payload(session: dict | None = None, post_id: int = 0) -> dict:
    session = session or {}
    live_id = int(session.get("id") or session.get("live_id") or 0)
    return {
        "post_id": int(post_id or session.get("feed_post_id") or 0),
        "live_session_id": live_id,
        "status": session.get("status") or "idle",
        "publish_state": session.get("publish_state") or "idle",
        "playback_url": session.get("playback_url") or session.get("hls_url") or "",
        "preview_url": session.get("preview_url") or session.get("thumbnail_url") or "",
        "replay_url": session.get("replay_url") or "",
        "viewer_count": int(session.get("viewer_count") or 0),
        "live_url": f"/pulse/live/{live_id}" if live_id else "",
    }
