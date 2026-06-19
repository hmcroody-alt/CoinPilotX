"""In-app notification event pipeline for the Command Center worker."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from services import db as db_service


ALLOWED_CHANNELS = {"in_app", "push", "email", "sms"}
EXTERNAL_CHANNELS = {"push", "email", "sms"}
MESSAGE_TYPES = {"message", "chat_message", "voice_message", "group_message", "room_message"}
MAX_PAYLOAD_BYTES = 12_000


class NotificationValidationError(ValueError):
    pass


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _positive_int(value: Any, field: str, *, required: bool = True) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if required and parsed <= 0:
        raise NotificationValidationError(f"invalid_{field}")
    return max(0, parsed)


def _clean_text(value: Any, limit: int) -> str:
    text_value = re.sub(r"<[^>]*>", "", str(value or ""))
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text_value).strip()[:limit]


def _sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        output = {}
        for key, item in list(value.items())[:60]:
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "", str(key or ""))[:80]
            if not safe_key or any(word in safe_key.lower() for word in ("token", "secret", "password", "credential")):
                continue
            output[safe_key] = _sanitize_payload(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, depth + 1) for item in list(value)[:60]]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _clean_text(value, 2000)


def _payload_json(payload: dict | None) -> str:
    return json.dumps(_sanitize_payload(payload or {}), separators=(",", ":"), ensure_ascii=True)[:MAX_PAYLOAD_BYTES]


def _open_db():
    conn = db_service.connect()
    cur = conn.cursor()
    ensure_notification_schema(cur, conn)
    return conn, cur


def ensure_notification_schema(cur=None, conn=None) -> bool:
    own_connection = cur is None
    if own_connection:
        conn = db_service.connect()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS command_center_notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                recipient_id INTEGER NOT NULL,
                actor_id INTEGER,
                notification_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                payload_json TEXT,
                channel TEXT NOT NULL DEFAULT 'in_app',
                status TEXT NOT NULL DEFAULT 'pending',
                read_at TEXT,
                delivered_at TEXT,
                created_at TEXT,
                processed_at TEXT
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_cc_notification_recipient ON command_center_notification_events(recipient_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_notification_type ON command_center_notification_events(notification_type, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_notification_status ON command_center_notification_events(status, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_notification_created ON command_center_notification_events(created_at)",
        ):
            cur.execute(statement)
        if own_connection:
            conn.commit()
        return True
    finally:
        if own_connection:
            conn.close()


def accept_notification_event(
    recipient_id: int,
    notification_type: str,
    title: str,
    body: str = "",
    actor_id: int | None = None,
    payload: dict | None = None,
    channel: str = "in_app",
    event_id: str = "",
) -> dict:
    recipient_id = _positive_int(recipient_id, "recipient_id")
    actor_id = _positive_int(actor_id, "actor_id", required=False) if actor_id is not None else None
    notification_type = _clean_text(notification_type, 80).lower().replace(" ", "_")
    if not notification_type:
        raise NotificationValidationError("invalid_notification_type")
    if notification_type in MESSAGE_TYPES:
        raise NotificationValidationError("message_notification_not_allowed")
    title = _clean_text(title, 180)
    if not title:
        raise NotificationValidationError("invalid_title")
    body = _clean_text(body, 2000)
    channel = _clean_text(channel or "in_app", 30).lower()
    if channel not in ALLOWED_CHANNELS:
        raise NotificationValidationError("invalid_channel")
    normalized_event_id = re.sub(r"[^a-zA-Z0-9_.:-]", "", str(event_id or ""))[:160] or f"note_evt_{secrets.token_urlsafe(18)}"
    now = iso_now()
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            INSERT OR IGNORE INTO command_center_notification_events
            (event_id, recipient_id, actor_id, notification_type, title, body, payload_json, channel, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (normalized_event_id, recipient_id, actor_id, notification_type, title, body, _payload_json(payload), channel, now),
        )
        conn.commit()
        return {
            "accepted": True,
            "event_id": normalized_event_id,
            "recipient_id": recipient_id,
            "notification_type": notification_type,
            "channel": channel,
            "status": "pending",
            "created_at": now,
        }
    finally:
        conn.close()


def mark_delivered(event_id: str, recipient_id: int) -> dict:
    recipient_id = _positive_int(recipient_id, "recipient_id")
    event_id = _clean_text(event_id, 160)
    if not event_id:
        raise NotificationValidationError("invalid_event_id")
    now = iso_now()
    conn, cur = _open_db()
    try:
        cur.execute(
            "UPDATE command_center_notification_events SET status='delivered', delivered_at=?, processed_at=? WHERE event_id=? AND recipient_id=?",
            (now, now, event_id, recipient_id),
        )
        changed = int(cur.rowcount or 0)
        conn.commit()
        return {"ok": True, "updated": changed, "event_id": event_id, "status": "delivered" if changed else "not_found"}
    finally:
        conn.close()


def mark_read(recipient_id: int, event_id: str = "", mark_all: bool = False) -> dict:
    recipient_id = _positive_int(recipient_id, "recipient_id")
    event_id = _clean_text(event_id, 160)
    if not mark_all and not event_id:
        raise NotificationValidationError("invalid_event_id")
    now = iso_now()
    conn, cur = _open_db()
    try:
        if mark_all:
            cur.execute(
                "UPDATE command_center_notification_events SET status='read', read_at=?, processed_at=COALESCE(processed_at, ?) WHERE recipient_id=? AND read_at IS NULL",
                (now, now, recipient_id),
            )
        else:
            cur.execute(
                "UPDATE command_center_notification_events SET status='read', read_at=?, processed_at=COALESCE(processed_at, ?) WHERE recipient_id=? AND event_id=?",
                (now, now, recipient_id, event_id),
            )
        changed = int(cur.rowcount or 0)
        conn.commit()
        return {"ok": True, "updated": changed, "recipient_id": recipient_id, "event_id": event_id, "status": "read"}
    finally:
        conn.close()


def get_unread_count(recipient_id: int) -> dict:
    recipient_id = _positive_int(recipient_id, "recipient_id")
    conn, cur = _open_db()
    try:
        cur.execute(
            "SELECT COUNT(*) AS total FROM command_center_notification_events WHERE recipient_id=? AND read_at IS NULL AND status NOT IN ('failed','discarded')",
            (recipient_id,),
        )
        row = cur.fetchone()
        count = int((row["total"] if hasattr(row, "keys") else row[0]) or 0)
        return {"recipient_id": recipient_id, "alert_unread_count": count, "unread_count": count, "count": count}
    finally:
        conn.close()


def get_recent_notifications(recipient_id: int, limit: int = 50) -> dict:
    recipient_id = _positive_int(recipient_id, "recipient_id")
    limit = max(1, min(int(limit or 50), 100))
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            SELECT event_id, recipient_id, actor_id, notification_type, title, body, channel, status,
                   read_at, delivered_at, created_at, processed_at
            FROM command_center_notification_events
            WHERE recipient_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (recipient_id, limit),
        )
        items = [dict(row) for row in cur.fetchall()]
        return {"recipient_id": recipient_id, "notifications": items, "items": items}
    finally:
        conn.close()


def process_pending_notifications(limit: int = 50) -> dict:
    limit = max(1, min(int(limit or 50), 200))
    now = iso_now()
    conn, cur = _open_db()
    try:
        cur.execute(
            "SELECT event_id, recipient_id, channel FROM command_center_notification_events WHERE status='pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        delivered = 0
        external_deferred = 0
        for item in rows:
            if item.get("channel") in EXTERNAL_CHANNELS:
                external_deferred += 1
                continue
            cur.execute(
                "UPDATE command_center_notification_events SET status='delivered', delivered_at=?, processed_at=? WHERE event_id=? AND recipient_id=?",
                (now, now, item.get("event_id"), int(item.get("recipient_id") or 0)),
            )
            delivered += int(cur.rowcount or 0)
        conn.commit()
        return {"ok": True, "processed": delivered, "external_deferred": external_deferred, "examined": len(rows)}
    finally:
        conn.close()
