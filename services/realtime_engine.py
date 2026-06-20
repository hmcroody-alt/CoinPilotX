"""Reusable realtime foundation for PulseSoc social activity.

The web app already persists PulseSoc events for SSE/polling. This module gives
the rest of the platform one dependency-light interface that can later sit in
front of Redis, Socket.IO, or another websocket service without changing
feature code.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Condition, RLock
from typing import Any
import hashlib
import json
import time


MAX_EVENTS_PER_CHANNEL = 500
ONLINE_WINDOW_SECONDS = 120
COALESCE_WINDOW_SECONDS = 1.25
MAX_PAYLOAD_CHARS = 8000


@dataclass
class RealtimeEnvelope:
    id: int
    channel: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


_lock = RLock()
_event_condition = Condition(_lock)
_event_id = 0
_channels: dict[str, deque[RealtimeEnvelope]] = defaultdict(lambda: deque(maxlen=MAX_EVENTS_PER_CHANNEL))
_sessions: dict[str, dict[str, Any]] = {}
_failed_broadcasts = 0
_reconnect_count = 0
_coalesced_events = 0
_last_publish_by_key: dict[str, float] = {}


def _now() -> datetime:
    return datetime.utcnow()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _session_key(user_id: int | str = 0, session_id: str = "") -> str:
    return f"{int(user_id or 0)}:{str(session_id or 'anonymous')[:160]}"


def _compact_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep realtime envelopes small enough for mobile SSE without hiding data."""
    clean = dict(payload or {})
    try:
        encoded = json.dumps(clean, default=str)
    except Exception:
        return {"raw": str(clean)[:MAX_PAYLOAD_CHARS], "payload_compacted": True}
    if len(encoded) <= MAX_PAYLOAD_CHARS:
        return clean
    for key in ("html", "body", "content", "caption", "description", "media_metadata"):
        if key in clean and isinstance(clean.get(key), str):
            clean[key] = clean[key][:800]
    clean["payload_compacted"] = True
    encoded = json.dumps(clean, default=str)
    if len(encoded) > MAX_PAYLOAD_CHARS:
        digest = hashlib.sha1(encoded.encode("utf-8", "ignore")).hexdigest()[:12]
        return {
            "payload_compacted": True,
            "payload_digest": digest,
            "id": clean.get("id"),
            "message_id": clean.get("message_id"),
            "conversation_id": clean.get("conversation_id"),
            "post_id": clean.get("post_id"),
        }
    return clean


def _coalesce_key(channel: str, event_type: str, payload: dict[str, Any]) -> str:
    event_type = str(event_type or "")
    if not any(token in event_type for token in ("typing", "presence", "heartbeat", "online")):
        return ""
    identity = payload.get("user_id") or payload.get("actor_user_id") or payload.get("session_id") or ""
    scope = payload.get("conversation_id") or payload.get("room_id") or payload.get("post_id") or payload.get("path") or ""
    return f"{channel}:{event_type}:{identity}:{scope}"


def publish_event(channel: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    global _event_id, _failed_broadcasts, _coalesced_events
    clean_channel = str(channel or "pulse:global")[:120]
    clean_type = str(event_type or "event")[:100]
    clean_payload = _compact_payload(payload)
    try:
        with _lock:
            key = _coalesce_key(clean_channel, clean_type, clean_payload)
            now_float = time.time()
            if key:
                previous = _last_publish_by_key.get(key, 0)
                if now_float - previous < COALESCE_WINDOW_SECONDS:
                    _coalesced_events += 1
                    return {
                        "ok": True,
                        "coalesced": True,
                        "channel": clean_channel,
                        "event_type": clean_type,
                        "payload": clean_payload,
                    }
                _last_publish_by_key[key] = now_float
            _event_id += 1
            event = RealtimeEnvelope(
                id=_event_id,
                channel=clean_channel,
                event_type=clean_type,
                payload=clean_payload,
                created_at=_now_iso(),
            )
            _channels[clean_channel].append(event)
            if clean_channel != "pulse:global":
                _channels["pulse:global"].append(event)
            _event_condition.notify_all()
            return serialize_event(event)
    except Exception:
        _failed_broadcasts += 1
        raise


def subscribe_client(user_id: int | str = 0, session_id: str = "", channel: str = "pulse:global", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    key = _session_key(user_id, session_id)
    with _lock:
        existing = _sessions.get(key)
        if existing:
            channels = set(existing.get("channels") or [])
            channels.add(channel)
            existing.update({"channels": sorted(channels), "last_seen_at": _now(), "status": "online"})
            existing["metadata"] = {**(existing.get("metadata") or {}), **(metadata or {})}
        else:
            _sessions[key] = {
                "user_id": int(user_id or 0),
                "session_id": str(session_id or "anonymous")[:160],
                "channels": [channel],
                "metadata": dict(metadata or {}),
                "status": "online",
                "created_at": _now(),
                "last_seen_at": _now(),
            }
        return {"ok": True, "session_key": key, "channel": channel}


def heartbeat(user_id: int | str = 0, session_id: str = "", channel: str = "pulse:global", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return subscribe_client(user_id, session_id, channel, metadata)


def cleanup_stale_sessions(max_age_seconds: int = ONLINE_WINDOW_SECONDS) -> int:
    cutoff = _now() - timedelta(seconds=max_age_seconds)
    removed = 0
    with _lock:
        for key, item in list(_sessions.items()):
            if item.get("last_seen_at") < cutoff:
                _sessions.pop(key, None)
                removed += 1
    return removed


def get_online_count(max_age_seconds: int = ONLINE_WINDOW_SECONDS) -> int:
    cleanup_stale_sessions(max_age_seconds)
    cutoff = _now() - timedelta(seconds=max_age_seconds)
    with _lock:
        return len({item.get("user_id") for item in _sessions.values() if item.get("last_seen_at") >= cutoff and int(item.get("user_id") or 0) > 0})


def poll_events(channel: str = "pulse:global", after_id: int = 0, limit: int = 80) -> list[dict[str, Any]]:
    with _lock:
        events = [event for event in _channels.get(str(channel or "pulse:global")[:120], []) if int(event.id) > int(after_id or 0)]
        return [serialize_event(event) for event in events[-max(1, min(int(limit or 80), 200)):]]


def poll_events_for_channels(channels: list[str] | tuple[str, ...], after_id: int = 0, limit: int = 80) -> list[dict[str, Any]]:
    events_by_id: dict[int, RealtimeEnvelope] = {}
    with _lock:
        for channel in channels or ["pulse:global"]:
            clean_channel = str(channel or "pulse:global")[:120]
            for event in _channels.get(clean_channel, []):
                if int(event.id) > int(after_id or 0):
                    events_by_id[int(event.id)] = event
        ordered = [events_by_id[key] for key in sorted(events_by_id)]
        return [serialize_event(event) for event in ordered[-max(1, min(int(limit or 80), 200)):]]


def wait_events(channels: list[str] | tuple[str, ...], after_id: int = 0, limit: int = 80, timeout_seconds: float = 24.0) -> list[dict[str, Any]]:
    deadline = time.time() + max(1.0, min(float(timeout_seconds or 24.0), 30.0))
    with _event_condition:
        while True:
            events = poll_events_for_channels(list(channels or ["pulse:global"]), after_id=after_id, limit=limit)
            if events:
                return events
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            _event_condition.wait(timeout=min(remaining, 5.0))


def get_post_live_state(post_id: int | str) -> dict[str, Any]:
    post_id = int(post_id or 0)
    events = []
    with _lock:
        for event in _channels.get("pulse:global", []):
            payload = event.payload or {}
            if int(payload.get("post_id") or event.payload.get("id") or 0) == post_id:
                events.append(serialize_event(event))
    return {
        "post_id": post_id,
        "events": events[-40:],
        "reaction_events": sum(1 for event in events if "reaction" in event.get("event_type", "")),
        "comment_events": sum(1 for event in events if "comment" in event.get("event_type", "")),
    }


def health_snapshot() -> dict[str, Any]:
    cleanup_stale_sessions()
    with _lock:
        events_total = sum(len(events) for events in _channels.values())
        active_clients = len(_sessions)
    return {
        "online_users": get_online_count(),
        "active_realtime_clients": active_clients,
        "events_buffered": events_total,
        "failed_broadcasts": _failed_broadcasts,
        "coalesced_events": _coalesced_events,
        "reconnect_count": _reconnect_count,
        "transport": "sse_polling_ready",
    }


def serialize_event(event: RealtimeEnvelope) -> dict[str, Any]:
    return {
        "id": event.id,
        "channel": event.channel,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at,
    }
