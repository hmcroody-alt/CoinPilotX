"""Pulse Communications 2.0 service layer.

This module is intentionally thin: it centralizes the v2 API contract while
delegating persistence to the proven Pulse Messenger tables and helpers in
``bot.py``. That keeps existing data safe and gives the new UI one consistent
conversation/message shape across direct messages, rooms, groups, and legacy
Dashboard chat bridges.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from .flags import PULSE_COMMUNICATIONS_V2_ENABLED
from .models import COMM_V2_TABLES
from .schemas import ServiceResult


DISABLED_MESSAGE = "Pulse Communications 2.0 is disabled."


def _bot():
    import bot  # Imported lazily to avoid a route-registration cycle.

    return bot


def disabled_result(action: str) -> dict:
    return ServiceResult(
        ok=False,
        status="disabled",
        message=DISABLED_MESSAGE,
        data={"action": action, "enabled": bool(PULSE_COMMUNICATIONS_V2_ENABLED)},
    ).to_dict()


def ensure_v2_schema(cur) -> None:
    for table in COMM_V2_TABLES:
        cur.execute(table.create_sql)


def _disabled_if_needed(action: str) -> dict | None:
    if PULSE_COMMUNICATIONS_V2_ENABLED:
        return None
    return disabled_result(action)


def _features() -> list[dict]:
    return [
        {"key": "voice", "label": "Voice", "state": "Coming Soon"},
        {"key": "video", "label": "Video", "state": "Coming Soon"},
        {"key": "files", "label": "Files", "state": "Coming Soon"},
        {"key": "undx", "label": "UNDX Collaboration", "state": "Coming Soon"},
    ]


def _open_db():
    bot = _bot()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    ensure_v2_schema(cur)
    return bot, conn, cur


def _ok(data=None, message="") -> dict:
    payload = {"ok": True, "status": "ready", "features": _features(), "trace_id": secrets.token_hex(6)}
    if message:
        payload["message"] = message
    if data:
        payload.update(data)
    return payload


def create_conversation(user_id: int, payload=None) -> dict:
    disabled = _disabled_if_needed("create_conversation")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    payload = payload or {}
    conversation_type = bot.clean_html(payload.get("conversation_type") or payload.get("type") or "direct").lower()
    try:
        if conversation_type == "direct":
            target_user_id = bot.safe_int(payload.get("target_user_id") or payload.get("receiver_user_id") or payload.get("user_id"), 0)
            result, status = bot.pulse_start_conversation(cur, user_id, target_user_id=target_user_id, public_player_id=payload.get("public_player_id") or "")
        elif conversation_type == "room":
            result, status = bot.pulse_get_or_create_room_conversation(cur, user_id, room_key=payload.get("room_key") or payload.get("room_id") or "general-pulse")
        else:
            result, status = bot.pulse_get_or_create_group_conversation(
                cur,
                user_id,
                title=payload.get("title") or "Group Chat",
                participant_ids=payload.get("participant_ids") or payload.get("member_ids") or [],
            )
        if result.get("ok"):
            conn.commit()
            return _ok({"conversation": result.get("conversation") or {}, "conversation_id": str(result.get("conversation_id") or "")}, "Conversation ready.")
        conn.rollback()
        return {**result, "status": "error", "http_status": status, "features": _features()}
    finally:
        conn.close()


def list_conversations(user_id: int, filters=None) -> dict:
    disabled = _disabled_if_needed("list_conversations")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    filters = filters or {}
    kind = bot.clean_html(filters.get("type") or "all").lower()
    items = []
    skipped = []
    try:
        if kind in {"all", "direct"}:
            direct, skipped = bot.pulse_comm_pulse_conversations(cur, user_id, {"direct"})
            items.extend(direct)
            items.extend(bot.pulse_comm_legacy_conversations(user_id))
        if kind in {"all", "rooms", "room"}:
            items.extend(bot.pulse_comm_rooms(cur, user_id))
        if kind in {"all", "groups", "group"}:
            groups, group_skipped = bot.pulse_comm_pulse_conversations(cur, user_id, {"group", "community", "community_group", "creator", "live"})
            skipped.extend(group_skipped)
            items.extend(groups)
        conn.commit()
        return _ok({"items": items, "conversations": items, "partial": bool(skipped), "skipped": len(skipped)})
    finally:
        conn.close()


def send_message(user_id: int, conversation_id: int | str, payload=None) -> dict:
    disabled = _disabled_if_needed("send_message")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    payload = payload or {}
    user = {"user_id": user_id}
    source, resolved_id = bot.pulse_comm_ref(conversation_id)
    body = bot.clean_html(payload.get("message") or payload.get("body") or payload.get("content") or "")[:2000].strip()
    try:
        if source == "legacy":
            result, status = bot.chat_realtime_service.send_message(user_id, resolved_id, body)
            return {**result, "status": "ready" if result.get("ok") else "error", "http_status": status, "features": _features()}
        if source == "room":
            room, room_status = bot.pulse_get_or_create_room_conversation(cur, user_id, room_key=resolved_id)
            if not room.get("ok"):
                conn.rollback()
                return {**room, "status": "error", "http_status": room_status}
            resolved_id = bot.safe_int(room.get("conversation_id"), 0)
        result, status = bot.pulse_send_conversation_message(
            cur,
            user,
            resolved_id,
            body,
            payload.get("message_type") or payload.get("type") or "text",
            payload.get("media_url") or "",
            payload.get("thumbnail_url") or "",
            payload.get("media_metadata") if isinstance(payload.get("media_metadata"), dict) else {},
            bot.safe_int(payload.get("reply_to_id"), 0),
            bot.safe_int(payload.get("file_size"), 0),
            float(payload.get("duration") or payload.get("duration_seconds") or 0),
            payload.get("client_message_id") or payload.get("local_id") or "",
            payload.get("local_created_at") or "",
        )
        if result.get("ok"):
            conn.commit()
            return _ok({"message": result.get("data"), "message_id": result.get("message_id"), "conversation_id": str(resolved_id)}, "Message sent.")
        conn.rollback()
        return {**result, "status": "error", "http_status": status, "features": _features()}
    finally:
        conn.close()


def list_messages(user_id: int, conversation_id: int | str, filters=None) -> dict:
    disabled = _disabled_if_needed("list_messages")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    filters = filters or {}
    user = {"user_id": user_id}
    source, resolved_id = bot.pulse_comm_ref(conversation_id)
    try:
        if source == "legacy":
            payload, status = bot.pulse_comm_legacy_messages(user, resolved_id, limit=bot.safe_int(filters.get("limit"), 80), after_id=bot.safe_int(filters.get("after_id"), 0))
            return {**payload, "status": "ready" if payload.get("ok") else "error", "http_status": status, "trace_id": payload.get("trace_id") or secrets.token_hex(6)}
        if source == "room":
            room, room_status = bot.pulse_get_or_create_room_conversation(cur, user_id, room_key=resolved_id)
            if not room.get("ok"):
                conn.rollback()
                return {**room, "status": "error", "http_status": room_status}
            resolved_id = bot.safe_int(room.get("conversation_id"), 0)
        payload, status = bot.pulse_comm_pulse_messages(cur, user, resolved_id, limit=bot.safe_int(filters.get("limit"), 80), before_id=bot.safe_int(filters.get("before_id"), 0))
        payload["http_status"] = status
        payload["status"] = "ready" if payload.get("ok") else "error"
        payload["trace_id"] = payload.get("trace_id") or secrets.token_hex(6)
        conn.commit()
        return payload
    finally:
        conn.close()


def create_community(user_id: int, payload=None) -> dict:
    disabled = _disabled_if_needed("create_community")
    if disabled:
        return disabled_result("create_community")
    payload = payload or {}
    name = str(payload.get("name") or "").strip()[:140] or "Pulse Community"
    return _ok({"community": {"name": name, "status": "draft", "owner_user_id": user_id}}, "Community foundation ready.")


def create_channel(user_id: int, community_id: int, payload=None) -> dict:
    disabled = _disabled_if_needed("create_channel")
    if disabled:
        return disabled
    payload = payload or {}
    name = str(payload.get("name") or "").strip()[:140] or "general"
    return _ok({"channel": {"name": name, "community_id": int(community_id or 0), "status": "draft"}}, "Channel foundation ready.")


def list_members(user_id: int, conversation_id: int | str) -> dict:
    disabled = _disabled_if_needed("list_members")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    source, resolved_id = bot.pulse_comm_ref(conversation_id)
    try:
        if source == "room":
            room, _ = bot.pulse_get_or_create_room_conversation(cur, user_id, room_key=resolved_id)
            resolved_id = bot.safe_int(room.get("conversation_id"), 0)
        if source == "legacy":
            return _ok({"members": [], "conversation_id": str(conversation_id)})
        conversation, access = bot.pulse_comm_conversation_access(cur, user_id, resolved_id)
        if access != "ok":
            return {"ok": False, "status": "error", "message": "Conversation not found." if access == "missing" else "You do not have access to this chat.", "http_status": 404 if access == "missing" else 403}
        cur.execute(
            """
            SELECT p.user_id, COALESCE(p.role,'member') AS role, p.joined_at, p.last_seen_at,
                   COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   COALESCE(u.avatar_url,'') AS avatar_url
            FROM pulse_conversation_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id=? AND COALESCE(p.left_at,'')=''
            ORDER BY CASE COALESCE(p.role,'member') WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'moderator' THEN 2 ELSE 3 END,
                     COALESCE(p.last_seen_at,p.joined_at,p.created_at,'') DESC
            LIMIT 120
            """,
            (resolved_id,),
        )
        members = [dict(row) for row in cur.fetchall()]
        return _ok({"members": members, "conversation_id": str(resolved_id)})
    finally:
        conn.close()


def mark_read(user_id: int, conversation_id: int | str) -> dict:
    disabled = _disabled_if_needed("mark_read")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    source, resolved_id = bot.pulse_comm_ref(conversation_id)
    try:
        if source == "room":
            room, _ = bot.pulse_get_or_create_room_conversation(cur, user_id, room_key=resolved_id)
            resolved_id = bot.safe_int(room.get("conversation_id"), 0)
        if source == "legacy":
            return _ok({"conversation_id": str(conversation_id), "last_read_message_id": 0})
        conversation, access = bot.pulse_comm_conversation_access(cur, user_id, resolved_id)
        if access != "ok":
            return {"ok": False, "status": "error", "message": "Conversation not found." if access == "missing" else "You do not have access to this chat.", "http_status": 404 if access == "missing" else 403}
        last_id = bot.pulse_mark_conversation_read(cur, resolved_id, user_id)
        conn.commit()
        return _ok({"conversation_id": str(resolved_id), "last_read_message_id": last_id, "unread_count": 0}, "Marked read.")
    finally:
        conn.close()


def set_reaction(user_id: int, message_id: int, reaction: str) -> dict:
    disabled = _disabled_if_needed("set_reaction")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    reaction = bot.pulse_normalize_emoji_reaction(reaction or "heart")
    if not reaction:
        conn.close()
        return {"ok": False, "status": "error", "message": "Choose a supported emoji reaction.", "http_status": 400}
    try:
        cur.execute("SELECT * FROM pulse_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id or 0),))
        message = dict(cur.fetchone() or {})
        if not message:
            return {"ok": False, "status": "error", "message": "Message not found.", "http_status": 404}
        conversation, access = bot.pulse_comm_conversation_access(cur, user_id, int(message.get("conversation_id") or 0))
        if access != "ok":
            return {"ok": False, "status": "error", "message": "You do not have access to this chat.", "http_status": 403}
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur.execute("DELETE FROM pulse_message_reactions WHERE message_id=? AND user_id=?", (int(message_id), user_id))
        cur.execute(
            "INSERT INTO pulse_message_reactions (message_id, conversation_id, user_id, reaction_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(message_id), int(message.get("conversation_id") or 0), user_id, reaction, now, now),
        )
        conn.commit()
        return _ok({"message_id": int(message_id), "reaction": reaction}, "Reaction updated.")
    finally:
        conn.close()


def pin_message(user_id: int, conversation_id: int | str, message_id: int, pinned=True) -> dict:
    disabled = _disabled_if_needed("pin_message")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    source, resolved_id = bot.pulse_comm_ref(conversation_id)
    try:
        if source != "pulse":
            return {"ok": False, "status": "error", "message": "Pinning is available for Pulse conversations only.", "http_status": 400}
        conversation, access = bot.pulse_comm_conversation_access(cur, user_id, resolved_id)
        if access != "ok":
            return {"ok": False, "status": "error", "message": "Conversation not found." if access == "missing" else "You do not have access to this chat.", "http_status": 404 if access == "missing" else 403}
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur.execute("UPDATE pulse_messages SET updated_at=? WHERE id=? AND conversation_id=?", (now, int(message_id or 0), resolved_id))
        cur.execute("UPDATE pulse_conversations SET last_activity_at=?, updated_at=? WHERE id=?", (now, now, resolved_id))
        conn.commit()
        return _ok({"conversation_id": str(resolved_id), "message_id": int(message_id or 0), "pinned": bool(pinned)}, "Message pin state updated.")
    finally:
        conn.close()


def search_messages(user_id: int, query: str) -> dict:
    disabled = _disabled_if_needed("search_messages")
    if disabled:
        return disabled
    bot, conn, cur = _open_db()
    q = bot.clean_html(query or "")[:120].strip()
    if not q:
        conn.close()
        return _ok({"messages": [], "conversations": []})
    like = f"%{q.lower()}%"
    try:
        cur.execute(
            """
            SELECT m.* FROM pulse_messages m
            JOIN pulse_conversation_participants p ON p.conversation_id=m.conversation_id AND p.user_id=? AND COALESCE(p.left_at,'')=''
            WHERE COALESCE(m.deleted_at,'')='' AND lower(COALESCE(m.body,'')) LIKE ?
            ORDER BY m.id DESC LIMIT 50
            """,
            (user_id, like),
        )
        messages = [bot._pulse_message_payload(row, user_id) for row in cur.fetchall()]
        return _ok({"messages": messages, "items": messages})
    finally:
        conn.close()
