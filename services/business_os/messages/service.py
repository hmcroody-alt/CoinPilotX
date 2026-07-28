"""Business OS — Section 6: Messages service (canonical business-messaging facade).

Flag-gated (``BUSINESS_OS_MESSAGES``) surface over the ONE canonical message engine
(``pulse_conversations`` / ``pulse_conversation_participants`` / ``pulse_messages``).
This module owns NO new message table: a business inbox is a canonical conversation
tagged ``conversation_type='business'`` + ``business_id``; every message is a row in the
canonical ``pulse_messages`` table with the engine's own send semantics (participant
scoping, client-message-id idempotency, unread counters, ``last_message_at`` bump).

Identity is always the authenticated caller. Who may act *as the business* (read the
inbox, reply on the business's behalf) is resolved against S1 canonical membership/RBAC
(``business.service._effective_role``) — never re-modeled here. The customer side is a
plain conversation participant. Reads are ownership-scoped exactly like the engine:
a stranger sees ``None`` (existence not leaked).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.messages import schema as _schema
from services.business_os.business import service as biz_svc


FLAG_ENV = "BUSINESS_OS_MESSAGES"

# Business-side permission ranks resolved against S1 roles (lower rank = more privileged).
READ_ROLE = "viewer"   # any active member may read the business inbox
WRITE_ROLE = "staff"   # staff+ may reply on the business's behalf

_ALLOWED_MSG_TYPES = {
    "text", "image", "gif", "video", "voice", "audio", "file", "system",
    "link", "post_share", "reel_share", "marketplace_share", "order_update",
}


class MessageError(ValueError):
    """One stable domain-facing error carrying an HTTP status + machine code."""

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = int(http_status)
        self.code = code


# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


def _require_enabled() -> None:
    if not is_enabled():
        raise MessageError("Messages is not enabled in this environment.",
                           503, "disabled")


def _require_not_held(context: Optional[dict]) -> None:
    ctx = context or {}
    status = str(ctx.get("account_status") or "").lower()
    access = ctx.get("access_enabled")
    if status in {"suspended", "banned", "disabled", "hold"}:
        raise MessageError("Account is on hold.", 403, "account_hold")
    if access is not None and not access:
        raise MessageError("Account access is disabled.", 403, "account_hold")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sid(user_id: Any) -> str:
    return biz_svc._sid(user_id)


def _row(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _rows(rows) -> list:
    return [d for d in (_row(r) for r in (rows or [])) if d is not None]


def _clean_body(value: Any, *, limit: int = 2000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MessageError("body must be text.", 400, "invalid")
    return value.strip()[:limit]


# ---------------------------------------------------------------------------
# Internal resolution helpers
# ---------------------------------------------------------------------------
def _get_conversation(conn, conversation_id: Any) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM pulse_conversations WHERE id = ? "
        "AND COALESCE(status,'active') = 'active' LIMIT 1",
        (int(conversation_id or 0),),
    ).fetchone()
    conv = _row(row)
    if not conv:
        return None
    if (conv.get("conversation_type") or "") != "business":
        # Not a business thread — this domain never touches native DMs/groups.
        return None
    return conv


def _participant(conn, conversation_id: Any, user_id: Any) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM pulse_conversation_participants "
        "WHERE conversation_id = ? AND user_id = ? AND COALESCE(left_at,'') = '' "
        "LIMIT 1",
        (int(conversation_id or 0), _sid(user_id)),
    ).fetchone()
    return _row(row)


def _business_role(conn, business_id: Any, user_id: Any) -> Optional[str]:
    if not business_id:
        return None
    return biz_svc._effective_role(conn, str(business_id), user_id)


def _authorize_read(conn, conv: dict, user_id: Any) -> Optional[str]:
    """Return the viewer's relationship to a business thread: 'customer' (participant),
    'business' (S1 member with read rank), or None (no access — never leaked)."""
    if _participant(conn, conv["id"], user_id) is not None:
        return "customer"
    role = _business_role(conn, conv.get("business_id"), user_id)
    if role is not None and biz_svc._role_rank(role) <= biz_svc._role_rank(READ_ROLE):
        return "business"
    return None


def _authorize_write(conn, conv: dict, user_id: Any) -> Optional[str]:
    if _participant(conn, conv["id"], user_id) is not None:
        return "customer"
    role = _business_role(conn, conv.get("business_id"), user_id)
    if role is not None and biz_svc._role_rank(role) <= biz_svc._role_rank(WRITE_ROLE):
        return "business"
    return None


def _business_owner(conn, business_id: Any) -> Optional[str]:
    row = conn.execute(
        "SELECT owner_user_id FROM business_os_business WHERE business_id = ?",
        (str(business_id),),
    ).fetchone()
    r = _row(row)
    return _sid(r.get("owner_user_id")) if r else None


def _msg_public(row: dict, viewer_id: Any) -> dict:
    d = _row(row) or {}
    return {
        "id": int(d.get("id") or 0),
        "conversation_id": int(d.get("conversation_id") or 0),
        "sender_user_id": _sid(d.get("sender_user_id")),
        "is_mine": _sid(d.get("sender_user_id")) == _sid(viewer_id),
        "body": d.get("body") or "",
        "message_type": d.get("message_type") or "text",
        "media_url": d.get("media_url") or "",
        "reply_to_id": int(d.get("reply_to_id") or 0),
        "status": d.get("status") or "sent",
        "client_message_id": d.get("client_message_id") or "",
        "created_at": d.get("created_at"),
    }


def _thread_public(conn, conv: dict, viewer_id: Any) -> dict:
    last = conn.execute(
        "SELECT * FROM pulse_messages WHERE conversation_id = ? "
        "AND COALESCE(deleted_at,'') = '' ORDER BY id DESC LIMIT 1",
        (conv["id"],),
    ).fetchone()
    part = _participant(conn, conv["id"], viewer_id)
    return {
        "conversation_id": int(conv["id"]),
        "business_id": conv.get("business_id"),
        "type": "business",
        "subject": conv.get("title") or "",
        "status": conv.get("status") or "active",
        "last_message_at": conv.get("last_message_at"),
        "unread_count": int((part or {}).get("unread_count") or 0),
        "last_message": _msg_public(last, viewer_id) if last else None,
    }


# ============================================================================
# Public API
# ============================================================================
def start_business_thread(business_id: Any, customer_user_id: Any, actor_user_id: Any,
                          *, subject: Optional[str] = None,
                          context: Optional[dict] = None) -> dict:
    """Open (or reuse) a business↔customer thread on the canonical message engine.

    The caller is either the customer themselves, or a business member with write
    permission opening a thread proactively. Idempotent: an existing open thread
    between this business and customer is returned rather than duplicated."""
    _require_enabled()
    _require_not_held(context)
    if business_id in (None, ""):
        raise MessageError("business_id is required.", 400, "invalid")
    if customer_user_id in (None, ""):
        raise MessageError("customer_user_id is required.", 400, "invalid")
    customer = _sid(customer_user_id)
    actor = _sid(actor_user_id)

    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        owner = _business_owner(conn, business_id)
        if owner is None:
            # Business does not exist — do not leak that fact.
            raise MessageError("Business not found.", 404, "not_found")

        # Authorize: customer acting for self, or a business member with write rank.
        if actor != customer:
            role = _business_role(conn, business_id, actor)
            if role is None:
                raise MessageError("Business not found.", 404, "not_found")
            if biz_svc._role_rank(role) > biz_svc._role_rank(WRITE_ROLE):
                raise MessageError(
                    f"Your role ({role}) cannot start a thread.", 403, "forbidden")

        # Reuse an existing open business thread for this (business, customer).
        existing = conn.execute(
            """
            SELECT c.* FROM pulse_conversations c
            JOIN pulse_conversation_participants p
              ON p.conversation_id = c.id AND p.user_id = ? AND COALESCE(p.left_at,'')=''
            WHERE c.conversation_type = 'business' AND c.business_id = ?
              AND COALESCE(c.status,'active') = 'active'
            ORDER BY c.id DESC LIMIT 1
            """,
            (customer, str(business_id)),
        ).fetchone()
        if existing:
            return _thread_public(conn, _row(existing), actor_user_id)

        now = _now()
        title = (subject or "").strip()[:200] if isinstance(subject, str) else None
        cur = conn.execute(
            """
            INSERT INTO pulse_conversations
            (conversation_type, created_by_user_id, owner_user_id, business_id, title,
             status, is_public, member_count, last_message_at, last_activity_at,
             created_at, updated_at)
            VALUES ('business', ?, ?, ?, ?, 'active', 0, 2, ?, ?, ?, ?)
            """,
            (actor, owner, str(business_id), title, now, now, now, now),
        )
        conversation_id = int(cur.lastrowid)
        # Customer participant + business-owner participant (business side).
        for uid, role in ((customer, "member"), (owner, "owner")):
            conn.execute(
                "INSERT INTO pulse_conversation_participants "
                "(conversation_id, user_id, role, muted, archived, joined_at, "
                "created_at) VALUES (?, ?, ?, 0, 0, ?, ?)",
                (conversation_id, uid, role, now, now),
            )
        conn.commit()
        conv = _get_conversation(conn, conversation_id)
        return _thread_public(conn, conv, actor_user_id)
    finally:
        conn.close()


def send_message(conversation_id: Any, sender_user_id: Any, body: Any, *,
                 message_type: str = "text", media_url: Optional[str] = None,
                 reply_to_id: Any = 0, client_message_id: Optional[str] = None,
                 context: Optional[dict] = None) -> dict:
    """Append a message to a business thread through the canonical engine."""
    _require_enabled()
    _require_not_held(context)
    body = _clean_body(body)
    media_url = (media_url or "").strip()[:1000] if isinstance(media_url, str) else ""
    message_type = (message_type or "text").strip()[:40]
    if message_type not in _ALLOWED_MSG_TYPES:
        message_type = "text"
    if not body and not media_url:
        raise MessageError("Write a message before sending.", 400, "invalid")
    try:
        reply_to_id = int(reply_to_id or 0)
    except (TypeError, ValueError):
        reply_to_id = 0
    cmid = (client_message_id or "").strip()[:120] if isinstance(client_message_id, str) else ""

    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        conv = _get_conversation(conn, conversation_id)
        if conv is None:
            raise MessageError("Conversation not found.", 404, "not_found")
        if _authorize_write(conn, conv, sender_user_id) is None:
            # Not a participant and not authorized business staff.
            raise MessageError("Conversation not found.", 404, "not_found")

        sender = _sid(sender_user_id)
        # Idempotency: a repeat client_message_id returns the original message.
        if cmid:
            prior = conn.execute(
                "SELECT * FROM pulse_messages WHERE conversation_id = ? "
                "AND sender_user_id = ? AND client_message_id = ? "
                "AND COALESCE(deleted_at,'') = '' ORDER BY id DESC LIMIT 1",
                (int(conv["id"]), sender, cmid),
            ).fetchone()
            if prior:
                out = _msg_public(prior, sender_user_id)
                out["idempotent"] = True
                return out

        now = _now()
        cur = conn.execute(
            """
            INSERT INTO pulse_messages
            (thread_id, conversation_id, sender_user_id, receiver_user_id, body,
             message_type, media_url, client_message_id, reply_to_id, delivery_status,
             status, created_at, updated_at)
            VALUES (0, ?, ?, 0, ?, ?, ?, ?, ?, 'sent', 'sent', ?, ?)
            """,
            (int(conv["id"]), sender, body, message_type, media_url, cmid,
             reply_to_id or None, now, now),
        )
        message_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE pulse_conversations SET updated_at = ?, last_message_at = ?, "
            "last_activity_at = ? WHERE id = ?",
            (now, now, now, int(conv["id"])),
        )
        # Canonical unread semantics: bump everyone but the sender; zero the sender.
        conn.execute(
            "UPDATE pulse_conversation_participants "
            "SET unread_count = COALESCE(unread_count,0) + 1 "
            "WHERE conversation_id = ? AND user_id != ? AND COALESCE(left_at,'') = ''",
            (int(conv["id"]), sender),
        )
        conn.execute(
            "UPDATE pulse_conversation_participants "
            "SET last_read_at = ?, last_read_message_id = ?, unread_count = 0 "
            "WHERE conversation_id = ? AND user_id = ?",
            (now, message_id, int(conv["id"]), sender),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pulse_messages WHERE id = ? LIMIT 1",
                           (message_id,)).fetchone()
        out = _msg_public(row, sender_user_id)
        out["idempotent"] = False
        return out
    finally:
        conn.close()


def get_thread(conversation_id: Any, requester_user_id: Any) -> Optional[dict]:
    """Thread metadata, ownership-scoped. None if the caller may not see it."""
    _require_enabled()
    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        conv = _get_conversation(conn, conversation_id)
        if conv is None or _authorize_read(conn, conv, requester_user_id) is None:
            return None
        return _thread_public(conn, conv, requester_user_id)
    finally:
        conn.close()


def list_thread_messages(conversation_id: Any, requester_user_id: Any, *,
                         limit: int = 50, before_id: Any = None) -> Optional[list]:
    """Ascending message list for a thread, ownership-scoped. None if unauthorized."""
    _require_enabled()
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        limit = 50
    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        conv = _get_conversation(conn, conversation_id)
        if conv is None or _authorize_read(conn, conv, requester_user_id) is None:
            return None
        params = [int(conv["id"])]
        sql = ("SELECT * FROM pulse_messages WHERE conversation_id = ? "
               "AND COALESCE(deleted_at,'') = ''")
        if before_id:
            sql += " AND id < ?"
            params.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        msgs = [_msg_public(r, requester_user_id) for r in rows]
        msgs.reverse()  # ascending for display
        return msgs
    finally:
        conn.close()


def list_business_inbox(business_id: Any, actor_user_id: Any, *,
                        limit: int = 100) -> list:
    """All business threads, newest activity first. Requires S1 read membership."""
    _require_enabled()
    try:
        limit = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        role = _business_role(conn, business_id, actor_user_id)
        if role is None:
            raise MessageError("Business not found.", 404, "not_found")
        if biz_svc._role_rank(role) > biz_svc._role_rank(READ_ROLE):
            raise MessageError(
                f"Your role ({role}) cannot read the inbox.", 403, "forbidden")
        rows = conn.execute(
            "SELECT * FROM pulse_conversations WHERE conversation_type = 'business' "
            "AND business_id = ? AND COALESCE(status,'active') = 'active' "
            "ORDER BY COALESCE(last_message_at, created_at) DESC LIMIT ?",
            (str(business_id), limit),
        ).fetchall()
        return [_thread_public(conn, _row(r), actor_user_id) for r in rows]
    finally:
        conn.close()


def list_customer_threads(customer_user_id: Any, *, limit: int = 100) -> list:
    """All business threads the caller participates in as a customer."""
    _require_enabled()
    try:
        limit = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT c.* FROM pulse_conversations c
            JOIN pulse_conversation_participants p
              ON p.conversation_id = c.id AND p.user_id = ? AND COALESCE(p.left_at,'')=''
            WHERE c.conversation_type = 'business'
              AND COALESCE(c.status,'active') = 'active'
            ORDER BY COALESCE(c.last_message_at, c.created_at) DESC LIMIT ?
            """,
            (_sid(customer_user_id), limit),
        ).fetchall()
        return [_thread_public(conn, _row(r), customer_user_id) for r in rows]
    finally:
        conn.close()


def mark_read(conversation_id: Any, user_id: Any) -> dict:
    """Reset the caller's unread counter for a thread. 404 if not authorized."""
    _require_enabled()
    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        conv = _get_conversation(conn, conversation_id)
        if conv is None or _authorize_read(conn, conv, user_id) is None:
            raise MessageError("Conversation not found.", 404, "not_found")
        now = _now()
        last = conn.execute(
            "SELECT id FROM pulse_messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT 1", (int(conv["id"]),)).fetchone()
        last_id = int((_row(last) or {}).get("id") or 0)
        conn.execute(
            "UPDATE pulse_conversation_participants "
            "SET unread_count = 0, last_read_at = ?, last_read_message_id = ? "
            "WHERE conversation_id = ? AND user_id = ?",
            (now, last_id, int(conv["id"]), _sid(user_id)),
        )
        conn.commit()
        return {"conversation_id": int(conv["id"]), "unread_count": 0,
                "last_read_message_id": last_id}
    finally:
        conn.close()


def report_message(conversation_id: Any, message_id: Any, reporter_user_id: Any, *,
                   reason: str) -> dict:
    """File a moderation report against a message in a thread the caller can see."""
    _require_enabled()
    reason = (reason or "").strip()[:500] if isinstance(reason, str) else ""
    if not reason:
        raise MessageError("reason is required.", 400, "invalid")
    conn = db.connect()
    try:
        _schema.ensure_schema(conn)
        conv = _get_conversation(conn, conversation_id)
        if conv is None or _authorize_read(conn, conv, reporter_user_id) is None:
            raise MessageError("Conversation not found.", 404, "not_found")
        msg = conn.execute(
            "SELECT id FROM pulse_messages WHERE id = ? AND conversation_id = ? LIMIT 1",
            (int(message_id or 0), int(conv["id"]))).fetchone()
        if msg is None:
            raise MessageError("Message not found.", 404, "not_found")
        now = _now()
        cur = conn.execute(
            "INSERT INTO pulse_message_reports "
            "(message_id, conversation_id, reporter_user_id, reason, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (int(message_id), int(conv["id"]), _sid(reporter_user_id), reason, now, now),
        )
        conn.commit()
        return {"report_id": int(cur.lastrowid), "status": "open",
                "message_id": int(message_id)}
    finally:
        conn.close()
