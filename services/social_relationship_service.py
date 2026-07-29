"""Caller-scoped reads for PulseSoc's canonical relationship graph."""

from __future__ import annotations

from typing import Any

from services import db as db_service
from services.undx_agent_contracts import clean


def _public_person(row: Any) -> dict[str, Any]:
    data = dict(row or {})
    return {
        "user_id": int(data.get("user_id") or 0),
        "username": clean(data.get("username"), 80),
        "display_name": clean(
            data.get("display_name") or data.get("full_name") or data.get("username") or "PulseSoc Member",
            120,
        ),
        "avatar_url": clean(data.get("avatar_thumbnail_url") or data.get("avatar_url"), 800),
        "profile_url": f"/pulse/profile/{int(data.get('user_id') or 0)}",
        "followed_at": clean(data.get("followed_at"), 40),
    }


def list_relationships(
    user_id: int,
    *,
    direction: str = "followers",
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List followers or following for the authenticated account only."""
    owner_id = int(user_id or 0)
    if owner_id <= 0:
        return []
    selected = clean(direction or "followers", 20).lower()
    if selected not in {"followers", "following"}:
        selected = "followers"
    search = clean(query, 120).lower()
    bounded_limit = max(1, min(int(limit or 20), 50))
    if selected == "followers":
        edge_user, owner_column = "follower_user_id", "followed_user_id"
    else:
        edge_user, owner_column = "followed_user_id", "follower_user_id"
    params: list[Any] = [owner_id]
    search_sql = ""
    if search:
        search_sql = (
            " AND (lower(COALESCE(u.username,'')) LIKE ?"
            " OR lower(COALESCE(u.display_name,u.full_name,'')) LIKE ?)"
        )
        like = f"%{search}%"
        params.extend((like, like))
    params.append(bounded_limit)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT u.user_id, u.username, u.display_name, u.full_name,
                   u.avatar_url, u.avatar_thumbnail_url, f.created_at AS followed_at
            FROM pulse_follows f
            JOIN users u ON u.user_id=f.{edge_user}
            WHERE f.{owner_column}=? AND COALESCE(u.deleted_at,'')=''
            {search_sql}
            ORDER BY f.created_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [_public_person(row) for row in cur.fetchall()]
    finally:
        conn.close()


def is_following(user_id: int, target_user_id: int) -> bool | None:
    """Read one directed relationship; never treat the reverse edge as evidence."""
    actor_id, target_id = int(user_id or 0), int(target_user_id or 0)
    if actor_id <= 0 or target_id <= 0 or actor_id == target_id:
        return None
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM users
               WHERE user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1""",
            (target_id,),
        )
        if not cur.fetchone():
            return None
        cur.execute(
            """SELECT 1 FROM pulse_follows
               WHERE follower_user_id=? AND followed_user_id=? LIMIT 1""",
            (actor_id, target_id),
        )
        return bool(cur.fetchone())
    finally:
        conn.close()


def set_following(user_id: int, target_user_id: int, *, following: bool) -> dict[str, Any]:
    """Set a directed follow edge explicitly and idempotently."""
    actor_id, target_id = int(user_id or 0), int(target_user_id or 0)
    if actor_id <= 0 or target_id <= 0 or actor_id == target_id:
        return {"ok": False, "error": "invalid_target"}
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT user_id FROM users
               WHERE user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1""",
            (target_id,),
        )
        if not cur.fetchone():
            return {"ok": False, "error": "user_not_found"}
        cur.execute(
            """SELECT 1 FROM pulse_follows
               WHERE follower_user_id=? AND followed_user_id=? LIMIT 1""",
            (actor_id, target_id),
        )
        before = bool(cur.fetchone())
        desired = bool(following)
        if before == desired:
            return {
                "ok": True, "target_user_id": target_id,
                "following": desired, "changed": False,
            }
        if desired:
            cur.execute(
                """INSERT OR IGNORE INTO pulse_follows
                   (follower_user_id,followed_user_id,followed_public_player_id,created_at)
                   VALUES (?,?,'',CURRENT_TIMESTAMP)""",
                (actor_id, target_id),
            )
        else:
            cur.execute(
                """DELETE FROM pulse_follows
                   WHERE follower_user_id=? AND followed_user_id=?""",
                (actor_id, target_id),
            )
        conn.commit()
        return {
            "ok": True, "target_user_id": target_id,
            "following": desired, "changed": True,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = ["is_following", "list_relationships", "set_following"]
