"""Canonical, privacy-scoped content graph reads and reversible interactions for UNDX."""

from __future__ import annotations

from typing import Any

from services import db as db_service
from services.undx_agent_contracts import clean


def _rows(cur) -> list[dict[str, Any]]:
    return [dict(row) for row in cur.fetchall()]


def list_reels(user_id: int, *, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    viewer = int(user_id or 0)
    if viewer <= 0:
        return []
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        like = f"%{clean(query, 80)}%"
        cur.execute(
            """SELECT r.id AS reel_id,r.post_id,r.user_id AS creator_id,r.caption,r.category,
                      r.created_at,r.share_count,r.completion_rate,r.replay_count,
                      p.visibility,p.title
               FROM pulse_reels r JOIN pulse_posts p ON p.id=r.post_id
               WHERE r.status='active' AND r.moderation_status='approved'
                 AND p.deleted_at IS NULL AND p.status='published'
                 AND (p.user_id=? OR p.visibility='public')
                 AND (?='' OR COALESCE(r.caption,'') LIKE ? OR COALESCE(p.title,'') LIKE ?)
               ORDER BY r.created_at DESC LIMIT ?""",
            (viewer, clean(query, 80), like, like, max(1, min(int(limit or 20), 40))),
        )
        return [_reel_record(viewer, row, cur) for row in _rows(cur)]
    finally:
        conn.close()


def _reel_row(cur, viewer: int, reel_id: int) -> dict[str, Any] | None:
    cur.execute(
        """SELECT r.id AS reel_id,r.post_id,r.user_id AS creator_id,r.caption,r.category,
                  r.created_at,r.share_count,r.completion_rate,r.replay_count,
                  p.visibility,p.title
           FROM pulse_reels r JOIN pulse_posts p ON p.id=r.post_id
           WHERE r.id=? AND r.status='active' AND r.moderation_status='approved'
             AND p.deleted_at IS NULL AND p.status='published'
             AND (p.user_id=? OR p.visibility='public') LIMIT 1""",
        (int(reel_id), viewer),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _reel_record(viewer: int, row: dict[str, Any], cur) -> dict[str, Any]:
    reel_id, post_id = int(row["reel_id"]), int(row["post_id"])
    cur.execute("SELECT COUNT(*) AS n FROM pulse_reactions WHERE post_id=?", (post_id,))
    reactions = int(dict(cur.fetchone()).get("n") or 0)
    cur.execute("SELECT COUNT(*) AS n FROM pulse_comments WHERE post_id=? AND deleted_at IS NULL", (post_id,))
    comments = int(dict(cur.fetchone()).get("n") or 0)
    cur.execute("SELECT 1 FROM pulse_post_saves WHERE post_id=? AND user_id=? LIMIT 1", (post_id, viewer))
    saved = cur.fetchone() is not None
    cur.execute("SELECT reaction_type FROM pulse_reactions WHERE post_id=? AND user_id=? LIMIT 1", (post_id, viewer))
    reaction = cur.fetchone()
    return {
        "reel_id": reel_id, "post_id": post_id, "creator_id": int(row["creator_id"]),
        "caption": clean(row.get("caption") or row.get("title"), 500),
        "category": clean(row.get("category"), 60), "visibility": clean(row.get("visibility"), 30),
        "created_at": clean(row.get("created_at"), 40), "reactions": reactions, "comments": comments,
        "shares": int(row.get("share_count") or 0), "replays": int(row.get("replay_count") or 0),
        "completion_rate": float(row.get("completion_rate") or 0), "saved": saved,
        "liked": bool(reaction and dict(reaction).get("reaction_type") == "like"),
        "source_url": f"/pulse/reels/{reel_id}",
    }


def get_reel(user_id: int, reel_id: int) -> dict[str, Any] | None:
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        row = _reel_row(cur, int(user_id), int(reel_id))
        return _reel_record(int(user_id), row, cur) if row else None
    finally:
        conn.close()


def reel_performance(user_id: int, reel_id: int) -> dict[str, Any] | None:
    record = get_reel(user_id, reel_id)
    if not record or int(record["creator_id"]) != int(user_id):
        return None
    return {**record, "title": "Reel performance"}


def reel_comment_summary(user_id: int, reel_id: int, *, limit: int = 40) -> dict[str, Any] | None:
    record = get_reel(user_id, reel_id)
    if not record or int(record["creator_id"]) != int(user_id):
        return None
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT body,user_id FROM pulse_comments
               WHERE post_id=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT ?""",
            (int(record["post_id"]), max(1, min(int(limit), 80))),
        )
        comments = _rows(cur)
    finally:
        conn.close()
    excerpts = [clean(row.get("body"), 140) for row in comments if clean(row.get("body"), 140)]
    return {
        "reel_id": int(reel_id), "comment_count": len(comments),
        "participant_count": len({int(row.get("user_id") or 0) for row in comments}),
        "summary": " · ".join(excerpts[:5]) or "No visible comments are available.",
        "source_url": f"/pulse/reels/{int(reel_id)}",
    }


def set_reel_saved(user_id: int, reel_id: int, *, saved: bool) -> dict[str, Any]:
    record = get_reel(user_id, reel_id)
    if not record:
        return {"ok": False, "error": "not_found"}
    from services.saved_content_service import set_post_saved
    outcome = set_post_saved(int(user_id), int(record["post_id"]), saved=saved)
    return {**outcome, "reel_id": int(reel_id)}


def set_reel_liked(user_id: int, reel_id: int, *, liked: bool) -> dict[str, Any]:
    record = get_reel(user_id, reel_id)
    if not record:
        return {"ok": False, "error": "not_found"}
    owner, post_id = int(user_id), int(record["post_id"])
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,reaction_type FROM pulse_reactions WHERE post_id=? AND user_id=? LIMIT 1",
            (post_id, owner),
        )
        row = cur.fetchone()
        before = bool(row and dict(row).get("reaction_type") == "like")
        desired = bool(liked)
        if before != desired:
            cur.execute("DELETE FROM pulse_reactions WHERE post_id=? AND user_id=?", (post_id, owner))
            if desired:
                cur.execute(
                    """INSERT INTO pulse_reactions(post_id,user_id,reaction_type,created_at)
                       VALUES (?,?, 'like', CURRENT_TIMESTAMP)""", (post_id, owner),
                )
            conn.commit()
        return {"ok": True, "post_id": post_id, "liked": desired,
                "changed": before != desired, "reel_id": int(reel_id)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_statuses(user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    viewer = int(user_id or 0)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT s.* FROM pulse_statuses s
               WHERE s.deleted_at IS NULL AND (s.expires_at IS NULL OR s.expires_at>CURRENT_TIMESTAMP)
                 AND (s.user_id=? OR s.visibility='public' OR
                      (s.visibility='followers' AND EXISTS(
                       SELECT 1 FROM pulse_follows f WHERE f.follower_user_id=? AND f.followed_user_id=s.user_id)))
               ORDER BY s.created_at DESC LIMIT ?""",
            (viewer, viewer, max(1, min(int(limit), 40))),
        )
        return [_status_record(viewer, row, cur) for row in _rows(cur)]
    finally:
        conn.close()


def _status_record(viewer: int, row: dict[str, Any], cur) -> dict[str, Any]:
    status_id = int(row["id"])
    cur.execute("SELECT COUNT(*) AS n FROM pulse_status_views WHERE status_id=?", (status_id,))
    views = int(dict(cur.fetchone()).get("n") or 0)
    cur.execute("SELECT COUNT(*) AS n FROM pulse_status_reactions WHERE status_id=?", (status_id,))
    reactions = int(dict(cur.fetchone()).get("n") or 0)
    return {
        "status_id": status_id, "creator_id": int(row["user_id"]),
        "body": clean(row.get("body"), 500), "status_type": clean(row.get("status_type"), 30),
        "visibility": clean(row.get("visibility"), 30), "created_at": clean(row.get("created_at"), 40),
        "expires_at": clean(row.get("expires_at"), 40), "views": views, "reactions": reactions,
        "source_url": f"/pulse/status?status={status_id}",
    }


def get_status(user_id: int, status_id: int) -> dict[str, Any] | None:
    return next((row for row in list_statuses(user_id, limit=40) if row["status_id"] == int(status_id)), None)


def status_viewer_summary(user_id: int, status_id: int) -> dict[str, Any] | None:
    record = get_status(user_id, status_id)
    if not record or record["creator_id"] != int(user_id):
        return None
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT v.viewer_user_id,u.display_name,u.username,v.viewed_at,v.completion_ratio
               FROM pulse_status_views v LEFT JOIN users u ON u.user_id=v.viewer_user_id
               WHERE v.status_id=? ORDER BY v.viewed_at DESC LIMIT 80""", (int(status_id),),
        )
        viewers = _rows(cur)
    finally:
        conn.close()
    return {**record, "viewer_count": len(viewers), "viewers": [
        {"user_id": int(v["viewer_user_id"]), "name": clean(v.get("display_name") or v.get("username"), 100),
         "viewed_at": clean(v.get("viewed_at"), 40), "completion_ratio": float(v.get("completion_ratio") or 0)}
        for v in viewers
    ]}


def status_reaction_summary(user_id: int, status_id: int) -> dict[str, Any] | None:
    record = get_status(user_id, status_id)
    if not record or record["creator_id"] != int(user_id):
        return None
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT reaction_type,COUNT(*) AS n FROM pulse_status_reactions WHERE status_id=? GROUP BY reaction_type",
            (int(status_id),),
        )
        counts = {clean(row["reaction_type"], 30): int(row["n"]) for row in _rows(cur)}
    finally:
        conn.close()
    return {**record, "reaction_counts": counts}


def get_profile(user_id: int, target_user_id: int | None = None) -> dict[str, Any] | None:
    viewer, target = int(user_id), int(target_user_id or user_id)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT user_id,username,display_name,bio,avatar_url,profile_visibility,
                      created_at,last_seen_at FROM users
               WHERE user_id=? AND deleted_at IS NULL AND account_status='active' LIMIT 1""", (target,),
        )
        row = cur.fetchone()
        if not row:
            return None
        record = dict(row)
        if target != viewer and clean(record.get("profile_visibility"), 20) == "private":
            return None
        return {
            "user_id": target, "username": clean(record.get("username"), 80),
            "display_name": clean(record.get("display_name"), 120), "bio": clean(record.get("bio"), 500),
            "avatar_url": clean(record.get("avatar_url"), 300),
            "profile_visibility": clean(record.get("profile_visibility"), 20),
            "created_at": clean(record.get("created_at"), 40), "last_seen_at": clean(record.get("last_seen_at"), 40),
            "source_url": f"/pulse/profile/{target}",
        }
    finally:
        conn.close()


def profile_activity_summary(user_id: int) -> dict[str, Any]:
    owner = int(user_id)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        counts = {}
        for name, table in (("posts", "pulse_posts"), ("reels", "pulse_reels"), ("statuses", "pulse_statuses")):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?", (owner,))
            counts[name] = int(dict(cur.fetchone()).get("n") or 0)
        return {"user_id": owner, **counts, "source_url": f"/pulse/profile/{owner}"}
    finally:
        conn.close()


def profile_relationship_summary(user_id: int) -> dict[str, Any]:
    owner = int(user_id)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_follows WHERE followed_user_id=?", (owner,))
        followers = int(dict(cur.fetchone()).get("n") or 0)
        cur.execute("SELECT COUNT(*) AS n FROM pulse_follows WHERE follower_user_id=?", (owner,))
        following = int(dict(cur.fetchone()).get("n") or 0)
        return {"user_id": owner, "followers": followers, "following": following,
                "source_url": f"/pulse/profile/{owner}"}
    finally:
        conn.close()


def get_profile_preferences(user_id: int) -> dict[str, Any] | None:
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT preferred_language FROM users WHERE user_id=? LIMIT 1", (int(user_id),))
        row = cur.fetchone()
        return {"user_id": int(user_id), "preferred_language": clean(dict(row).get("preferred_language") or "en", 8)} if row else None
    finally:
        conn.close()


def update_profile_preferences(user_id: int, *, preferred_language: str) -> dict[str, Any]:
    language = clean(preferred_language, 8).lower()
    if language not in {"en", "es", "fr"}:
        return {"ok": False, "error": "unsupported_language"}
    before = get_profile_preferences(user_id)
    if not before:
        return {"ok": False, "error": "not_found"}
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET preferred_language=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                    (language, int(user_id)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "user_id": int(user_id), "preferred_language": language,
            "changed": before["preferred_language"] != language}


__all__ = [
    "get_profile", "get_reel", "get_status", "list_reels", "list_statuses",
    "get_profile_preferences", "profile_activity_summary", "profile_relationship_summary",
    "update_profile_preferences", "reel_comment_summary",
    "reel_performance", "set_reel_liked", "set_reel_saved", "status_reaction_summary",
    "status_viewer_summary",
]
