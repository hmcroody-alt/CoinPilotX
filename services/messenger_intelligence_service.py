"""Side-effect-free, membership-scoped Messenger reads for UNDX."""

from __future__ import annotations

from typing import Any

from services import db as db_service
from services.undx_agent_contracts import clean


def list_my_conversations(
    user_id: int,
    *,
    conversation_type: str = "all",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List active memberships only; public discovery rooms are deliberately excluded."""
    owner_id = int(user_id or 0)
    if owner_id <= 0:
        return []
    selected = clean(conversation_type or "all", 40).lower()
    allowed = {"all", "direct", "group", "room", "community_channel"}
    if selected not in allowed:
        selected = "all"
    bounded_limit = max(1, min(int(limit or 20), 50))
    type_clause = ""
    params: list[Any] = [owner_id]
    if selected != "all":
        type_clause = " AND c.conversation_type=?"
        params.append(selected)
    params.append(bounded_limit)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT c.id AS conversation_id, c.public_id, c.conversation_type,
                   c.title, c.last_message_id, c.last_message_at,
                   p.unread_count, p.muted_until, p.pinned_at
            FROM comm_v2_participants p
            JOIN comm_v2_conversations c ON c.id=p.conversation_id
            WHERE p.user_id=? AND p.membership_state='active'
              AND COALESCE(p.left_at,'')=''
              AND c.status='active' AND COALESCE(c.deleted_at,'')=''
              {type_clause}
            ORDER BY CASE WHEN COALESCE(p.pinned_at,'')!='' THEN 0 ELSE 1 END,
                     COALESCE(p.pinned_at,c.last_message_at,c.last_activity_at,c.updated_at,c.created_at) DESC,
                     c.id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        records = []
        for row in cur.fetchall():
            data = dict(row)
            conversation_id = int(data.get("conversation_id") or 0)
            kind = clean(data.get("conversation_type") or "conversation", 40)
            records.append({
                "conversation_id": conversation_id,
                "public_id": clean(data.get("public_id"), 100),
                "conversation_type": kind,
                "title": clean(data.get("title") or ("Direct conversation" if kind == "direct" else "PulseSoc conversation"), 160),
                "unread_count": max(0, int(data.get("unread_count") or 0)),
                "last_message_id": int(data.get("last_message_id") or 0),
                "last_message_at": clean(data.get("last_message_at"), 40),
                "muted": bool(clean(data.get("muted_until"), 40)),
                "pinned": bool(clean(data.get("pinned_at"), 40)),
                "source_url": f"/pulse/messages/{conversation_id}",
            })
        return records
    finally:
        conn.close()


def list_conversation_messages(
    user_id: int,
    conversation_id: int,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Return a bounded message window without changing read or delivery state.

    Membership is checked before the message table is queried.  An invalid,
    departed, deleted, or foreign conversation therefore has the same empty
    result and cannot be used as an existence oracle.
    """
    owner_id = int(user_id or 0)
    target_id = int(conversation_id or 0)
    if owner_id <= 0 or target_id <= 0:
        return []
    bounded_limit = max(1, min(int(limit or 30), 100))
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM comm_v2_participants p
            JOIN comm_v2_conversations c ON c.id=p.conversation_id
            WHERE p.user_id=? AND p.conversation_id=?
              AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
              AND c.status='active' AND COALESCE(c.deleted_at,'')=''
            LIMIT 1
            """,
            (owner_id, target_id),
        )
        if cur.fetchone() is None:
            return []
        cur.execute(
            """
            SELECT id AS message_id, public_id, sender_user_id, message_type,
                   body, reply_to_message_id, created_at, edited_at
            FROM comm_v2_messages
            WHERE conversation_id=? AND COALESCE(deleted_at,'')=''
              AND moderation_status='approved'
            ORDER BY id DESC
            LIMIT ?
            """,
            (target_id, bounded_limit),
        )
        records = []
        for row in reversed(cur.fetchall()):
            data = dict(row)
            records.append({
                "message_id": int(data.get("message_id") or 0),
                "public_id": clean(data.get("public_id"), 100),
                "conversation_id": target_id,
                "sender_user_id": int(data.get("sender_user_id") or 0),
                "message_type": clean(data.get("message_type") or "text", 40),
                "body": clean(data.get("body"), 500),
                "reply_to_message_id": int(data.get("reply_to_message_id") or 0),
                "created_at": clean(data.get("created_at"), 40),
                "edited": bool(clean(data.get("edited_at"), 40)),
                "source_url": f"/pulse/messages/{target_id}",
            })
        return records
    finally:
        conn.close()


__all__ = ["list_my_conversations", "list_conversation_messages"]
