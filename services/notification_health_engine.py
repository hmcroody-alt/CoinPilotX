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


def _enabled(name, default="true"):
    return os.getenv(name, default).lower() not in {"0", "false", "no", "off"}


def _delivery_evidence(cur, channel, since):
    sent = _count(
        cur,
        "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE channel=? AND status IN ('sent','created') AND created_at>=?",
        (channel, since),
    )
    failed = _count(
        cur,
        "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE channel=? AND status IN ('failed','rate_limited') AND created_at>=?",
        (channel, since),
    )
    setup_skipped = _count(
        cur,
        "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE channel=? AND status IN ('not_configured','permission_denied','skipped') AND created_at>=?",
        (channel, since),
    )
    return {"sent_24h": sent, "failed_24h": failed, "setup_skipped_24h": setup_skipped}


def _push_evidence(cur, since):
    statuses = {}
    try:
        cur.execute(
            "SELECT status, COUNT(*) AS total FROM push_delivery_jobs WHERE created_at>=? GROUP BY status",
            (since,),
        )
        for row in cur.fetchall():
            item = dict(row) if hasattr(row, "keys") else {"status": row[0], "total": row[1]}
            statuses[str(item.get("status") or "unknown")] = int(item.get("total") or 0)
    except Exception:
        pass
    sent = sum(statuses.get(key, 0) for key in ("sent", "delivered"))
    failed = sum(statuses.get(key, 0) for key in ("failed", "dead_letter"))
    pending = sum(statuses.get(key, 0) for key in ("pending", "queued", "processing", "retry"))
    return {"jobs_24h": statuses, "sent_24h": sent, "failed_24h": failed, "pending_24h": pending}


def provider_config_status(conn):
    cur = conn.cursor()
    day = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
    email_enabled = _enabled("BREVO_EMAIL_ENABLED")
    email_configured = bool(os.getenv("BREVO_API_KEY") and email_enabled)
    email_evidence = _delivery_evidence(cur, "email", day)
    email_status = (
        "degraded"
        if email_evidence["failed_24h"] >= max(1, email_evidence["sent_24h"])
        else ("healthy" if email_evidence["sent_24h"] else ("ready" if email_configured else "missing_config"))
    )

    active_subscriptions = _count(cur, "SELECT COUNT(*) AS total FROM push_subscriptions WHERE COALESCE(is_active, active, 1)=1")
    active_devices = _count(cur, "SELECT COUNT(*) AS total FROM user_device_tokens WHERE COALESCE(enabled,1)=1 AND revoked_at IS NULL")
    push_evidence = _push_evidence(cur, day)
    if push_evidence["failed_24h"] >= max(1, push_evidence["sent_24h"]):
        push_status = "degraded"
    elif push_evidence["sent_24h"]:
        push_status = "healthy"
    elif push_evidence["pending_24h"]:
        push_status = "processing"
    elif active_subscriptions or active_devices:
        push_status = "ready"
    else:
        push_status = "no_devices"

    telegram_configured = bool(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN"))
    sms_configured = bool(
        (os.getenv("BREVO_SMS_API_KEY") or os.getenv("BREVO_API_KEY"))
        and os.getenv("BREVO_SMS_ENABLED", "").lower() == "true"
        and os.getenv("BREVO_SMS_SENDER")
    )
    return {
        "in_app": {
            "status": "healthy",
            "configured": True,
            "message": "Database notifications are available.",
            **_delivery_evidence(cur, "in_app", day),
        },
        "email": {
            "status": email_status,
            "configured": email_configured,
            "enabled": email_enabled,
            "message": "Recent delivery verified." if email_evidence["sent_24h"] else ("Provider configured; no recent delivery proof." if email_configured else "Email provider is not configured in the Main App."),
            **email_evidence,
        },
        "push": {
            "status": push_status,
            "configured": None,
            "runtime_scope": "command_center_worker",
            "message": "Recent worker delivery verified." if push_evidence["sent_24h"] else ("Push jobs are failing or dead-lettered." if push_evidence["failed_24h"] else ("Push jobs are waiting for worker processing." if push_evidence["pending_24h"] else ("Registered devices are ready; send a test for delivery proof." if active_subscriptions or active_devices else "No active push subscription or device token is registered."))),
            "subscriptions": active_subscriptions,
            "devices": active_devices,
            **push_evidence,
        },
        "telegram": {
            "status": "ready" if telegram_configured else "disabled",
            "configured": telegram_configured,
            "linked_users": _count(cur, "SELECT COUNT(*) AS total FROM users WHERE telegram_chat_id IS NOT NULL AND telegram_chat_id!=''"),
            "message": "Optional companion channel." if telegram_configured else "Optional companion channel is disabled.",
        },
        "sms": {
            "status": "ready" if sms_configured else "disabled",
            "configured": sms_configured,
            "provider": "brevo_sms",
            "opted_in": _count(cur, "SELECT COUNT(*) AS total FROM users WHERE COALESCE(sms_opt_in,0)=1"),
            "message": "Optional SMS channel." if sms_configured else "Optional SMS channel is disabled.",
        },
    }


def health_snapshot(conn):
    cur = conn.cursor()
    now = datetime.utcnow()
    day = (now - timedelta(days=1)).isoformat(timespec="seconds")
    sent = _count(cur, "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE status IN ('sent','created') AND created_at>=?", (day,))
    failed = _count(cur, "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE status IN ('failed','rate_limited') AND created_at>=?", (day,))
    setup_skipped = _count(cur, "SELECT COUNT(*) AS total FROM notification_delivery_logs WHERE status IN ('not_configured','permission_denied','skipped') AND created_at>=?", (day,))
    alert_failed = _count(cur, "SELECT COUNT(*) AS total FROM alert_delivery_jobs WHERE status='failed' AND created_at>=?", (day,))
    alert_pending = _count(cur, "SELECT COUNT(*) AS total FROM alert_delivery_jobs WHERE status IN ('pending','sending') AND created_at>=?", (day,))
    push = _push_evidence(cur, day)
    total = sent + failed
    success_rate = round((sent / total) * 100, 1) if total else 100.0
    provider_status = provider_config_status(conn)
    health_score = max(
        0,
        min(
            100,
            int(success_rate)
            - min(35, alert_failed * 2)
            - min(25, push["failed_24h"] * 3)
            - min(10, (alert_pending + push["pending_24h"]) // 25),
        ),
    )
    return {
        "health_score": health_score,
        "success_rate_24h": success_rate,
        "sent_24h": sent,
        "failed_24h": failed,
        "setup_skipped_24h": setup_skipped,
        "alert_failed_jobs": alert_failed,
        "alert_pending_jobs": alert_pending,
        "push_jobs": push,
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
                WHERE status='failed' AND COALESCE(attempts,0)>=3
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
