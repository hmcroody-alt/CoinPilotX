"""Owner-scoped domain reads for the canonical PulseSoc Saved library.

The HTTP handler historically owned this query.  UNDX must not import Flask or
re-enter an authenticated route, so this module is the shared domain boundary:
the authenticated ``user_id`` is mandatory, every query is scoped by it in SQL,
and only bounded display fields leave the service.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from services import db as db_service
from services.undx_agent_contracts import clean


SAVED_CONTENT_TYPES = (
    "all", "post", "reel", "status", "live_replay", "marketplace", "room",
    "group", "teacher", "comment", "thread", "video", "image", "learning",
)


def _public_item(row: Any) -> dict[str, Any]:
    data = dict(row or {})
    return {
        "item_id": int(data.get("id") or 0),
        "collection_id": int(data.get("collection_id") or 0),
        "collection_name": clean(data.get("collection_name") or "Favorites", 120),
        "content_type": clean(data.get("content_type") or "post", 40),
        "content_id": clean(data.get("content_id"), 80),
        "title": clean(data.get("title") or "Saved item", 220),
        "preview_text": clean(data.get("preview_text"), 320),
        "thumbnail_url": clean(data.get("thumbnail_url"), 800),
        "source_url": clean(data.get("source_url") or "/pulse/saved", 500),
        "saved_at": clean(data.get("updated_at") or data.get("created_at"), 40),
    }


def list_saved_items(
    user_id: int,
    *,
    content_type: str = "all",
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the authenticated account's saved items, newest first."""
    owner_id = int(user_id or 0)
    if owner_id <= 0:
        return []
    selected_type = clean(content_type or "all", 40).lower()
    if selected_type not in SAVED_CONTENT_TYPES:
        selected_type = "all"
    search = clean(query, 120)
    bounded_limit = max(1, min(int(limit or 20), 50))
    where = ["i.user_id=?"]
    params: list[Any] = [owner_id]
    if selected_type != "all":
        where.append("i.content_type=?")
        params.append(selected_type)
    if search:
        where.append("(lower(COALESCE(i.title,'')) LIKE ? OR lower(COALESCE(i.preview_text,'')) LIKE ?)")
        like = f"%{search.lower()}%"
        params.extend((like, like))
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT i.*, c.name AS collection_name
            FROM pulse_saved_items i
            LEFT JOIN pulse_saved_collections c
              ON c.id=i.collection_id AND c.user_id=i.user_id
            WHERE {' AND '.join(where)}
            ORDER BY i.updated_at DESC, i.id DESC
            LIMIT ?
            """,
            (*params, bounded_limit),
        )
        return [_public_item(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_post_saved(user_id: int, post_id: int) -> dict[str, Any] | None:
    """Read the caller's canonical saved state for one existing post."""
    owner_id, target_id = int(user_id or 0), int(post_id or 0)
    if owner_id <= 0 or target_id <= 0:
        return None
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT p.id,
                      EXISTS(
                        SELECT 1 FROM pulse_post_saves s
                        WHERE s.post_id=p.id AND s.user_id=?
                      ) AS saved
               FROM pulse_posts p
               WHERE p.id=? AND p.deleted_at IS NULL
               LIMIT 1""",
            (owner_id, target_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        return {"post_id": int(data["id"]), "saved": bool(data["saved"])}
    finally:
        conn.close()


def _ensure_default_collection(cur, user_id: int, now: str) -> int:
    cur.execute(
        """SELECT id FROM pulse_saved_collections
           WHERE user_id=? AND COALESCE(is_default,0)=1
           ORDER BY id LIMIT 1""",
        (int(user_id),),
    )
    row = cur.fetchone()
    if row:
        return int(dict(row)["id"])
    cur.execute(
        """INSERT INTO pulse_saved_collections
           (user_id,name,slug,is_default,created_at,updated_at)
           VALUES (?,'Favorites','favorites',1,?,?)""",
        (int(user_id), now, now),
    )
    return int(cur.lastrowid)


def set_post_saved(user_id: int, post_id: int, *, saved: bool) -> dict[str, Any]:
    """Set, never toggle, one post's Saved state and return the persisted result."""
    owner_id, requested_id = int(user_id or 0), int(post_id or 0)
    if owner_id <= 0 or requested_id <= 0:
        return {"ok": False, "error": "invalid_target"}
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id,title,body,post_type,repost_of_post_id
               FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1""",
            (requested_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "post_not_found"}
        post = dict(row)
        target_id = int(post.get("repost_of_post_id") or post["id"])
        if target_id != int(post["id"]):
            cur.execute(
                """SELECT id,title,body,post_type,repost_of_post_id
                   FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1""",
                (target_id,),
            )
            original = cur.fetchone()
            if not original:
                return {"ok": False, "error": "post_not_found"}
            post = dict(original)
        cur.execute(
            "SELECT 1 FROM pulse_post_saves WHERE post_id=? AND user_id=? LIMIT 1",
            (target_id, owner_id),
        )
        before = bool(cur.fetchone())
        desired = bool(saved)
        if before == desired:
            return {
                "ok": True, "post_id": target_id, "saved": desired,
                "changed": False,
            }
        if not desired:
            cur.execute(
                "DELETE FROM pulse_post_saves WHERE post_id=? AND user_id=?",
                (target_id, owner_id),
            )
            cur.execute(
                """DELETE FROM pulse_saved_items
                   WHERE user_id=? AND content_type='post' AND content_id=?""",
                (owner_id, str(target_id)),
            )
        else:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur.execute(
                """INSERT OR IGNORE INTO pulse_post_saves
                   (post_id,user_id,collection_name,created_at)
                   VALUES (?,?,'Saved',?)""",
                (target_id, owner_id, now),
            )
            collection_id = _ensure_default_collection(cur, owner_id, now)
            title = clean(post.get("title") or f"PulseSoc {post.get('post_type') or 'post'}", 220)
            preview = clean(post.get("body"), 700)
            cur.execute(
                """INSERT INTO pulse_saved_items
                   (user_id,collection_id,content_type,content_id,title,preview_text,
                    thumbnail_url,media_url,source_url,metadata_json,created_at,updated_at)
                   VALUES (?,?,'post',?,?,?,'','',?,?,?,?)
                   ON CONFLICT(user_id,content_type,content_id) DO UPDATE SET
                     collection_id=excluded.collection_id,
                     title=excluded.title,
                     preview_text=excluded.preview_text,
                     updated_at=excluded.updated_at""",
                (
                    owner_id, collection_id, str(target_id), title, preview,
                    f"/pulse/post/{target_id}",
                    json.dumps({"post_type": post.get("post_type") or "post"}),
                    now, now,
                ),
            )
        conn.commit()
        return {
            "ok": True, "post_id": target_id, "saved": desired,
            "changed": True,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "SAVED_CONTENT_TYPES",
    "get_post_saved",
    "list_saved_items",
    "set_post_saved",
]
