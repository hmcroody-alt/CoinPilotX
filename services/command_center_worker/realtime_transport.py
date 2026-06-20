"""Realtime transport foundation for the PulseSoc Command Center worker.

This module is intentionally dependency-light. It keeps an in-process replay
buffer and connection registry today, while preserving the API shape needed for
Redis/WebSocket fanout later.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from services import db as db_service
from .redis_manager import safe_delete, safe_get, safe_publish, safe_rate_limit, safe_scan, safe_set


VALID_EVENT_TYPES = {
    "presence_updated",
    "message_created",
    "message_delivered",
    "message_read",
    "typing_started",
    "typing_stopped",
    "unread_count_updated",
    "notification_created",
    "security_alert_created",
}
CONVERSATION_EVENT_TYPES = {
    "message_created",
    "message_delivered",
    "message_read",
    "typing_started",
    "typing_stopped",
    "unread_count_updated",
}
NOISY_EVENT_TYPES = {"typing_started", "typing_stopped"}
MAX_EVENTS = 1000
MAX_PAYLOAD_BYTES = 12_000
CONNECTION_TTL_SECONDS = 180
TYPING_RATE_SECONDS = 0.7
EVENT_CACHE_TTL_SECONDS = 10 * 60

_lock = threading.RLock()
_event_id = 0
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_connections: dict[str, dict[str, Any]] = {}
_metrics: Counter[str] = Counter()
_last_noisy_event: dict[str, float] = {}


class RealtimeValidationError(ValueError):
    pass


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _positive_int(value: Any, field: str, *, required: bool = True) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if required and parsed <= 0:
        raise RealtimeValidationError(f"invalid_{field}")
    return max(0, parsed)


def _clean_text(value: Any, limit: int = 160) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()[:limit]


def _clean_event_type(value: Any) -> str:
    event_type = _clean_text(value, 80).lower().replace(" ", "_")
    if event_type not in VALID_EVENT_TYPES:
        raise RealtimeValidationError("invalid_event_type")
    return event_type


def _sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        output = {}
        for key, item in list(value.items())[:80]:
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "", str(key or ""))[:80]
            if not safe_key or any(word in safe_key.lower() for word in ("token", "secret", "password", "credential")):
                continue
            output[safe_key] = _sanitize_payload(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, depth + 1) for item in list(value)[:80]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean_text(value, 2000)


def _compact_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    clean = _sanitize_payload(payload or {})
    if not isinstance(clean, dict):
        clean = {"value": clean}
    try:
        encoded = json.dumps(clean, separators=(",", ":"), ensure_ascii=True, default=str)
    except Exception:
        return {"payload_compacted": True}
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return clean
    compact = {
        "payload_compacted": True,
        "conversation_id": clean.get("conversation_id"),
        "message_id": clean.get("message_id") or clean.get("id"),
        "user_id": clean.get("user_id"),
    }
    return {key: value for key, value in compact.items() if value not in {None, ""}}


def _connection_key(user_id: int, session_id: str) -> str:
    return f"{int(user_id)}:{_clean_text(session_id or 'session', 120)}"


def _conversation_participants(conversation_id: int) -> set[int]:
    if conversation_id <= 0:
        return set()
    participants: set[int] = set()
    conn = db_service.connect()
    cur = conn.cursor()
    try:
        try:
            cur.execute(
                """
                SELECT user_id FROM comm_v2_participants
                WHERE conversation_id=? AND COALESCE(left_at,'')='' AND COALESCE(membership_state,'active')='active'
                """,
                (conversation_id,),
            )
            participants.update(int(row["user_id"] if hasattr(row, "keys") else row[0]) for row in cur.fetchall())
        except Exception:
            pass
        try:
            cur.execute(
                """
                SELECT user_id FROM pulse_conversation_participants
                WHERE conversation_id=? AND COALESCE(left_at,'')=''
                """,
                (conversation_id,),
            )
            participants.update(int(row["user_id"] if hasattr(row, "keys") else row[0]) for row in cur.fetchall())
        except Exception:
            pass
    finally:
        conn.close()
    return {item for item in participants if item > 0}


def _is_conversation_participant(user_id: int, conversation_id: int) -> bool:
    return user_id in _conversation_participants(conversation_id)


def _normalize_recipient_ids(recipient_ids: Any, event_type: str, conversation_id: int, actor_id: int) -> list[int]:
    recipients: set[int] = set()
    raw_values = recipient_ids if isinstance(recipient_ids, (list, tuple, set)) else [recipient_ids]
    for value in raw_values:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            recipients.add(parsed)
    if event_type in CONVERSATION_EVENT_TYPES and conversation_id > 0:
        recipients.update(_conversation_participants(conversation_id))
    if actor_id > 0 and event_type in {"message_created", "typing_started", "typing_stopped"}:
        recipients.discard(actor_id)
    return sorted(recipients)


def _rate_allowed(event_type: str, user_id: int, conversation_id: int) -> bool:
    if event_type not in NOISY_EVENT_TYPES:
        return True
    redis_limit = safe_rate_limit(f"typing:{int(user_id)}:{int(conversation_id)}", limit=1, window_seconds=max(1, int(TYPING_RATE_SECONDS)))
    if redis_limit.get("redis"):
        return bool(redis_limit.get("allowed"))
    key = f"{event_type}:{int(user_id)}:{int(conversation_id)}"
    now = time.monotonic()
    with _lock:
        previous = _last_noisy_event.get(key, 0.0)
        _last_noisy_event[key] = now
        for item, stamp in list(_last_noisy_event.items()):
            if now - stamp > 60:
                _last_noisy_event.pop(item, None)
    return now - previous >= TYPING_RATE_SECONDS


def cleanup_stale_connections(max_age_seconds: int = CONNECTION_TTL_SECONDS) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    removed = 0
    with _lock:
        for key, item in list(_connections.items()):
            last_seen = item.get("last_seen_dt")
            if not isinstance(last_seen, datetime) or last_seen < cutoff:
                _connections.pop(key, None)
                removed += 1
    for key in safe_scan("connection:*", limit=1000):
        value = safe_get(key)
        if not isinstance(value, dict):
            continue
        last_seen = value.get("last_seen") or value.get("connected_at") or ""
        try:
            parsed = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except Exception:
            parsed = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds + 1)
        if parsed < cutoff:
            safe_delete(key)
            removed += 1
    return removed


def connect_user(user_id: Any, session_id: str = "", device_type: str = "", subscribed_conversations: list[Any] | None = None) -> dict:
    normalized_user_id = _positive_int(user_id, "user_id")
    normalized_session_id = _clean_text(session_id or secrets.token_urlsafe(12), 120)
    requested_conversations = []
    for item in subscribed_conversations or []:
        conversation_id = _positive_int(item, "conversation_id", required=False)
        if conversation_id and _is_conversation_participant(normalized_user_id, conversation_id):
            requested_conversations.append(conversation_id)
    now = datetime.now(timezone.utc)
    key = _connection_key(normalized_user_id, normalized_session_id)
    with _lock:
        existing = _connections.get(key) or {}
        subscribed = sorted(set(existing.get("subscribed_conversations") or []) | set(requested_conversations))
        _connections[key] = {
            "user_id": normalized_user_id,
            "session_id": normalized_session_id,
            "device_type": _clean_text(device_type or "web", 40),
            "connected_at": existing.get("connected_at") or iso_now(),
            "last_seen": iso_now(),
            "last_seen_dt": now,
            "subscribed_conversations": subscribed,
        }
        _metrics["connections_total"] += 1
    safe_set(
        f"connection:{normalized_user_id}:{normalized_session_id}",
        {
            "user_id": normalized_user_id,
            "session_id": normalized_session_id,
            "device": _clean_text(device_type or "web", 40),
            "device_type": _clean_text(device_type or "web", 40),
            "transport": "sse",
            "connected_at": _connections[key]["connected_at"],
            "last_seen": _connections[key]["last_seen"],
            "subscriptions": subscribed,
        },
        ttl_seconds=CONNECTION_TTL_SECONDS,
    )
    return {"ok": True, "connected": True, "user_id": normalized_user_id, "session_id": normalized_session_id, "subscribed_conversations": subscribed}


def disconnect_user(user_id: Any, session_id: str = "") -> dict:
    normalized_user_id = _positive_int(user_id, "user_id")
    normalized_session_id = _clean_text(session_id or "session", 120)
    with _lock:
        removed = _connections.pop(_connection_key(normalized_user_id, normalized_session_id), None)
        if removed:
            _metrics["disconnects_total"] += 1
    safe_delete(f"connection:{normalized_user_id}:{normalized_session_id}")
    return {"ok": True, "disconnected": bool(removed)}


def subscribe_conversation(user_id: Any, session_id: str, conversation_id: Any) -> dict:
    normalized_user_id = _positive_int(user_id, "user_id")
    normalized_conversation_id = _positive_int(conversation_id, "conversation_id")
    if not _is_conversation_participant(normalized_user_id, normalized_conversation_id):
        raise PermissionError("conversation_access_denied")
    key = _connection_key(normalized_user_id, session_id or "session")
    with _lock:
        item = _connections.get(key)
        if not item:
            connect_user(normalized_user_id, session_id=session_id, subscribed_conversations=[normalized_conversation_id])
        else:
            subscribed = set(item.get("subscribed_conversations") or [])
            subscribed.add(normalized_conversation_id)
            item["subscribed_conversations"] = sorted(subscribed)
            item["last_seen"] = iso_now()
            item["last_seen_dt"] = datetime.now(timezone.utc)
    safe_set(
        f"connection:{normalized_user_id}:{_clean_text(session_id or 'session', 120)}",
        {
            "user_id": normalized_user_id,
            "session_id": _clean_text(session_id or "session", 120),
            "transport": "sse",
            "last_seen": iso_now(),
            "subscriptions": sorted(set((safe_get(f"connection:{normalized_user_id}:{_clean_text(session_id or 'session', 120)}") or {}).get("subscriptions") or []) | {normalized_conversation_id}),
        },
        ttl_seconds=CONNECTION_TTL_SECONDS,
    )
    return {"ok": True, "subscribed": True, "conversation_id": normalized_conversation_id}


def publish_event(
    event_type: Any,
    payload: dict[str, Any] | None = None,
    *,
    recipient_ids: list[Any] | None = None,
    conversation_id: Any = 0,
    actor_id: Any = 0,
    event_id: str = "",
) -> dict:
    global _event_id
    normalized_type = _clean_event_type(event_type)
    normalized_conversation_id = _positive_int(conversation_id or (payload or {}).get("conversation_id"), "conversation_id", required=False)
    normalized_actor_id = _positive_int(actor_id or (payload or {}).get("sender_id") or (payload or {}).get("user_id"), "actor_id", required=False)
    if not _rate_allowed(normalized_type, normalized_actor_id, normalized_conversation_id):
        _metrics["rate_limited"] += 1
        return {"ok": True, "accepted": True, "rate_limited": True, "event_type": normalized_type}
    recipients = _normalize_recipient_ids(recipient_ids, normalized_type, normalized_conversation_id, normalized_actor_id)
    if normalized_type in CONVERSATION_EVENT_TYPES and normalized_conversation_id > 0 and not recipients:
        raise RealtimeValidationError("no_authorized_recipients")
    now = iso_now()
    with _lock:
        _event_id += 1
        event = {
            "id": _event_id,
            "event_id": _clean_text(event_id, 160) or f"rt_evt_{secrets.token_urlsafe(18)}",
            "event_type": normalized_type,
            "conversation_id": normalized_conversation_id,
            "actor_id": normalized_actor_id,
            "recipient_ids": recipients,
            "payload": _compact_payload(payload),
            "created_at": now,
        }
        _events.append(event)
        _metrics["events_published"] += 1
        _metrics[f"type:{normalized_type}"] += 1
    public = public_event(event)
    for recipient_id in recipients:
        safe_set(f"realtime:user:{recipient_id}:event:{event['id']}", public, ttl_seconds=EVENT_CACHE_TTL_SECONDS)
        safe_set(f"cc:user:{recipient_id}:event:{event['id']}", public, ttl_seconds=EVENT_CACHE_TTL_SECONDS)
        safe_publish(f"realtime:user:{recipient_id}", public)
        safe_publish(f"cc:user:{recipient_id}", public)
    if normalized_conversation_id:
        safe_publish(f"realtime:conversation:{normalized_conversation_id}", public)
        safe_publish(f"cc:conversation:{normalized_conversation_id}", public)
    return {"ok": True, "accepted": True, "event": public, "recipient_count": len(recipients)}


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(event.get("id") or 0),
        "event_id": event.get("event_id") or "",
        "event_type": event.get("event_type") or "",
        "conversation_id": int(event.get("conversation_id") or 0),
        "actor_id": int(event.get("actor_id") or 0),
        "payload": event.get("payload") if isinstance(event.get("payload"), dict) else {},
        "created_at": event.get("created_at") or "",
    }


def poll_user_events(user_id: Any, after_id: Any = 0, limit: Any = 80) -> dict:
    normalized_user_id = _positive_int(user_id, "user_id")
    normalized_after_id = _positive_int(after_id, "after_id", required=False)
    safe_limit = max(1, min(int(limit or 80), 200))
    redis_events = []
    for key in safe_scan(f"realtime:user:{normalized_user_id}:event:*", limit=500):
        item = safe_get(key)
        if isinstance(item, dict) and int(item.get("id") or 0) > normalized_after_id:
            redis_events.append(item)
    if redis_events:
        events = sorted(redis_events, key=lambda item: int(item.get("id") or 0))[-safe_limit:]
        return {
            "ok": True,
            "user_id": normalized_user_id,
            "events": events,
            "latest_event_id": max([int(event.get("id") or 0) for event in events] or [normalized_after_id]),
            "transport": "redis_sse_polling_fallback_ready",
        }
    with _lock:
        events = [
            public_event(event)
            for event in _events
            if int(event.get("id") or 0) > normalized_after_id and normalized_user_id in set(event.get("recipient_ids") or [])
        ][-safe_limit:]
    return {
        "ok": True,
        "user_id": normalized_user_id,
        "events": events,
        "latest_event_id": max([int(event.get("id") or 0) for event in events] or [normalized_after_id]),
        "transport": "sse_polling_fallback_ready",
    }


def status_snapshot() -> dict:
    cleanup_stale_connections()
    with _lock:
        connected_users = sorted({int(item.get("user_id") or 0) for item in _connections.values() if int(item.get("user_id") or 0) > 0})
        recent_events = [event for event in _events if str(event.get("created_at") or "") >= (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds").replace("+00:00", "Z")]
        subscribed = sum(len(item.get("subscribed_conversations") or []) for item in _connections.values())
        failed_sends = int(_metrics.get("failed_sends", 0))
        reconnect_count = int(_metrics.get("reconnects", 0))
    redis_connection_keys = safe_scan("connection:*", limit=2000)
    redis_event_keys = safe_scan("realtime:user:*:event:*", limit=2000)
    redis_connected_users = set()
    for key in redis_connection_keys:
        item = safe_get(key)
        if isinstance(item, dict) and int(item.get("user_id") or 0) > 0:
            redis_connected_users.add(int(item.get("user_id") or 0))
    return {
        "ok": True,
        "transport": "redis_sse_first_polling_fallback" if redis_connection_keys or redis_event_keys else "sse_first_polling_fallback",
        "active_connections": max(len(_connections), len(redis_connection_keys)),
        "connected_users": max(len(connected_users), len(redis_connected_users)),
        "subscribed_conversations": subscribed,
        "events_buffered": max(len(_events), len(redis_event_keys)),
        "events_per_minute": len(recent_events),
        "failed_sends": failed_sends,
        "reconnect_count": reconnect_count,
        "rate_limited_events": int(_metrics.get("rate_limited", 0)),
    }
