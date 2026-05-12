import json
from datetime import datetime

from . import user_context


def _now():
    return datetime.now().isoformat()


def queue_notification(user_id, title, message, notification_type="general", metadata=None):
    if not user_id:
        return {"ok": False, "message": "User required."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO notifications (user_id, notification_type, title, message, status, metadata, created_at)
        VALUES (?, ?, ?, ?, 'unread', ?, ?)
        """,
        (user_id, notification_type, title[:180], message[:2000], json.dumps(metadata or {})[:4000], _now()),
    )
    conn.commit()
    notification_id = cur.lastrowid
    conn.close()
    return {"ok": True, "notification_id": notification_id}


def list_notifications(user_id, limit=50):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"ok": True, "notifications": rows}


def save_push_subscription(user_id, subscription, user_agent=""):
    endpoint = (subscription or {}).get("endpoint") or ""
    if not user_id or not endpoint:
        return {"ok": False, "message": "Push subscription endpoint required."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO push_subscriptions (user_id, endpoint, subscription_json, user_agent, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id=excluded.user_id,
            subscription_json=excluded.subscription_json,
            user_agent=excluded.user_agent,
            active=1,
            updated_at=excluded.updated_at
        """,
        (user_id, endpoint, json.dumps(subscription)[:8000], user_agent[:600], _now(), _now()),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Push notifications connected."}


def send_in_app_notification(user_id, title, message, notification_type="general", metadata=None):
    return queue_notification(user_id, title, message, notification_type, metadata)


def send_push_notification(*_args, **_kwargs):
    return {"ok": False, "message": "Web push provider is prepared but not configured yet."}


def send_email_alert(*_args, **_kwargs):
    return {"ok": False, "message": "Email alert delivery is handled by the centralized email service."}


def send_telegram_alert(*_args, **_kwargs):
    return {"ok": False, "message": "Telegram alert delivery is handled by the bot runtime."}


def retry_failed_notification():
    return {"ok": True, "retried": 0}
