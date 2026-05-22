"""Reusable realtime foundation for Pulse social activity.

The web app already persists Pulse events for SSE/polling. This module gives
the rest of the platform one dependency-light interface that can later sit in
front of Redis, Socket.IO, or another websocket service without changing
feature code.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Any


MAX_EVENTS_PER_CHANNEL = 500
ONLINE_WINDOW_SECONDS = 120


@dataclass
class RealtimeEnvelope:
    id: int
    channel: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


_lock = RLock()
_event_id = 0
_channels: dict[str, deque[RealtimeEnvelope]] = defaultdict(lambda: deque(maxlen=MAX_EVENTS_PER_CHANNEL))
_sessions: dict[str, dict[str, Any]] = {}
_failed_broadcasts = 0
_reconnect_count = 0


def _now() -> datetime:
    return datetime.utcnow()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _session_key(user_id: int | str = 0, session_id: str = "") -> str:
    return f"{int(user_id or 0)}:{str(session_id or 'anonymous')[:160]}"


def publish_event(channel: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    global _event_id, _failed_broadcasts
    clean_channel = str(channel or "pulse:global")[:120]
    clean_type = str(event_type or "event")[:100]
    try:
        with _lock:
            _event_id += 1
            event = RealtimeEnvelope(
                id=_event_id,
                channel=clean_channel,
                event_type=clean_type,
                payload=dict(payload or {}),
                created_at=_now_iso(),
            )
            _channels[clean_channel].append(event)
            if clean_channel != "pulse:global":
                _channels["pulse:global"].append(event)
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
