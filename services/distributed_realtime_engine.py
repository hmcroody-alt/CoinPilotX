"""Distributed realtime nervous-system foundation.

This module provides deterministic channel partitioning, priority-aware
batching, replay buffers, adaptive throttling, and delivery metrics. It is
in-process today and shaped so Redis/NATS/Kafka can replace the storage later
without changing feature code.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime
from hashlib import sha1
from itertools import count
from threading import RLock
import json


PRIORITY_TIERS = {
    "critical": 0,
    "high": 1,
    "normal": 2,
}

CRITICAL_EVENTS = {"pulse_live_started", "moderation_event", "security_alert", "admin_alert_created"}
HIGH_EVENTS = {"pulse_message_created", "pulse_comment_created", "pulse_reaction_updated", "pulse_reel_comment_created", "pulse_reel_reaction_updated"}

_lock = RLock()
_event_ids = count(1)
_partitions = defaultdict(lambda: deque(maxlen=1500))
_replay_buffers = defaultdict(lambda: deque(maxlen=600))
_metrics = Counter()
_delivery_failures = deque(maxlen=300)


def priority_for_event(event_type: str, explicit: str = "") -> str:
    explicit = str(explicit or "").lower()
    if explicit in PRIORITY_TIERS:
        return explicit
    event_type = str(event_type or "")
    if event_type in CRITICAL_EVENTS or "security" in event_type or "moderation" in event_type:
        return "critical"
    if event_type in HIGH_EVENTS or "message" in event_type or "comment" in event_type or "reaction" in event_type:
        return "high"
    return "normal"


def partition_for_channel(channel: str, partitions: int = 16) -> str:
    clean = str(channel or "global")[:160]
    digest = sha1(clean.encode("utf-8")).hexdigest()
    return f"p{int(digest[:6], 16) % max(1, int(partitions or 16)):02d}"


def publish(channel: str, event_type: str, payload=None, priority: str = "", trace_id: str = "") -> dict:
    channel = str(channel or "pulse:global")[:160]
    event_type = str(event_type or "event")[:120]
    tier = priority_for_event(event_type, priority)
    event = {
        "id": next(_event_ids),
        "channel": channel,
        "partition": partition_for_channel(channel),
        "event_type": event_type,
        "priority": tier,
        "payload": payload or {},
        "trace_id": trace_id or "",
        "created_at": datetime.utcnow().isoformat(timespec="milliseconds"),
    }
    with _lock:
        try:
            json.dumps(event, default=str)
            key = (event["priority"], event["partition"])
            _partitions[key].append(event)
            _replay_buffers[channel].append(event)
            if channel != "pulse:global":
                _replay_buffers["pulse:global"].append(event)
            _metrics["published"] += 1
            _metrics[f"priority:{tier}"] += 1
            _metrics[f"partition:{event['partition']}"] += 1
            _metrics[f"type:{event_type}"] += 1
            return {**event, "ok": True}
        except Exception as exc:
            failed = {**event, "ok": False, "error": repr(exc)}
            _delivery_failures.append(failed)
            _metrics["failed"] += 1
            return failed


def batch(channel: str = "pulse:global", after_id: int = 0, limit: int = 80) -> list[dict]:
    channel = str(channel or "pulse:global")[:160]
    after_id = int(after_id or 0)
    limit = max(1, min(250, int(limit or 80)))
    with _lock:
        events = [event for event in _replay_buffers.get(channel, []) if int(event.get("id") or 0) > after_id]
        return events[-limit:]


def replay(channel: str = "pulse:global", last_seen_id: int = 0, limit: int = 120) -> dict:
    events = batch(channel, last_seen_id, limit)
    return {
        "channel": channel,
        "last_seen_id": int(last_seen_id or 0),
        "events": events,
        "recovered": len(events),
        "latest_id": max([int(e.get("id") or 0) for e in events] or [int(last_seen_id or 0)]),
    }


def adaptive_throttle(channel: str = "pulse:global") -> dict:
    recent = batch(channel, 0, 250)
    critical = sum(1 for event in recent if event.get("priority") == "critical")
    high = sum(1 for event in recent if event.get("priority") == "high")
    normal = len(recent) - critical - high
    pressure = min(100, critical * 8 + high * 3 + normal)
    return {
        "channel": channel,
        "pressure": pressure,
        "mode": "shed_normal" if pressure >= 85 else "batch_normal" if pressure >= 60 else "live",
        "max_batch_size": 25 if pressure >= 85 else 60 if pressure >= 60 else 120,
    }


def metrics() -> dict:
    with _lock:
        partition_depths = {f"{priority}:{partition}": len(events) for (priority, partition), events in _partitions.items()}
        replay_depths = {channel: len(events) for channel, events in list(_replay_buffers.items())[:40]}
        published = int(_metrics.get("published", 0))
        failed = int(_metrics.get("failed", 0))
    return {
        "published": published,
        "failed": failed,
        "delivery_success_rate": round(100 * (published / max(1, published + failed)), 2),
        "partition_depths": partition_depths,
        "replay_depths": replay_depths,
        "priority_counts": {tier: int(_metrics.get(f"priority:{tier}", 0)) for tier in PRIORITY_TIERS},
        "failed_events": list(_delivery_failures)[-20:][::-1],
        "throughput_score": min(100, published),
    }


def health() -> dict:
    data = metrics()
    pressure = max(data["partition_depths"].values() or [0])
    score = max(0, min(100, int(data["delivery_success_rate"] - min(35, pressure / 80))))
    return {
        "status": "healthy" if score >= 80 else "watch" if score >= 55 else "degraded",
        "score": score,
        "pressure": pressure,
        "metrics": data,
    }
