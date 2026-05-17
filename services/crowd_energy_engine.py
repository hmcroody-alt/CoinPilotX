"""Crowd energy scoring for creator-grade live rooms."""

from datetime import datetime, timedelta

from . import user_context


def room_energy(match_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(minutes=3)).isoformat(timespec="seconds")
    try:
        cur.execute("SELECT COUNT(*) AS total FROM roast_reactions WHERE match_id=? AND created_at>=?", (int(match_id), cutoff))
        reactions = int((cur.fetchone() or {"total": 0})["total"] or 0)
        cur.execute("SELECT COUNT(*) AS total FROM roast_messages WHERE match_id=? AND created_at>=?", (int(match_id), cutoff))
        messages = int((cur.fetchone() or {"total": 0})["total"] or 0)
        cur.execute("SELECT COUNT(*) AS total FROM roast_votes WHERE match_id=? AND created_at>=?", (int(match_id), cutoff))
        votes = int((cur.fetchone() or {"total": 0})["total"] or 0)
        cur.execute("SELECT call_sign, current_balance, crowd_score FROM arena_roast_participants WHERE match_id=? ORDER BY crowd_score DESC, current_balance DESC LIMIT 1", (int(match_id),))
        favorite = cur.fetchone()
    except Exception:
        reactions = messages = votes = 0
        favorite = None
    conn.close()
    heat = min(100, 32 + reactions * 4 + messages * 9 + votes * 5)
    phase = "climax" if heat >= 88 else "surge" if heat >= 70 else "tension" if heat >= 48 else "calm"
    return {
        "match_id": int(match_id),
        "crowd_intensity": heat,
        "reaction_velocity": reactions,
        "active_spectators": 1200 + int(match_id) * 7 + reactions * 13 + votes * 5,
        "room_heat": heat,
        "emotional_phase": phase,
        "stage_glow": min(1.0, heat / 100),
        "emoji_storm_intensity": "high" if heat >= 88 else "medium" if heat >= 70 else "low",
        "crowd_favorite": (favorite["call_sign"] if favorite else "") if hasattr(favorite, "keys") else "",
    }
