"""Production-ready optional Web Push delivery for CoinPilotXAI.

This module works when pywebpush + VAPID env vars are configured, and returns
honest not_configured/skipped statuses when they are not.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import hashlib
from datetime import datetime

import requests

from . import user_context


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _keys(subscription):
    keys = (subscription or {}).get("keys") or {}
    return keys.get("p256dh") or "", keys.get("auth") or ""


def _push_trace_enabled():
    return str(os.getenv("PUSH_TRACE_ENABLED", "1")).lower() not in {"0", "false", "off", "no"}


def _endpoint_hash(endpoint):
    return hashlib.sha256(str(endpoint or "").encode("utf-8")).hexdigest()[:16] if endpoint else ""


def _trace(stage, **fields):
    if not _push_trace_enabled():
        return
    safe = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key in {"endpoint", "token", "subscription", "subscription_json", "auth", "p256dh"}:
            continue
        safe[key] = value
    logging.info("PUSH_TRACE stage=%s %s", stage, json.dumps(safe, default=str, sort_keys=True)[:2000])


def _ensure_user_device_tokens(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            platform TEXT,
            device_id TEXT,
            push_token TEXT,
            push_provider TEXT,
            environment TEXT,
            app_version TEXT,
            device_label TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            last_seen_at TEXT,
            revoked_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_device_tokens_user_enabled ON user_device_tokens(user_id, enabled)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_device_tokens_device ON user_device_tokens(device_id)")


def _device_label(subscription, user_agent="", device_type="", browser=""):
    label = (subscription or {}).get("device_label") or (subscription or {}).get("deviceLabel") or ""
    if label:
        return str(label)[:160]
    return " ".join(part for part in [device_type or "", browser or ""] if part).strip()[:160] or (user_agent or "device")[:160]


def save_subscription(user_id, subscription, user_agent="", device_type="", browser=""):
    endpoint = (subscription or {}).get("endpoint") or ""
    if not user_id or not endpoint:
        _trace("token_register_rejected", user_id=int(user_id or 0), reason="missing_endpoint")
        return {"ok": False, "message": "Push subscription endpoint required."}
    p256dh, auth = _keys(subscription)
    provider = "expo" if _is_expo_token(endpoint, subscription) else "webpush"
    conn = user_context.connect()
    cur = conn.cursor()
    _ensure_user_device_tokens(cur)
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
    device_id = (
        (subscription or {}).get("device_id")
        or (subscription or {}).get("deviceId")
        or (subscription or {}).get("installation_id")
        or _endpoint_hash(endpoint)
    )
    platform = (subscription or {}).get("platform") or device_type or ("ios" if "iphone" in (user_agent or "").lower() else "android" if "android" in (user_agent or "").lower() else "web")
    environment = (subscription or {}).get("environment") or os.getenv("PUSH_ENVIRONMENT") or os.getenv("RAILWAY_ENVIRONMENT_NAME") or "production"
    app_version = (subscription or {}).get("app_version") or (subscription or {}).get("appVersion") or ""
    label = _device_label(subscription, user_agent, device_type, browser)
    cur.execute(
        """
        UPDATE user_device_tokens
        SET user_id=?, platform=?, push_token=?, push_provider=?, environment=?, app_version=?,
            device_label=?, enabled=1, updated_at=?, last_seen_at=?, revoked_at=''
        WHERE device_id=?
        """,
        (int(user_id), str(platform)[:80], endpoint, provider, str(environment)[:120], str(app_version)[:80], label, _now(), _now(), str(device_id)[:180]),
    )
    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO user_device_tokens
            (user_id, platform, device_id, push_token, push_provider, environment, app_version, device_label, enabled, created_at, updated_at, last_seen_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, '')
            """,
            (int(user_id), str(platform)[:80], str(device_id)[:180], endpoint, provider, str(environment)[:120], str(app_version)[:80], label, _now(), _now(), _now()),
        )
    conn.commit()
    conn.close()
    _trace(
        "token_registered",
        user_id=int(user_id or 0),
        endpoint_hash=_endpoint_hash(endpoint),
        provider=provider,
        device_type=device_type,
        browser=browser,
        user_agent_family=(user_agent or "")[:80],
        token_type=provider,
        platform=str(platform)[:80],
    )
    return {"ok": True, "message": "Push notifications connected."}


def _payload(title, body, data=None, push_type="general"):
    data = data or {}
    conversation_id = data.get("conversationId") or data.get("conversation_id")
    deep_link = data.get("deepLink") or data.get("deep_link") or data.get("target_url")
    url = data.get("url") or deep_link or {
        "arena_invite": "/arena",
        "private_message": f"/pulse/messages/{conversation_id}" if conversation_id else "/pulse/messages",
        "chat_message": f"/pulse/messages/{conversation_id}" if conversation_id else "/pulse/messages",
        "message": f"/pulse/messages/{conversation_id}" if conversation_id else "/pulse/messages",
        "voice_message": f"/pulse/messages/{conversation_id}" if conversation_id else "/pulse/messages",
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
        "tag": f"pulsesoc-message-{conversation_id}" if conversation_id and push_type in {"private_message", "chat_message", "message", "voice_message"} else f"coinpilotxai-{push_type}",
        "renotify": push_type in {"arena_invite", "scam_warning", "private_message", "chat_message", "message", "market_alert"},
        "vibrate": [200, 100, 200],
        "data": {"url": url, "deepLink": deep_link or url, "push_type": push_type, **data},
        "actions": [{"action": "open", "title": "Open"}, {"action": "dismiss", "title": "Dismiss"}],
    }


def _is_expo_token(endpoint, subscription=None):
    endpoint = str(endpoint or "")
    subscription = subscription or {}
    token = subscription.get("expo_push_token") or subscription.get("token") or endpoint
    return str(token or "").startswith(("ExponentPushToken[", "ExpoPushToken["))


def _expo_token(endpoint, subscription=None):
    subscription = subscription or {}
    return str(subscription.get("expo_push_token") or subscription.get("token") or endpoint or "")


def _send_expo_push(endpoint, payload):
    token = _expo_token(endpoint, payload.get("subscription") or {})
    if not token:
        return {"ok": False, "status": "failed", "message": "Expo push token missing."}
    data = payload.get("data") or {}
    push_type = str(data.get("push_type") or data.get("type") or "").strip()
    channel_id = str(data.get("channel_id") or data.get("channelId") or "").strip()
    if not channel_id:
        channel_id = "messages" if push_type in {"private_message", "chat_message", "message", "voice_message"} or data.get("conversationId") or data.get("conversation_id") else "default"
    message = {
        "to": token,
        "title": payload.get("title") or "PulseSoc",
        "body": payload.get("body") or "New PulseSoc notification.",
        "data": data,
        "sound": os.getenv("PUSH_DEFAULT_SOUND") or "default",
        "priority": "high",
        "channelId": channel_id,
        "categoryId": push_type or "pulse",
        "ttl": 3600,
    }
    if str(os.getenv("PUSH_BADGE_ENABLED", "1")).lower() not in {"0", "false", "off", "no"} and data.get("badge") is not None:
        try:
            message["badge"] = int(data.get("badge") or 0)
        except Exception:
            pass
    try:
        response = requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=message,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        response_json = response.json() if response.content else {}
        status = (response_json.get("data") or {}).get("status")
        details = (response_json.get("data") or {}).get("details") or {}
        http_status = int(getattr(response, "status_code", 0) or 0)
        if response.ok and status == "ok":
            return {"ok": True, "status": "sent", "provider": "expo", "http_status": http_status, "provider_status": status}
        if details.get("error") == "DeviceNotRegistered":
            return {"ok": False, "status": "invalid", "provider": "expo", "message": "Expo device token is no longer registered.", "http_status": http_status, "provider_status": status, "provider_error": "DeviceNotRegistered"}
        return {"ok": False, "status": "failed", "provider": "expo", "message": "Expo push service rejected the notification.", "http_status": http_status, "provider_status": status, "provider_error": details.get("error") or status or "rejected"}
    except Exception as exc:
        return {"ok": False, "status": "failed", "provider": "expo", "message": "Expo push service request failed.", "error_type": type(exc).__name__}


def send_push(user_id, title, body, data=None, push_type="general"):
    data = data or {}
    trace_id = data.get("push_trace_id") or data.get("trace_id") or secrets.token_hex(6)
    data = {**data, "push_trace_id": trace_id}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, endpoint, subscription_json, device_type, browser, updated_at, last_seen_at
        FROM push_subscriptions
        WHERE user_id=? AND COALESCE(is_active, active, 1)=1
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conversation_id = data.get("conversationId") or data.get("conversation_id")
    message_id = data.get("messageId") or data.get("message_id")
    _trace(
        "send_push_start",
        trace_id=trace_id,
        user_id=int(user_id or 0),
        push_type=push_type,
        conversation_id=conversation_id,
        message_id=message_id,
        subscription_count=len(rows),
        title_len=len(title or ""),
        body_len=len(body or ""),
    )
    if not rows:
        conn.close()
        _trace("send_push_no_tokens", trace_id=trace_id, user_id=int(user_id or 0), push_type=push_type)
        return {"ok": False, "status": "not_configured", "message": "No active push subscription."}
    sent = 0
    failures = []
    invalid_ids = []
    payload = _payload(title, body, data, push_type)
    for row in rows:
        sub_id, endpoint, subscription_json = row[0], row[1], row[2]
        device_type = row[3] if len(row) > 3 else ""
        browser = row[4] if len(row) > 4 else ""
        try:
            subscription = json.loads(subscription_json or "{}")
        except Exception:
            subscription = {}
        if _is_expo_token(endpoint, subscription):
            _trace("provider_request", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="expo", device_type=device_type, browser=browser, push_type=push_type, conversation_id=conversation_id, message_id=message_id)
            expo_payload = {**payload, "subscription": subscription}
            expo_result = _send_expo_push(endpoint, expo_payload)
            _trace("provider_response", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="expo", status=expo_result.get("status"), ok=bool(expo_result.get("ok")), provider_status=expo_result.get("provider_status"), provider_error=expo_result.get("provider_error"), http_status=expo_result.get("http_status"), error_type=expo_result.get("error_type"))
            if expo_result.get("ok"):
                sent += 1
            elif expo_result.get("status") == "invalid":
                invalid_ids.append(sub_id)
                failures.append(expo_result.get("message", "Expo device token is invalid."))
            else:
                failures.append(expo_result.get("message", "Expo push failed."))
            continue
        if not os.getenv("VAPID_PUBLIC_KEY") or not os.getenv("VAPID_PRIVATE_KEY"):
            failures.append("VAPID push variables are not configured.")
            _trace("provider_response", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="webpush", status="not_configured", provider_error="missing_vapid")
            continue
        try:
            from pywebpush import WebPushException, webpush
        except Exception:
            failures.append("pywebpush is not installed.")
            _trace("provider_response", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="webpush", status="not_configured", provider_error="pywebpush_missing")
            continue

        try:
            _trace("provider_request", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="webpush", device_type=device_type, browser=browser, push_type=push_type, conversation_id=conversation_id, message_id=message_id)
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload),
                vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
                vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:support@pulsesoc.com")},
                timeout=10,
            )
            sent += 1
            _trace("provider_response", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="webpush", status="sent", ok=True)
        except WebPushException as exc:
            message = str(exc)[:400]
            failures.append(message)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            _trace("provider_response", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="webpush", status="invalid" if status_code in (404, 410) else "failed", http_status=status_code, error_type=type(exc).__name__)
            if getattr(exc, "response", None) is not None and exc.response.status_code in (404, 410):
                invalid_ids.append(sub_id)
        except Exception as exc:
            failures.append(str(exc)[:400])
            _trace("provider_response", trace_id=trace_id, user_id=int(user_id or 0), subscription_id=int(sub_id or 0), endpoint_hash=_endpoint_hash(endpoint), provider="webpush", status="failed", error_type=type(exc).__name__)
    for sub_id in invalid_ids:
        cur.execute("UPDATE push_subscriptions SET active=0, is_active=0, updated_at=? WHERE id=?", (_now(), sub_id))
    conn.commit()
    conn.close()
    result = {"ok": sent > 0, "status": "sent" if sent else "failed", "sent": sent, "failures": failures, "invalidated": len(invalid_ids), "trace_id": trace_id}
    _trace("send_push_complete", trace_id=trace_id, user_id=int(user_id or 0), push_type=push_type, status=result["status"], sent=sent, invalidated=len(invalid_ids), failures=len(failures), conversation_id=conversation_id, message_id=message_id)
    return result


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
