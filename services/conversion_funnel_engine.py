"""Conversion funnel tracking for ads and retention experiments."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import db as db_service


FUNNEL_STEPS = {
    "page_view": "visit",
    "signup_started": "homepage_to_signup",
    "signup_click": "homepage_to_signup",
    "signup_form_submit": "homepage_to_signup",
    "signup_completed": "account_creation",
    "account_created": "account_creation",
    "arena_entered": "signup_to_arena",
    "arena_session_start": "signup_to_arena",
    "roast_battle_entered": "roast_to_replay_share",
    "roast_battle_join": "roast_to_replay_share",
    "replay_view": "replay_to_signup",
    "replay_share": "roast_to_replay_share",
    "scam_shield_scan": "scam_shield_to_account_creation",
    "scam_shield_used": "scam_shield_to_account_creation",
    "return_visit": "arena_to_return_visit",
    "alert_activation": "alert_activation",
    "alert_created": "alert_activation",
    "pro_upgrade": "pro_upgrade",
    "pro_payment_success": "pro_upgrade",
    "pro_subscription_active": "pro_upgrade",
    "telegram_connect": "telegram_connect",
    "sms_activation": "sms_activation",
}


def normalize_funnel(event_name: str) -> str:
    return FUNNEL_STEPS.get((event_name or "").strip(), "general")


def track_step(user_id=0, session_id="", event_name="", source_path="", metadata=None):
    """Persist a lightweight funnel step without blocking the product flow."""
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversion_funnel_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id TEXT,
                funnel_name TEXT,
                step_name TEXT,
                source_path TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            INSERT INTO conversion_funnel_events
            (user_id, session_id, funnel_name, step_name, source_path, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id or 0),
                (session_id or "")[:120],
                normalize_funnel(event_name),
                (event_name or "unknown")[:100],
                (source_path or "")[:700],
                json.dumps(metadata or {})[:4000],
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}


def funnel_summary(days=30):
    """Return compact funnel counts for admin dashboards and ad checks."""
    since = (datetime.now() - timedelta(days=int(days or 30))).isoformat()
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT funnel_name, step_name, COUNT(*) AS count
            FROM conversion_funnel_events
            WHERE created_at >= ?
            GROUP BY funnel_name, step_name
            ORDER BY count DESC
            """,
            (since,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return {"ok": True, "days": days, "rows": rows}
    except Exception as exc:
        return {"ok": False, "days": days, "rows": [], "error": str(exc)[:240]}
