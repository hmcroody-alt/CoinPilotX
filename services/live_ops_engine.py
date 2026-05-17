"""Live-ops planning for the CoinPilotXAI core loop."""

import json
import sqlite3
from datetime import date, datetime

from . import user_context


START_HERE_STEPS = [
    {"key": "training", "title": "Training Mission", "url": "/arena/daily", "xp": 35},
    {"key": "scam_hunter", "title": "Scam Hunter", "url": "/arena/scam-rush", "xp": 50},
    {"key": "quick_battle", "title": "Quick Battle", "url": "/arena/quick-battle", "xp": 40},
    {"key": "live_room", "title": "Live Room", "url": "/arena/live", "xp": 25},
    {"key": "roast_battle", "title": "Roast Battle", "url": "/arena/roast-battle", "xp": 30},
]


def _today():
    return date.today().isoformat()


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _plan_for_day(day_key):
    modes = ["Training Mission", "Scam Hunter", "Quick Battle", "Live Room", "Roast Battle"]
    themes = ["Discipline under pressure", "Spot the trap", "Clean first win", "Crowd energy", "Sharp but safe banter"]
    index = sum(ord(ch) for ch in day_key) % len(modes)
    return {
        "date": day_key,
        "featured_mode": modes[index],
        "daily_mission": START_HERE_STEPS[index],
        "daily_reward": "+50 XP and Core Loop badge progress",
        "scam_lesson": "Verify before you sign. Urgency is the oldest crypto exploit.",
        "roast_theme": themes[index],
        "leaderboard_spotlight": "Crowd Favorite and Scam Sentinel are rotating today.",
        "weekly_event": "Weekend Alpha Arena ladder: Training to Live Room progression.",
    }


def get_daily_plan():
    day_key = _today()
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT payload_json FROM live_ops_plans WHERE plan_date=? LIMIT 1", (day_key,))
    row = cur.fetchone()
    if row:
        try:
            payload = json.loads(row[0] if not hasattr(row, "keys") else row["payload_json"])
        except Exception:
            payload = _plan_for_day(day_key)
    else:
        payload = _plan_for_day(day_key)
        cur.execute(
            "INSERT INTO live_ops_plans (plan_date, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (day_key, json.dumps(payload), _now(), _now()),
        )
        conn.commit()
    conn.close()
    return {"ok": True, "plan": payload, "start_here": START_HERE_STEPS}


def user_start_here_progress(user_id):
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT step_key, completed_at FROM user_onboarding_progress WHERE user_id=?", (int(user_id),))
    completed = {row["step_key"] if hasattr(row, "keys") else row[0]: row["completed_at"] if hasattr(row, "keys") else row[1] for row in cur.fetchall()}
    conn.close()
    steps = []
    for item in START_HERE_STEPS:
        steps.append({**item, "completed": item["key"] in completed, "completed_at": completed.get(item["key"])})
    return {"ok": True, "steps": steps, "completed_count": sum(1 for step in steps if step["completed"]), "total": len(steps)}


def mark_step(user_id, step_key):
    allowed = {step["key"] for step in START_HERE_STEPS}
    if step_key not in allowed:
        return {"ok": False, "message": "Unknown onboarding step."}
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        "INSERT OR IGNORE INTO user_onboarding_progress (user_id, step_key, completed_at, created_at) VALUES (?, ?, ?, ?)",
        (int(user_id), step_key, now, now),
    )
    conn.commit()
    conn.close()
    return user_start_here_progress(user_id)
