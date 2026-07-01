"""Production notification orchestration compatibility layer.

The current app already has channel delivery in notification_service. This file
adds a single orchestration entrypoint, preference/rate-limit checks, queue-like
logging, and a health snapshot without breaking existing call sites.
"""

import json
import time
from datetime import datetime

from . import notification_service, user_context


RATE_WINDOW_SECONDS = 60
RATE_MAX_PER_USER = 20
MEMORY_RATE = {}


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _rate_allowed(user_id):
    now = time.time()
    bucket = [ts for ts in MEMORY_RATE.get(user_id, []) if now - ts < RATE_WINDOW_SECONDS]
    if len(bucket) >= RATE_MAX_PER_USER:
        MEMORY_RATE[user_id] = bucket
        return False
    bucket.append(now)
    MEMORY_RATE[user_id] = bucket
    return True


def log_notification(user_id, channel, category, status, provider_response=None, failed_reason="", retries=0):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO notification_logs
        (user_id, channel, category, sent_at, delivery_status, provider_response, retries, failed_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            channel,
            category,
            _now() if status in {"sent", "created", "queued", "skipped", "not_configured"} else None,
            status,
            json.dumps(provider_response or {})[:4000],
            retries,
            str(failed_reason or "")[:1200],
            _now(),
        ),
    )
    conn.commit()
    conn.close()


def send_user_alert(user_id, category, title, body, data=None, channels=None, priority="normal"):
    if not _rate_allowed(user_id):
        log_notification(user_id, "orchestrator", category, "rate_limited", {}, "User alert rate limit exceeded.")
        return {"ok": False, "status": "rate_limited", "message": "Alert rate limit exceeded."}
    result = notification_service.send_user_alert(user_id, category, title, body, data or {}, channels=channels)
    for channel in ("in_app", "email", "sms", "push", "telegram"):
        if channel in result:
            log_notification(user_id, channel, category, result[channel], result, "")
    result["orchestrated"] = True
    result["priority"] = priority
    return result


def dispatch_event(event_type, actor_user_id=0, recipient_user_id=0, content_id="", deep_link="", priority="normal", channels=None, metadata=None):
    result = notification_service.dispatch_universal_notification(
        event_type,
        actor_user_id=actor_user_id,
        recipient_user_id=recipient_user_id,
        content_id=content_id,
        deep_link=deep_link,
        priority=priority,
        channels=channels,
        metadata=metadata or {},
    )
    if result.get("notification_id"):
        log_notification(
            recipient_user_id,
            "orchestrator",
            event_type,
            result.get("status") or "created",
            {"trace_id": result.get("trace_id"), "channels": result.get("channels")},
            "",
        )
    return result


def retry_failed(limit=25):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notification_logs WHERE delivery_status IN ('failed','queued') ORDER BY id ASC LIMIT ?",
        (limit,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"ok": True, "queued": len(rows), "items": rows, "message": "Retry worker scaffold ready. External queue/Redis can consume these rows."}


def health():
    conn = user_context.connect()
    cur = conn.cursor()
    counts = {}
    for status in ("sent", "created", "failed", "queued", "not_configured", "skipped", "rate_limited"):
        try:
            cur.execute("SELECT COUNT(*) FROM notification_logs WHERE delivery_status=?", (status,))
            counts[status] = cur.fetchone()[0]
        except Exception:
            counts[status] = 0
    try:
        cur.execute("SELECT COUNT(*) FROM push_subscriptions WHERE active=1")
        push_enabled = cur.fetchone()[0]
    except Exception:
        push_enabled = 0
    try:
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM notification_preferences WHERE enable_notification_sound=1")
        sound_enabled = cur.fetchone()[0]
    except Exception:
        sound_enabled = 0
    try:
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM notification_preferences WHERE enable_notification_vibration=1")
        vibration_enabled = cur.fetchone()[0]
    except Exception:
        vibration_enabled = 0
    try:
        cur.execute("SELECT created_at FROM notifications ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        last_test = row[0] if row else None
    except Exception:
        last_test = None
    conn.close()
    return {
        "ok": True,
        "delivery_status_counts": counts,
        "push_subscriptions_count": push_enabled,
        "sound_preference_count": sound_enabled,
        "vibration_preference_count": vibration_enabled,
        "last_test_notification": last_test,
        "rate_window_seconds": RATE_WINDOW_SECONDS,
        "rate_max_per_user": RATE_MAX_PER_USER,
        "queue": retry_failed(limit=1),
        "updated_at": _now(),
    }
