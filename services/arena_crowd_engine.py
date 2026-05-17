"""Crowd reaction helpers for Alpha Arena live rooms and victories."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import user_context


ALLOWED_REACTIONS = {"📣", "🔥", "🚀", "👑", "💎", "⚡", "🌊", "🎯", "🧠", "🏆"}


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def safe_reaction(value):
    reaction = str(value or "").strip()[:4]
    return reaction if reaction in ALLOWED_REACTIONS else "🔥"


def snapshot(match_id=0, room_id=0):
    conn = user_context.connect()
    cur = conn.cursor()
    if match_id:
        cur.execute("SELECT COUNT(*) AS c FROM arena_spectators WHERE match_id=?", (int(match_id),))
        spectators = int((cur.fetchone() or {"c": 0})["c"] or 0)
        cur.execute("SELECT COUNT(*) AS c FROM arena_emotes WHERE match_id=?", (int(match_id),))
        total_reactions = int((cur.fetchone() or {"c": 0})["c"] or 0)
        cutoff = (datetime.utcnow() - timedelta(seconds=60)).isoformat()
        cur.execute("SELECT COUNT(*) AS c FROM arena_emotes WHERE match_id=? AND created_at>=?", (int(match_id), cutoff))
        recent_reactions = int((cur.fetchone() or {"c": 0})["c"] or 0)
    else:
        cur.execute("SELECT COUNT(*) AS c FROM arena_emotes WHERE room_id=?", (int(room_id or 0),))
        total_reactions = int((cur.fetchone() or {"c": 0})["c"] or 0)
        cutoff = (datetime.utcnow() - timedelta(seconds=60)).isoformat()
        cur.execute("SELECT COUNT(*) AS c FROM arena_emotes WHERE room_id=? AND created_at>=?", (int(room_id or 0), cutoff))
        recent_reactions = int((cur.fetchone() or {"c": 0})["c"] or 0)
        spectators = 0
    conn.close()
    hype_meter = min(100, 18 + spectators * 7 + recent_reactions * 10 + total_reactions * 2)
    state = "Arena exploding" if hype_meter >= 82 else "Clutch moment" if hype_meter >= 58 else "Crowd building"
    return {
        "spectator_count": spectators,
        "reaction_count": total_reactions,
        "reaction_velocity": recent_reactions,
        "hype_meter": hype_meter,
        "state": state,
        "overlay": "Perfect read" if recent_reactions >= 4 else state,
        "updated_at": now_iso(),
    }


def record_reaction(match_id, user_id, emoji="🔥", room_id=0, intensity=1):
    emoji = safe_reaction(emoji)
    conn = user_context.connect()
    cur = conn.cursor()
    created_at = now_iso()
    cur.execute(
        "INSERT INTO arena_emotes (match_id, room_id, user_id, emote, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(match_id or 0), int(room_id or 0), int(user_id or 0), emoji, created_at),
    )
    try:
        cur.execute(
            """
            INSERT INTO arena_crowd_reactions (match_id, room_id, user_id, emoji, intensity, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(match_id or 0), int(room_id or 0), int(user_id or 0), emoji, int(intensity or 1), created_at),
        )
    except Exception:
        pass
    if match_id:
        cur.execute(
            """
            INSERT INTO arena_match_events (match_id, user_id, event_type, title, body, payload_json, created_at)
            VALUES (?, ?, 'reaction', 'Crowd reaction', ?, ?, ?)
            """,
            (int(match_id), int(user_id or 0), f"Crowd reaction: {emoji}", f'{{"emoji":"{emoji}"}}', created_at),
        )
    conn.commit()
    conn.close()
    result = snapshot(match_id=match_id, room_id=room_id)
    result.update({"ok": True, "emoji": emoji, "message": "Reaction sent."})
    return result
