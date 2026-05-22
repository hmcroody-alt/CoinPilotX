"""Analytics over the realtime/event streaming layer."""

from __future__ import annotations


def stream_pressure(event_bus=None, distributed=None) -> dict:
    event_bus = event_bus or {}
    distributed = distributed or {}
    events_per_min = int(event_bus.get("events_per_minute") or 0)
    dead = int(event_bus.get("dead_letters") or 0)
    failed = int(distributed.get("failed") or 0)
    published = int(distributed.get("published") or 0)
    queue_lag = max(distributed.get("partition_depths", {}).values() or [0])
    pressure = min(100, events_per_min + dead * 8 + failed * 6 + queue_lag // 20)
    return {
        "events_per_minute": events_per_min,
        "queue_lag": queue_lag,
        "dropped_events": failed,
        "replay_count": sum(distributed.get("replay_depths", {}).values()) if distributed.get("replay_depths") else 0,
        "stream_pressure": pressure,
        "status": "healthy" if pressure < 55 else "watch" if pressure < 85 else "degraded",
        "published_total": published,
    }
