import json
import logging
import os
from datetime import datetime

from . import user_context
from . import email_service


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


def mark_read(user_id, notification_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE notifications SET status='read', read_at=? WHERE id=? AND user_id=?",
        (_now(), notification_id, user_id),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "updated": changed}


def mark_all_read(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE notifications SET status='read', read_at=? WHERE user_id=? AND status!='read'",
        (_now(), user_id),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "updated": changed}


def get_preferences(user_id):
    categories = [
        "payment_confirmations",
        "account_security",
        "pro_activation",
        "market_alerts",
        "whale_alerts",
        "scam_alerts",
        "wallet_alerts",
        "portfolio_alerts",
        "sports_edge_alerts",
        "product_updates",
    ]
    conn = user_context.connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT category, in_app, push, email, telegram, sms FROM notification_preferences WHERE user_id=?",
            (user_id,),
        )
        rows = cur.fetchall()
    except Exception:
        cur.execute(
            "SELECT category, in_app, push, email, telegram FROM notification_preferences WHERE user_id=?",
            (user_id,),
        )
        rows = [tuple(row) + (0,) for row in cur.fetchall()]
    existing = {
        row[0]: {
            "in_app": bool(row[1]),
            "push": bool(row[2]),
            "email": bool(row[3]),
            "telegram": bool(row[4]),
            "sms": bool(row[5]),
        }
        for row in rows
    }
    conn.close()
    return {"ok": True, "preferences": {category: existing.get(category, {"in_app": True, "push": False, "email": False, "sms": False, "telegram": False}) for category in categories}}


def update_preferences(user_id, preferences):
    conn = user_context.connect()
    cur = conn.cursor()
    for category, values in (preferences or {}).items():
        cur.execute(
            """
            INSERT INTO notification_preferences (user_id, category, in_app, push, email, telegram, sms, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
                in_app=excluded.in_app,
                push=excluded.push,
                email=excluded.email,
                telegram=excluded.telegram,
                sms=excluded.sms,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                str(category)[:80],
                1 if values.get("in_app", True) else 0,
                1 if values.get("push") else 0,
                1 if values.get("email") else 0,
                1 if values.get("telegram") else 0,
                1 if values.get("sms") else 0,
                _now(),
            ),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


def _log_delivery(user_id, notification_id, channel, status, provider_response="", error_message=""):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO notification_delivery_logs
        (user_id, notification_id, channel, status, provider_response, error_message, retry_count, created_at, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            user_id,
            notification_id,
            channel,
            status,
            json.dumps(provider_response)[:4000] if isinstance(provider_response, (dict, list)) else str(provider_response or "")[:4000],
            str(error_message or "")[:1200],
            _now(),
            _now() if status in {"sent", "created", "skipped", "not_configured"} else None,
        ),
    )
    try:
        cur.execute(
            """
            INSERT INTO notification_logs
            (user_id, channel, category, sent_at, delivery_status, provider_response, retries, failed_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                user_id,
                channel,
                channel,
                _now() if status in {"sent", "created", "skipped", "not_configured", "queued"} else None,
                status,
                json.dumps(provider_response)[:4000] if isinstance(provider_response, (dict, list)) else str(provider_response or "")[:4000],
                str(error_message or "")[:1200],
                _now(),
            ),
        )
    except Exception:
        logging.info("notification_logs table not ready; delivery log kept in notification_delivery_logs")
    conn.commit()
    conn.close()


def _user_record(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=? LIMIT 1", (user_id,))
    row = user_context.row_to_dict(cur.fetchone())
    conn.close()
    return row or {}


def _category_prefs(user_id, alert_type):
    prefs = get_preferences(user_id).get("preferences", {})
    return prefs.get(alert_type) or prefs.get("market_alerts") or {"in_app": True, "push": False, "email": False, "sms": False, "telegram": False}


def send_sms_alert(user, title, message, notification_id=None):
    user = user or {}
    if not os.getenv("TWILIO_ACCOUNT_SID") or not os.getenv("TWILIO_AUTH_TOKEN") or not os.getenv("TWILIO_FROM_NUMBER"):
        return {"ok": False, "status": "not_configured", "message": "Twilio SMS variables are not configured."}
    if not user.get("phone") or int(user.get("sms_opt_in") or 0) != 1:
        return {"ok": False, "status": "skipped", "message": "User has not opted in to SMS alerts or has no phone."}
    try:
        import requests

        sid = os.getenv("TWILIO_ACCOUNT_SID")
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, os.getenv("TWILIO_AUTH_TOKEN")),
            data={
                "From": os.getenv("TWILIO_FROM_NUMBER"),
                "To": user.get("phone"),
                "Body": f"{title}\n\n{message}"[:1400],
            },
            timeout=12,
        )
        ok = 200 <= response.status_code < 300
        return {"ok": ok, "status": "sent" if ok else "failed", "response": response.text[:1000], "status_code": response.status_code}
    except Exception as exc:
        logging.info("SMS alert failed safely: %s", exc)
        return {"ok": False, "status": "failed", "message": str(exc)}


def send_push_alert(user_id, title, message, metadata=None):
    if not os.getenv("VAPID_PUBLIC_KEY") or not os.getenv("VAPID_PRIVATE_KEY"):
        return {"ok": False, "status": "not_configured", "message": "VAPID push variables are not configured."}
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return {"ok": False, "status": "not_configured", "message": "pywebpush is not installed."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT endpoint, subscription_json FROM push_subscriptions WHERE user_id=? AND active=1", (user_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return {"ok": False, "status": "skipped", "message": "No active browser push subscription."}
    sent = 0
    failures = []
    for endpoint, subscription_json in rows:
        try:
            webpush(
                subscription_info=json.loads(subscription_json),
                data=json.dumps({"title": title, "body": message, "data": metadata or {}, "url": (metadata or {}).get("url") or "/notifications"}),
                vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
                vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:support@coinpilotx.app")},
            )
            sent += 1
        except WebPushException as exc:
            failures.append(str(exc)[:300])
        except Exception as exc:
            failures.append(str(exc)[:300])
    return {"ok": sent > 0, "status": "sent" if sent else "failed", "sent": sent, "failures": failures}


def send_telegram_alert(user, title, message):
    user = user or {}
    if not user.get("telegram_chat_id"):
        return {"ok": False, "status": "skipped", "message": "Telegram companion is not linked."}
    return {"ok": False, "status": "queued", "message": "Telegram delivery is handled by the bot runtime."}


def send_user_alert(user_id, alert_type, title, body, data=None, channels=None):
    """Create a real in-app alert and attempt enabled delivery channels without blocking the app."""
    data = data or {}
    user = _user_record(user_id)
    prefs = _category_prefs(user_id, alert_type)
    requested = set(channels or [])
    if not requested:
        requested = {"in_app"}
        for key in ("email", "sms", "push", "telegram"):
            if prefs.get(key):
                requested.add(key)
    created = queue_notification(user_id, title, body, alert_type, data)
    notification_id = created.get("notification_id")
    result = {"ok": True, "notification_id": notification_id, "in_app": "created"}
    _log_delivery(user_id, notification_id, "in_app", "created", {"notification_id": notification_id}, "")
    if "email" in requested:
        if prefs.get("email") or "email" in (channels or []):
            email_result = email_service.send_email(user.get("email"), title, f"<p>{body}</p>", body, email_type=alert_type)
            status = "sent" if email_result.get("ok") else "failed"
            result["email"] = status
            _log_delivery(user_id, notification_id, "email", status, email_result.get("provider_response") or email_result, email_result.get("error"))
        else:
            result["email"] = "skipped"
            _log_delivery(user_id, notification_id, "email", "skipped", "", "Email alerts disabled.")
    if "sms" in requested:
        sms_result = send_sms_alert(user, title, body, notification_id)
        result["sms"] = sms_result.get("status", "failed")
        _log_delivery(user_id, notification_id, "sms", result["sms"], sms_result, sms_result.get("message"))
    if "push" in requested:
        push_result = send_push_alert(user_id, title, body, data)
        result["push"] = push_result.get("status", "failed")
        _log_delivery(user_id, notification_id, "push", result["push"], push_result, push_result.get("message"))
    if "telegram" in requested:
        telegram_result = send_telegram_alert(user, title, body)
        result["telegram"] = telegram_result.get("status", "failed")
        _log_delivery(user_id, notification_id, "telegram", result["telegram"], telegram_result, telegram_result.get("message"))
    return result


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


def unsubscribe_push(user_id, endpoint=""):
    conn = user_context.connect()
    cur = conn.cursor()
    if endpoint:
        cur.execute("UPDATE push_subscriptions SET active=0, updated_at=? WHERE user_id=? AND endpoint=?", (_now(), user_id, endpoint))
    else:
        cur.execute("UPDATE push_subscriptions SET active=0, updated_at=? WHERE user_id=?", (_now(), user_id))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "updated": changed}


def send_in_app_notification(user_id, title, message, notification_type="general", metadata=None):
    return queue_notification(user_id, title, message, notification_type, metadata)


def send_push_notification(*_args, **_kwargs):
    return {"ok": False, "message": "Web push provider is prepared but not configured yet."}


def send_email_alert(*_args, **_kwargs):
    return {"ok": False, "message": "Email alert delivery is handled by the centralized email service."}


def send_telegram_alert(user=None, title="", message=""):
    user = user or {}
    if not user.get("telegram_chat_id"):
        return {"ok": False, "status": "skipped", "message": "Telegram companion is not linked."}
    return {"ok": False, "status": "queued", "message": "Telegram delivery is handled by the bot runtime.", "title": title, "body": message}


def retry_failed_notification():
    return {"ok": True, "retried": 0}
