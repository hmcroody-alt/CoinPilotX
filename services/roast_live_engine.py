"""Realtime-style live stream helpers for Roast Battle."""

import sqlite3
from datetime import datetime

from . import crowd_energy_engine, live_event_engine, realtime_sync_engine, user_context

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
        "target_player_id": result.get("target_player_id") or "",
        "target_call_sign": result.get("target_call_sign") or "",
        "participants": result.get("participants") or [],
    }
    payload["emotional_phase"] = realtime_sync_engine.emotional_phase((payload.get("line_weight") or {}).get("weight"), 0, "roast_message")
    return realtime_sync_engine.publish_synced(_channel(match_id), "roast_message", payload, entity_id=message.get("id"), cooldown_seconds=60)


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
        "reaction": {"id": int(reaction_id), "match_id": int(match_id), "emoji": emoji},
        "crowd_delta": 3,
        "commentator_line": "Chat is lighting up. The room felt that reaction.",
    }
    live_event_engine.publish(_channel(match_id), "crowd_reaction", payload, cooldown_seconds=0)
    return {"ok": True, **payload}


def _target_from_public(cur, target_public_player_id):
    target_public_player_id = str(target_public_player_id or "").strip()
    if not target_public_player_id:
        return 0
    cur.execute("SELECT user_id FROM arena_profiles WHERE lower(public_player_id)=lower(?) LIMIT 1", (target_public_player_id,))
    row = cur.fetchone()
    if row:
        return int(row["user_id"] if hasattr(row, "keys") else row[0])
    if target_public_player_id.lower().startswith(("pilot-", "pulse-")):
        try:
            return int(target_public_player_id.rsplit("-", 1)[-1])
        except Exception:
            return 0
    return 0


def record_vote(user_id, match_id, target_user_id=0, target_public_player_id=""):
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if not target_user_id and target_public_player_id:
        target_user_id = _target_from_public(cur, target_public_player_id)
    cur.execute(
        "INSERT INTO roast_votes (match_id, user_id, target_user_id, created_at) VALUES (?, ?, ?, ?)",
        (int(match_id), int(user_id), int(target_user_id or 0), _now()),
    )
    vote_id = cur.lastrowid
    conn.commit()
    cur.execute(
        """
        SELECT COALESCE(p.call_sign, u.roast_call_sign, 'Arena Pilot #' || p.user_id) AS call_sign,
               COALESCE(ap.public_player_id, 'PulseSoc-' || p.user_id) AS public_player_id
        FROM arena_roast_participants p
        LEFT JOIN users u ON u.user_id=p.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=p.user_id
        WHERE p.match_id=? AND p.user_id=?
        LIMIT 1
        """,
        (int(match_id), int(target_user_id or 0)),
    )
    target = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS total FROM roast_votes WHERE match_id=? AND target_user_id=?", (int(match_id), int(target_user_id or 0)))
    vote_count = int((cur.fetchone() or {"total": 0})["total"] or 0)
    conn.close()
    energy = crowd_energy_engine.room_energy(match_id)
    payload = {
        "vote": {
            "id": int(vote_id),
            "match_id": int(match_id),
            "player_id": (target["public_player_id"] if target else target_public_player_id) or "",
            "call_sign": (target["call_sign"] if target else "Arena Pilot"),
            "vote_count": vote_count,
            "crowd_heat": energy.get("room_heat") or 0,
        },
        "crowd_delta": 5,
    }
    realtime_sync_engine.publish_synced(_channel(match_id), "crowd_vote", payload, entity_id=vote_id, cooldown_seconds=0)
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
    energy = crowd_energy_engine.room_energy(match_id)
    return {
        "watching_worldwide": energy.get("active_spectators") or (1200 + int(match_id) * 37 + messages * 19 + reactions * 11),
        "heat_meter": max(heat, int(energy.get("room_heat") or 0)),
        "emotional_phase": energy.get("emotional_phase") or "calm",
        "crowd_favorite": energy.get("crowd_favorite") or "",
        "ticker": [
            "New challenger entered the global stage.",
            "Crowd energy rising across the room.",
            "AI commentators are watching for comeback moments.",
        ],
    }


def poll_match(match_id, after_id=0):
    events = realtime_sync_engine.poll_synced(_channel(match_id), after_id=after_id, limit=80)
    return {"ok": True, "events": events, "last_event_id": int(events[-1]["id"]) if events else int(after_id or 0), "snapshot": snapshot(match_id)}
