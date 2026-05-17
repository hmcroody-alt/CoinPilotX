"""Victory orchestration for Alpha Arena."""

from __future__ import annotations

import json
from datetime import datetime

from . import arena_crowd_engine, arena_replay_engine, realtime_service, user_context


EDUCATIONAL_DISCLAIMER = (
    "Alpha Arena is a simulated educational trading environment using virtual dollars. "
    "No real-money trading occurs inside Arena matches."
)


COMMENTARY_LINES = {
    "ranked": "DISCIPLINE WINS AGAIN!",
    "scam_hunter": "SCAM DETECTED — HUGE SAVE!",
    "live_battle": "THE CROWD IS GOING INSANE!",
    "comeback": "WHAT A REVERSAL!",
}


def rank_for_xp(xp):
    xp = int(xp or 0)
    if xp >= 5000:
        return "Legend"
    if xp >= 2500:
        return "Elite"
    if xp >= 1200:
        return "Strategist"
    if xp >= 500:
        return "Operator"
    if xp >= 150:
        return "Scout"
    return "Rookie"


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def mode_win_logic(mode):
    mode = (mode or "ranked").lower()
    if "scam" in mode:
        return {
            "mode": "Scam Hunter",
            "win_condition": "survival, scam identification, wallet protection, and phishing detection",
            "reward_focus": "Scam Defense XP",
        }
    if "live" in mode:
        return {
            "mode": "Live Battle",
            "win_condition": "final score, crowd rating, AI discipline score, and strategy multiplier",
            "reward_focus": "Arena IQ and crowd recognition",
        }
    return {
        "mode": "Ranked",
        "win_condition": "portfolio growth, discipline score, drawdown control, trade accuracy, and scam avoidance",
        "reward_focus": "rank progress and XP",
    }


def loser_encouragement(mode="ranked"):
    return {
        "title": "Comeback path unlocked",
        "body": "High discipline despite market chaos. Review the replay, protect your drawdown, and queue the next challenge.",
        "stats": [
            "Survived longer than 83% of new pilots.",
            "Risk management improved.",
            "XP earned for finishing the session.",
        ],
    }


def trigger_victory_event(match_id, winner_id, loser_id=0, mode="", metadata=None):
    metadata = metadata or {}
    match_id = int(match_id or 0)
    winner_id = int(winner_id or 0)
    if not match_id or not winner_id:
        return {"ok": False, "message": "match_id and winner_id are required."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM arena_matches WHERE id=? LIMIT 1", (match_id,))
    match = user_context.row_to_dict(cur.fetchone()) or {}
    mode = mode or match.get("match_type") or "ranked"
    cur.execute("SELECT * FROM arena_profiles WHERE user_id=? LIMIT 1", (winner_id,))
    winner = user_context.row_to_dict(cur.fetchone()) or {}
    display_name = winner.get("display_name") or winner.get("public_player_id") or "Arena Pilot"
    logic = mode_win_logic(mode)
    commentator_line = COMMENTARY_LINES.get("scam_hunter" if "scam" in mode else "ranked")
    if metadata.get("comeback"):
        commentator_line = COMMENTARY_LINES["comeback"]
    xp_awarded = int(metadata.get("xp_awarded") or 120)
    next_xp = int(winner.get("xp") or 0) + xp_awarded
    next_iq = min(999, int(winner.get("arena_iq") or 50) + 3)
    next_streak = int(winner.get("streak_count") or 0) + 1
    crowd = arena_crowd_engine.snapshot(match_id=match_id)
    payload = {
        "winner_id": winner_id,
        "winner_display_name": display_name,
        "mode": logic,
        "commentator_line": commentator_line,
        "xp_awarded": xp_awarded,
        "crowd": crowd,
        "effects": {
            "energy_flash": True,
            "glow_sweep": True,
            "emoji_storm": ["📣", "🔥", "🚀", "👑", "💎", "⚡", "🌊", "🎯", "🧠", "🏆"],
            "holographic_overlay": True,
        },
        "losing_experience": loser_encouragement(mode),
        "disclaimer": EDUCATIONAL_DISCLAIMER,
    }
    created_at = now_iso()
    cur.execute("UPDATE arena_matches SET status='completed', ends_at=? WHERE id=?", (created_at, match_id))
    cur.execute(
        """
        UPDATE arena_match_participants
        SET score=COALESCE(score, 0)+?, result_json=?
        WHERE match_id=? AND user_id=?
        """,
        (xp_awarded, json.dumps({"result": "victory", "xp_awarded": xp_awarded, "created_at": created_at}), match_id, winner_id),
    )
    cur.execute(
        """
        UPDATE arena_profiles
        SET xp=?, rank=?, arena_iq=?, streak_count=?, updated_at=?
        WHERE user_id=?
        """,
        (next_xp, rank_for_xp(next_xp), next_iq, next_streak, created_at, winner_id),
    )
    cur.execute(
        """
        INSERT INTO arena_match_events (match_id, user_id, event_type, title, body, payload_json, created_at)
        VALUES (?, ?, 'victory', 'Victory cinematic triggered', ?, ?, ?)
        """,
        (match_id, winner_id, f"{display_name} won Alpha Arena. {commentator_line}", json.dumps(payload), created_at),
    )
    cur.execute(
        """
        INSERT INTO arena_victory_events
        (match_id, winner_id, loser_id, mode, victory_type, xp_awarded, crowd_intensity, commentator_line, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            winner_id,
            int(loser_id or 0),
            mode,
            metadata.get("victory_type") or "standard",
            xp_awarded,
            int(crowd.get("hype_meter") or 0),
            commentator_line,
            json.dumps(payload),
            created_at,
        ),
    )
    victory_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    replay = arena_replay_engine.generate_replay(match_id, winner_id)
    try:
        realtime_service.publish_arena_event("match", match_id, "victory", {"victory_id": victory_id, **payload, "replay": replay})
        realtime_service.publish_arena_event("global", "victories", "victory", {"victory_id": victory_id, **payload, "replay": replay})
    except Exception:
        pass
    return {
        "ok": True,
        "victory_id": victory_id,
        "match_id": match_id,
        "winner_id": winner_id,
        "winner_display_name": display_name,
        "commentator_line": commentator_line,
        "xp_awarded": xp_awarded,
        "crowd": crowd,
        "replay": replay,
        "payload": payload,
        "disclaimer": EDUCATIONAL_DISCLAIMER,
    }
