"""Production-ready optional Web Push delivery for CoinPilotXAI.

This module works when pywebpush + VAPID env vars are configured, and returns
honest not_configured/skipped statuses when they are not.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from . import user_context


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _keys(subscription):
    keys = (subscription or {}).get("keys") or {}
    return keys.get("p256dh") or "", keys.get("auth") or ""


def save_subscription(user_id, subscription, user_agent="", device_type="", browser=""):
    endpoint = (subscription or {}).get("endpoint") or ""
    if not user_id or not endpoint:
        return {"ok": False, "message": "Push subscription endpoint required."}
    p256dh, auth = _keys(subscription)
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO push_subscriptions
        (user_id, endpoint, subscription_json, p256dh, auth, user_agent, device_type, browser, active, is_active, created_at, updated_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id=excluded.user_id,
            subscription_json=excluded.subscription_json,
            p256dh=excluded.p256dh,
            auth=excluded.auth,
            user_agent=excluded.user_agent,
            device_type=excluded.device_type,
            browser=excluded.browser,
            active=1,
            is_active=1,
            updated_at=excluded.updated_at,
            last_seen_at=excluded.last_seen_at
        """,
        (user_id, endpoint, json.dumps(subscription)[:8000], p256dh, auth, user_agent[:600], device_type[:80], browser[:120], _now(), _now(), _now()),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Push notifications connected."}


def _payload(title, body, data=None, push_type="general"):
    data = data or {}
    url = data.get("url") or {
        "arena_invite": "/arena",
        "private_message": "/messages",
        "market_alert": "/alerts",
        "AI_briefing": "/chat",
        "quest_complete": "/arena/quests",
        "faction_attack": "/arena/world",
        "watchlist_move": "/watch",
        "btc_breakout": "/quote/crypto/BTC",
        "whale_alert": "/whale-alerts",
        "scam_warning": "/scam-shield",
    }.get(push_type, "/pulse/notifications")
    return {
        "title": title[:120],
        "body": body[:240],
        "tag": f"coinpilotxai-{push_type}",
        "renotify": push_type in {"arena_invite", "scam_warning", "private_message", "market_alert"},
        "vibrate": [200, 100, 200],
        "data": {"url": url, "push_type": push_type, **data},
        "actions": [{"action": "open", "title": "Open"}, {"action": "dismiss", "title": "Dismiss"}],
    }


def send_push(user_id, title, body, data=None, push_type="general"):
    if not os.getenv("VAPID_PUBLIC_KEY") or not os.getenv("VAPID_PRIVATE_KEY"):
        return {"ok": False, "status": "not_configured", "message": "VAPID push variables are not configured."}
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        return {"ok": False, "status": "not_configured", "message": "pywebpush is not installed."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT id, endpoint, subscription_json FROM push_subscriptions WHERE user_id=? AND COALESCE(is_active, active, 1)=1", (user_id,))
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return {"ok": False, "status": "not_configured", "message": "No active browser push subscription."}
    sent = 0
    failures = []
    invalid_ids = []
    payload = _payload(title, body, data, push_type)
    for row in rows:
        sub_id, endpoint, subscription_json = row[0], row[1], row[2]
        try:
            webpush(
                subscription_info=json.loads(subscription_json),
                data=json.dumps(payload),
                vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
                vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:support@pulsesoc.com")},
                timeout=10,
            )
            sent += 1
        except WebPushException as exc:
            message = str(exc)[:400]
            failures.append(message)
            if getattr(exc, "response", None) is not None and exc.response.status_code in (404, 410):
                invalid_ids.append(sub_id)
        except Exception as exc:
            failures.append(str(exc)[:400])
    for sub_id in invalid_ids:
        cur.execute("UPDATE push_subscriptions SET active=0, is_active=0, updated_at=? WHERE id=?", (_now(), sub_id))
    conn.commit()
    conn.close()
    return {"ok": sent > 0, "status": "sent" if sent else "failed", "sent": sent, "failures": failures, "invalidated": len(invalid_ids)}


def broadcast_user_notification(user_id, notification):
    notification = notification or {}
    return send_push(
        user_id,
        notification.get("title") or "PulseSoc Alert",
        notification.get("message") or notification.get("body") or "New intelligence update.",
        notification.get("data") or {},
        notification.get("push_type") or notification.get("notification_type") or "general",
    )


def cleanup_invalid_subscriptions():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE push_subscriptions SET active=0, is_active=0 WHERE endpoint='' OR endpoint IS NULL")
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "cleaned": changed}
