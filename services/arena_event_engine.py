"""Realtime-style event generator for Arena world events."""

from __future__ import annotations

from datetime import datetime


def flash_event(world_state=None):
    world_state = world_state or {}
    title = world_state.get("title") or "Arena Intelligence Pulse"
    return {
        "event_type": "world_pulse",
        "title": title,
        "description": f"{title} is influencing missions, rooms, and simulated battles right now.",
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "xp_modifier": world_state.get("xp_modifier", 1.0),
    }


def boss_invasion(boss_key="fomo_beast"):
    return {
        "event_type": "boss_invasion",
        "title": boss_key.replace("_", " ").title(),
        "description": "An AI training boss is active. Practice the skill without real-money risk.",
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
