"""Fast shared chat helpers for dashboard private chat and Arena redirects."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import media_service, user_context


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def _clean(value, limit=2000):
    return str(value or "").replace("<", "").replace(">", "").strip()[:limit]


def _row(row):
    return dict(row) if row else None


def _public_profile(cur, user_id):
    cur.execute(
        """
        SELECT u.user_id, u.display_name, u.full_name, u.last_seen_at,
               ap.public_player_id, ap.display_name AS arena_name, ap.rank, ap.avatar_url, ap.faction
        FROM users u
        LEFT JOIN arena_profiles ap ON ap.user_id=u.user_id
        WHERE u.user_id=?
        LIMIT 1
        """,
        (int(user_id),),
    )
    row = _row(cur.fetchone()) or {}
    return {
        "user_id": int(row.get("user_id") or 0),
        "display_name": row.get("arena_name") or row.get("display_name") or row.get("full_name") or "CoinPilotXAI user",
        "public_player_id": row.get("public_player_id") or "",
        "rank": row.get("rank") or "Rookie",
        "avatar": row.get("avatar_url") or "",
        "faction": row.get("faction") or "",
        "recently_active": bool(row.get("last_seen_at")),
    }


def direct_thread(cur, user_id, other_user_id):
    user_id = int(user_id)
    other_user_id = int(other_user_id)
    cur.execute(
        """
        SELECT c.id
        FROM conversations c
        JOIN conversation_members a ON a.conversation_id=c.id AND a.user_id=?
        JOIN conversation_members b ON b.conversation_id=c.id AND b.user_id=?
        WHERE c.conversation_type='direct'
        ORDER BY c.id DESC
        LIMIT 1
        """,
        (user_id, other_user_id),
    )
    existing = cur.fetchone()
    if existing:
        return int(existing["id"])
    created_at = now_iso()
    cur.execute(
        "INSERT INTO conversations (conversation_type, created_by, created_at, updated_at) VALUES ('direct', ?, ?, ?)",
        (user_id, created_at, created_at),
    )
    thread_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO conversation_members (conversation_id, user_id, joined_at, last_read_at) VALUES (?, ?, ?, ?)",
        (thread_id, user_id, created_at, created_at),
    )
    cur.execute(
        "INSERT INTO conversation_members (conversation_id, user_id, joined_at, last_read_at) VALUES (?, ?, ?, ?)",
        (thread_id, other_user_id, created_at, ""),
    )
    return thread_id


def list_threads(user_id, limit=80):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, c.updated_at, ou.user_id AS other_user_id,
               COALESCE(NULLIF(ap.display_name, ''), NULLIF(ap.public_player_id, ''), NULLIF(ou.display_name, ''), 'CoinPilotXAI user') AS other_name,
               ap.public_player_id AS other_public_player_id, ou.last_seen_at AS other_last_seen_at,
               MAX(pm.created_at) AS last_message_at,
               (
                 SELECT body FROM private_messages p2
                 WHERE p2.conversation_id=c.id AND p2.deleted_at IS NULL
                 ORDER BY p2.id DESC LIMIT 1
               ) AS latest_message,
               COUNT(CASE WHEN pm.sender_user_id != ? AND pm.created_at > COALESCE(cm.last_read_at, '') THEN 1 END) AS unread_count
        FROM conversations c
        JOIN conversation_members cm ON cm.conversation_id=c.id AND cm.user_id=?
        LEFT JOIN conversation_members other_cm ON other_cm.conversation_id=c.id AND other_cm.user_id != ?
        LEFT JOIN users ou ON ou.user_id=other_cm.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=ou.user_id
        LEFT JOIN private_messages pm ON pm.conversation_id=c.id AND pm.deleted_at IS NULL
        GROUP BY c.id, c.updated_at, ou.user_id, ap.display_name, ap.public_player_id, ou.display_name, ou.last_seen_at
        ORDER BY COALESCE(MAX(pm.created_at), c.updated_at) DESC
        LIMIT ?
        """,
        (int(user_id), int(user_id), int(user_id), int(limit or 80)),
    )
    rows = []
    for row in cur.fetchall():
        rows.append({
            "thread_id": row["id"],
            "id": row["id"],
            "title": row["other_name"] or f"Conversation {row['id']}",
            "other_user_id": row["other_user_id"],
            "other_public_player_id": row["other_public_player_id"],
            "other_last_seen_at": row["other_last_seen_at"],
            "updated_at": row["updated_at"],
            "last_message_at": row["last_message_at"],
            "latest_message": row["latest_message"],
            "unread_count": row["unread_count"] or 0,
        })
    conn.close()
    return {"ok": True, "threads": rows, "conversations": rows}


def is_participant(cur, user_id, thread_id):
    cur.execute("SELECT id FROM conversation_members WHERE user_id=? AND conversation_id=? LIMIT 1", (int(user_id), int(thread_id)))
    return bool(cur.fetchone())


def messages(user_id, thread_id, after_id=0, limit=50):
    conn = user_context.connect()
    cur = conn.cursor()
    if not is_participant(cur, user_id, thread_id):
        conn.close()
        return None
    if int(after_id or 0):
        cur.execute(
            "SELECT id, conversation_id, sender_user_id, body, created_at FROM private_messages WHERE conversation_id=? AND id>? AND deleted_at IS NULL ORDER BY id ASC LIMIT ?",
            (int(thread_id), int(after_id), int(limit or 50)),
        )
    else:
        cur.execute(
            "SELECT id, conversation_id, sender_user_id, body, created_at FROM private_messages WHERE conversation_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT ?",
            (int(thread_id), int(limit or 50)),
        )
    raw = [dict(row) for row in cur.fetchall()]
    if not int(after_id or 0):
        raw.reverse()
    now = now_iso()
    cur.execute("UPDATE conversation_members SET last_read_at=? WHERE user_id=? AND conversation_id=?", (now, int(user_id), int(thread_id)))
    cur.execute("SELECT user_id FROM conversation_members WHERE conversation_id=? AND user_id != ? LIMIT 1", (int(thread_id), int(user_id)))
    other_row = cur.fetchone()
    other_id = int(other_row["user_id"]) if other_row else 0
    conn.commit()
    me = _public_profile(cur, user_id)
    other = _public_profile(cur, other_id) if other_id else {}
    conn.close()
    media_map = media_service.media_for_messages([message["id"] for message in raw])
    items = [
        {
            "message_id": int(message["id"]),
            "id": int(message["id"]),
            "thread_id": int(message["conversation_id"]),
            "sender_id": int(message["sender_user_id"]),
            "is_mine": int(message["sender_user_id"]) == int(user_id),
            "body": message["body"],
            "media": media_map.get(int(message["id"]), []),
            "created_at": message["created_at"],
            "delivery_status": "delivered",
        }
        for message in raw
    ]
    return {
        "ok": True,
        "thread": {"id": int(thread_id), "thread_id": int(thread_id), "other": other},
        "me": me,
        "other": other,
        "messages": items,
        "last_message_id": max([item["message_id"] for item in items] or [int(after_id or 0)]),
    }


def send_message(user_id, thread_id, body, media_ids=None):
    body = _clean(body)
    media_ids = media_ids or []
    if not body and not media_ids:
        return {"ok": False, "message": "Message or media required."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    if not is_participant(cur, user_id, thread_id):
        conn.close()
        return {"ok": False, "message": "Conversation not found."}, 404
    one_minute_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    cur.execute("SELECT COUNT(*) AS count FROM private_messages WHERE sender_user_id=? AND created_at>=?", (int(user_id), one_minute_ago))
    if int((cur.fetchone() or {"count": 0})["count"] or 0) >= 12:
        conn.close()
        return {"ok": False, "message": "Slow down before sending more messages."}, 429
    cur.execute(
        """
        SELECT blocked_user_id FROM blocked_users
        WHERE (blocker_user_id=? AND blocked_user_id IN (SELECT user_id FROM conversation_members WHERE conversation_id=?))
           OR (blocked_user_id=? AND blocker_user_id IN (SELECT user_id FROM conversation_members WHERE conversation_id=?))
        LIMIT 1
        """,
        (int(user_id), int(thread_id), int(user_id), int(thread_id)),
    )
    if cur.fetchone():
        conn.close()
        return {"ok": False, "message": "Messaging is blocked for this conversation."}, 403
    created_at = now_iso()
    cur.execute(
        "INSERT INTO private_messages (conversation_id, sender_user_id, body, created_at) VALUES (?, ?, ?, ?)",
        (int(thread_id), int(user_id), body, created_at),
    )
    message_id = int(cur.lastrowid)
    cur.execute("UPDATE conversations SET updated_at=? WHERE id=?", (created_at, int(thread_id)))
    conn.commit()
    conn.close()
    attached_media = media_service.attach_media_to_message(user_id, message_id, media_ids, context_type="private_chat", context_id=thread_id)
    return {
        "ok": True,
        "message": {
            "message_id": message_id,
            "id": message_id,
            "thread_id": int(thread_id),
            "sender_id": int(user_id),
            "is_mine": True,
            "body": body,
            "media": attached_media,
            "created_at": created_at,
            "delivery_status": "delivered",
        },
        "message_id": message_id,
    }, 200


def start_thread(user_id, public_player_id="", query="", message=""):
    lookup = _clean(public_player_id or query, 160)
    if not lookup:
        return {"ok": False, "message": "Public player ID required."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id FROM users u
        LEFT JOIN arena_profiles ap ON ap.user_id=u.user_id
        WHERE lower(ap.public_player_id)=lower(?)
           OR lower(ap.display_name)=lower(?)
           OR lower(u.display_name)=lower(?)
           OR lower(u.full_name)=lower(?)
           OR lower(u.username)=lower(?)
        ORDER BY u.user_id LIMIT 1
        """,
        (lookup, lookup, lookup, lookup, lookup),
    )
    other = cur.fetchone()
    if not other:
        conn.close()
        return {"ok": False, "message": "Player not found."}, 404
    other_user_id = int(other["user_id"])
    if other_user_id == int(user_id):
        conn.close()
        return {"ok": False, "message": "You cannot message yourself."}, 400
    thread_id = direct_thread(cur, user_id, other_user_id)
    conn.commit()
    conn.close()
    if message:
        send_message(user_id, thread_id, message)
    return {"ok": True, "thread_id": thread_id, "conversation_id": thread_id, "next_url": f"/chat/thread/{thread_id}"}, 200
