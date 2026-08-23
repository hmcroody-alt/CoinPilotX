"""Bridge legacy Room/Group conversation entities onto the canonical v2 message stack.

Why this exists
---------------
PulseSoc has two chat stacks:

* **legacy** — ``pulse_conversations`` / ``pulse_conversation_participants`` /
  ``pulse_messages``. This is where a community Room (``/api/pulse/communications/rooms``)
  and a Group chat (``/api/pulse/groups/<slug>/chat/open``) are created. It owns
  the room/group *entity*: rate limits, invitees, owner-cannot-orphan, admin audit,
  member roles.
* **v2** — ``comm_v2_conversations`` / ``comm_v2_participants`` / ``comm_v2_messages``,
  served by the ``pulse_communications_v2`` blueprint under
  ``/api/pulse/communications/v2``.

Every *client* reads the v2 stack. ``mobile-native/src/api/messenger.ts`` points at
``/api/pulse/communications/v2`` and ``ChatScreen`` only imports that client;
``/pulse/messages/<id>`` renders ``pulse_messages_v2.html`` with the id as
``initial_conversation_id``. Nothing consumes the legacy conversation endpoints.

So a Room or Group chat handed the client a *legacy* conversation id, the client
asked the *v2* stack for it, and got ``404 Conversation not found`` — a room you
can create, list, join and manage but never actually talk in. A dead shell.

The fix keeps the legacy row as the source of truth for identity, membership and
lifecycle (none of that behaviour is worth re-implementing) and gives it a paired
v2 conversation that carries the messages. Callers hand the client the *v2* id for
chat, and keep using the legacy id for every lifecycle call.

Reconciliation, not mirroring
-----------------------------
``sync_thread`` is a full idempotent reconcile rather than a per-mutation mirror:
it derives the v2 conversation's members, roles and status from the legacy row
every time a room/group is created, joined, opened or managed. That means a route
that forgets to call it is merely stale, not corrupt — the next call repairs it —
and rooms created before this change are healed the first time anyone opens them.

This module never touches audio, calls, RTC or livestream. A community Room here
is a text conversation row; the audio/live surfaces are a different subsystem.
"""

from __future__ import annotations

import logging
import json
import secrets
from datetime import datetime

# Legacy conversation statuses that must not carry an open chat thread.
_CLOSED_STATUSES = {"deleted", "archived", "suspended", "closed", "ended"}

# Roles the v2 participant table understands, mapped from the legacy vocabulary.
_ROLE_MAP = {
    "owner": "owner",
    "admin": "admin",
    "moderator": "moderator",
    "member": "member",
    "": "member",
}


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _row(row) -> dict:
    if not row:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _v2_service():
    """Import the v2 service lazily.

    The blueprint is registered inside a ``try/except`` in ``bot.py``; importing at
    module scope would make this bridge fail to load whenever that pack is broken,
    which is exactly the failure mode the route-pack pattern exists to avoid.
    """
    from pulse_communications_v2 import service as comm_v2_service

    return comm_v2_service


def _v2_enabled() -> bool:
    try:
        from pulse_communications_v2 import flags as comm_v2_flags

        return bool(comm_v2_flags.is_enabled())
    except Exception:
        return False


def _ensure_v2_schema(cur, conn) -> None:
    """Create the v2 tables/columns on the caller's own connection.

    ``service._open_db()`` would open a *second* connection; running that inside a
    route that already holds an open transaction invites a SQLite writer lock
    against ourselves. Reusing the caller's cursor keeps this to one transaction.
    """
    service = _v2_service()
    service.ensure_v2_schema(cur)
    try:
        import bot

        service._ensure_columns(bot, cur, conn)
    except Exception:
        logging.debug("PULSE_CHAT_BRIDGE_V2_COLUMN_CHECK_SKIPPED", exc_info=True)
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_comm_v2_conversations_legacy "
        "ON comm_v2_conversations(legacy_conversation_id)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_comm_v2_messages_legacy "
        "ON comm_v2_messages(legacy_message_id)"
    )


def _legacy_conversation(cur, legacy_conversation_id: int) -> dict:
    cur.execute(
        "SELECT * FROM pulse_conversations WHERE id=? LIMIT 1",
        (int(legacy_conversation_id),),
    )
    return _row(cur.fetchone())


def _legacy_participants(cur, legacy_conversation_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT user_id, COALESCE(role,'') AS role, COALESCE(left_at,'') AS left_at
        FROM pulse_conversation_participants
        WHERE conversation_id=?
        """,
        (int(legacy_conversation_id),),
    )
    return [_row(row) for row in cur.fetchall()]


def _v2_conversation_type(legacy: dict) -> str:
    kind = str(legacy.get("conversation_type") or "").lower()
    if kind in {"community_group", "group"}:
        return "group"
    return "room"


def _create_v2_conversation(cur, legacy: dict) -> int:
    """Insert the paired v2 conversation shell. Members are filled in by the reconcile."""
    now = _now()
    conversation_type = _v2_conversation_type(legacy)
    privacy = str(legacy.get("privacy") or "public").lower()
    if conversation_type == "group":
        # A Group chat is members-only regardless of whether the *group* is
        # publicly listed: joining the group is what grants the chat.
        privacy = "private"
    elif privacy not in {"public", "private"}:
        privacy = "public" if _int(legacy.get("is_public")) else "private"
    is_public = 1 if privacy == "public" else 0
    prefix = "room" if conversation_type == "room" else "grp"
    cur.execute(
        """
        INSERT INTO comm_v2_conversations
        (public_id, conversation_type, title, description, owner_user_id, created_by_user_id, legacy_conversation_id,
         privacy, visibility, status, is_discoverable, member_count, created_at, updated_at, last_activity_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)
        """,
        (
            f"{prefix}_{secrets.token_hex(8)}",
            conversation_type,
            str(legacy.get("title") or "")[:120],
            str(legacy.get("description") or "")[:500],
            _int(legacy.get("owner_user_id")),
            _int(legacy.get("created_by_user_id") or legacy.get("owner_user_id")),
            _int(legacy.get("id")),
            privacy,
            "public" if is_public else "members",
            is_public,
            now,
            now,
            now,
        ),
    )
    return _int(cur.lastrowid)


def _reconcile_participants(cur, v2_conversation_id: int, legacy: dict, participants: list[dict]) -> None:
    now = _now()
    owner_user_id = _int(legacy.get("owner_user_id"))
    seen: set[int] = set()
    for participant in participants:
        user_id = _int(participant.get("user_id"))
        if not user_id:
            continue
        seen.add(user_id)
        has_left = bool(str(participant.get("left_at") or "").strip())
        role = _ROLE_MAP.get(str(participant.get("role") or "").lower(), "member")
        if user_id == owner_user_id:
            role = "owner"
        cur.execute(
            "SELECT id FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1",
            (int(v2_conversation_id), user_id),
        )
        existing = _row(cur.fetchone())
        if not existing:
            if has_left:
                continue
            cur.execute(
                """
                INSERT INTO comm_v2_participants
                (conversation_id, user_id, role, membership_state, joined_at, left_at, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, '', ?, ?)
                """,
                (int(v2_conversation_id), user_id, role, now, now, now),
            )
            continue
        if has_left:
            cur.execute(
                """
                UPDATE comm_v2_participants
                SET membership_state='left', left_at=?, updated_at=?
                WHERE conversation_id=? AND user_id=?
                """,
                (participant.get("left_at") or now, now, int(v2_conversation_id), user_id),
            )
        else:
            cur.execute(
                """
                UPDATE comm_v2_participants
                SET membership_state='active', left_at='', role=?, updated_at=?
                WHERE conversation_id=? AND user_id=?
                """,
                (role, now, int(v2_conversation_id), user_id),
            )

    # Reconcile the other direction. Someone can become a v2 participant without a
    # legacy row: ``service.send_message`` calls ``_conversation_access(join_public=True)``,
    # so speaking in a *public* room joins you. That is a legitimate join through a
    # different door, and evicting them here would drop them off the member roster
    # and out of notifications every time anyone touched the room. Adopt them into
    # the legacy membership instead.
    #
    # For a private room or a group chat there is no such door, so an unexplained v2
    # participant is stale state (or a membership that was revoked out-of-band) and
    # must lose access.
    is_public_room = _v2_conversation_type(legacy) == "room" and str(legacy.get("privacy") or "").lower() == "public"
    cur.execute(
        """
        SELECT user_id FROM comm_v2_participants
        WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
        """,
        (int(v2_conversation_id),),
    )
    unexplained = [
        _int(_row(row).get("user_id"))
        for row in cur.fetchall()
        if _int(_row(row).get("user_id")) and _int(_row(row).get("user_id")) not in seen
    ]
    legacy_conversation_id = _int(legacy.get("id"))
    for user_id in unexplained:
        if is_public_room and legacy_conversation_id:
            cur.execute(
                """
                INSERT INTO pulse_conversation_participants
                (conversation_id, user_id, role, muted, archived, joined_at, created_at)
                VALUES (?, ?, 'member', 0, 0, ?, ?)
                """,
                (legacy_conversation_id, user_id, now, now),
            )
            continue
        cur.execute(
            """
            UPDATE comm_v2_participants
            SET membership_state='left', left_at=?, updated_at=?
            WHERE conversation_id=? AND user_id=?
            """,
            (now, now, int(v2_conversation_id), user_id),
        )
    if unexplained and is_public_room and legacy_conversation_id:
        cur.execute(
            """
            UPDATE pulse_conversations
            SET member_count=(SELECT COUNT(*) FROM pulse_conversation_participants
                              WHERE conversation_id=? AND COALESCE(left_at,'')=''),
                updated_at=?
            WHERE id=?
            """,
            (legacy_conversation_id, now, legacy_conversation_id),
        )


def _reconcile_conversation(cur, v2_conversation_id: int, legacy: dict) -> None:
    now = _now()
    status = str(legacy.get("status") or "active").lower()
    deleted_at = str(legacy.get("deleted_at") or "").strip()
    if status == "deleted" or deleted_at:
        v2_status = "deleted"
        v2_deleted_at = deleted_at or now
    elif status in _CLOSED_STATUSES:
        v2_status = status
        v2_deleted_at = ""
    else:
        v2_status = "active"
        v2_deleted_at = ""
    privacy = str(legacy.get("privacy") or "").lower()
    if _v2_conversation_type(legacy) == "group":
        privacy = "private"
    elif privacy not in {"public", "private"}:
        privacy = "public" if _int(legacy.get("is_public")) else "private"
    is_public = 1 if privacy == "public" else 0
    cur.execute(
        """
        UPDATE comm_v2_conversations
        SET title=?, description=?, owner_user_id=?, legacy_conversation_id=?, privacy=?, visibility=?, is_discoverable=?,
            status=?, deleted_at=?, updated_at=?,
            member_count=(SELECT COUNT(*) FROM comm_v2_participants
                          WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')='')
        WHERE id=?
        """,
        (
            str(legacy.get("title") or "")[:120],
            str(legacy.get("description") or "")[:500],
            _int(legacy.get("owner_user_id")),
            _int(legacy.get("id")),
            privacy,
            "public" if is_public else "members",
            is_public,
            v2_status,
            v2_deleted_at,
            now,
            int(v2_conversation_id),
            int(v2_conversation_id),
        ),
    )


def _migrate_legacy_messages(cur, legacy_conversation_id: int, v2_conversation_id: int) -> None:
    """Copy visible legacy history once, preserving the source rows in place."""
    cur.execute(
        """
        SELECT id, sender_user_id, COALESCE(body,'') AS body,
               COALESCE(message_type,'text') AS message_type,
               COALESCE(reply_to_id,0) AS reply_to_id,
               COALESCE(client_message_id,'') AS client_message_id,
               COALESCE(delivery_status,'sent') AS delivery_status,
               COALESCE(created_at,'') AS created_at,
               COALESCE(edited_at,'') AS edited_at
        FROM pulse_messages
        WHERE conversation_id=? AND COALESCE(deleted_at,'')=''
        ORDER BY id ASC
        """,
        (int(legacy_conversation_id),),
    )
    messages = [_row(row) for row in cur.fetchall()]
    now = _now()
    for message in messages:
        legacy_message_id = _int(message.get("id"))
        if not legacy_message_id:
            continue
        cur.execute("SELECT id FROM comm_v2_messages WHERE legacy_message_id=? LIMIT 1", (legacy_message_id,))
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO comm_v2_messages
            (public_id, legacy_message_id, conversation_id, sender_user_id, message_type, body,
             reply_to_message_id, client_message_id, delivery_status, moderation_status,
             metadata_json, created_at, updated_at, edited_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 'approved', ?, ?, ?, ?)
            """,
            (
                f"legacy_{legacy_conversation_id}_{legacy_message_id}",
                legacy_message_id,
                int(v2_conversation_id),
                _int(message.get("sender_user_id")),
                str(message.get("message_type") or "text")[:40],
                str(message.get("body") or "")[:4000],
                str(message.get("client_message_id") or "")[:120],
                str(message.get("delivery_status") or "sent")[:40],
                json.dumps({"legacy_conversation_id": int(legacy_conversation_id)}, separators=(",", ":")),
                message.get("created_at") or now,
                message.get("edited_at") or message.get("created_at") or now,
                message.get("edited_at") or "",
            ),
        )
    for message in messages:
        reply_to_id = _int(message.get("reply_to_id"))
        if not reply_to_id:
            continue
        cur.execute("SELECT id FROM comm_v2_messages WHERE legacy_message_id=? LIMIT 1", (reply_to_id,))
        target = _int(_row(cur.fetchone()).get("id"))
        if target:
            cur.execute(
                "UPDATE comm_v2_messages SET reply_to_message_id=? WHERE legacy_message_id=?",
                (target, _int(message.get("id"))),
            )


def sync_thread(cur, conn, legacy_conversation_id, commit: bool = True) -> int:
    """Reconcile the v2 chat thread paired with a legacy room/group conversation.

    Returns the v2 conversation id, or ``0`` when the pairing could not be made.
    Callers should fall back to their previous behaviour on ``0`` rather than
    failing the request — a room that exists but is briefly unreachable for chat
    is recoverable; a room that failed to be created is not.

    Safe to call repeatedly. Intended to run *after* the caller has committed the
    legacy write, so a bridge failure can never roll back the room itself.
    """
    legacy_conversation_id = _int(legacy_conversation_id)
    if not legacy_conversation_id or not _v2_enabled():
        return 0
    try:
        legacy = _legacy_conversation(cur, legacy_conversation_id)
        if not legacy:
            return 0
        _ensure_v2_schema(cur, conn)

        v2_conversation_id = _int(legacy.get("comm_v2_conversation_id"))
        if v2_conversation_id:
            cur.execute(
                "SELECT id FROM comm_v2_conversations WHERE id=? LIMIT 1",
                (v2_conversation_id,),
            )
            if not cur.fetchone():
                v2_conversation_id = 0
        if not v2_conversation_id:
            cur.execute(
                "SELECT id FROM comm_v2_conversations WHERE legacy_conversation_id=? LIMIT 1",
                (legacy_conversation_id,),
            )
            v2_conversation_id = _int(_row(cur.fetchone()).get("id"))
        if not v2_conversation_id:
            v2_conversation_id = _create_v2_conversation(cur, legacy)
            if not v2_conversation_id:
                return 0
            cur.execute(
                "UPDATE pulse_conversations SET comm_v2_conversation_id=? WHERE id=?",
                (v2_conversation_id, legacy_conversation_id),
            )

        _reconcile_participants(cur, v2_conversation_id, legacy, _legacy_participants(cur, legacy_conversation_id))
        _reconcile_conversation(cur, v2_conversation_id, legacy)
        _migrate_legacy_messages(cur, legacy_conversation_id, v2_conversation_id)
        if commit:
            conn.commit()
        return v2_conversation_id
    except Exception:
        if commit:
            try:
                conn.rollback()
            except Exception:
                pass
        logging.exception(
            "PULSE_CHAT_BRIDGE_SYNC_FAILED legacy_conversation_id=%s",
            legacy_conversation_id,
        )
        return 0


def thread_id(cur, legacy_conversation_id) -> int:
    """Return the already-paired v2 conversation id without reconciling. 0 if unpaired."""
    legacy_conversation_id = _int(legacy_conversation_id)
    if not legacy_conversation_id:
        return 0
    try:
        cur.execute(
            "SELECT comm_v2_conversation_id FROM pulse_conversations WHERE id=? LIMIT 1",
            (legacy_conversation_id,),
        )
        return _int(_row(cur.fetchone()).get("comm_v2_conversation_id"))
    except Exception:
        logging.debug("PULSE_CHAT_BRIDGE_LOOKUP_FAILED id=%s", legacy_conversation_id, exc_info=True)
        return 0
