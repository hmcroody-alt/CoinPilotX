"""Notification infrastructure health and cleanup helpers."""

from __future__ import annotations

import os
from datetime import datetime, timedelta


def _count(cur, query, params=()):
    try:
        cur.execute(query, params)
        row = cur.fetchone()
        if row is None:
            return 0
        return int((row[0] if not hasattr(row, "keys") else dict(row).get("total")) or 0)
    except Exception:
        return 0


def provider_config_status(conn):
    cur = conn.cursor()
    return {
        "in_app": {"status": "healthy", "configured": True, "message": "Database notifications are available."},
        "email": {
            "status": "healthy" if os.getenv("BREVO_API_KEY") and os.getenv("BREVO_EMAIL_ENABLED", "true").lower() not in {"0", "false", "no", "off"} else "missing_config",
            "configured": bool(os.getenv("BREVO_API_KEY") and os.getenv("BREVO_EMAIL_ENABLED", "true").lower() not in {"0", "false", "no", "off"}),
            "enabled": os.getenv("BREVO_EMAIL_ENABLED", "true").lower() not in {"0", "false", "no", "off"},
            "sender": os.getenv("BREVO_SENDER_EMAIL") or os.getenv("MAIL_FROM_ADDRESS") or "noreply@pulsesoc.com",
        },
        "push": {
            "status": "healthy" if os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY") else "missing_config",
            "configured": bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY")),
            "subscriptions": _count(cur, "SELECT COUNT(*) AS total FROM push_subscriptions WHERE COALESCE(is_active, active, 1)=1"),
        },
        "telegram": {
            "status": "healthy" if os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") else "missing_config",
            "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")),
            "linked_users": _count(cur, "SELECT COUNT(*) AS total FROM users WHERE telegram_chat_id IS NOT NULL AND telegram_chat_id!=''"),
        },
        "sms": {
            "status": "healthy" if (os.getenv("BREVO_SMS_API_KEY") or os.getenv("BREVO_API_KEY")) and os.getenv("BREVO_SMS_ENABLED", "").lower() == "true" and os.getenv("BREVO_SMS_SENDER") else "missing_config",
            "configured": bool((os.getenv("BREVO_SMS_API_KEY") or os.getenv("BREVO_API_KEY")) and os.getenv("BREVO_SMS_ENABLED", "").lower() == "true" and os.getenv("BREVO_SMS_SENDER")),
            "provider": "brevo_sms",
            "opted_in": _count(cur, "SELECT COUNT(*) AS total FROM users WHERE COALESCE(sms_opt_in,0)=1"),
        },
    }


def health_snapshot(conn):
    cur = conn.cursor()
    now = datetime.utcnow()
    day = (now - timedelta(days=1)).isoformat(timespec="seconds")
    sent = _count(cur, "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE status IN ('sent','created') AND created_at>=?", (day,))
    failed = _count(cur, "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE status IN ('failed','not_configured','rate_limited') AND created_at>=?", (day,))
    alert_failed = _count(cur, "SELECT COUNT(*) AS total FROM alert_delivery_jobs WHERE status IN ('failed','not_configured')")
    alert_pending = _count(cur, "SELECT COUNT(*) AS total FROM alert_delivery_jobs WHERE status IN ('pending','sending')")
    total = sent + failed
    success_rate = round((sent / total) * 100, 1) if total else 100.0
    provider_status = provider_config_status(conn)
    health_score = max(0, min(100, int(success_rate) - min(40, alert_failed // 100) - min(20, alert_pending // 250)))
    return {
        "health_score": health_score,
        "success_rate_24h": success_rate,
        "sent_24h": sent,
        "failed_24h": failed,
        "alert_failed_jobs": alert_failed,
        "alert_pending_jobs": alert_pending,
        "providers": provider_status,
        "updated_at": now.isoformat(timespec="seconds"),
    }


def quarantine_failed_jobs(conn, limit=500):
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        cur.execute(
            """
            UPDATE alert_delivery_jobs
            SET status='skipped', error_message=COALESCE(error_message,'') || ' | quarantined after repeated failure', next_retry_at=NULL
            WHERE id IN (
                SELECT id FROM alert_delivery_jobs
                WHERE status IN ('failed','not_configured') AND COALESCE(attempts,0)>=3
                ORDER BY id ASC
                LIMIT ?
            )
            """,
            (int(limit or 500),),
        )
        changed = cur.rowcount
        cur.execute(
            """
            INSERT INTO notification_failures (source_table, source_id, provider, channel, error_message, status, created_at)
            SELECT 'alert_delivery_jobs', id, provider, channel, error_message, 'quarantined', ?
            FROM alert_delivery_jobs
            WHERE status='skipped' AND error_message LIKE '%quarantined after repeated failure%'
            ORDER BY id DESC
            LIMIT ?
            """,
            (now, int(limit or 500)),
        )
        conn.commit()
        return {"ok": True, "quarantined": max(0, changed)}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
