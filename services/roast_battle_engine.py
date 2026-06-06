"""Safe, esports-style Roast Battle engine for Alpha Arena."""

import json
import re
from datetime import datetime

from . import roast_line_weight_engine, roast_live_engine, roast_safety_filter, user_context

DEFAULT_STAGE_BALANCE = 1_000_000
TURN_SECONDS = 30


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _row_to_dict(row):
    if not row:
        return {}
    return dict(row) if hasattr(row, "keys") else {}


def _slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:40] or "arena-pilot"


def _clean_call_sign(value):
    return re.sub(r"\s+", " ", str(value or "").strip())[:24]


def _call_sign_for_user(cur, user_id):
    try:
        cur.execute("SELECT roast_call_sign FROM users WHERE user_id=? LIMIT 1", (int(user_id),))
        row = cur.fetchone()
        if row:
            data = _row_to_dict(row)
            return data.get("roast_call_sign") or f"Arena Pilot #{int(user_id)}"
    except Exception:
        pass
    return f"Arena Pilot #{int(user_id)}"


def set_call_sign(user_id, call_sign):
    call_sign = _clean_call_sign(call_sign)
    if len(call_sign) < 3:
        return {"ok": False, "message": "Call sign must be at least 3 characters."}, 400
    if re.search(r"@|https?://|\+?\d[\d\-\s]{6,}", call_sign):
        return {"ok": False, "message": "Call sign cannot include private contact info."}, 400
    moderation = roast_safety_filter.moderate(call_sign)
    if not moderation.get("ok"):
        return {"ok": False, "message": "Choose a cleaner call sign for the Arena."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "UPDATE users SET roast_call_sign=?, roast_call_sign_slug=?, roast_call_sign_updated_at=?, updated_at=? WHERE user_id=?",
        (call_sign, _slug(call_sign), now, now, int(user_id)),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "call_sign": call_sign}, 200


def ensure_match(match_id=1, room_id=None, max_players=None):
    conn = user_context.connect()
    conn.row_factory = getattr(conn, "row_factory", None)
    cur = conn.cursor()
    now = _now()
    cur.execute("SELECT * FROM roast_matches WHERE id=? LIMIT 1", (int(match_id or 1),))
    row = cur.fetchone()
    if not row:
        max_players = int(max_players or 2)
        match_type = "four_player" if max_players >= 4 else "two_player"
        cur.execute(
            "INSERT INTO roast_matches (id, room_id, status, created_at, updated_at, max_players, turn_duration_seconds, match_type, turn_started_at) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)",
            (int(match_id or 1), int(room_id or match_id or 1), now, now, max_players, TURN_SECONDS, match_type, now),
        )
    conn.commit()
    conn.close()


def _participants(cur, match_id):
    cur.execute(
        """
        SELECT p.*, u.roast_call_sign, ap.public_player_id
        FROM arena_roast_participants p
        LEFT JOIN users u ON u.user_id=p.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=p.user_id
        WHERE p.match_id=?
        ORDER BY p.current_balance DESC, p.joined_at ASC
        """,
        (int(match_id),),
    )
    return [_row_to_dict(row) for row in cur.fetchall()]


def _format_participant(row):
    balance = float(row.get("current_balance") or DEFAULT_STAGE_BALANCE)
    status = "Dominating" if balance >= 1_075_000 else "Hot" if balance >= 1_025_000 else "Shaken" if balance < 960_000 else "Recovering"
    public_id = row.get("public_player_id") or f"Pulse-{int(row.get('user_id') or 0)}"
    call_sign = row.get("call_sign") or row.get("roast_call_sign") or f"Arena Pilot #{int(row.get('user_id') or 0)}"
    return {
        "player_id": public_id,
        "public_player_id": public_id,
        "call_sign": call_sign,
        "display_name": call_sign,
        "starting_balance": float(row.get("starting_balance") or DEFAULT_STAGE_BALANCE),
        "current_balance": balance,
        "stage_balance": f"${balance:,.0f}",
        "crowd_score": float(row.get("crowd_score") or 0),
        "safety_score": float(row.get("safety_score") or 100),
        "wit_score": float(row.get("wit_score") or 0),
        "status": status,
    }


def ensure_participant(match_id, user_id):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    ensure_match(match_id)
    call_sign = _call_sign_for_user(cur, user_id)
    now = _now()
    cur.execute("SELECT id FROM arena_roast_participants WHERE match_id=? AND user_id=? LIMIT 1", (int(match_id), int(user_id)))
    existing = cur.fetchone()
    if not existing:
        cur.execute(
            """
            INSERT INTO arena_roast_participants
            (match_id, user_id, public_player_id, call_sign, starting_balance, current_balance, crowd_score, safety_score, wit_score, status, joined_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 100, 0, 'active', ?)
            """,
            (int(match_id), int(user_id), f"Pulse-{int(user_id)}", call_sign, DEFAULT_STAGE_BALANCE, DEFAULT_STAGE_BALANCE, now),
        )
    else:
        cur.execute(
            "UPDATE arena_roast_participants SET call_sign=COALESCE(call_sign, ?) WHERE match_id=? AND user_id=?",
            (call_sign, int(match_id), int(user_id)),
        )
    conn.commit()
    conn.close()


def _choose_target(cur, match_id, sender_user_id, target_user_id=None, target_type="single"):
    participants = _participants(cur, match_id)
    opponents = [p for p in participants if int(p.get("user_id") or 0) != int(sender_user_id)]
    if target_type == "crowd" or not opponents:
        return None
    if target_type == "leader":
        return max(opponents, key=lambda p: float(p.get("current_balance") or 0))
    if target_user_id:
        for participant in opponents:
            if int(participant.get("user_id") or 0) == int(target_user_id):
                return participant
    return opponents[0] if opponents else None


def _user_id_from_public(cur, public_player_id):
    public_player_id = str(public_player_id or "").strip()
    if not public_player_id:
        return 0
    cur.execute("SELECT user_id FROM arena_profiles WHERE lower(public_player_id)=lower(?) LIMIT 1", (public_player_id,))
    row = cur.fetchone()
    if row:
        return int(row["user_id"] if hasattr(row, "keys") else row[0])
    if public_player_id.lower().startswith(("pilot-", "pulse-")):
        try:
            return int(public_player_id.rsplit("-", 1)[-1])
        except Exception:
            return 0
    return 0


def match_state(match_id, user_id=None):
    ensure_match(match_id)
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM roast_matches WHERE id=? LIMIT 1", (int(match_id),))
    match = _row_to_dict(cur.fetchone())
    participants = [_format_participant(row) for row in _participants(cur, match_id)]
    cur.execute(
        "SELECT * FROM arena_roast_lines WHERE match_id=? ORDER BY id DESC LIMIT 12",
        (int(match_id),),
    )
    lines = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    return {
        "ok": True,
        "match": {
            "id": int(match_id),
            "max_players": int(match.get("max_players") or 2),
            "match_type": match.get("match_type") or "two_player",
            "current_turn_player_id": participants[0]["public_player_id"] if participants else "",
            "turn_started_at": match.get("turn_started_at"),
            "turn_duration_seconds": int(match.get("turn_duration_seconds") or TURN_SECONDS),
        },
        "participants": participants,
        "lines": lines,
        "disclaimer": "Roast Battle uses virtual dollars for entertainment scoring only. No real-money value.",
    }


def enroll(user_id, room_id=1):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute("SELECT id, max_players FROM roast_matches WHERE room_id=? AND status='active' ORDER BY id DESC LIMIT 1", (int(room_id or 1),))
    row = cur.fetchone()
    match_id = int(row["id"]) if row else int(room_id or 1)
    if not row:
        ensure_match(match_id, room_id=room_id, max_players=2)
    cur.execute("SELECT COUNT(*) AS total FROM arena_roast_participants WHERE match_id=?", (match_id,))
    count = int((cur.fetchone() or {"total": 0})["total"] or 0)
    max_players = 4 if count + 1 >= 4 else 2
    match_type = "four_player" if max_players == 4 else "two_player"
    cur.execute("UPDATE roast_matches SET max_players=?, match_type=?, updated_at=? WHERE id=?", (max_players, match_type, _now(), match_id))
    conn.commit()
    conn.close()
    ensure_participant(match_id, user_id)
    state = match_state(match_id, user_id)
    return {"ok": True, "room_id": int(room_id or 1), "match_id": match_id, "queue_position": 0, "next_url": f"/arena/roast-battle/match/{match_id}", "message": "You are on the Roast Battle stage.", **state}


def _commentator_line(call_sign, impact, sender_delta, target_delta, target_name=None):
    if impact == "Unsafe Blocked":
        return f"{call_sign} crossed the line and lost virtual dollars. Keep it clever, not harmful."
    if sender_delta < 0:
        return f"{call_sign} missed the moment. That line actually cost virtual dollars."
    if target_name and target_delta < 0:
        return f"{call_sign} just wiped ${abs(int(target_delta)):,.0f} virtual dollars off {target_name}'s stage balance."
    return f"{call_sign} landed a {impact}. The crowd is rewarding clean pressure."


def submit_message(user_id, match_id, message, target_user_id=None, target_type="single", target_public_player_id=None):
    message = str(message or "").strip()[:500]
    if not message:
        return {"ok": False, "message": "Send a clean, clever line first."}, 400
    ensure_participant(match_id, user_id)
    moderation = roast_safety_filter.moderate(message)
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    sender_name = _call_sign_for_user(cur, user_id)
    cur.execute("SELECT public_player_id FROM arena_profiles WHERE user_id=? LIMIT 1", (int(user_id),))
    sender_profile = cur.fetchone()
    sender_public_id = (sender_profile["public_player_id"] if sender_profile else "") or f"Pulse-{int(user_id)}"
    if not target_user_id and target_public_player_id:
        target_user_id = _user_id_from_public(cur, target_public_player_id)
    target = _choose_target(cur, match_id, user_id, target_user_id, target_type)
    weight = roast_line_weight_engine.score_line(message, moderation=moderation)
    sender_delta = float(weight.get("balance_delta") or 0)
    target_delta = float(weight.get("target_balance_delta") or 0) if target else 0
    target_id = int(target.get("user_id") or 0) if target else None
    target_name = (target or {}).get("call_sign") or (target or {}).get("display_name")
    now = _now()
    cur.execute(
        """
        INSERT INTO arena_roast_lines
        (match_id, sender_user_id, target_user_id, target_type, message, weight_score, impact_label, balance_delta_sender, balance_delta_target, emotion, safe, moderation_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(match_id),
            int(user_id),
            target_id,
            target_type or "single",
            message,
            float(weight.get("weight") or 0),
            weight.get("impact_label"),
            sender_delta,
            target_delta,
            weight.get("emotion"),
            1 if weight.get("safe") else 0,
            weight.get("moderation_reason"),
            now,
        ),
    )
    line_id = cur.lastrowid
    cur.execute(
        "UPDATE arena_roast_participants SET current_balance=current_balance+?, wit_score=wit_score+?, safety_score=safety_score+?, crowd_score=crowd_score+? WHERE match_id=? AND user_id=?",
        (sender_delta, float(weight.get("weight") or 0), 0 if weight.get("safe") else -20, max(0, sender_delta / 10000), int(match_id), int(user_id)),
    )
    if target_id:
        cur.execute(
            "UPDATE arena_roast_participants SET current_balance=current_balance+? WHERE match_id=? AND user_id=?",
            (target_delta, int(match_id), int(target_id)),
        )
    moderation_status = "approved" if weight.get("safe") else "blocked"
    score = {
        "total": int(weight.get("weight") or 0),
        "avatar_reaction": "laughing" if sender_delta > 0 else "shocked",
        "crowd_delta": max(0, min(18, int(abs(sender_delta) / 5000))),
        "impact_label": weight.get("impact_label"),
    }
    cur.execute(
        "INSERT INTO roast_messages (match_id, user_id, message, moderation_status, score_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (int(match_id), int(user_id), message, moderation_status, json.dumps(score), now),
    )
    message_id = cur.lastrowid
    conn.commit()
    state = match_state(match_id)
    conn.close()
    result = {
        "ok": bool(weight.get("safe")),
        "message": {"id": message_id, "line_id": line_id, "match_id": int(match_id), "player_id": sender_public_id, "public_player_id": sender_public_id, "call_sign": sender_name, "body": message, "created_at": now},
        "line_weight": weight,
        "score": score,
        "avatar_reaction": score["avatar_reaction"],
        "crowd_delta": score["crowd_delta"],
        "balance_delta_sender": sender_delta,
        "balance_delta_target": target_delta,
        "impact_label": weight.get("impact_label"),
        "target_player_id": (target or {}).get("public_player_id") or "",
        "target_call_sign": target_name,
        "participants": state.get("participants") or [],
        "commentator_line": _commentator_line(sender_name, weight.get("impact_label"), sender_delta, target_delta, target_name),
    }
    try:
        roast_live_engine.broadcast_roast_message(result)
    except Exception:
        pass
    if not weight.get("safe"):
        result["message"] = weight.get("moderation_reason") or "Too personal. Keep it clever, not harmful."
        return result, 400
    return result, 200


def leaderboard(limit=20):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT call_sign, user_id, MAX(current_balance) AS best_balance, MAX(crowd_score) AS crowd_score
        FROM arena_roast_participants
        GROUP BY user_id, call_sign
        ORDER BY best_balance DESC, crowd_score DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    return {"ok": True, "players": rows}


def rooms():
    return {
        "ok": True,
        "rooms": [
            {"id": 1, "title": "Main Roast Stage", "status": "open", "queue_length": 0, "energy": 76},
            {"id": 2, "title": "Rivalry Room", "status": "watch", "queue_length": 2, "energy": 64},
        ],
    }
