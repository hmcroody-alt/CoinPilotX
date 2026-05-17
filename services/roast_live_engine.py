"""Realtime-style live stream helpers for Roast Battle."""

import sqlite3
from datetime import datetime

from . import live_event_engine, user_context

ROAST_EMOJIS = {"🔥", "😂", "👑", "💀", "🚀", "🎯", "⚡", "🧠", "🏆", "😤"}


def _channel(match_id):
    return f"roast:{int(match_id or 1)}"


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def broadcast_roast_message(result):
    message = result.get("message") or {}
    match_id = int(message.get("match_id") or 1)
    payload = {
        "message": message,
        "score": result.get("score") or {},
        "avatar_reaction": result.get("avatar_reaction"),
        "crowd_delta": result.get("crowd_delta") or 0,
        "commentator_line": result.get("commentator_line"),
        "line_weight": result.get("line_weight") or {},
        "impact_label": result.get("impact_label"),
        "balance_delta_sender": result.get("balance_delta_sender") or 0,
        "balance_delta_target": result.get("balance_delta_target") or 0,
        "target_user_id": result.get("target_user_id"),
        "participants": result.get("participants") or [],
    }
    return live_event_engine.publish(_channel(match_id), "roast_message", payload, dedupe_key=f"roast-message:{message.get('id')}", cooldown_seconds=60)


def record_reaction(user_id, match_id, emoji):
    emoji = str(emoji or "🔥").strip()
    if emoji not in ROAST_EMOJIS:
        emoji = "🔥"
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO roast_reactions (match_id, user_id, emoji, created_at) VALUES (?, ?, ?, ?)",
        (int(match_id), int(user_id), emoji, _now()),
    )
    reaction_id = cur.lastrowid
    conn.commit()
    conn.close()
    payload = {
        "reaction": {"id": int(reaction_id), "match_id": int(match_id), "user_id": int(user_id), "emoji": emoji},
        "crowd_delta": 3,
        "commentator_line": "Chat is lighting up. The room felt that reaction.",
    }
    live_event_engine.publish(_channel(match_id), "crowd_reaction", payload, cooldown_seconds=0)
    return {"ok": True, **payload}


def record_vote(user_id, match_id, target_user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO roast_votes (match_id, user_id, target_user_id, created_at) VALUES (?, ?, ?, ?)",
        (int(match_id), int(user_id), int(target_user_id or 0), _now()),
    )
    vote_id = cur.lastrowid
    conn.commit()
    conn.close()
    payload = {"vote": {"id": int(vote_id), "match_id": int(match_id), "target_user_id": int(target_user_id or 0)}, "crowd_delta": 5}
    live_event_engine.publish(_channel(match_id), "crowd_vote", payload, cooldown_seconds=0)
    return {"ok": True, **payload}


def snapshot(match_id):
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM roast_messages WHERE match_id=?", (int(match_id),))
    messages = int((cur.fetchone() or {"total": 0})["total"])
    cur.execute("SELECT COUNT(*) AS total FROM roast_reactions WHERE match_id=?", (int(match_id),))
    reactions = int((cur.fetchone() or {"total": 0})["total"])
    conn.close()
    heat = min(100, 42 + messages * 8 + reactions * 3)
    return {
        "watching_worldwide": 1200 + int(match_id) * 37 + messages * 19 + reactions * 11,
        "heat_meter": heat,
        "ticker": [
            "New challenger entered the global stage.",
            "Crowd energy rising across the room.",
            "AI commentators are watching for comeback moments.",
        ],
    }


def poll_match(match_id, after_id=0):
    events = live_event_engine.poll(_channel(match_id), after_id=after_id, limit=80)
    return {"ok": True, "events": events, "last_event_id": int(events[-1]["id"]) if events else int(after_id or 0), "snapshot": snapshot(match_id)}
