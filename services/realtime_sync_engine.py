"""Realtime synchronization helpers for live Arena and Roast Battle events."""

from datetime import datetime

from . import live_event_engine


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def publish_synced(channel, event_type, payload=None, entity_id=None, sequence=None, cooldown_seconds=1):
    payload = dict(payload or {})
    payload.setdefault("synced_at", now_iso())
    payload.setdefault("sequence", int(sequence or 0))
    dedupe_key = f"{event_type}:{entity_id or payload.get('id') or payload.get('event_id') or payload.get('message_id') or ''}:{payload.get('sequence') or ''}"
    return live_event_engine.publish(channel, event_type, payload, dedupe_key=dedupe_key, cooldown_seconds=cooldown_seconds)


def poll_synced(channel, after_id=0, limit=80):
    events = live_event_engine.poll(channel, after_id=after_id, limit=limit)
    seen = set()
    ordered = []
    for event in events:
        payload = event.get("payload") or {}
        key = (event.get("event_type"), payload.get("id") or payload.get("event_id") or payload.get("message_id") or event.get("id"))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(event)
    return ordered


def emotional_phase(weight=0, heat=0, event_type=""):
    weight = float(weight or 0)
    heat = float(heat or 0)
    if event_type in {"victory", "match_end"} or weight >= 90 or heat >= 88:
        return {"phase": "climax", "effect_intensity": "high", "storm_size": 22, "cooldown_ms": 2200}
    if weight >= 70 or heat >= 70:
        return {"phase": "surge", "effect_intensity": "medium", "storm_size": 14, "cooldown_ms": 1600}
    if weight >= 38 or heat >= 45:
        return {"phase": "tension", "effect_intensity": "low", "storm_size": 8, "cooldown_ms": 1200}
    return {"phase": "calm", "effect_intensity": "minimal", "storm_size": 3, "cooldown_ms": 900}
