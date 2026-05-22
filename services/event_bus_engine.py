"""Standard event bus primitives for realtime and admin observability.

The first implementation is in-process and serializable. The interface is
ready for Redis/Kafka later without changing route handlers.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime
from itertools import count
import json


_EVENT_ID = count(1)
_EVENTS = deque(maxlen=1000)
_DEAD_LETTERS = deque(maxlen=300)
_METRICS = Counter()


def publish(channel: str, event_type: str, payload=None, trace_id: str = "", priority: str = "normal") -> dict:
    event = {
        "id": next(_EVENT_ID),
        "channel": str(channel or "global")[:120],
        "event_type": str(event_type or "event")[:120],
        "priority": str(priority or "normal")[:40],
        "payload": payload or {},
        "trace_id": trace_id or "",
        "status": "published",
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    try:
        json.dumps(event, default=str)
        _EVENTS.append(event)
        _METRICS["published"] += 1
        _METRICS[f"channel:{event['channel']}"] += 1
        _METRICS[f"type:{event['event_type']}"] += 1
        return event
    except Exception as exc:
        failed = {**event, "status": "failed", "error": repr(exc)}
        _DEAD_LETTERS.append(failed)
        _METRICS["failed"] += 1
        return failed


def subscribe_snapshot(channel: str = "", after_id: int = 0, limit: int = 80) -> list[dict]:
    items = []
    for event in list(_EVENTS):
        if channel and event.get("channel") != channel:
            continue
        if int(event.get("id") or 0) <= int(after_id or 0):
            continue
        items.append(event)
    return items[-max(1, min(200, int(limit or 80))):]


def retry_dead_letter(event_id: int = 0) -> dict:
    for event in list(_DEAD_LETTERS):
        if int(event.get("id") or 0) == int(event_id or 0):
            _DEAD_LETTERS.remove(event)
            return publish(event.get("channel"), event.get("event_type"), event.get("payload"), event.get("trace_id"), event.get("priority"))
    return {"ok": False, "message": "Dead-letter event not found."}


def metrics() -> dict:
    events = list(_EVENTS)
    recent = events[-120:]
    type_counts = Counter(event.get("event_type") for event in recent)
    channel_counts = Counter(event.get("channel") for event in recent)
    return {
        "published": int(_METRICS.get("published", 0)),
        "failed": int(_METRICS.get("failed", 0)),
        "dead_letters": len(_DEAD_LETTERS),
        "buffered_events": len(_EVENTS),
        "events_per_minute": len(recent),
        "top_event_types": type_counts.most_common(8),
        "top_channels": channel_counts.most_common(8),
        "latest_events": events[-20:][::-1],
        "dead_letter_events": list(_DEAD_LETTERS)[-20:][::-1],
    }


def trace(event_type: str, system: str, confidence: float = 0, duration_ms: float = 0, fallback_used: bool = False, error: str = "") -> dict:
    return publish(
        "ai:observability",
        "ai_engine_trace",
        {
            "system": system,
            "confidence": round(float(confidence or 0), 3),
            "duration_ms": round(float(duration_ms or 0), 2),
            "fallback_used": bool(fallback_used),
            "error": str(error or "")[:500],
            "observed_event": event_type,
        },
    )
