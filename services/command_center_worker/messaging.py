"""Real-time messaging event foundation for the Command Center worker."""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services import db as db_service
from .redis_manager import safe_delete, safe_get, safe_scan, safe_set


LOGGER = logging.getLogger(__name__)
VALID_EVENT_TYPES = {
    "message_created",
    "message_delivered",
    "message_read",
    "message_edited",
    "message_deleted",
    "reaction_added",
    "reaction_removed",
    "typing_started",
    "typing_stopped",
}
TYPING_TTL_SECONDS = 5
MAX_PAYLOAD_BYTES = 12_000
UNREAD_CACHE_TTL_SECONDS = 10 * 60
_typing_rate_lock = threading.Lock()
_typing_rate: dict[str, float] = {}


class MessagingValidationError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _positive_int(value: Any, field: str, *, required: bool = True) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if required and parsed <= 0:
        raise MessagingValidationError(f"invalid_{field}")
    return max(0, parsed)


def _clean_event_type(value: Any) -> str:
    event_type = str(value or "").strip().lower()
    if event_type not in VALID_EVENT_TYPES:
        raise MessagingValidationError("invalid_event_type")
    return event_type


def _sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        output = {}
        for key, item in list(value.items())[:60]:
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "", str(key or ""))[:80]
            if not safe_key or any(secret_key in safe_key.lower() for secret_key in ("token", "secret", "password", "credential")):
                continue
            output[safe_key] = _sanitize_payload(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, depth + 1) for item in list(value)[:60]]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))[:2000]


def _payload_json(payload: dict | None) -> str:
    serialized = json.dumps(_sanitize_payload(payload or {}), separators=(",", ":"), ensure_ascii=True)
    return serialized[:MAX_PAYLOAD_BYTES]


def _open_db():
    conn = db_service.connect()
    cur = conn.cursor()
    ensure_messaging_schema(cur, conn)
    return conn, cur


def ensure_messaging_schema(cur=None, conn=None) -> bool:
    own_connection = cur is None
    if own_connection:
        conn = db_service.connect()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS command_center_message_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                conversation_id INTEGER NOT NULL,
                message_id INTEGER DEFAULT 0,
                sender_id INTEGER DEFAULT 0,
                recipient_id INTEGER,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                status TEXT DEFAULT 'received',
                created_at TEXT,
                processed_at TEXT
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_cc_message_events_conversation ON command_center_message_events(conversation_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_message_events_recipient ON command_center_message_events(recipient_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_message_events_type ON command_center_message_events(event_type, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_message_events_created ON command_center_message_events(created_at)",
        ):
            cur.execute(statement)
        if own_connection:
            conn.commit()
        return True
    finally:
        if own_connection:
            conn.close()


def accept_message_event(
    event_type: str,
    conversation_id: int,
    message_id: int = 0,
    sender_id: int = 0,
    recipient_id: int | None = None,
    payload: dict | None = None,
    event_id: str = "",
) -> dict:
    normalized_type = _clean_event_type(event_type)
    normalized_conversation_id = _positive_int(conversation_id, "conversation_id")
    normalized_message_id = _positive_int(message_id, "message_id", required=False)
    normalized_sender_id = _positive_int(sender_id, "sender_id", required=False)
    normalized_recipient_id = _positive_int(recipient_id, "recipient_id", required=False) if recipient_id is not None else None
    if normalized_type.startswith("message_") and normalized_type != "message_created" and normalized_message_id <= 0:
        raise MessagingValidationError("invalid_message_id")
    normalized_event_id = re.sub(r"[^a-zA-Z0-9_.:-]", "", str(event_id or ""))[:160] or f"msg_evt_{secrets.token_urlsafe(18)}"
    now = iso_now()
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            INSERT OR IGNORE INTO command_center_message_events
            (event_id, conversation_id, message_id, sender_id, recipient_id, event_type, payload_json, status, created_at, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)
            """,
            (
                normalized_event_id,
                normalized_conversation_id,
                normalized_message_id,
                normalized_sender_id,
                normalized_recipient_id,
                normalized_type,
                _payload_json(payload),
                now,
                now,
            ),
        )
        conn.commit()
        if normalized_type == "message_created" and normalized_recipient_id:
            safe_delete(f"unread:user:{normalized_recipient_id}")
        if normalized_type == "message_read" and normalized_recipient_id:
            safe_delete(f"unread:user:{normalized_recipient_id}")
        return {
            "accepted": True,
            "event_id": normalized_event_id,
            "event_type": normalized_type,
            "conversation_id": normalized_conversation_id,
            "message_id": normalized_message_id,
            "status": "received",
            "created_at": now,
        }
    finally:
        conn.close()


def mark_delivered(conversation_id: int, message_id: int, recipient_id: int, sender_id: int = 0) -> dict:
    return accept_message_event(
        "message_delivered",
        conversation_id,
        message_id=message_id,
        sender_id=sender_id,
        recipient_id=recipient_id,
        payload={"delivered_at": iso_now()},
    )


def mark_read(conversation_id: int, message_id: int, recipient_id: int, sender_id: int = 0) -> dict:
    return accept_message_event(
        "message_read",
        conversation_id,
        message_id=message_id,
        sender_id=sender_id,
        recipient_id=recipient_id,
        payload={"read_at": iso_now()},
    )


def _typing_rate_allowed(conversation_id: int, sender_id: int) -> bool:
    key = f"{int(conversation_id)}:{int(sender_id)}"
    now = time.monotonic()
    with _typing_rate_lock:
        previous = _typing_rate.get(key, 0.0)
        _typing_rate[key] = now
        stale = [item for item, stamp in _typing_rate.items() if now - stamp > 30]
        for item in stale:
            _typing_rate.pop(item, None)
    return now - previous >= 0.25


def set_typing(conversation_id: int, sender_id: int, payload: dict | None = None) -> dict:
    conversation_id = _positive_int(conversation_id, "conversation_id")
    sender_id = _positive_int(sender_id, "sender_id")
    if not _typing_rate_allowed(conversation_id, sender_id):
        return {"accepted": True, "status": "rate_limited", "conversation_id": conversation_id, "sender_id": sender_id}
    safe_set(
        f"typing:{conversation_id}:{sender_id}",
        {
            "conversation_id": conversation_id,
            "user_id": sender_id,
            "sender_id": sender_id,
            "is_typing": True,
            "updated_at": iso_now(),
        },
        ttl_seconds=TYPING_TTL_SECONDS,
    )
    return accept_message_event(
        "typing_started",
        conversation_id,
        sender_id=sender_id,
        payload={**(payload or {}), "expires_at": (utc_now() + timedelta(seconds=TYPING_TTL_SECONDS)).isoformat(timespec="seconds").replace("+00:00", "Z")},
    )


def clear_typing(conversation_id: int, sender_id: int, payload: dict | None = None) -> dict:
    safe_delete(f"typing:{int(conversation_id or 0)}:{int(sender_id or 0)}")
    return accept_message_event("typing_stopped", conversation_id, sender_id=sender_id, payload=payload or {})


def get_unread_counts(user_id: int) -> dict:
    normalized_user_id = _positive_int(user_id, "user_id")
    cached = safe_get(f"unread:user:{normalized_user_id}")
    if isinstance(cached, dict):
        cached.setdefault("user_id", normalized_user_id)
        cached.setdefault("source", "redis")
        return cached
    conn = db_service.connect()
    cur = conn.cursor()
    try:
        try:
            cur.execute(
                """
                SELECT conversation_id, COALESCE(unread_count,0) AS unread_count
                FROM comm_v2_participants
                WHERE user_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
                  AND COALESCE(unread_count,0)>0
                ORDER BY unread_count DESC, conversation_id DESC
                LIMIT 200
                """,
                (normalized_user_id,),
            )
            conversations = [
                {"conversation_id": int(row["conversation_id"]), "unread_count": int(row["unread_count"] or 0)}
                for row in cur.fetchall()
            ]
        except Exception:
            conversations = []
        result = {
            "user_id": normalized_user_id,
            "total_unread": sum(item["unread_count"] for item in conversations),
            "conversations": conversations,
            "source": "comm_v2_participants",
        }
        safe_set(f"unread:user:{normalized_user_id}", result, ttl_seconds=UNREAD_CACHE_TTL_SECONDS)
        return result
    finally:
        conn.close()


def _latest_typing(cur, conversation_id: int) -> list[dict]:
    redis_typing = []
    for key in safe_scan(f"typing:{int(conversation_id)}:*", limit=120):
        cached = safe_get(key)
        if isinstance(cached, dict) and int(cached.get("user_id") or cached.get("sender_id") or 0) > 0:
            redis_typing.append(
                {
                    "user_id": int(cached.get("user_id") or cached.get("sender_id") or 0),
                    "is_typing": True,
                    "expires_in_seconds": TYPING_TTL_SECONDS,
                    "source": "redis",
                }
            )
    if redis_typing:
        return redis_typing
    cutoff = (utc_now() - timedelta(seconds=TYPING_TTL_SECONDS)).isoformat(timespec="seconds").replace("+00:00", "Z")
    cur.execute(
        """
        SELECT sender_id, event_type, created_at
        FROM command_center_message_events
        WHERE conversation_id=? AND event_type IN ('typing_started','typing_stopped') AND created_at>=?
        ORDER BY id DESC LIMIT 80
        """,
        (conversation_id, cutoff),
    )
    latest: dict[int, dict] = {}
    for row in cur.fetchall():
        sender_id = int(row["sender_id"] or 0)
        if sender_id and sender_id not in latest:
            latest[sender_id] = dict(row)
    return [
        {"user_id": sender_id, "is_typing": True, "expires_in_seconds": TYPING_TTL_SECONDS}
        for sender_id, item in latest.items()
        if item.get("event_type") == "typing_started"
    ]


def get_conversation_state(conversation_id: int, viewer_user_id: int = 0) -> dict:
    normalized_conversation_id = _positive_int(conversation_id, "conversation_id")
    normalized_viewer_id = _positive_int(viewer_user_id, "viewer_user_id", required=False)
    conn, cur = _open_db()
    try:
        if normalized_viewer_id:
            try:
                cur.execute(
                    """
                    SELECT 1 FROM comm_v2_participants
                    WHERE conversation_id=? AND user_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
                    LIMIT 1
                    """,
                    (normalized_conversation_id, normalized_viewer_id),
                )
                if not cur.fetchone():
                    raise PermissionError("conversation_access_denied")
            except PermissionError:
                raise
            except Exception:
                raise PermissionError("conversation_access_unavailable")
        cur.execute(
            """
            SELECT event_id, event_type, message_id, sender_id, recipient_id, status, created_at
            FROM command_center_message_events
            WHERE conversation_id=?
            ORDER BY id DESC LIMIT 80
            """,
            (normalized_conversation_id,),
        )
        events = [dict(row) for row in cur.fetchall()]
        counts: dict[str, int] = {}
        for event in events:
            counts[event.get("event_type") or "unknown"] = counts.get(event.get("event_type") or "unknown", 0) + 1
        latest_message_id = max((int(event.get("message_id") or 0) for event in events), default=0)
        return {
            "conversation_id": normalized_conversation_id,
            "latest_event_id": events[0].get("event_id") if events else "",
            "latest_message_id": latest_message_id,
            "event_counts": counts,
            "typing": _latest_typing(cur, normalized_conversation_id),
            "updated_at": events[0].get("created_at") if events else "",
        }
    finally:
        conn.close()
