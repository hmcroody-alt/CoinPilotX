"""Pulse Communications 2.0 service layer."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from . import flags, infrastructure, twilio_service
from .models import ensure_schema


DISABLED_MESSAGE = "Pulse Communications 2.0 is not public yet."
ALLOWED_CONVERSATION_TYPES = {"direct", "group", "room", "community_channel"}
ALLOWED_MESSAGE_TYPES = {"text", "image", "gif", "video", "audio", "voice", "file", "media", "system"}
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trace() -> str:
    return secrets.token_hex(6)


def _public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def _clean(value: Any, limit: int = 2000) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()[:limit]


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _row(row) -> dict:
    return dict(row or {})


def _disabled(action: str) -> dict | None:
    if flags.is_enabled():
        return None
    return {"ok": False, "status": "disabled", "message": DISABLED_MESSAGE, "action": action, "enabled": False, "trace_id": _trace()}


def _ok(data: dict | None = None, message: str = "") -> dict:
    payload = {"ok": True, "status": "ready", "enabled": True, "trace_id": _trace()}
    if message:
        payload["message"] = message
    if data:
        payload.update(data)
    return payload


def _err(message: str, status: int = 400, code: str = "error") -> dict:
    return {"ok": False, "status": code, "message": message, "http_status": status, "trace_id": _trace()}


def _open_db():
    bot = _bot()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    _ensure_schema_ready(bot, cur, conn)
    return conn, cur


def _ensure_schema_ready(bot, cur, conn) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        started = datetime.now(timezone.utc)
        ensure_schema(cur)
        _ensure_columns(bot, cur, conn)
        conn.commit()
        _SCHEMA_READY = True
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        logging.info("PULSE_COMM_V2_SCHEMA_READY duration_ms=%s", elapsed_ms)


def _ensure_columns(bot, cur, conn) -> None:
    add_missing = getattr(bot, "add_columns_if_missing", None)
    table_columns = getattr(bot, "migration_table_columns", None)
    if not add_missing:
        return

    def add(cur, table, columns, conn=None):
        existing = table_columns(cur, table) if table_columns else set()
        missing = [(name, definition) for name, definition in columns if name not in existing]
        if missing:
            add_missing(cur, table, missing, conn=conn)

    add(cur, "comm_v2_conversations", [
        ("public_id", "TEXT"),
        ("conversation_type", "TEXT"),
        ("title", "TEXT"),
        ("description", "TEXT"),
        ("owner_user_id", "INTEGER"),
        ("created_by_user_id", "INTEGER"),
        ("direct_key", "TEXT"),
        ("community_id", "INTEGER"),
        ("channel_id", "INTEGER"),
        ("privacy", "TEXT DEFAULT 'private'"),
        ("visibility", "TEXT DEFAULT 'members'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("is_discoverable", "INTEGER DEFAULT 0"),
        ("member_count", "INTEGER DEFAULT 0"),
        ("last_message_id", "INTEGER DEFAULT 0"),
        ("last_message_at", "TEXT"),
        ("last_activity_at", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_participants", [
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("role", "TEXT DEFAULT 'member'"),
        ("membership_state", "TEXT DEFAULT 'active'"),
        ("joined_at", "TEXT"),
        ("left_at", "TEXT"),
        ("muted_until", "TEXT"),
        ("notifications_level", "TEXT DEFAULT 'all'"),
        ("last_seen_at", "TEXT"),
        ("last_read_message_id", "INTEGER DEFAULT 0"),
        ("last_read_at", "TEXT"),
        ("unread_count", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_messages", [
        ("public_id", "TEXT"),
        ("conversation_id", "INTEGER"),
        ("sender_user_id", "INTEGER"),
        ("message_type", "TEXT DEFAULT 'text'"),
        ("body", "TEXT"),
        ("reply_to_message_id", "INTEGER DEFAULT 0"),
        ("thread_root_message_id", "INTEGER DEFAULT 0"),
        ("client_message_id", "TEXT"),
        ("delivery_status", "TEXT DEFAULT 'sent'"),
        ("moderation_status", "TEXT DEFAULT 'approved'"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("edited_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_attachments", [
        ("attachment_public_id", "TEXT"),
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("media_upload_id", "INTEGER"),
        ("uploader_user_id", "INTEGER"),
        ("media_type", "TEXT"),
        ("storage_provider", "TEXT"),
        ("storage_key", "TEXT"),
        ("url", "TEXT"),
        ("cdn_url", "TEXT"),
        ("playback_url", "TEXT"),
        ("thumbnail_url", "TEXT"),
        ("mime_type", "TEXT"),
        ("file_size", "INTEGER DEFAULT 0"),
        ("file_size_bytes", "INTEGER DEFAULT 0"),
        ("duration_seconds", "REAL DEFAULT 0"),
        ("waveform_json", "TEXT"),
        ("voice_note", "INTEGER DEFAULT 0"),
        ("width", "INTEGER DEFAULT 0"),
        ("height", "INTEGER DEFAULT 0"),
        ("mux_asset_id", "TEXT"),
        ("mux_playback_id", "TEXT"),
        ("mux_status", "TEXT"),
        ("scan_status", "TEXT DEFAULT 'approved'"),
        ("created_at", "TEXT"),
    ], conn=conn)
    add(cur, "chat_media_uploads", [
        ("duration_seconds", "REAL DEFAULT 0"),
        ("waveform_json", "TEXT"),
        ("voice_note", "INTEGER DEFAULT 0"),
    ], conn=conn)
    add(cur, "comm_v2_live_streams", [
        ("public_id", "TEXT"),
        ("conversation_id", "INTEGER"),
        ("creator_user_id", "INTEGER"),
        ("mux_live_stream_id", "TEXT"),
        ("mux_stream_key", "TEXT"),
        ("mux_playback_id", "TEXT"),
        ("mux_live_status", "TEXT"),
        ("mux_recording_asset_id", "TEXT"),
        ("mux_recording_playback_id", "TEXT"),
        ("ingest_url", "TEXT"),
        ("rtmp_url", "TEXT"),
        ("playback_url", "TEXT"),
        ("status", "TEXT DEFAULT 'created'"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("ended_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_message_reactions", [
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("reaction_type", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_read_receipts", [
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("delivered_at", "TEXT"),
        ("seen_at", "TEXT"),
        ("read_at", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_user_settings", [
        ("user_id", "INTEGER"),
        ("presence_privacy", "TEXT DEFAULT 'everyone'"),
        ("read_receipts_enabled", "INTEGER DEFAULT 1"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_presence", [
        ("user_id", "INTEGER"),
        ("status", "TEXT DEFAULT 'offline'"),
        ("last_seen_at", "TEXT"),
        ("active_until", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_message_deletions", [
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_typing", [
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("is_typing", "INTEGER DEFAULT 1"),
        ("expires_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_reports", [
        ("conversation_id", "INTEGER"),
        ("message_id", "INTEGER"),
        ("reporter_user_id", "INTEGER"),
        ("reported_user_id", "INTEGER DEFAULT 0"),
        ("reason", "TEXT"),
        ("status", "TEXT DEFAULT 'open'"),
        ("created_at", "TEXT"),
        ("reviewed_at", "TEXT"),
        ("reviewed_by_admin_id", "INTEGER DEFAULT 0"),
    ], conn=conn)
    add(cur, "comm_v2_blocks", [
        ("blocker_user_id", "INTEGER"),
        ("blocked_user_id", "INTEGER"),
        ("reason", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_moderation_events", [
        ("conversation_id", "INTEGER"),
        ("message_id", "INTEGER"),
        ("actor_user_id", "INTEGER DEFAULT 0"),
        ("admin_user_id", "INTEGER DEFAULT 0"),
        ("target_user_id", "INTEGER DEFAULT 0"),
        ("event_type", "TEXT"),
        ("reason", "TEXT"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_communities", [
        ("public_id", "TEXT"),
        ("name", "TEXT"),
        ("slug", "TEXT"),
        ("description", "TEXT"),
        ("owner_user_id", "INTEGER"),
        ("privacy", "TEXT DEFAULT 'public'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_channels", [
        ("public_id", "TEXT"),
        ("community_id", "INTEGER"),
        ("conversation_id", "INTEGER DEFAULT 0"),
        ("name", "TEXT"),
        ("slug", "TEXT"),
        ("description", "TEXT"),
        ("channel_type", "TEXT DEFAULT 'text'"),
        ("visibility", "TEXT DEFAULT 'members'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)


def ensure_v2_schema(cur) -> tuple[str, ...]:
    return ensure_schema(cur)


def _user_summary(cur, user_id: int) -> dict:
    cur.execute("SELECT user_id, username, display_name, avatar_url FROM users WHERE user_id=? LIMIT 1", (int(user_id),))
    item = _row(cur.fetchone())
    return {
        "user_id": int(item.get("user_id") or user_id or 0),
        "display_name": item.get("display_name") or item.get("username") or f"Member {user_id}",
        "username": item.get("username") or "",
        "avatar_url": item.get("avatar_url") or "",
    }


def _participant_ids(cur, conversation_id: int) -> list[int]:
    cur.execute(
        "SELECT user_id FROM comm_v2_participants WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')=''",
        (int(conversation_id),),
    )
    return [int(row["user_id"]) for row in cur.fetchall()]


def _settings(cur, user_id: int) -> dict:
    cur.execute("SELECT * FROM comm_v2_user_settings WHERE user_id=? LIMIT 1", (int(user_id),))
    row = _row(cur.fetchone())
    if not row:
        return {"presence_privacy": "everyone", "read_receipts_enabled": 1}
    return {
        "presence_privacy": row.get("presence_privacy") or "everyone",
        "read_receipts_enabled": 1 if int(row.get("read_receipts_enabled") or 0) else 0,
    }


def _touch_presence(cur, user_id: int, status: str = "online") -> dict:
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    active_until = (now_dt + timedelta(seconds=90)).isoformat(timespec="seconds")
    normalized = "online" if status in {"online", "active", "active_now"} else "offline"
    cur.execute(
        """
        INSERT OR IGNORE INTO comm_v2_presence (user_id, status, last_seen_at, active_until, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(user_id), normalized, now, active_until, now),
    )
    cur.execute(
        "UPDATE comm_v2_presence SET status=?, last_seen_at=?, active_until=?, updated_at=? WHERE user_id=?",
        (normalized, now, active_until, now, int(user_id)),
    )
    return {"user_id": int(user_id), "status": normalized, "last_seen_at": now, "active_until": active_until}


def _presence_visible(cur, viewer_user_id: int, target_user_id: int) -> bool:
    if int(viewer_user_id) == int(target_user_id):
        return True
    privacy = (_settings(cur, int(target_user_id)).get("presence_privacy") or "everyone").lower()
    if privacy == "nobody":
        return False
    if privacy == "contacts":
        cur.execute(
            """
            SELECT 1
            FROM comm_v2_participants a
            JOIN comm_v2_participants b ON b.conversation_id=a.conversation_id
            WHERE a.user_id=? AND b.user_id=?
              AND a.membership_state='active' AND b.membership_state='active'
              AND COALESCE(a.left_at,'')='' AND COALESCE(b.left_at,'')=''
            LIMIT 1
            """,
            (int(viewer_user_id), int(target_user_id)),
        )
        return cur.fetchone() is not None
    return True


def _read_receipts_allowed(cur, user_id: int) -> bool:
    return bool(int(_settings(cur, user_id).get("read_receipts_enabled") or 0))


def _blocked_between(cur, user_id: int, other_ids: list[int]) -> bool:
    ids = [int(x) for x in other_ids if int(x or 0) != int(user_id)]
    if not ids:
        return False
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(
        f"""
        SELECT id FROM comm_v2_blocks
        WHERE status='active'
          AND ((blocker_user_id=? AND blocked_user_id IN ({placeholders}))
            OR (blocked_user_id=? AND blocker_user_id IN ({placeholders})))
        LIMIT 1
        """,
        (int(user_id), *ids, int(user_id), *ids),
    )
    return cur.fetchone() is not None


def _conversation_access(cur, user_id: int, conversation_ref: int | str, join_public: bool = False) -> tuple[dict, str]:
    ref = str(conversation_ref or "").strip()
    if ref.startswith("public-"):
        ref = ref[7:]
    if ref.isdigit():
        cur.execute("SELECT * FROM comm_v2_conversations WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(ref),))
    else:
        cur.execute("SELECT * FROM comm_v2_conversations WHERE public_id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (ref,))
    conversation = _row(cur.fetchone())
    if not conversation:
        return {}, "missing"
    conversation_id = int(conversation["id"])
    cur.execute(
        "SELECT * FROM comm_v2_participants WHERE conversation_id=? AND user_id=? AND membership_state='active' AND COALESCE(left_at,'')='' LIMIT 1",
        (conversation_id, int(user_id)),
    )
    participant = _row(cur.fetchone())
    is_public_room = conversation.get("conversation_type") == "room" and conversation.get("privacy") == "public"
    if not participant and is_public_room and join_public:
        _add_participant(cur, conversation_id, int(user_id), "member")
        participant = {"role": "member"}
    if not participant and not is_public_room:
        return conversation, "denied"
    if _blocked_between(cur, user_id, _participant_ids(cur, conversation_id)):
        return conversation, "blocked"
    return conversation, "ok"


def _add_participant(cur, conversation_id: int, user_id: int, role: str = "member") -> None:
    now = _now()
    cur.execute(
        """
        INSERT OR IGNORE INTO comm_v2_participants
        (conversation_id, user_id, role, membership_state, joined_at, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (int(conversation_id), int(user_id), role, now, now, now),
    )
    cur.execute(
        """
        UPDATE comm_v2_participants
        SET membership_state='active', left_at='', role=COALESCE(NULLIF(role,''), ?), updated_at=?
        WHERE conversation_id=? AND user_id=?
        """,
        (role, now, int(conversation_id), int(user_id)),
    )
    cur.execute(
        "UPDATE comm_v2_conversations SET member_count=(SELECT COUNT(*) FROM comm_v2_participants WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')=''), updated_at=? WHERE id=?",
        (int(conversation_id), now, int(conversation_id)),
    )


def _conversation_payload(cur, conversation: dict, viewer_user_id: int) -> dict:
    conversation_id = int(conversation.get("id") or 0)
    cur.execute(
        "SELECT unread_count, last_read_message_id, role FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1",
        (conversation_id, int(viewer_user_id)),
    )
    mine = _row(cur.fetchone())
    cur.execute(
        """
        SELECT p.user_id, COALESCE(u.display_name,u.username,'Pulse member') AS display_name, COALESCE(u.avatar_url,'') AS avatar_url
        FROM comm_v2_participants p
        LEFT JOIN users u ON u.user_id=p.user_id
        WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
        ORDER BY p.id ASC LIMIT 6
        """,
        (conversation_id,),
    )
    members = [dict(row) for row in cur.fetchall()]
    title = conversation.get("title") or ""
    if conversation.get("conversation_type") == "direct":
        others = [m for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)]
        if others:
            title = others[0].get("display_name") or title
    return {
        "id": conversation_id,
        "conversation_id": conversation_id,
        "public_id": conversation.get("public_id") or "",
        "conversation_type": conversation.get("conversation_type") or "direct",
        "title": title or "Untitled chat",
        "description": conversation.get("description") or "",
        "privacy": conversation.get("privacy") or "private",
        "visibility": conversation.get("visibility") or "members",
        "member_count": int(conversation.get("member_count") or len(members) or 0),
        "last_message_id": int(conversation.get("last_message_id") or 0),
        "last_message_at": conversation.get("last_message_at") or "",
        "last_activity_at": conversation.get("last_activity_at") or conversation.get("updated_at") or conversation.get("created_at") or "",
        "unread_count": int(mine.get("unread_count") or 0),
        "last_read_message_id": int(mine.get("last_read_message_id") or 0),
        "role": mine.get("role") or ("viewer" if conversation.get("privacy") == "public" else ""),
        "participants_preview": members,
    }


def _conversation_payloads(cur, conversations: list[dict], viewer_user_id: int) -> list[dict]:
    if not conversations:
        return []
    conversation_ids = [int(item.get("id") or 0) for item in conversations if int(item.get("id") or 0)]
    placeholders = ",".join(["?"] * len(conversation_ids))
    mine_by_conversation: dict[int, dict] = {}
    preview_by_conversation: dict[int, list[dict]] = {conversation_id: [] for conversation_id in conversation_ids}
    if conversation_ids:
        cur.execute(
            f"""
            SELECT conversation_id, unread_count, last_read_message_id, role
            FROM comm_v2_participants
            WHERE user_id=? AND conversation_id IN ({placeholders})
            """,
            (int(viewer_user_id), *conversation_ids),
        )
        mine_by_conversation = {int(row["conversation_id"]): dict(row) for row in cur.fetchall()}
        cur.execute(
            f"""
            SELECT p.conversation_id, p.user_id,
                   COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   COALESCE(u.avatar_url,'') AS avatar_url
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id IN ({placeholders})
              AND p.membership_state='active'
              AND COALESCE(p.left_at,'')=''
            ORDER BY p.conversation_id, p.id ASC
            """,
            tuple(conversation_ids),
        )
        for row in cur.fetchall():
            conversation_id = int(row["conversation_id"])
            if len(preview_by_conversation.setdefault(conversation_id, [])) < 6:
                preview_by_conversation[conversation_id].append({
                    "user_id": int(row["user_id"] or 0),
                    "display_name": row["display_name"] or "Pulse member",
                    "avatar_url": row["avatar_url"] or "",
                })
    out = []
    for conversation in conversations:
        conversation_id = int(conversation.get("id") or 0)
        mine = mine_by_conversation.get(conversation_id, {})
        members = preview_by_conversation.get(conversation_id, [])
        title = conversation.get("title") or ""
        if conversation.get("conversation_type") == "direct":
            others = [m for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)]
            if others:
                title = others[0].get("display_name") or title
        out.append({
            "id": conversation_id,
            "conversation_id": conversation_id,
            "public_id": conversation.get("public_id") or "",
            "conversation_type": conversation.get("conversation_type") or "direct",
            "title": title or "Untitled chat",
            "description": conversation.get("description") or "",
            "privacy": conversation.get("privacy") or "private",
            "visibility": conversation.get("visibility") or "members",
            "member_count": int(conversation.get("member_count") or len(members) or 0),
            "last_message_id": int(conversation.get("last_message_id") or 0),
            "last_message_at": conversation.get("last_message_at") or "",
            "last_activity_at": conversation.get("last_activity_at") or conversation.get("updated_at") or conversation.get("created_at") or "",
            "unread_count": int(mine.get("unread_count") or 0),
            "last_read_message_id": int(mine.get("last_read_message_id") or 0),
            "role": mine.get("role") or ("viewer" if conversation.get("privacy") == "public" else ""),
            "participants_preview": members,
        })
    return out


def create_conversation(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("create_conversation")
    if disabled:
        return disabled
    payload = payload or {}
    conversation_type = _clean(payload.get("conversation_type") or payload.get("type") or "direct", 40).lower()
    if conversation_type not in ALLOWED_CONVERSATION_TYPES:
        return _err("Choose a supported conversation type.", 400, "invalid_type")
    conn, cur = _open_db()
    try:
        now = _now()
        if conversation_type == "direct":
            target_id = int(payload.get("target_user_id") or payload.get("user_id") or 0)
            if not target_id or target_id == int(user_id):
                return _err("Choose another member to message.", 400, "invalid_recipient")
            cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (target_id,))
            if not cur.fetchone():
                return _err("That member was not found.", 404, "missing_user")
            if _blocked_between(cur, user_id, [target_id]):
                return _err("This direct message is unavailable.", 403, "blocked")
            direct_key = ":".join(str(x) for x in sorted([int(user_id), target_id]))
            cur.execute("SELECT * FROM comm_v2_conversations WHERE direct_key=? AND COALESCE(deleted_at,'')='' LIMIT 1", (direct_key,))
            existing = _row(cur.fetchone())
            if existing:
                conn.commit()
                return _ok({"conversation": _conversation_payload(cur, existing, user_id), "conversation_id": int(existing["id"])}, "Direct message ready.")
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, owner_user_id, created_by_user_id, direct_key, privacy, visibility, status, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'direct', '', ?, ?, ?, 'private', 'members', 'active', 0, ?, ?, ?)
                """,
                (_public_id("dm"), int(user_id), int(user_id), direct_key, now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            _add_participant(cur, conversation_id, int(user_id), "member")
            _add_participant(cur, conversation_id, target_id, "member")
        elif conversation_type == "group":
            title = _clean(payload.get("title") or "Group chat", 120)
            participant_ids = [int(x) for x in payload.get("participant_ids") or payload.get("member_ids") or [] if int(x or 0)]
            participant_ids = sorted({int(user_id), *participant_ids})
            if len(participant_ids) < 2:
                return _err("Add at least one other member to create a group.", 400, "too_few_members")
            if _blocked_between(cur, user_id, participant_ids):
                return _err("One or more members cannot be added to this group.", 403, "blocked")
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, owner_user_id, created_by_user_id, privacy, visibility, status, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'group', ?, ?, ?, 'private', 'members', 'active', 0, ?, ?, ?)
                """,
                (_public_id("grp"), title, int(user_id), int(user_id), now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            for member_id in participant_ids:
                _add_participant(cur, conversation_id, member_id, "owner" if member_id == int(user_id) else "member")
        elif conversation_type == "room":
            title = _clean(payload.get("title") or payload.get("name") or "Pulse room", 120)
            privacy = _clean(payload.get("privacy") or "public", 20).lower()
            privacy = "private" if privacy == "private" else "public"
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, description, owner_user_id, created_by_user_id, privacy, visibility, status, is_discoverable, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'room', ?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)
                """,
                (_public_id("room"), title, _clean(payload.get("description") or "", 500), int(user_id), int(user_id), privacy, "public" if privacy == "public" else "members", 1 if privacy == "public" else 0, now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            _add_participant(cur, conversation_id, int(user_id), "owner")
        else:
            community_id = int(payload.get("community_id") or 0)
            title = _clean(payload.get("title") or payload.get("name") or "community-channel", 120)
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, owner_user_id, created_by_user_id, community_id, privacy, visibility, status, is_discoverable, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'community_channel', ?, ?, ?, ?, 'private', 'members', 'active', 0, 0, ?, ?, ?)
                """,
                (_public_id("chan"), title, int(user_id), int(user_id), community_id, now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            _add_participant(cur, conversation_id, int(user_id), "owner")
        cur.execute("SELECT * FROM comm_v2_conversations WHERE id=?", (conversation_id,))
        conversation = _conversation_payload(cur, _row(cur.fetchone()), user_id)
        conn.commit()
        return _ok({"conversation": conversation, "conversation_id": conversation_id}, "Conversation ready.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_conversations(user_id: int, filters: dict | None = None) -> dict:
    disabled = _disabled("list_conversations")
    if disabled:
        return disabled
    filters = filters or {}
    kind = _clean(filters.get("type") or "all", 40).lower()
    conn, cur = _open_db()
    try:
        params: list[Any] = [int(user_id)]
        type_clause = ""
        if kind in {"direct", "group", "room", "community_channel"}:
            type_clause = "AND c.conversation_type=?"
            params.append(kind)
        cur.execute(
            f"""
            SELECT c.*
            FROM comm_v2_conversations c
            LEFT JOIN comm_v2_participants p ON p.conversation_id=c.id AND p.user_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            WHERE COALESCE(c.deleted_at,'')='' AND c.status='active'
              AND (p.id IS NOT NULL OR (c.conversation_type='room' AND c.privacy='public' AND c.is_discoverable=1))
              {type_clause}
            ORDER BY COALESCE(c.last_activity_at,c.updated_at,c.created_at) DESC, c.id DESC
            LIMIT 120
            """,
            tuple(params),
        )
        items = _conversation_payloads(cur, [_row(row) for row in cur.fetchall()], user_id)
        return _ok({"items": items, "conversations": items})
    finally:
        conn.close()


def send_message(user_id: int, conversation_ref: int | str, payload: dict | None = None) -> dict:
    disabled = _disabled("send_message")
    if disabled:
        return disabled
    payload = payload or {}
    body = _clean(payload.get("body") or payload.get("message") or payload.get("content") or "", 4000)
    message_type = _clean(payload.get("message_type") or payload.get("type") or "text", 40).lower()
    if message_type not in ALLOWED_MESSAGE_TYPES:
        message_type = "text"
    media_ids = [int(x) for x in payload.get("media_ids") or payload.get("attachment_media_ids") or [] if int(x or 0)]
    max_attachments = int(os.getenv("COMM_V2_MAX_ATTACHMENTS", "8") or 8)
    if len(media_ids) > max_attachments:
        return _err(f"Send up to {max_attachments} attachments at once.", 400, "too_many_attachments")
    if not body and not media_ids:
        return _err("Write a message or attach a file before sending.", 400, "empty_message")
    conn, cur = _open_db()
    step = "open_db"
    message_id = 0
    try:
        step = "conversation_access"
        conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=True)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access == "denied":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        if access == "blocked":
            return _err("Messaging is unavailable for this conversation.", 403, "blocked")
        conversation_id = int(conversation["id"])
        step = "validate_attachments"
        valid_media_ids, media_error = _validate_message_media_ids(cur, user_id, conversation_id, media_ids)
        if media_error:
            return media_error
        media_ids = valid_media_ids
        client_id = _clean(payload.get("client_message_id") or "", 120)
        if client_id:
            cur.execute(
                "SELECT * FROM comm_v2_messages WHERE conversation_id=? AND sender_user_id=? AND client_message_id=? AND COALESCE(deleted_at,'')='' LIMIT 1",
                (conversation_id, int(user_id), client_id),
            )
            existing = _row(cur.fetchone())
            if existing:
                return _ok({"message": _message_payload(cur, existing, user_id), "message_id": int(existing["id"]), "idempotent": True})
        reply_to = int(payload.get("reply_to_message_id") or payload.get("reply_to_id") or 0)
        thread_root = int(payload.get("thread_root_message_id") or 0)
        if reply_to and not thread_root:
            thread_root = reply_to
        now = _now()
        step = "insert_message"
        logging.info(
            "COMM_V2_SEND_STEP step=%s user_id=%s conversation_id=%s message_type=%s body_len=%s media_ids=%s client_message_id=%s",
            step,
            int(user_id),
            conversation_id,
            message_type,
            len(body or ""),
            media_ids,
            client_id,
        )
        cur.execute(
            """
            INSERT INTO comm_v2_messages
            (public_id, conversation_id, sender_user_id, message_type, body, reply_to_message_id, thread_root_message_id, client_message_id, delivery_status, moderation_status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent', 'approved', ?, ?, ?)
            """,
            (_public_id("msg"), conversation_id, int(user_id), message_type, body, reply_to, thread_root, client_id, json.dumps(payload.get("metadata") or {}, default=str)[:4000], now, now),
        )
        message_id = int(cur.lastrowid)
        step = "attach_media"
        attachments = _attach_media(cur, user_id, conversation_id, message_id, media_ids)
        step = "update_conversation"
        cur.execute(
            "UPDATE comm_v2_conversations SET last_message_id=?, last_message_at=?, last_activity_at=?, updated_at=? WHERE id=?",
            (message_id, now, now, now, conversation_id),
        )
        step = "update_participants"
        cur.execute(
            """
            UPDATE comm_v2_participants
            SET unread_count=CASE WHEN user_id=? THEN 0 ELSE COALESCE(unread_count,0)+1 END,
                last_seen_at=CASE WHEN user_id=? THEN ? ELSE last_seen_at END,
                updated_at=?
            WHERE conversation_id=? AND membership_state='active'
            """,
            (int(user_id), int(user_id), now, now, conversation_id),
        )
        step = "mark_read"
        mark_read(user_id, conversation_id, existing_conn=(conn, cur), commit=False)
        step = "message_payload"
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=?", (message_id,))
        message = _message_payload(cur, _row(cur.fetchone()), user_id)
        if attachments:
            message["attachments"] = attachments
        step = "commit"
        conn.commit()
        side_effects = _dispatch_message_side_effects(user_id, conversation_id, message)
        logging.info(
            "COMM_V2_SEND_COMPLETE user_id=%s conversation_id=%s message_id=%s attachment_count=%s side_effects=%s",
            int(user_id),
            conversation_id,
            message_id,
            len(attachments or []),
            side_effects,
        )
        return _ok({"message": message, "message_id": message_id, "conversation_id": conversation_id}, "Message sent.")
    except Exception as exc:
        conn.rollback()
        logging.exception(
            "COMM_V2_SEND_FAILED step=%s user_id=%s conversation_ref=%s message_id=%s message_type=%s body_len=%s media_ids=%s payload_keys=%s error_type=%s",
            step,
            int(user_id or 0),
            conversation_ref,
            message_id,
            message_type,
            len(body or ""),
            media_ids,
            sorted((payload or {}).keys()),
            type(exc).__name__,
        )
        raise
    finally:
        conn.close()


def _validate_message_media_ids(cur, user_id: int, conversation_id: int, media_ids: list[int]) -> tuple[list[int], dict | None]:
    ids = [int(x) for x in (media_ids or []) if int(x or 0)]
    if not ids:
        return [], None
    unique_ids = []
    seen = set()
    for media_id in ids:
        if media_id not in seen:
            unique_ids.append(media_id)
            seen.add(media_id)
    placeholders = ",".join(["?"] * len(unique_ids))
    cur.execute(
        f"""
        SELECT *
        FROM chat_media_uploads
        WHERE id IN ({placeholders})
          AND uploader_user_id=?
          AND COALESCE(deleted_at,'')=''
          AND COALESCE(moderation_status,'approved')!='blocked'
        """,
        (*unique_ids, int(user_id)),
    )
    rows = {_row(row).get("id"): _row(row) for row in cur.fetchall()}
    missing = [media_id for media_id in unique_ids if media_id not in rows]
    if missing:
        logging.warning(
            "COMM_V2_ATTACHMENT_INVALID user_id=%s conversation_id=%s missing_media_ids=%s requested_media_ids=%s",
            int(user_id),
            int(conversation_id),
            missing,
            unique_ids,
        )
        return [], _err("Attachment invalid or expired. Please upload it again.", 400, "attachment_invalid")
    invalid = []
    for media_id, media in rows.items():
        availability_error = str(media.get("availability_error") or media.get("error_message") or "")
        verification = str(media.get("verification_status") or "verified").lower()
        processing = str(media.get("processing_status") or "ready").lower()
        has_deliverable_url = any(media.get(key) for key in ("media_url", "public_url", "cdn_url", "playback_url", "storage_key", "object_key"))
        if verification in {"failed", "blocked"} or processing in {"failed", "blocked"} or (availability_error and not has_deliverable_url):
            invalid.append({"media_id": int(media_id), "verification": verification, "processing": processing, "availability_error": availability_error[:160]})
    if invalid:
        logging.warning(
            "COMM_V2_ATTACHMENT_VERIFY_FAILED user_id=%s conversation_id=%s invalid=%s",
            int(user_id),
            int(conversation_id),
            invalid,
        )
        return [], _err("Attachment could not be verified. Please upload it again.", 400, "attachment_verification_failed")
    return unique_ids, None


def _dispatch_message_side_effects(user_id: int, conversation_id: int, message: dict) -> dict:
    results = {"notifications": "skipped", "realtime": "skipped"}
    try:
        recipient_ids = [uid for uid in _participant_ids_for_side_effects(conversation_id) if int(uid) != int(user_id)]
        if recipient_ids:
            from services import notification_service

            preview = message.get("body") or ("Sent a voice note." if message.get("message_type") == "voice" else "Sent an attachment.")
            for recipient_id in recipient_ids[:25]:
                notification_service.create_pulse_notification(
                    int(recipient_id),
                    note_type="voice_message" if message.get("message_type") == "voice" else "message",
                    title="New Pulse message",
                    body=preview[:220],
                    actor_user_id=int(user_id),
                    entity_type="comm_v2_message",
                    entity_id=int(message.get("id") or 0),
                    deep_link=f"/pulse/messages-v2?conversation={int(conversation_id)}",
                    metadata={"conversation_id": int(conversation_id), "message_id": int(message.get("id") or 0)},
                )
            results["notifications"] = f"created:{len(recipient_ids[:25])}"
    except Exception as exc:
        logging.exception("COMM_V2_NOTIFICATION_DISPATCH_FAILED conversation_id=%s message_id=%s error_type=%s", conversation_id, message.get("id"), type(exc).__name__)
        results["notifications"] = "failed"
    try:
        from services import realtime_engine

        realtime_engine.publish_event(
            f"comm_v2:conversation:{int(conversation_id)}",
            "message_created",
            {"conversation_id": int(conversation_id), "message": message},
        )
        results["realtime"] = "published"
    except Exception as exc:
        logging.exception("COMM_V2_REALTIME_BROADCAST_FAILED conversation_id=%s message_id=%s error_type=%s", conversation_id, message.get("id"), type(exc).__name__)
        results["realtime"] = "failed"
    return results


def _participant_ids_for_side_effects(conversation_id: int) -> list[int]:
    conn, cur = _open_db()
    try:
        return _participant_ids(cur, int(conversation_id))
    finally:
        conn.close()


def _attach_media(cur, user_id: int, conversation_id: int, message_id: int, media_ids: list[int]) -> list[dict]:
    out = []
    max_attachments = int(os.getenv("COMM_V2_MAX_ATTACHMENTS", "8") or 8)
    for media_id in media_ids[:max_attachments]:
        logging.info(
            "COMM_V2_ATTACHMENT_STEP step=load_media user_id=%s conversation_id=%s message_id=%s media_id=%s",
            int(user_id),
            int(conversation_id),
            int(message_id),
            int(media_id),
        )
        cur.execute(
            "SELECT * FROM chat_media_uploads WHERE id=? AND uploader_user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1",
            (int(media_id), int(user_id)),
        )
        media = _row(cur.fetchone())
        if not media:
            logging.warning("COMM_V2_ATTACHMENT_MISSING user_id=%s conversation_id=%s message_id=%s media_id=%s", int(user_id), int(conversation_id), int(message_id), int(media_id))
            continue
        logging.info(
            "COMM_V2_ATTACHMENT_STEP step=prepare_media user_id=%s conversation_id=%s message_id=%s media_id=%s media_type=%s mime_type=%s processing=%s verification=%s",
            int(user_id),
            int(conversation_id),
            int(message_id),
            int(media_id),
            media.get("media_type") or "",
            media.get("mime_type") or "",
            media.get("processing_status") or "",
            media.get("verification_status") or "",
        )
        media = _prepare_attachment_media(cur, media, media_id)
        now = _now()
        logging.info(
            "COMM_V2_ATTACHMENT_STEP step=insert_attachment user_id=%s conversation_id=%s message_id=%s media_id=%s url=%s cdn=%s playback=%s",
            int(user_id),
            int(conversation_id),
            int(message_id),
            int(media_id),
            bool(media.get("url") or media.get("media_url") or media.get("public_url")),
            bool(media.get("cdn_url") or media.get("valid_url")),
            bool(media.get("playback_url")),
        )
        cur.execute(
            """
            INSERT INTO comm_v2_attachments
            (attachment_public_id, message_id, conversation_id, media_upload_id, uploader_user_id, media_type, storage_provider, storage_key, url, cdn_url, playback_url, thumbnail_url, mime_type, file_size, file_size_bytes, duration_seconds, waveform_json, voice_note, width, height, mux_asset_id, mux_playback_id, mux_status, scan_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _public_id("att"),
                int(message_id),
                int(conversation_id),
                int(media_id),
                int(user_id),
                media.get("media_type") or "file",
                media.get("storage_provider") or "",
                media.get("storage_key") or media.get("object_key") or "",
                media.get("url") or media.get("media_url") or media.get("public_url") or media.get("cdn_url") or "",
                media.get("cdn_url") or media.get("valid_url") or media.get("media_url") or "",
                media.get("playback_url") or "",
                media.get("thumbnail_url") or media.get("poster_url") or "",
                media.get("mime_type") or "",
                int(media.get("file_size") or media.get("file_size_bytes") or 0),
                int(media.get("file_size_bytes") or 0),
                float(media.get("duration_seconds") or media.get("duration") or 0),
                media.get("waveform_json") or "",
                1 if int(media.get("voice_note") or 0) else 0,
                int(media.get("width") or 0),
                int(media.get("height") or 0),
                media.get("mux_asset_id") or "",
                media.get("mux_playback_id") or "",
                media.get("mux_status") or "",
                media.get("moderation_status") or "approved",
                now,
            ),
        )
        logging.info("COMM_V2_ATTACHMENT_STEP step=link_upload user_id=%s conversation_id=%s message_id=%s media_id=%s", int(user_id), int(conversation_id), int(message_id), int(media_id))
        cur.execute(
            "UPDATE chat_media_uploads SET message_id=?, context_type='pulse_comm_v2', context_id=? WHERE id=?",
            (int(message_id), str(conversation_id), int(media_id)),
        )
        attachment_id = int(cur.lastrowid)
        out.append(_attachment_payload(_row({**media, "id": attachment_id, "media_upload_id": media_id})))
    return out


def _prepare_attachment_media(cur, media: dict, media_id: int) -> dict:
    try:
        from services import media_service
    except Exception:
        media_service = None
    resolved = media_service.resolve_media(media, check_remote=False) if media_service else {}
    out = {
        **media,
        "url": resolved.get("media_url") or media.get("media_url") or media.get("public_url") or media.get("cdn_url") or "",
        "cdn_url": resolved.get("valid_url") or media.get("cdn_url") or media.get("public_url") or media.get("media_url") or "",
        "playback_url": resolved.get("playback_url") or media.get("playback_url") or "",
        "thumbnail_url": resolved.get("thumbnail_url") or media.get("thumbnail_url") or media.get("poster_url") or "",
        "mux_asset_id": resolved.get("mux_asset_id") or media.get("mux_asset_id") or "",
        "mux_playback_id": resolved.get("mux_playback_id") or media.get("mux_playback_id") or "",
        "mux_status": resolved.get("mux_status") or media.get("mux_status") or "",
    }
    if (out.get("media_type") or "").lower() == "video" and media_service and not out.get("mux_playback_id"):
        source = out.get("cdn_url") or out.get("url")
        mux = media_service.create_mux_asset_from_url(source, trace_id=_trace(), media_id=int(media_id))
        if mux.get("ok"):
            playback = media_service.mux_playback_urls(mux.get("playback_id") or "")
            out.update({
                "mux_asset_id": mux.get("asset_id") or "",
                "mux_playback_id": mux.get("playback_id") or "",
                "mux_status": mux.get("status") or "created",
                "playback_url": playback.get("hls_url") or out.get("playback_url") or "",
                "thumbnail_url": playback.get("thumbnail_url") or out.get("thumbnail_url") or "",
            })
            cur.execute(
                "UPDATE chat_media_uploads SET mux_asset_id=?, mux_playback_id=?, mux_status=?, playback_url=?, thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), ?) WHERE id=?",
                (out["mux_asset_id"], out["mux_playback_id"], out["mux_status"], out["playback_url"], out["thumbnail_url"], int(media_id)),
            )
        else:
            logging.info("COMM_V2_MUX_ASSET_SKIPPED media_id=%s status=%s", int(media_id), mux.get("status") or "unknown")
    return out


def _attachment_payload(row: dict) -> dict:
    mux_playback_id = row.get("mux_playback_id") or ""
    playback_url = row.get("playback_url") or ""
    if mux_playback_id and not playback_url:
        try:
            from services import media_service

            playback_url = media_service.mux_playback_urls(mux_playback_id).get("hls_url") or ""
        except Exception:
            playback_url = ""
    cdn_url = row.get("cdn_url") or row.get("valid_url") or row.get("media_url") or row.get("public_url") or row.get("url") or ""
    url = playback_url if (row.get("media_type") or "").lower() == "video" and playback_url else (row.get("url") or cdn_url)
    try:
        waveform = json.loads(row.get("waveform_json") or "[]")
    except Exception:
        waveform = []
    return {
        "id": int(row.get("id") or row.get("media_upload_id") or 0),
        "attachment_id": int(row.get("id") or 0),
        "attachment_public_id": row.get("attachment_public_id") or "",
        "media_upload_id": int(row.get("media_upload_id") or row.get("id") or 0),
        "media_type": row.get("media_type") or "file",
        "url": url,
        "cdn_url": cdn_url,
        "playback_url": playback_url,
        "thumbnail_url": row.get("thumbnail_url") or row.get("poster_url") or "",
        "mime_type": row.get("mime_type") or "",
        "file_size": int(row.get("file_size") or row.get("file_size_bytes") or 0),
        "file_size_bytes": int(row.get("file_size_bytes") or 0),
        "duration_seconds": float(row.get("duration_seconds") or row.get("duration") or 0),
        "waveform": waveform if isinstance(waveform, list) else [],
        "voice_note": bool(int(row.get("voice_note") or 0)),
        "storage_provider": row.get("storage_provider") or "",
        "storage_key": row.get("storage_key") or row.get("object_key") or "",
        "mux_asset_id": row.get("mux_asset_id") or "",
        "mux_playback_id": mux_playback_id,
        "mux_status": row.get("mux_status") or "",
    }


def _voice_upload_metadata(payload: dict | None) -> dict:
    payload = payload or {}
    kind = _clean(payload.get("attachment_kind") or payload.get("kind") or "", 40).lower()
    is_voice = kind in {"voice", "voice_note", "audio_note"}
    try:
        duration = max(0.0, float(payload.get("duration_seconds") or payload.get("duration") or 0))
    except Exception:
        duration = 0.0
    waveform_raw = payload.get("waveform_json") or payload.get("waveform") or "[]"
    waveform = []
    try:
        candidate = json.loads(waveform_raw) if isinstance(waveform_raw, str) else waveform_raw
        if isinstance(candidate, list):
            waveform = [max(0, min(100, int(float(value)))) for value in candidate[:80]]
    except Exception:
        waveform = []
    return {"is_voice": is_voice, "duration_seconds": duration, "waveform": waveform}


def _validate_voice_upload(file_storage, metadata: dict) -> dict:
    if not metadata.get("is_voice"):
        return {"ok": True}
    mime = (getattr(file_storage, "mimetype", "") or "").lower()
    name = (getattr(file_storage, "filename", "") or "").lower()
    allowed_mimes = {
        "audio/webm",
        "audio/ogg",
        "application/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/aac",
        "audio/mp4a-latm",
        "audio/wav",
        "audio/x-wav",
        "audio/x-m4a",
        "audio/m4a",
        "application/octet-stream",
        "video/webm",
    }
    allowed_ext = (".webm", ".ogg", ".oga", ".m4a", ".mp3", ".aac", ".wav")
    if mime and mime not in allowed_mimes and not mime.startswith("audio/"):
        logging.warning(
            "COMM_V2_VOICE_MIME_REJECTED filename=%s mime_type=%s duration_seconds=%s",
            name,
            mime,
            metadata.get("duration_seconds") or 0,
        )
        return _err("That recording format is not supported. Try recording again.", 400, "unsupported_voice_mime")
    if name and not name.endswith(allowed_ext):
        logging.warning(
            "COMM_V2_VOICE_EXTENSION_REJECTED filename=%s mime_type=%s duration_seconds=%s",
            name,
            mime,
            metadata.get("duration_seconds") or 0,
        )
        return _err("Voice notes must be audio recordings.", 400, "unsupported_voice_extension")
    duration = float(metadata.get("duration_seconds") or 0)
    max_duration = int(os.getenv("COMM_V2_VOICE_MAX_SECONDS", "300") or 300)
    if duration <= 0:
        return _err("Record a voice note before sending.", 400, "missing_voice_duration")
    if duration > max_duration:
        return _err(f"Voice notes can be up to {max_duration // 60} minutes.", 400, "voice_duration_exceeded")
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = int(file_storage.stream.tell() or 0)
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    max_bytes = int(float(os.getenv("COMM_V2_VOICE_MAX_MB", os.getenv("MEDIA_UPLOAD_MAX_AUDIO_MB", "15"))) * 1024 * 1024)
    if size and size > max_bytes:
        return _err("Voice note is too large. Record a shorter note and try again.", 400, "voice_size_exceeded")
    return {"ok": True}


def _validate_attachment_upload(file_storage, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    name = (getattr(file_storage, "filename", "") or "").lower()
    mime = (getattr(file_storage, "mimetype", "") or "").lower()
    blocked_ext = (".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".js", ".jar", ".msi", ".ps1", ".sh")
    if name.endswith(blocked_ext):
        return _err("That file type is blocked for safety.", 400, "blocked_attachment_type")
    kind = _clean(metadata.get("attachment_kind") or metadata.get("kind") or "", 40).lower()
    if not kind:
        kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file"
    limits = {
        "image": int(float(os.getenv("COMM_V2_IMAGE_MAX_MB", "25")) * 1024 * 1024),
        "video": int(float(os.getenv("COMM_V2_VIDEO_MAX_MB", "250")) * 1024 * 1024),
        "audio": int(float(os.getenv("COMM_V2_AUDIO_MAX_MB", "25")) * 1024 * 1024),
        "voice_note": int(float(os.getenv("COMM_V2_VOICE_MAX_MB", "15")) * 1024 * 1024),
        "file": int(float(os.getenv("COMM_V2_FILE_MAX_MB", "50")) * 1024 * 1024),
    }
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = int(file_storage.stream.tell() or 0)
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    limit = limits.get(kind, limits["file"])
    if size and size > limit:
        return _err(f"Attachment is too large. Limit: {max(1, round(limit / 1024 / 1024))} MB.", 400, "attachment_size_exceeded")
    return {"ok": True, "attachment_kind": kind, "mime_type": mime, "virus_scan": "pending_hook", "moderation_scan": "pending_hook"}


def _message_payload(cur, message: dict, viewer_user_id: int) -> dict:
    message_id = int(message.get("id") or 0)
    cur.execute("SELECT * FROM comm_v2_attachments WHERE message_id=? ORDER BY id ASC", (message_id,))
    attachments = [_attachment_payload(_row(row)) for row in cur.fetchall()]
    cur.execute("SELECT reaction_type, COUNT(*) AS total FROM comm_v2_message_reactions WHERE message_id=? GROUP BY reaction_type", (message_id,))
    reactions = [{"reaction_type": row["reaction_type"], "count": int(row["total"] or 0)} for row in cur.fetchall()]
    cur.execute("SELECT reaction_type FROM comm_v2_message_reactions WHERE message_id=? AND user_id=? LIMIT 1", (message_id, int(viewer_user_id)))
    mine = _row(cur.fetchone())
    receipt_state = message.get("delivery_status") or "sent"
    if int(message.get("sender_user_id") or 0) == int(viewer_user_id):
        cur.execute("SELECT COUNT(*) AS total FROM comm_v2_read_receipts WHERE message_id=? AND COALESCE(seen_at,'')!=''", (message_id,))
        if int(_row(cur.fetchone()).get("total") or 0):
            receipt_state = "seen"
        else:
            cur.execute("SELECT COUNT(*) AS total FROM comm_v2_read_receipts WHERE message_id=? AND COALESCE(delivered_at,'')!=''", (message_id,))
            if int(_row(cur.fetchone()).get("total") or 0):
                receipt_state = "delivered"
    sender = _user_summary(cur, int(message.get("sender_user_id") or 0))
    reply_preview = None
    if int(message.get("reply_to_message_id") or 0):
        cur.execute("SELECT id, sender_user_id, body, message_type FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message.get("reply_to_message_id") or 0),))
        reply = _row(cur.fetchone())
        if reply:
            reply_preview = {"id": int(reply.get("id") or 0), "sender": _user_summary(cur, int(reply.get("sender_user_id") or 0)), "body": reply.get("body") or "", "message_type": reply.get("message_type") or "text"}
    return {
        "id": message_id,
        "public_id": message.get("public_id") or "",
        "conversation_id": int(message.get("conversation_id") or 0),
        "sender_user_id": int(message.get("sender_user_id") or 0),
        "sender": sender,
        "is_mine": int(message.get("sender_user_id") or 0) == int(viewer_user_id),
        "message_type": message.get("message_type") or "text",
        "body": message.get("body") or "",
        "reply_to_message_id": int(message.get("reply_to_message_id") or 0),
        "thread_root_message_id": int(message.get("thread_root_message_id") or 0),
        "delivery_status": receipt_state,
        "moderation_status": message.get("moderation_status") or "approved",
        "reply_preview": reply_preview,
        "attachments": attachments,
        "reactions": reactions,
        "my_reaction": mine.get("reaction_type") or "",
        "created_at": message.get("created_at") or "",
        "updated_at": message.get("updated_at") or "",
        "edited_at": message.get("edited_at") or "",
        "is_edited": bool(message.get("edited_at")),
    }


def _message_payloads(cur, message_rows: list[dict], viewer_user_id: int) -> list[dict]:
    if not message_rows:
        return []
    message_ids = [int(item.get("id") or 0) for item in message_rows if int(item.get("id") or 0)]
    sender_ids = sorted({int(item.get("sender_user_id") or 0) for item in message_rows if int(item.get("sender_user_id") or 0)})
    attachment_map: dict[int, list[dict]] = {message_id: [] for message_id in message_ids}
    reaction_map: dict[int, list[dict]] = {message_id: [] for message_id in message_ids}
    mine_map: dict[int, str] = {}
    receipt_map: dict[int, str] = {}
    reply_map: dict[int, dict] = {}
    sender_map: dict[int, dict] = {}
    if message_ids:
        placeholders = ",".join(["?"] * len(message_ids))
        cur.execute(f"SELECT * FROM comm_v2_attachments WHERE message_id IN ({placeholders}) ORDER BY message_id, id ASC", tuple(message_ids))
        for row in cur.fetchall():
            item = _row(row)
            attachment_map.setdefault(int(item.get("message_id") or 0), []).append(_attachment_payload(item))
        cur.execute(
            f"""
            SELECT message_id, reaction_type, COUNT(*) AS total
            FROM comm_v2_message_reactions
            WHERE message_id IN ({placeholders})
            GROUP BY message_id, reaction_type
            """,
            tuple(message_ids),
        )
        for row in cur.fetchall():
            reaction_map.setdefault(int(row["message_id"]), []).append({"reaction_type": row["reaction_type"], "count": int(row["total"] or 0)})
        cur.execute(
            f"SELECT message_id, reaction_type FROM comm_v2_message_reactions WHERE user_id=? AND message_id IN ({placeholders})",
            (int(viewer_user_id), *message_ids),
        )
        mine_map = {int(row["message_id"]): row["reaction_type"] or "" for row in cur.fetchall()}
        cur.execute(
            f"""
            SELECT message_id,
                   MAX(CASE WHEN COALESCE(seen_at,'')!='' THEN 1 ELSE 0 END) AS seen,
                   MAX(CASE WHEN COALESCE(delivered_at,'')!='' THEN 1 ELSE 0 END) AS delivered
            FROM comm_v2_read_receipts
            WHERE message_id IN ({placeholders})
            GROUP BY message_id
            """,
            tuple(message_ids),
        )
        for row in cur.fetchall():
            receipt_map[int(row["message_id"])] = "seen" if int(row["seen"] or 0) else "delivered" if int(row["delivered"] or 0) else "sent"
        reply_ids = sorted({int(item.get("reply_to_message_id") or 0) for item in message_rows if int(item.get("reply_to_message_id") or 0)})
        if reply_ids:
            reply_placeholders = ",".join(["?"] * len(reply_ids))
            cur.execute(f"SELECT id, sender_user_id, body, message_type FROM comm_v2_messages WHERE id IN ({reply_placeholders})", tuple(reply_ids))
            for row in cur.fetchall():
                reply = _row(row)
                reply_map[int(reply.get("id") or 0)] = {"id": int(reply.get("id") or 0), "sender_user_id": int(reply.get("sender_user_id") or 0), "body": reply.get("body") or "", "message_type": reply.get("message_type") or "text"}
    if sender_ids:
        placeholders = ",".join(["?"] * len(sender_ids))
        cur.execute(
            f"SELECT user_id, username, display_name, avatar_url FROM users WHERE user_id IN ({placeholders})",
            tuple(sender_ids),
        )
        sender_map = {
            int(row["user_id"]): {
                "user_id": int(row["user_id"] or 0),
                "display_name": row["display_name"] or row["username"] or f"Member {row['user_id']}",
                "username": row["username"] or "",
                "avatar_url": row["avatar_url"] or "",
            }
            for row in cur.fetchall()
        }
    out = []
    for message in message_rows:
        message_id = int(message.get("id") or 0)
        sender_user_id = int(message.get("sender_user_id") or 0)
        reply_preview = reply_map.get(int(message.get("reply_to_message_id") or 0))
        if reply_preview:
            reply_preview = {**reply_preview, "sender": sender_map.get(int(reply_preview.get("sender_user_id") or 0), {"display_name": f"Member {reply_preview.get('sender_user_id')}"})}
        out.append({
            "id": message_id,
            "public_id": message.get("public_id") or "",
            "conversation_id": int(message.get("conversation_id") or 0),
            "sender_user_id": sender_user_id,
            "sender": sender_map.get(sender_user_id) or {
                "user_id": sender_user_id,
                "display_name": f"Member {sender_user_id}",
                "username": "",
                "avatar_url": "",
            },
            "is_mine": sender_user_id == int(viewer_user_id),
            "message_type": message.get("message_type") or "text",
            "body": message.get("body") or "",
            "reply_to_message_id": int(message.get("reply_to_message_id") or 0),
            "thread_root_message_id": int(message.get("thread_root_message_id") or 0),
            "delivery_status": receipt_map.get(message_id, message.get("delivery_status") or "sent") if sender_user_id == int(viewer_user_id) else message.get("delivery_status") or "sent",
            "moderation_status": message.get("moderation_status") or "approved",
            "reply_preview": reply_preview,
            "attachments": attachment_map.get(message_id, []),
            "reactions": reaction_map.get(message_id, []),
            "my_reaction": mine_map.get(message_id, ""),
            "created_at": message.get("created_at") or "",
            "updated_at": message.get("updated_at") or "",
            "edited_at": message.get("edited_at") or "",
            "is_edited": bool(message.get("edited_at")),
        })
    return out


def list_messages(user_id: int, conversation_ref: int | str, filters: dict | None = None) -> dict:
    disabled = _disabled("list_messages")
    if disabled:
        return disabled
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 40), 80))
    fetch_limit = limit + 1
    before_id = int(filters.get("before_id") or 0)
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access == "denied":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        if access == "blocked":
            return _err("Messaging is unavailable for this conversation.", 403, "blocked")
        conversation_id = int(conversation["id"])
        if before_id:
            cur.execute(
                """
                SELECT m.* FROM comm_v2_messages m
                LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
                WHERE m.conversation_id=? AND m.id<? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL
                ORDER BY m.id DESC LIMIT ?
                """,
                (int(user_id), conversation_id, before_id, fetch_limit),
            )
        else:
            cur.execute(
                """
                SELECT m.* FROM comm_v2_messages m
                LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
                WHERE m.conversation_id=? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL
                ORDER BY m.id DESC LIMIT ?
                """,
                (int(user_id), conversation_id, fetch_limit),
            )
        fetched = [_row(row) for row in cur.fetchall()]
        has_older = len(fetched) > limit
        fetched = fetched[:limit]
        raw_messages = list(reversed(fetched))
        messages = _message_payloads(cur, raw_messages, user_id)
        now = _now()
        for message in raw_messages:
            if int(message.get("sender_user_id") or 0) != int(user_id):
                cur.execute(
                    """
                    INSERT OR IGNORE INTO comm_v2_read_receipts
                    (message_id, conversation_id, user_id, delivered_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (int(message.get("id") or 0), conversation_id, int(user_id), now, now, now),
                )
                cur.execute(
                    "UPDATE comm_v2_read_receipts SET delivered_at=COALESCE(NULLIF(delivered_at,''), ?), updated_at=? WHERE message_id=? AND user_id=?",
                    (now, now, int(message.get("id") or 0), int(user_id)),
                )
        oldest_message_id = int(raw_messages[0].get("id") or 0) if raw_messages else 0
        typing = typing_state(user_id, conversation_id, existing_conn=(conn, cur)).get("typing") or []
        mark_read(user_id, conversation_id, existing_conn=(conn, cur), commit=False)
        conn.commit()
        return _ok({
            "conversation": _conversation_payload(cur, conversation, user_id),
            "messages": messages,
            "typing": typing,
            "has_older": has_older,
            "oldest_message_id": oldest_message_id,
            "limit": limit,
        })
    finally:
        conn.close()


def search_messages(user_id: int, query: str = "", filters: dict | None = None) -> dict:
    disabled = _disabled("search_messages")
    if disabled:
        return disabled
    query = _clean(query, 200)
    if not query:
        return _ok({"messages": [], "items": []})
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 25), 50))
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            SELECT DISTINCT m.*
            FROM comm_v2_messages m
            JOIN comm_v2_conversations c ON c.id=m.conversation_id
            LEFT JOIN comm_v2_participants p ON p.conversation_id=c.id AND p.user_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            WHERE COALESCE(m.deleted_at,'')='' AND COALESCE(c.deleted_at,'')='' AND c.status='active'
              AND m.body LIKE ?
              AND (p.id IS NOT NULL OR (c.conversation_type='room' AND c.privacy='public' AND c.is_discoverable=1))
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (int(user_id), f"%{query}%", limit),
        )
        items = _message_payloads(cur, [_row(row) for row in cur.fetchall()], user_id)
        return _ok({"messages": items, "items": items, "query": query})
    finally:
        conn.close()


def search_people(user_id: int, query: str = "", filters: dict | None = None) -> dict:
    disabled = _disabled("search_people")
    if disabled:
        return disabled
    query = _clean(query, 160)
    if len(query) < 2:
        return _ok({"people": [], "items": [], "query": query})
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 12), 25))
    like = f"%{query.lower()}%"
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            SELECT user_id, username, display_name, avatar_url,
                   CASE WHEN LOWER(COALESCE(email,'')) LIKE ? THEN 1 ELSE 0 END AS matched_email
            FROM users
            WHERE user_id!=?
              AND COALESCE(account_status,'active')!='deleted'
              AND (
                LOWER(COALESCE(display_name,'')) LIKE ?
                OR LOWER(COALESCE(username,'')) LIKE ?
                OR LOWER(COALESCE(email,'')) LIKE ?
              )
            ORDER BY
              CASE WHEN LOWER(COALESCE(username,''))=? THEN 0
                   WHEN LOWER(COALESCE(display_name,''))=? THEN 1
                   WHEN LOWER(COALESCE(username,'')) LIKE ? THEN 2
                   ELSE 3 END,
              COALESCE(display_name, username, 'Pulse member') ASC
            LIMIT ?
            """,
            (like, int(user_id), like, like, like, query.lower(), query.lower(), f"{query.lower()}%", limit),
        )
        items = []
        for row in cur.fetchall():
            item = dict(row)
            items.append({
                "user_id": int(item.get("user_id") or 0),
                "display_name": item.get("display_name") or item.get("username") or "Pulse member",
                "username": item.get("username") or "",
                "avatar_url": item.get("avatar_url") or "",
                "matched_email": bool(item.get("matched_email")),
            })
        return _ok({"people": items, "items": items, "query": query})
    finally:
        conn.close()


def mark_read(user_id: int, conversation_ref: int | str, existing_conn=None, commit: bool = True) -> dict:
    disabled = _disabled("mark_read")
    if disabled:
        return disabled
    own_conn = existing_conn is None
    conn, cur = existing_conn or _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        conversation_id = int(conversation["id"])
        cur.execute("SELECT COALESCE(MAX(id),0) AS max_id FROM comm_v2_messages WHERE conversation_id=? AND COALESCE(deleted_at,'')=''", (conversation_id,))
        max_id = int(_row(cur.fetchone()).get("max_id") or 0)
        now = _now()
        cur.execute(
            "UPDATE comm_v2_participants SET last_read_message_id=?, last_read_at=?, unread_count=0, last_seen_at=?, updated_at=? WHERE conversation_id=? AND user_id=?",
            (max_id, now, now, now, conversation_id, int(user_id)),
        )
        if _read_receipts_allowed(cur, user_id):
            cur.execute("SELECT id FROM comm_v2_messages WHERE conversation_id=? AND id<=? AND sender_user_id!=? AND COALESCE(deleted_at,'')=''", (conversation_id, max_id, int(user_id)))
            for row in cur.fetchall():
                cur.execute(
                    """
                    INSERT OR IGNORE INTO comm_v2_read_receipts
                    (message_id, conversation_id, user_id, delivered_at, seen_at, read_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(row["id"]), conversation_id, int(user_id), now, now, now, now, now),
                )
                cur.execute(
                    "UPDATE comm_v2_read_receipts SET delivered_at=COALESCE(NULLIF(delivered_at,''), ?), seen_at=?, read_at=?, updated_at=? WHERE message_id=? AND user_id=?",
                    (now, now, now, now, int(row["id"]), int(user_id)),
                )
        if commit:
            conn.commit()
        return _ok({"conversation_id": conversation_id, "last_read_message_id": max_id})
    finally:
        if own_conn:
            conn.close()


def heartbeat(user_id: int, status: str = "online") -> dict:
    disabled = _disabled("heartbeat")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        presence = _touch_presence(cur, user_id, status)
        conn.commit()
        return _ok({"presence": presence}, "Presence updated.")
    finally:
        conn.close()


def update_settings(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("update_settings")
    if disabled:
        return disabled
    payload = payload or {}
    privacy = _clean(payload.get("presence_privacy") or payload.get("presence") or "everyone", 20).lower()
    if privacy not in {"everyone", "contacts", "nobody"}:
        return _err("Choose a valid presence privacy setting.", 400, "invalid_presence_privacy")
    read_receipts_enabled = 1 if payload.get("read_receipts_enabled", payload.get("read_receipts", True)) not in {False, 0, "0", "false", "off", "no"} else 0
    conn, cur = _open_db()
    try:
        now = _now()
        cur.execute(
            """
            INSERT OR IGNORE INTO comm_v2_user_settings (user_id, presence_privacy, read_receipts_enabled, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(user_id), privacy, read_receipts_enabled, now),
        )
        cur.execute(
            "UPDATE comm_v2_user_settings SET presence_privacy=?, read_receipts_enabled=?, updated_at=? WHERE user_id=?",
            (privacy, read_receipts_enabled, now, int(user_id)),
        )
        conn.commit()
        return _ok({"settings": {"presence_privacy": privacy, "read_receipts_enabled": bool(read_receipts_enabled)}}, "Communication settings saved.")
    finally:
        conn.close()


def get_settings(user_id: int) -> dict:
    disabled = _disabled("get_settings")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        settings = _settings(cur, user_id)
        return _ok({
            "settings": {
                "presence_privacy": settings.get("presence_privacy") or "everyone",
                "read_receipts_enabled": bool(settings.get("read_receipts_enabled", 1)),
            }
        })
    finally:
        conn.close()


def set_typing(user_id: int, conversation_ref: int | str, is_typing: bool = True) -> dict:
    disabled = _disabled("set_typing")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(seconds=12)).isoformat(timespec="seconds")
        now = now_dt.isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT OR IGNORE INTO comm_v2_typing (conversation_id, user_id, is_typing, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(conversation["id"]), int(user_id), 1 if is_typing else 0, expires, now),
        )
        cur.execute(
            "UPDATE comm_v2_typing SET is_typing=?, expires_at=?, updated_at=? WHERE conversation_id=? AND user_id=?",
            (1 if is_typing else 0, expires, now, int(conversation["id"]), int(user_id)),
        )
        conn.commit()
        return _ok({"conversation_id": int(conversation["id"]), "is_typing": bool(is_typing)})
    finally:
        conn.close()


def typing_state(user_id: int, conversation_ref: int | str, existing_conn=None) -> dict:
    disabled = _disabled("typing_state")
    if disabled:
        return disabled
    own_conn = existing_conn is None
    conn, cur = existing_conn or _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute(
            """
            SELECT t.user_id, COALESCE(u.display_name,u.username,'Pulse member') AS display_name
            FROM comm_v2_typing t
            LEFT JOIN users u ON u.user_id=t.user_id
            WHERE t.conversation_id=? AND t.user_id!=? AND t.is_typing=1 AND t.expires_at>=?
            ORDER BY t.updated_at DESC LIMIT 8
            """,
            (int(conversation["id"]), int(user_id), _now()),
        )
        return _ok({"typing": [dict(row) for row in cur.fetchall()]})
    finally:
        if own_conn:
            conn.close()


def conversation_presence(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("conversation_presence")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        _touch_presence(cur, user_id, "online")
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute(
            """
            SELECT p.user_id, COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   pr.status, pr.last_seen_at, pr.active_until
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            LEFT JOIN comm_v2_presence pr ON pr.user_id=p.user_id
            WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            ORDER BY p.id ASC
            """,
            (int(conversation["id"]),),
        )
        now_dt = datetime.now(timezone.utc)
        presence = []
        for row in cur.fetchall():
            item = dict(row)
            target_id = int(item.get("user_id") or 0)
            visible = _presence_visible(cur, int(user_id), target_id)
            active_now = False
            try:
                active_now = datetime.fromisoformat(item.get("active_until") or "") >= now_dt
            except Exception:
                active_now = False
            presence.append({
                "user_id": target_id,
                "display_name": item.get("display_name") or "Pulse member",
                "presence_visible": visible,
                "status": "online" if visible and active_now else "offline" if visible else "hidden",
                "active_now": bool(visible and active_now),
                "last_seen_at": item.get("last_seen_at") if visible else "",
            })
        typing = typing_state(user_id, int(conversation["id"]), existing_conn=(conn, cur)).get("typing") or []
        conn.commit()
        return _ok({"conversation_id": int(conversation["id"]), "presence": presence, "typing": typing})
    finally:
        conn.close()


def list_members(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("list_members")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute(
            """
            SELECT p.user_id, p.role, p.joined_at, p.last_seen_at, p.last_read_message_id,
                   COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   COALESCE(u.avatar_url,'') AS avatar_url
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            ORDER BY CASE p.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'moderator' THEN 2 ELSE 3 END, p.id ASC
            """,
            (int(conversation["id"]),),
        )
        return _ok({"members": [dict(row) for row in cur.fetchall()], "conversation_id": int(conversation["id"])})
    finally:
        conn.close()


def add_member(user_id: int, conversation_ref: int | str, target_user_id: int, role: str = "member") -> dict:
    disabled = _disabled("add_member")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute("SELECT role FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1", (int(conversation["id"]), int(user_id)))
        actor_role = (_row(cur.fetchone()).get("role") or "member").lower()
        if actor_role not in {"owner", "admin", "moderator"}:
            return _err("Only chat moderators can add members.", 403, "forbidden")
        if _blocked_between(cur, user_id, [int(target_user_id)]):
            return _err("That member cannot be added.", 403, "blocked")
        _add_participant(cur, int(conversation["id"]), int(target_user_id), _clean(role, 40) or "member")
        conn.commit()
        return list_members(user_id, int(conversation["id"]))
    finally:
        conn.close()


def set_reaction(user_id: int, message_id: int, reaction_type: str = "heart") -> dict:
    disabled = _disabled("set_reaction")
    if disabled:
        return disabled
    reaction_type = _clean(reaction_type, 40).lower()
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        now = _now()
        cur.execute("DELETE FROM comm_v2_message_reactions WHERE message_id=? AND user_id=?", (int(message_id), int(user_id)))
        if reaction_type and reaction_type not in {"none", "remove"}:
            cur.execute(
                "INSERT INTO comm_v2_message_reactions (message_id, conversation_id, user_id, reaction_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (int(message_id), int(message["conversation_id"]), int(user_id), reaction_type, now, now),
            )
        conn.commit()
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=?", (int(message_id),))
        return _ok({"message": _message_payload(cur, _row(cur.fetchone()), user_id)})
    finally:
        conn.close()


def edit_message(user_id: int, message_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("edit_message")
    if disabled:
        return disabled
    payload = payload or {}
    body = _clean(payload.get("body") or payload.get("message") or payload.get("content") or "", 4000)
    if not body:
        return _err("Edited message cannot be empty.", 400, "empty_message")
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        if int(message.get("sender_user_id") or 0) != int(user_id):
            return _err("You can only edit your own messages.", 403, "forbidden")
        created = datetime.fromisoformat(str(message.get("created_at") or _now()))
        if datetime.now(timezone.utc) - created > timedelta(minutes=int(payload.get("edit_window_minutes") or 15)):
            return _err("This message can no longer be edited.", 403, "edit_window_expired")
        now = _now()
        metadata = _json_loads(message.get("metadata_json"), {}) or {}
        history = metadata.get("edit_history") or []
        history.append({"body": message.get("body") or "", "edited_at": now})
        metadata["edit_history"] = history[-5:]
        cur.execute(
            "UPDATE comm_v2_messages SET body=?, metadata_json=?, edited_at=?, updated_at=? WHERE id=?",
            (body, json.dumps(metadata, default=str)[:4000], now, now, int(message_id)),
        )
        conn.commit()
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        return _ok({"message": _message_payload(cur, _row(cur.fetchone()), user_id)}, "Message edited.")
    finally:
        conn.close()


def delete_message(user_id: int, message_id: int, delete_for: str = "self") -> dict:
    disabled = _disabled("delete_message")
    if disabled:
        return disabled
    delete_for = _clean(delete_for, 40).lower()
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        now = _now()
        if delete_for in {"everyone", "all"}:
            if int(message.get("sender_user_id") or 0) != int(user_id):
                return _err("You can only delete your own message for everyone.", 403, "forbidden")
            created = datetime.fromisoformat(str(message.get("created_at") or _now()))
            if datetime.now(timezone.utc) - created > timedelta(minutes=30):
                return _err("This message can no longer be deleted for everyone.", 403, "delete_window_expired")
            cur.execute("UPDATE comm_v2_messages SET deleted_at=?, updated_at=? WHERE id=?", (now, now, int(message_id)))
            scope = "everyone"
        else:
            cur.execute(
                """
                INSERT OR IGNORE INTO comm_v2_message_deletions (message_id, conversation_id, user_id, deleted_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(message_id), int(message["conversation_id"]), int(user_id), now),
            )
            scope = "self"
        conn.commit()
        return _ok({"message_id": int(message_id), "delete_for": scope}, "Message deleted.")
    finally:
        conn.close()


def forward_message(user_id: int, message_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("forward_message")
    if disabled:
        return disabled
    payload = payload or {}
    targets = [int(x) for x in payload.get("conversation_ids") or payload.get("target_conversation_ids") or [] if int(x or 0)]
    targets = sorted(set(targets))[:10]
    if not targets:
        return _err("Choose at least one conversation to forward to.", 400, "missing_targets")
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        source = _row(cur.fetchone())
        if not source:
            return _err("Message not found.", 404, "not_found")
        _, source_access = _conversation_access(cur, user_id, int(source["conversation_id"]))
        if source_access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        created = []
        for target in targets:
            conversation, access = _conversation_access(cur, user_id, target, join_public=True)
            if access != "ok":
                continue
            now = _now()
            metadata = _json_loads(source.get("metadata_json"), {}) or {}
            metadata.update({"forwarded_from_message_id": int(message_id), "forwarded_at": now})
            cur.execute(
                """
                INSERT INTO comm_v2_messages
                (public_id, conversation_id, sender_user_id, message_type, body, delivery_status, moderation_status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'sent', 'approved', ?, ?, ?)
                """,
                (_public_id("msg"), int(conversation["id"]), int(user_id), source.get("message_type") or "text", source.get("body") or "", json.dumps(metadata, default=str)[:4000], now, now),
            )
            new_id = int(cur.lastrowid)
            cur.execute("SELECT * FROM comm_v2_attachments WHERE message_id=? ORDER BY id ASC", (int(message_id),))
            for attachment in cur.fetchall():
                item = _row(attachment)
                cur.execute(
                    """
                    INSERT INTO comm_v2_attachments
                    (attachment_public_id, message_id, conversation_id, media_upload_id, uploader_user_id, media_type, storage_provider, storage_key, url, cdn_url, playback_url, thumbnail_url, mime_type, file_size, file_size_bytes, width, height, mux_asset_id, mux_playback_id, mux_status, scan_status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_public_id("att"), new_id, int(conversation["id"]), int(item.get("media_upload_id") or 0), int(user_id), item.get("media_type") or "file", item.get("storage_provider") or "", item.get("storage_key") or "", item.get("url") or "", item.get("cdn_url") or "", item.get("playback_url") or "", item.get("thumbnail_url") or "", item.get("mime_type") or "", int(item.get("file_size") or 0), int(item.get("file_size_bytes") or 0), int(item.get("width") or 0), int(item.get("height") or 0), item.get("mux_asset_id") or "", item.get("mux_playback_id") or "", item.get("mux_status") or "", item.get("scan_status") or "approved", now),
                )
            cur.execute("UPDATE comm_v2_conversations SET last_message_id=?, last_message_at=?, last_activity_at=?, updated_at=? WHERE id=?", (new_id, now, now, now, int(conversation["id"])))
            created.append(new_id)
        conn.commit()
        return _ok({"forwarded_message_ids": created, "count": len(created)}, "Message forwarded.")
    finally:
        conn.close()


def report_message(user_id: int, message_id: int, reason: str = "") -> dict:
    disabled = _disabled("report_message")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        now = _now()
        cur.execute(
            "INSERT INTO comm_v2_reports (conversation_id, message_id, reporter_user_id, reported_user_id, reason, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (int(message["conversation_id"]), int(message_id), int(user_id), int(message.get("sender_user_id") or 0), _clean(reason, 500), now),
        )
        cur.execute(
            "INSERT INTO comm_v2_moderation_events (conversation_id, message_id, actor_user_id, target_user_id, event_type, reason, created_at) VALUES (?, ?, ?, ?, 'message_reported', ?, ?)",
            (int(message["conversation_id"]), int(message_id), int(user_id), int(message.get("sender_user_id") or 0), _clean(reason, 500), now),
        )
        conn.commit()
        return _ok({"report_id": int(cur.lastrowid)}, "Report sent to moderation.")
    finally:
        conn.close()


def block_user(user_id: int, blocked_user_id: int, reason: str = "") -> dict:
    disabled = _disabled("block_user")
    if disabled:
        return disabled
    if not blocked_user_id or int(blocked_user_id) == int(user_id):
        return _err("Choose a member to block.", 400, "invalid_user")
    conn, cur = _open_db()
    try:
        now = _now()
        cur.execute(
            "INSERT OR IGNORE INTO comm_v2_blocks (blocker_user_id, blocked_user_id, reason, status, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?)",
            (int(user_id), int(blocked_user_id), _clean(reason, 500), now, now),
        )
        cur.execute(
            "UPDATE comm_v2_blocks SET status='active', reason=?, updated_at=? WHERE blocker_user_id=? AND blocked_user_id=?",
            (_clean(reason, 500), now, int(user_id), int(blocked_user_id)),
        )
        conn.commit()
        return _ok({"blocked_user_id": int(blocked_user_id)}, "Member blocked.")
    finally:
        conn.close()


def infrastructure_diagnostics() -> dict:
    return {
        "ok": True,
        "status": "diagnostic",
        "enabled": flags.is_enabled(),
        "trace_id": _trace(),
        "diagnostics": infrastructure.diagnostics(),
    }


def stage_attachment_upload(user_id: int, file_storage, conversation_ref: int | str = "", metadata: dict | None = None) -> dict:
    disabled = _disabled("stage_attachment_upload")
    if disabled:
        return disabled
    if not file_storage:
        return _err("Choose an attachment to upload.", 400, "missing_file")
    voice_meta = _voice_upload_metadata(metadata)
    attachment_validation = _validate_attachment_upload(file_storage, metadata)
    if attachment_validation.get("ok") is False:
        return attachment_validation
    validation = _validate_voice_upload(file_storage, voice_meta)
    if validation.get("ok") is False:
        return validation
    context_id = "draft"
    if conversation_ref:
        conn, cur = _open_db()
        try:
            conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=True)
            if access == "missing":
                return _err("Conversation not found.", 404, "not_found")
            if access != "ok":
                return _err("You do not have access to this conversation.", 403, "forbidden")
            context_id = str(int(conversation["id"]))
        finally:
            conn.close()
    try:
        from services import upload_progress_service

        payload, status = upload_progress_service.stage_upload(
            int(user_id),
            file_storage,
            context_type="pulse_comm_v2",
            context_id=context_id,
        )
    except Exception:
        logging.exception("COMM_V2_ATTACHMENT_UPLOAD_FAILED user_id=%s", int(user_id or 0))
        return _err("Attachment upload could not be completed.", 500, "upload_failed")
    payload = payload or {}
    media = payload.get("media") or {}
    media_id = int(media.get("id") or media.get("media_id") or payload.get("media_id") or 0)
    if payload.get("ok") and media_id:
        duration_seconds = float(voice_meta.get("duration_seconds") or 0)
        waveform_json = json.dumps(voice_meta.get("waveform") or [])
        voice_note = 1 if voice_meta.get("is_voice") else 0
        conn, cur = _open_db()
        try:
            cur.execute(
                """
                UPDATE chat_media_uploads
                SET duration_seconds=?, waveform_json=?, voice_note=?
                WHERE id=? AND uploader_user_id=?
                """,
                (duration_seconds, waveform_json, voice_note, media_id, int(user_id)),
            )
            conn.commit()
        finally:
            conn.close()
        media["duration_seconds"] = duration_seconds
        media["waveform_json"] = waveform_json
        media["waveform"] = voice_meta.get("waveform") or []
        media["voice_note"] = bool(voice_note)
        media["media_type"] = "audio" if voice_note else media.get("media_type") or "file"
        payload["media"] = media
    payload.setdefault("http_status", status)
    if payload.get("ok") and payload.get("media"):
        payload["attachment_support"] = {
            "images": True,
            "files": True,
            "audio_voice_notes": True,
            "video_messages": "mux_preferred",
        }
    return payload


def create_comm_v2_mux_live_stream(user_id: int, conversation_ref: int | str, payload: dict | None = None) -> dict:
    disabled = _disabled("create_comm_v2_mux_live_stream")
    if disabled:
        return disabled
    payload = payload or {}
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=True)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access != "ok":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        try:
            from services import mux_live_service

            mux = mux_live_service.create_mux_live_stream(
                title=_clean(payload.get("title") or conversation.get("title") or "Pulse Live Room", 180),
                record=bool(payload.get("record", True)),
                low_latency=bool(payload.get("low_latency", True)),
                metadata={"source": "pulse_comm_v2", "conversation_id": str(conversation["id"])},
            )
        except Exception:
            logging.exception("COMM_V2_MUX_LIVE_CREATE_FAILED user_id=%s conversation_id=%s", int(user_id), int(conversation["id"]))
            return _err("Mux live stream could not be created.", 500, "mux_live_failed")
        if not mux.get("ok"):
            return _err(mux.get("message") or "Mux Live is not configured yet.", 503, mux.get("status") or "mux_not_ready")
        now = _now()
        cur.execute(
            """
            INSERT INTO comm_v2_live_streams
            (public_id, conversation_id, creator_user_id, mux_live_stream_id, mux_stream_key, mux_playback_id, mux_live_status, mux_recording_asset_id, ingest_url, rtmp_url, playback_url, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
            """,
            (
                _public_id("live"),
                int(conversation["id"]),
                int(user_id),
                mux.get("mux_live_stream_id") or "",
                mux.get("mux_stream_key") or "",
                mux.get("mux_playback_id") or "",
                mux.get("mux_live_status") or "",
                mux.get("mux_recording_asset_id") or "",
                mux.get("ingest_url") or "",
                mux.get("rtmp_url") or "",
                mux.get("playback_url") or "",
                json.dumps({"provider": "mux", "raw_status": mux.get("mux_live_status") or ""})[:2000],
                now,
                now,
            ),
        )
        live_id = int(cur.lastrowid)
        conn.commit()
        return _ok({"live_stream": _live_stream_payload({**mux, "id": live_id, "creator_user_id": user_id}, include_stream_key=True)}, "Live room foundation created.")
    finally:
        conn.close()


def _find_live_stream(cur, live_ref: int | str) -> dict:
    live_ref = str(live_ref or "").strip()
    if live_ref.isdigit():
        cur.execute("SELECT * FROM comm_v2_live_streams WHERE id=? LIMIT 1", (int(live_ref),))
    else:
        cur.execute("SELECT * FROM comm_v2_live_streams WHERE public_id=? OR mux_live_stream_id=? LIMIT 1", (live_ref, live_ref))
    return _row(cur.fetchone())


def get_comm_v2_mux_live_stream(user_id: int, live_ref: int | str) -> dict:
    disabled = _disabled("get_comm_v2_mux_live_stream")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        live = _find_live_stream(cur, live_ref)
        if not live:
            return _err("Live stream not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(live.get("conversation_id") or 0), join_public=True)
        if access != "ok":
            return _err("You do not have access to this live room.", 403, "forbidden")
        include_key = int(live.get("creator_user_id") or 0) == int(user_id)
        return _ok({"live_stream": _live_stream_payload(live, include_stream_key=include_key), "conversation_id": int(conversation.get("id") or 0)})
    finally:
        conn.close()


def disable_comm_v2_mux_live_stream(user_id: int, live_ref: int | str) -> dict:
    disabled = _disabled("disable_comm_v2_mux_live_stream")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        live = _find_live_stream(cur, live_ref)
        if not live:
            return _err("Live stream not found.", 404, "not_found")
        if int(live.get("creator_user_id") or 0) != int(user_id):
            return _err("Only the live room host can disable this stream.", 403, "forbidden")
        try:
            from services import mux_live_service

            mux = mux_live_service.disable_mux_live_stream(live.get("mux_live_stream_id") or "")
        except Exception:
            logging.exception("COMM_V2_MUX_LIVE_DISABLE_FAILED user_id=%s live_id=%s", int(user_id), int(live.get("id") or 0))
            mux = {"ok": False}
        now = _now()
        cur.execute(
            "UPDATE comm_v2_live_streams SET status='disabled', mux_live_status=?, updated_at=?, ended_at=COALESCE(ended_at, ?) WHERE id=?",
            (mux.get("mux_live_status") or "disabled", now, now, int(live["id"])),
        )
        conn.commit()
        return _ok({"live_stream_id": int(live["id"]), "mux": {"ok": bool(mux.get("ok")), "status": mux.get("mux_live_status") or "disabled"}}, "Live room disabled.")
    finally:
        conn.close()


def verify_mux_webhook_signature(payload: bytes, signature_header: str | None) -> dict:
    try:
        from services import mux_live_service

        return mux_live_service.verify_mux_webhook_signature(payload, signature_header)
    except Exception:
        logging.exception("COMM_V2_MUX_WEBHOOK_VERIFY_FAILED")
        return {"ok": False, "message": "Mux webhook verification failed."}


def process_mux_webhook(payload: dict) -> dict:
    event_type = _clean(payload.get("type") or "", 120)
    data = payload.get("data") or {}
    mux_live_stream_id = data.get("id") or data.get("live_stream_id") or ""
    if not mux_live_stream_id:
        return {"ok": True, "status": "ignored", "event_type": event_type, "message": "No live stream id in event."}
    conn, cur = _open_db()
    try:
        live = _find_live_stream(cur, mux_live_stream_id)
        if not live:
            return {"ok": True, "status": "unmatched", "event_type": event_type}
        now = _now()
        updates = {
            "video.live_stream.connected": "connected",
            "video.live_stream.disconnected": "disconnected",
            "video.live_stream.created": data.get("status") or "created",
            "video.asset.ready": "recording_ready",
            "video.asset.errored": "recording_error",
        }
        cur.execute(
            """
            UPDATE comm_v2_live_streams
            SET mux_live_status=?, mux_recording_asset_id=COALESCE(NULLIF(?, ''), mux_recording_asset_id), updated_at=?
            WHERE id=?
            """,
            (updates.get(event_type) or data.get("status") or event_type, data.get("asset_id") or data.get("id") or "", now, int(live["id"])),
        )
        conn.commit()
        return {"ok": True, "status": "processed", "event_type": event_type, "live_stream_id": int(live["id"])}
    finally:
        conn.close()


def _live_stream_payload(row: dict, *, include_stream_key: bool = False) -> dict:
    playback_id = row.get("mux_playback_id") or ""
    playback_url = row.get("playback_url") or ""
    if playback_id and not playback_url:
        try:
            from services import mux_live_service

            playback_url = mux_live_service.playback_url(playback_id)
        except Exception:
            playback_url = ""
    payload = {
        "id": int(row.get("id") or 0),
        "public_id": row.get("public_id") or "",
        "conversation_id": int(row.get("conversation_id") or 0),
        "creator_user_id": int(row.get("creator_user_id") or 0),
        "provider": "mux",
        "mux_live_stream_id": row.get("mux_live_stream_id") or "",
        "mux_playback_id": playback_id,
        "mux_live_status": row.get("mux_live_status") or row.get("status") or "",
        "mux_recording_asset_id": row.get("mux_recording_asset_id") or "",
        "mux_recording_playback_id": row.get("mux_recording_playback_id") or "",
        "playback_url": playback_url,
        "ingest_url": row.get("ingest_url") or row.get("rtmp_url") or "",
        "status": row.get("status") or "created",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }
    if include_stream_key:
        payload["mux_stream_key"] = row.get("mux_stream_key") or ""
    return payload


def twilio_notification_preview(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("twilio_notification_preview")
    if disabled:
        return disabled
    payload = payload or {}
    kind = _clean(payload.get("type") or "message_alert", 80)
    to_number = _clean(payload.get("to") or payload.get("to_number") or "", 80)
    if kind == "sms_verification":
        result = twilio_service.send_sms_verification(to_number, payload.get("code") or "000000", user_id=user_id)
    elif kind == "room_invite":
        result = twilio_service.send_room_invite_alert(to_number, payload.get("room_title") or "Pulse Room", payload.get("inviter") or "", user_id=user_id)
    elif kind == "security_alert":
        result = twilio_service.send_security_alert(to_number, payload.get("alert") or "Pulse security event", user_id=user_id)
    else:
        result = twilio_service.send_message_alert(to_number, payload.get("preview") or "Pulse message", user_id=user_id)
    result.setdefault("diagnostics", twilio_service.diagnostics())
    result.setdefault("trace_id", _trace())
    result.setdefault("enabled", flags.is_enabled())
    return result


def create_community(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("create_community")
    if disabled:
        return disabled
    payload = payload or {}
    name = _clean(payload.get("name") or "Pulse Community", 120)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or f"community-{secrets.token_hex(4)}"
    conn, cur = _open_db()
    try:
        now = _now()
        base_slug = slug
        counter = 1
        while True:
            cur.execute("SELECT id FROM comm_v2_communities WHERE slug=? LIMIT 1", (slug,))
            if not cur.fetchone():
                break
            counter += 1
            slug = f"{base_slug}-{counter}"[:90]
        cur.execute(
            "INSERT INTO comm_v2_communities (public_id, name, slug, description, owner_user_id, privacy, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (_public_id("com"), name, slug, _clean(payload.get("description") or "", 500), int(user_id), _clean(payload.get("privacy") or "public", 20), now, now),
        )
        community_id = int(cur.lastrowid)
        conn.commit()
        return _ok({"community": {"id": community_id, "name": name, "slug": slug}})
    finally:
        conn.close()


def create_channel(user_id: int, community_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("create_channel")
    if disabled:
        return disabled
    payload = payload or {}
    name = _clean(payload.get("name") or "general", 80)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or f"channel-{secrets.token_hex(4)}"
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_communities WHERE id=? AND owner_user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(community_id), int(user_id)))
        community = _row(cur.fetchone())
        if not community:
            return _err("Community not found or not manageable.", 404, "not_found")
        convo = create_conversation(user_id, {"conversation_type": "community_channel", "title": name, "community_id": int(community_id)})
        if not convo.get("ok"):
            return convo
        base_slug = slug
        counter = 1
        while True:
            cur.execute("SELECT id FROM comm_v2_channels WHERE community_id=? AND slug=? LIMIT 1", (int(community_id), slug))
            if not cur.fetchone():
                break
            counter += 1
            slug = f"{base_slug}-{counter}"[:90]
        now = _now()
        cur.execute(
            "INSERT INTO comm_v2_channels (public_id, community_id, conversation_id, name, slug, description, channel_type, visibility, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (_public_id("ch"), int(community_id), int(convo.get("conversation_id") or 0), name, slug, _clean(payload.get("description") or "", 500), _clean(payload.get("channel_type") or "text", 40), _clean(payload.get("visibility") or "members", 40), now, now),
        )
        channel_id = int(cur.lastrowid)
        conn.commit()
        return _ok({"channel": {"id": channel_id, "name": name, "slug": slug, "conversation_id": int(convo.get("conversation_id") or 0)}})
    finally:
        conn.close()


def moderation_summary(admin_user: dict | None = None) -> dict:
    disabled = _disabled("moderation_summary")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        cur.execute("SELECT COUNT(*) AS total FROM comm_v2_reports WHERE status='open'")
        open_reports = int(_row(cur.fetchone()).get("total") or 0)
        cur.execute("SELECT COUNT(*) AS total FROM comm_v2_blocks WHERE status='active'")
        active_blocks = int(_row(cur.fetchone()).get("total") or 0)
        cur.execute(
            """
            SELECT r.*, COALESCE(u.display_name,u.username,'Member') AS reporter_name
            FROM comm_v2_reports r
            LEFT JOIN users u ON u.user_id=r.reporter_user_id
            ORDER BY r.id DESC LIMIT 25
            """
        )
        reports = [dict(row) for row in cur.fetchall()]
        return _ok({"moderation": {"open_reports": open_reports, "active_blocks": active_blocks, "recent_reports": reports, "admin": bool(admin_user)}})
    finally:
        conn.close()


def moderate_message(admin_user: dict, message_id: int, action: str, reason: str = "") -> dict:
    disabled = _disabled("moderate_message")
    if disabled:
        return disabled
    action = _clean(action, 40).lower()
    if action not in {"approve", "hide", "delete"}:
        return _err("Choose a moderation action.", 400, "invalid_action")
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        now = _now()
        status = "approved" if action == "approve" else "hidden"
        deleted_at = now if action == "delete" else (message.get("deleted_at") or "")
        cur.execute("UPDATE comm_v2_messages SET moderation_status=?, deleted_at=?, updated_at=? WHERE id=?", (status, deleted_at, now, int(message_id)))
        cur.execute(
            "INSERT INTO comm_v2_moderation_events (conversation_id, message_id, admin_user_id, target_user_id, event_type, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(message["conversation_id"]), int(message_id), int((admin_user or {}).get("id") or 0), int(message.get("sender_user_id") or 0), f"message_{action}", _clean(reason, 500), now),
        )
        conn.commit()
        return _ok({"message_id": int(message_id), "action": action})
    finally:
        conn.close()
