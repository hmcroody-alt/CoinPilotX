"""Safe, esports-style Roast Battle engine for Alpha Arena."""

from datetime import datetime

from . import roast_safety_filter, user_context


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def score_roast(text):
    words = len((text or "").split())
    cleverness = min(95, 45 + words * 3)
    originality = 72 if words >= 6 else 55
    safe_intensity = 80
    total = round((cleverness + originality + safe_intensity) / 3)
    reaction = "laughing" if total >= 75 else "confident"
    return {
        "cleverness": cleverness,
        "originality": originality,
        "safe_intensity": safe_intensity,
        "total": total,
        "avatar_reaction": reaction,
        "crowd_delta": max(3, min(14, total // 8)),
    }


def submit_message(user_id, match_id, message):
    message = str(message or "").strip()[:500]
    moderation = roast_safety_filter.moderate(message)
    if not moderation.get("ok"):
        return {"ok": False, **moderation}, 400
    score = score_roast(message)
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT INTO roast_messages (match_id, user_id, message, moderation_status, score_json, created_at) VALUES (?, ?, ?, 'approved', ?, ?)",
        (int(match_id), int(user_id), message, str(score), now),
    )
    message_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "message": {"id": message_id, "match_id": int(match_id), "user_id": int(user_id), "body": message, "created_at": now},
        "score": score,
        "avatar_reaction": score["avatar_reaction"],
        "crowd_delta": score["crowd_delta"],
        "commentator_line": "The crowd felt that one. Sharp, clean, and still inside the lines.",
    }, 200


def rooms():
    return {
        "ok": True,
        "rooms": [
            {"id": 1, "title": "Main Roast Stage", "status": "open", "queue_length": 0, "energy": 76},
            {"id": 2, "title": "Rivalry Room", "status": "watch", "queue_length": 2, "energy": 64},
        ],
    }
