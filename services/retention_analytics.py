"""Retention and product-health summaries for the core loop."""

import json
import sqlite3
from datetime import datetime, timedelta

from . import user_context


CORE_EVENTS = [
    "signup_completed",
    "arena_entered",
    "arena_game_completed",
    "private_message_sent",
    "alert_created",
    "share_clicked",
    "pro_subscription_active",
]


def _since(days):
    return (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")


def _rows(cur, query, params=()):
    cur.execute(query, params)
    return [dict(row) for row in cur.fetchall()]


def retention_summary():
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    counts = {}
    for event in CORE_EVENTS:
        cur.execute("SELECT COUNT(*) AS total FROM analytics_events WHERE event_name=? AND created_at>=?", (event, _since(30)))
        counts[event] = int((cur.fetchone() or {"total": 0})["total"])
    cur.execute("SELECT COUNT(DISTINCT user_id) AS total FROM analytics_events WHERE user_id>0 AND created_at>=?", (_since(1),))
    active_1d = int((cur.fetchone() or {"total": 0})["total"])
    cur.execute("SELECT COUNT(DISTINCT user_id) AS total FROM analytics_events WHERE user_id>0 AND created_at>=?", (_since(7),))
    active_7d = int((cur.fetchone() or {"total": 0})["total"])
    top_modes = _rows(
        cur,
        """
        SELECT event_name AS mode, COUNT(*) AS total
        FROM analytics_events
        WHERE event_name LIKE 'arena_%' AND created_at>=?
        GROUP BY event_name
        ORDER BY total DESC
        LIMIT 8
        """,
        (_since(30),),
    )
    drops = [
        {"step": "signup_to_arena", "risk": "high" if counts.get("arena_entered", 0) < counts.get("signup_completed", 0) else "normal", "detail": f"{counts.get('arena_entered', 0)} arena entries after {counts.get('signup_completed', 0)} signups"},
        {"step": "arena_to_completion", "risk": "high" if counts.get("arena_game_completed", 0) < counts.get("arena_entered", 0) else "normal", "detail": f"{counts.get('arena_game_completed', 0)} completions from {counts.get('arena_entered', 0)} entries"},
        {"step": "play_to_share", "risk": "watch", "detail": f"{counts.get('share_clicked', 0)} shares in the last 30 days"},
    ]
    conn.close()
    return {"ok": True, "counts": counts, "active_1d": active_1d, "active_7d": active_7d, "top_modes": top_modes, "dropoffs": drops}


def product_health_summary():
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    recent_errors = _rows(cur, "SELECT event_name, page_url, metadata, created_at FROM analytics_events WHERE (event_name LIKE '%error%' OR event_name LIKE '%failed%' OR event_name LIKE '%blocked%') AND created_at>=? ORDER BY id DESC LIMIT 30", (_since(7),))
    cur.execute("SELECT COUNT(*) AS total FROM analytics_events WHERE page_url LIKE '%/arena/match/None%' OR page_url LIKE '%undefined%'")
    bad_routes = int((cur.fetchone() or {"total": 0})["total"])
    cur.execute("SELECT COUNT(*) AS total FROM alert_worker_heartbeat WHERE last_run_at>=?", (_since(1),))
    alert_worker_recent = int((cur.fetchone() or {"total": 0})["total"])
    chat_rows = _rows(cur, "SELECT COUNT(*) AS messages_today FROM private_messages WHERE created_at>=?", (_since(1),))
    checks = [
        {"name": "No /None route activity", "ok": bad_routes == 0, "detail": f"{bad_routes} suspicious routes recorded"},
        {"name": "Alert worker heartbeat", "ok": alert_worker_recent > 0, "detail": "worker active in last day" if alert_worker_recent else "no worker heartbeat in last day"},
        {"name": "Private chat activity", "ok": True, "detail": f"{(chat_rows[0] if chat_rows else {}).get('messages_today', 0)} messages today"},
    ]
    conn.close()
    return {"ok": True, "checks": checks, "recent_errors": recent_errors}


def record_product_health_check(admin_id=0):
    payload = product_health_summary()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO product_health_checks (admin_id, payload_json, created_at) VALUES (?, ?, ?)",
        (int(admin_id or 0), json.dumps(payload)[:8000], datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return payload
