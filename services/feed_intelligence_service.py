"""Privacy-scoped, side-effect-free Feed reads for UNDX."""

from __future__ import annotations

from typing import Any

from services import db as db_service
from services import pulse_feed_engine
from services.undx_agent_contracts import clean


def _post_record(post: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(post, dict) or not post:
        return {}
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    reaction_counts = post.get("reaction_counts") if isinstance(post.get("reaction_counts"), dict) else {}
    post_id = int(post.get("id") or post.get("post_id") or 0)
    return {
        "post_id": post_id,
        "author_user_id": int(post.get("user_id") or author.get("user_id") or 0),
        "author_name": clean(author.get("display_name") or author.get("username") or "PulseSoc Member", 120),
        "title": clean(post.get("title"), 180),
        "body": clean(post.get("body"), 700),
        "post_type": clean(post.get("post_type") or "text", 40),
        "visibility": clean(post.get("visibility") or "public", 40),
        "created_at": clean(post.get("created_at"), 40),
        "comment_count": max(0, int(post.get("comment_count") or 0)),
        "reaction_count": sum(max(0, int(value or 0)) for value in reaction_counts.values()),
        "reaction_counts": {
            clean(key, 32): max(0, int(value or 0))
            for key, value in reaction_counts.items()
        },
        "viewer_reaction": clean(post.get("viewer_reaction"), 32),
        "saved": bool(post.get("saved") or post.get("viewer_saved")),
        "source_url": clean(post.get("permalink") or f"/pulse/post/{post_id}", 200),
    }


def list_posts(
    user_id: int,
    *,
    feed: str = "for_you",
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    owner_id = int(user_id or 0)
    if owner_id <= 0:
        return []
    safe_feed = clean(feed or "for_you", 40).lower()
    if safe_feed not in pulse_feed_engine.FEEDS:
        safe_feed = "for_you"
    payload = pulse_feed_engine.list_feed(
        viewer_user_id=owner_id,
        feed=safe_feed,
        topic=clean(query, 80),
        limit=max(1, min(int(limit or 20), 40)),
        offset=0,
    )
    return [record for record in (_post_record(item) for item in payload.get("posts") or []) if record]


def get_post(user_id: int, post_id: int) -> dict[str, Any] | None:
    """Read public content or the caller's own private content; never another owner's private post."""
    owner_id = int(user_id or 0)
    target_id = int(post_id or 0)
    if owner_id <= 0 or target_id <= 0:
        return None
    post = pulse_feed_engine.get_post(
        target_id,
        viewer_user_id=owner_id,
        include_private=True,
    )
    return _post_record(post) or None


def list_post_comments(
    user_id: int,
    post_id: int,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """List comments only after the parent post passes the caller's visibility check."""
    owner_id = int(user_id or 0)
    target_id = int(post_id or 0)
    if get_post(owner_id, target_id) is None:
        return []
    payload = pulse_feed_engine.list_comments(
        target_id,
        limit=max(1, min(int(limit or 40), 80)),
        offset=0,
        viewer_user_id=owner_id,
    )
    records = []
    for item in payload.get("comments") or []:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        records.append({
            "comment_id": int(item.get("id") or 0),
            "post_id": target_id,
            "author_user_id": int(item.get("user_id") or 0),
            "author_name": clean(author.get("display_name") or author.get("username") or "PulseSoc Member", 120),
            "body": clean(item.get("body"), 500),
            "created_at": clean(item.get("created_at"), 40),
            "can_edit": bool(item.get("can_edit")),
            "can_delete": bool(item.get("can_delete")),
            "source_url": f"/pulse/post/{target_id}",
        })
    return records


def get_post_like(user_id: int, post_id: int) -> bool | None:
    """Return the caller's like state, or None when the post is not viewable."""
    owner_id, target_id = int(user_id or 0), int(post_id or 0)
    if get_post(owner_id, target_id) is None:
        return None
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT reaction_type FROM pulse_reactions
               WHERE post_id=? AND user_id=? LIMIT 1""",
            (target_id, owner_id),
        )
        row = cur.fetchone()
        return bool(row and dict(row).get("reaction_type") == "like")
    finally:
        conn.close()


def set_post_like(user_id: int, post_id: int, *, liked: bool) -> dict[str, Any]:
    """Set the caller's post-like state explicitly and idempotently."""
    owner_id, target_id = int(user_id or 0), int(post_id or 0)
    if get_post(owner_id, target_id) is None:
        return {"ok": False, "error": "post_not_found"}
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT reaction_type FROM pulse_reactions
               WHERE post_id=? AND user_id=? LIMIT 1""",
            (target_id, owner_id),
        )
        row = cur.fetchone()
        before = bool(row and dict(row).get("reaction_type") == "like")
        desired = bool(liked)
        if before == desired:
            return {"ok": True, "post_id": target_id, "liked": desired, "changed": False}
        if desired:
            cur.execute(
                """INSERT INTO pulse_reactions (post_id,user_id,reaction_type,created_at)
                   VALUES (?,?,'like',CURRENT_TIMESTAMP)
                   ON CONFLICT(post_id,user_id) DO UPDATE SET
                     reaction_type='like', created_at=CURRENT_TIMESTAMP""",
                (target_id, owner_id),
            )
        else:
            cur.execute(
                """DELETE FROM pulse_reactions
                   WHERE post_id=? AND user_id=? AND reaction_type='like'""",
                (target_id, owner_id),
            )
        conn.commit()
        return {"ok": True, "post_id": target_id, "liked": desired, "changed": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "get_post", "get_post_like", "list_post_comments", "list_posts", "set_post_like",
]
