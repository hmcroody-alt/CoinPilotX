"""Realtime presence and audience energy helpers for Pulse Live."""

from __future__ import annotations

from datetime import datetime
import json
import math


REACTIONS = ["🔥", "💚", "😂", "👏", "🚀", "🧠", "💯", "⚡"]


def _json(value, fallback=None):
    try:
        return json.loads(value or "{}")
    except Exception:
        return fallback if fallback is not None else {}


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def audience_pulse(viewer_count=0, chat_count=0, reaction_count=0, bitrate_kbps=0, fps=0):
    viewer_count = max(0, int(viewer_count or 0))
    chat_count = max(0, int(chat_count or 0))
    reaction_count = max(0, int(reaction_count or 0))
    bitrate_kbps = max(0, int(bitrate_kbps or 0))
    fps = max(0, int(fps or 0))
    base = min(100, viewer_count * 6 + chat_count * 4 + reaction_count * 5)
    signal = 12 if bitrate_kbps >= 2500 else 7 if bitrate_kbps >= 1000 else 3
    motion = 10 if fps >= 30 else 4 if fps else 2
    score = min(100, base + signal + motion)
    if score >= 72:
        label = "surging"
    elif score >= 38:
        label = "warming"
    else:
        label = "ready"
    return {
        "score": score,
        "label": label,
        "viewer_count": viewer_count,
        "chat_velocity": chat_count,
        "reaction_velocity": reaction_count,
        "updated_at": _now(),
    }


def reaction_cloud(rows=None, limit=12):
    rows = rows or []
    cloud = []
    for idx, row in enumerate(rows[: max(1, int(limit or 12))]):
        if isinstance(row, dict):
            reaction = row.get("reaction_type") or row.get("emoji") or REACTIONS[idx % len(REACTIONS)]
        else:
            reaction = REACTIONS[idx % len(REACTIONS)]
        cloud.append(
            {
                "emoji": reaction if reaction in REACTIONS or len(str(reaction)) <= 4 else "🔥",
                "x": 54 + int(34 * math.sin(idx * 1.7)),
                "delay_ms": idx * 140,
            }
        )
    if not cloud:
        cloud = [{"emoji": REACTIONS[i], "x": 58 + i * 4, "delay_ms": i * 180} for i in range(4)]
    return cloud


def viewer_momentum(chat_messages=None, reactions=None, viewers=None):
    chat_messages = chat_messages or []
    reactions = reactions or []
    viewers = viewers or []
    return audience_pulse(
        viewer_count=len(viewers),
        chat_count=len(chat_messages),
        reaction_count=len(reactions),
    )


def stream_energy_state(session=None, chat_messages=None, reactions=None, viewers=None):
    session = session or {}
    pulse = viewer_momentum(chat_messages, reactions, viewers)
    if (session.get("status") or "").lower() in {"ended", "offline"}:
        pulse["label"] = "ended"
        pulse["score"] = 0
    return {
        "pulse": pulse,
        "state": pulse["label"],
        "suggestion": "Pin a question for chat." if pulse["score"] < 40 else "Audience is warm. Ask for reactions.",
    }
