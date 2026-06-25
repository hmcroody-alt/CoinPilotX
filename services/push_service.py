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
import threading
from datetime import datetime, timedelta

import requests

from . import user_context

PUSH_PROCESSOR_LOCK = threading.Lock()


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


def _ensure_expo_push_tickets(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS expo_push_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_ticket_id TEXT UNIQUE,
            notification_id INTEGER,
            user_id INTEGER,
            subscription_id INTEGER,
            trace_id TEXT,
            status TEXT DEFAULT 'accepted',
            error_code TEXT,
            receipt_json TEXT,
            attempts INTEGER DEFAULT 0,
            created_at TEXT,
            checked_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_expo_push_tickets_status ON expo_push_tickets(status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_expo_push_tickets_user ON expo_push_tickets(user_id, created_at)")


def _ensure_push_delivery_jobs(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS push_delivery_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE,
            idempotency_key TEXT UNIQUE,
            notification_id INTEGER,
            user_id INTEGER,
            push_type TEXT,
            title TEXT,
            body TEXT,
            payload_json TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            next_retry_at TEXT,
            last_error TEXT,
            provider_response TEXT,
            trace_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            processed_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_push_delivery_jobs_status_retry ON push_delivery_jobs(status, next_retry_at, id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_push_delivery_jobs_user_created ON push_delivery_jobs(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_push_delivery_jobs_notification ON push_delivery_jobs(notification_id)")


def _async_push_enabled():
    return str(os.getenv("PUSH_ASYNC_DELIVERY_ENABLED", "1")).lower() not in {"0", "false", "off", "no"}


def _opportunistic_processor_enabled():
    return str(os.getenv("PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED", "1")).lower() not in {"0", "false", "off", "no"}


def _opportunistic_processor_limit():
    try:
        return max(1, min(int(os.getenv("PUSH_OPPORTUNISTIC_PROCESSOR_LIMIT", "25") or 25), 100))
    except (TypeError, ValueError):
        return 25


def schedule_push_delivery_processing(reason="enqueue"):
    """Drain queued push work in the background when a dedicated worker is unavailable."""
    if not _opportunistic_processor_enabled():
        return {"ok": True, "scheduled": False, "reason": "disabled"}
    if not PUSH_PROCESSOR_LOCK.acquire(blocking=False):
        return {"ok": True, "scheduled": False, "reason": "already_running"}

    def _run():
        try:
            result = process_push_delivery_jobs(limit=_opportunistic_processor_limit())
            if result.get("processed") or not result.get("ok"):
                _trace(
                    "opportunistic_processor_complete",
                    reason=reason,
                    processed=result.get("processed", 0),
                    sent=result.get("sent", 0),
                    retry=result.get("retry", 0),
                    dead_letter=result.get("dead_letter", 0),
                    failed=result.get("failed", 0),
                    ok=bool(result.get("ok")),
                )
            try:
                process_expo_receipts(limit=25)
            except Exception as exc:
                _trace("opportunistic_receipts_skipped", reason=reason, error_type=exc.__class__.__name__)
        except Exception as exc:
            _trace("opportunistic_processor_failed", reason=reason, error_type=exc.__class__.__name__)
        finally:
            try:
                PUSH_PROCESSOR_LOCK.release()
            except RuntimeError:
                pass

    threading.Thread(target=_run, name="push-delivery-opportunistic", daemon=True).start()
    return {"ok": True, "scheduled": True, "reason": reason}


def _delivery_job_key(user_id, title, body, data=None, push_type="general", notification_id=0):
    data = data or {}
    if notification_id:
        return f"notification:{int(notification_id)}:user:{int(user_id or 0)}:push:{str(push_type or 'general')[:80]}"
    conversation_id = data.get("conversationId") or data.get("conversation_id") or ""
    message_id = data.get("messageId") or data.get("message_id") or data.get("entity_id") or ""
    if conversation_id and message_id:
        return f"message:{int(user_id or 0)}:{conversation_id}:{message_id}:{str(push_type or 'message')[:80]}"
    digest = hashlib.sha256(
        json.dumps(
            {
                "user_id": int(user_id or 0),
                "title": str(title or "")[:180],
                "body": str(body or "")[:300],
                "data": data,
                "push_type": str(push_type or "general")[:80],
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"push:{digest}"


def _retry_at(attempts):
    delay_seconds = min(3600, 30 * (2 ** max(0, int(attempts or 1) - 1)))
    return (datetime.utcnow() + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")


def enqueue_push(user_id, title, body, data=None, push_type="general", notification_id=0, idempotency_key=""):
    """Persist a push delivery request without contacting the provider."""
    if not user_id:
        return {"ok": False, "status": "skipped", "message": "User required."}
    data = data or {}
    trace_id = data.get("push_trace_id") or data.get("trace_id") or secrets.token_hex(6)
    safe_data = {**data, "push_trace_id": trace_id, "notification_id": int(notification_id or data.get("notification_id") or 0)}
    key = str(idempotency_key or _delivery_job_key(user_id, title, body, safe_data, push_type, notification_id))[:240]
    job_id = f"push_{secrets.token_hex(12)}"
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    _ensure_push_delivery_jobs(cur)
    _ensure_expo_push_tickets(cur)
    try:
        cur.execute(
            """
            INSERT INTO push_delivery_jobs
            (job_id, idempotency_key, notification_id, user_id, push_type, title, body, payload_json,
             status, attempts, max_attempts, next_retry_at, trace_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 5, ?, ?, ?, ?)
            """,
            (
                job_id,
                key,
                int(notification_id or safe_data.get("notification_id") or 0),
                int(user_id or 0),
                str(push_type or "general")[:80],
                str(title or "PulseSoc notification")[:180],
                str(body or "")[:2000],
                json.dumps(safe_data, default=str)[:8000],
                now,
                str(trace_id)[:120],
                now,
                now,
            ),
        )
        queued_id = getattr(cur, "lastrowid", None) or 0
        conn.commit()
        _trace(
            "push_job_queued",
            trace_id=trace_id,
            user_id=int(user_id or 0),
            notification_id=int(notification_id or safe_data.get("notification_id") or 0),
            push_type=push_type,
            job_id=job_id,
        )
        schedule_push_delivery_processing(reason="job_queued")
        return {"ok": True, "status": "queued", "delivery_state": "queued", "job_id": job_id, "id": queued_id, "trace_id": trace_id}
    except Exception as exc:
        if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
            conn.rollback()
            conn.close()
            _trace("push_job_enqueue_failed", trace_id=trace_id, user_id=int(user_id or 0), push_type=push_type, error_type=type(exc).__name__)
            return {"ok": False, "status": "failed", "message": "Push job could not be queued.", "error_type": type(exc).__name__, "trace_id": trace_id}
        conn.rollback()
        cur.execute(
            """
            SELECT id, job_id, status, trace_id FROM push_delivery_jobs
            WHERE idempotency_key=?
            LIMIT 1
            """,
            (key,),
        )
        existing = cur.fetchone()
        conn.close()
        existing_id = existing[0] if existing else 0
        existing_job = existing[1] if existing else ""
        existing_status = existing[2] if existing else "queued"
        existing_trace = existing[3] if existing else trace_id
        _trace(
            "push_job_duplicate",
            trace_id=existing_trace,
            user_id=int(user_id or 0),
            notification_id=int(notification_id or safe_data.get("notification_id") or 0),
            push_type=push_type,
            job_id=existing_job,
        )
        schedule_push_delivery_processing(reason="job_duplicate")
        return {"ok": True, "status": "queued", "delivery_state": "deduped", "duplicate": True, "job_id": existing_job, "id": existing_id, "job_status": existing_status, "trace_id": existing_trace}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _deactivate_subscription(cur, subscription_id):
    cur.execute("SELECT endpoint, user_id FROM push_subscriptions WHERE id=? LIMIT 1", (int(subscription_id),))
    row = cur.fetchone()
    endpoint = row[0] if row else ""
    user_id = row[1] if row else 0
    cur.execute(
        "UPDATE push_subscriptions SET active=0, is_active=0, updated_at=? WHERE id=?",
        (_now(), int(subscription_id)),
    )
    if endpoint:
        try:
            cur.execute(
                "UPDATE user_device_tokens SET enabled=0, revoked_at=?, updated_at=? WHERE user_id=? AND push_token=?",
                (_now(), _now(), int(user_id or 0), endpoint),
            )
        except Exception:
            pass
        try:
            cur.execute(
                "UPDATE pulse_notification_devices SET active=0, updated_at=? WHERE user_id=? AND endpoint=?",
                (_now(), int(user_id or 0), endpoint),
            )
        except Exception:
            pass


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
    native_url = data.get("native_url") or data.get("app_url") or data.get("mobile_deep_link")
    deep_link = data.get("deep_link") or data.get("target_url") or data.get("deepLink")
    web_url = data.get("web_url") or data.get("url") or deep_link or {
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
    preferred_deep_link = native_url or data.get("deepLink") or deep_link or web_url
    payload_data = {
        **data,
        "url": web_url,
        "web_url": web_url,
        "deepLink": preferred_deep_link,
        "deep_link": deep_link or web_url,
        "push_type": push_type,
    }
    if native_url:
        payload_data["native_url"] = native_url
        payload_data["app_url"] = native_url
        payload_data["mobile_deep_link"] = native_url
    return {
        "title": title[:120],
        "body": body[:240],
        "tag": f"pulsesoc-message-{conversation_id}" if conversation_id and push_type in {"private_message", "chat_message", "message", "voice_message"} else f"coinpilotxai-{push_type}",
        "renotify": push_type in {"arena_invite", "scam_warning", "private_message", "chat_message", "message", "market_alert"},
        "vibrate": [200, 100, 200],
        "data": payload_data,
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
        channel_id = (
            os.getenv("PUSH_MESSAGE_CHANNEL_ID", "pulse-messages-v2")
            if push_type in {"private_message", "chat_message", "message", "voice_message"}
            or data.get("conversationId")
            or data.get("conversation_id")
            else "default"
        )
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
    if push_type in {"private_message", "chat_message", "message", "voice_message"} or data.get("conversationId") or data.get("conversation_id"):
        message["interruptionLevel"] = os.getenv("PUSH_MESSAGE_INTERRUPTION_LEVEL", "active")
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
            return {
                "ok": True,
                "status": "sent",
                "delivery_state": "accepted",
                "provider": "expo",
                "provider_ticket_id": str((response_json.get("data") or {}).get("id") or "")[:180],
                "http_status": http_status,
                "provider_status": status,
            }
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
    _ensure_expo_push_tickets(cur)
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
    accepted_tickets = 0
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
                provider_ticket_id = expo_result.get("provider_ticket_id")
                if provider_ticket_id:
                    cur.execute(
                        """
                        INSERT INTO expo_push_tickets
                        (provider_ticket_id, notification_id, user_id, subscription_id, trace_id, status, created_at)
                        VALUES (?, ?, ?, ?, ?, 'accepted', ?)
                        ON CONFLICT(provider_ticket_id) DO NOTHING
                        """,
                        (
                            provider_ticket_id,
                            int(data.get("notification_id") or 0),
                            int(user_id or 0),
                            int(sub_id or 0),
                            str(trace_id)[:120],
                            _now(),
                        ),
                    )
                    accepted_tickets += 1
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
        _deactivate_subscription(cur, sub_id)
    conn.commit()
    conn.close()
    result = {
        "ok": sent > 0,
        "status": "sent" if sent else "failed",
        "delivery_state": "accepted" if accepted_tickets else ("submitted" if sent else "failed"),
        "sent": sent,
        "accepted_tickets": accepted_tickets,
        "failures": failures,
        "invalidated": len(invalid_ids),
        "trace_id": trace_id,
    }
    _trace("send_push_complete", trace_id=trace_id, user_id=int(user_id or 0), push_type=push_type, status=result["status"], sent=sent, invalidated=len(invalid_ids), failures=len(failures), conversation_id=conversation_id, message_id=message_id)
    return result


def process_push_delivery_jobs(limit=50):
    """Send queued push jobs with retries and dead-lettering."""
    limit = max(1, min(int(limit or 50), 100))
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    _ensure_push_delivery_jobs(cur)
    cur.execute(
        """
        SELECT id, job_id, notification_id, user_id, push_type, title, body, payload_json,
               attempts, max_attempts, trace_id
        FROM push_delivery_jobs
        WHERE status IN ('pending', 'retry')
          AND (next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?)
        ORDER BY id ASC
        LIMIT ?
        """,
        (now, limit),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return {"ok": True, "processed": 0, "sent": 0, "retry": 0, "dead_letter": 0, "failed": 0}
    processed = sent = retry = dead_letter = failed = 0
    for row in rows:
        local_id, job_id, notification_id, user_id, push_type, title, body, payload_json, attempts, max_attempts, trace_id = row
        attempts = int(attempts or 0) + 1
        max_attempts = int(max_attempts or 5)
        cur.execute("UPDATE push_delivery_jobs SET status='processing', attempts=?, updated_at=? WHERE id=?", (attempts, _now(), int(local_id)))
        conn.commit()
        try:
            payload = json.loads(payload_json or "{}")
            payload.setdefault("notification_id", int(notification_id or 0))
            payload.setdefault("push_trace_id", trace_id or secrets.token_hex(6))
        except Exception:
            payload = {"notification_id": int(notification_id or 0), "push_trace_id": trace_id or secrets.token_hex(6)}
        _trace(
            "push_job_processing",
            trace_id=payload.get("push_trace_id") or trace_id,
            user_id=int(user_id or 0),
            notification_id=int(notification_id or 0),
            push_type=push_type,
            job_id=job_id,
            attempt=attempts,
        )
        try:
            result = send_push(int(user_id or 0), title, body, payload, push_type=push_type or "general")
        except Exception as exc:
            result = {"ok": False, "status": "failed", "message": "Push provider call crashed.", "error_type": type(exc).__name__}
        status = str(result.get("status") or ("sent" if result.get("ok") else "failed"))[:60]
        message = result.get("message") or "; ".join(result.get("failures") or []) or result.get("error_type") or ""
        if result.get("ok") or status in {"sent", "submitted"}:
            final_status = "sent"
            sent += 1
            processed += 1
            cur.execute(
                """
                UPDATE push_delivery_jobs
                SET status=?, last_error='', provider_response=?, updated_at=?, processed_at=?
                WHERE id=?
                """,
                (final_status, json.dumps(result, default=str)[:4000], _now(), _now(), int(local_id)),
            )
        elif status in {"not_configured", "skipped", "invalid"}:
            final_status = status
            failed += 1
            processed += 1
            cur.execute(
                """
                UPDATE push_delivery_jobs
                SET status=?, last_error=?, provider_response=?, updated_at=?, processed_at=?
                WHERE id=?
                """,
                (final_status, str(message or status)[:1200], json.dumps(result, default=str)[:4000], _now(), _now(), int(local_id)),
            )
        elif attempts >= max_attempts:
            final_status = "dead_letter"
            dead_letter += 1
            processed += 1
            cur.execute(
                """
                UPDATE push_delivery_jobs
                SET status='dead_letter', last_error=?, provider_response=?, updated_at=?, processed_at=?
                WHERE id=?
                """,
                (str(message or "max attempts reached")[:1200], json.dumps(result, default=str)[:4000], _now(), _now(), int(local_id)),
            )
        else:
            final_status = "retry"
            retry += 1
            processed += 1
            cur.execute(
                """
                UPDATE push_delivery_jobs
                SET status='retry', next_retry_at=?, last_error=?, provider_response=?, updated_at=?
                WHERE id=?
                """,
                (_retry_at(attempts), str(message or "provider unavailable")[:1200], json.dumps(result, default=str)[:4000], _now(), int(local_id)),
            )
        if notification_id:
            cur.execute(
                """
                UPDATE pulse_notification_deliveries
                SET status=?, error_message=?, provider_response=?, sent_at=CASE WHEN ?='sent' THEN COALESCE(sent_at, ?) ELSE sent_at END
                WHERE notification_id=? AND channel='push'
                """,
                (final_status, str(message or "")[:1200], json.dumps(result, default=str)[:4000], final_status, _now(), int(notification_id)),
            )
        _trace(
            "push_job_complete",
            trace_id=payload.get("push_trace_id") or trace_id,
            user_id=int(user_id or 0),
            notification_id=int(notification_id or 0),
            push_type=push_type,
            job_id=job_id,
            status=final_status,
            attempt=attempts,
        )
        conn.commit()
    conn.close()
    return {"ok": True, "processed": processed, "sent": sent, "retry": retry, "dead_letter": dead_letter, "failed": failed}


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


def process_expo_receipts(limit=100):
    """Reconcile Expo tickets without exposing device tokens or blocking message sends."""
    limit = max(1, min(int(limit or 100), 100))
    conn = user_context.connect()
    cur = conn.cursor()
    _ensure_expo_push_tickets(cur)
    cur.execute(
        """
        SELECT id, provider_ticket_id, notification_id, user_id, subscription_id, trace_id
        FROM expo_push_tickets
        WHERE status='accepted' AND COALESCE(attempts, 0) < 12
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return {"ok": True, "checked": 0, "confirmed": 0, "failed": 0, "invalidated": 0}
    ticket_ids = [str(row[1]) for row in rows if row[1]]
    try:
        response = requests.post(
            "https://exp.host/--/api/v2/push/getReceipts",
            json={"ids": ticket_ids},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        response_json = response.json() if response.content else {}
        receipts = response_json.get("data") if isinstance(response_json.get("data"), dict) else {}
        if not response.ok:
            raise RuntimeError("expo_receipt_request_rejected")
    except Exception as exc:
        conn.close()
        _trace("receipt_request_failed", ticket_count=len(ticket_ids), error_type=exc.__class__.__name__)
        return {"ok": False, "checked": 0, "confirmed": 0, "failed": 0, "invalidated": 0, "reason": "provider_unavailable"}

    confirmed = 0
    failed = 0
    invalidated = 0
    now = _now()
    for row in rows:
        local_id, ticket_id, notification_id, user_id, subscription_id, trace_id = row
        receipt = receipts.get(str(ticket_id))
        if not isinstance(receipt, dict):
            cur.execute("UPDATE expo_push_tickets SET attempts=COALESCE(attempts,0)+1, checked_at=? WHERE id=?", (now, local_id))
            continue
        provider_status = str(receipt.get("status") or "error")[:40]
        details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
        error_code = str(details.get("error") or receipt.get("message") or "")[:120]
        final_status = "provider_confirmed" if provider_status == "ok" else "invalid" if error_code == "DeviceNotRegistered" else "failed"
        safe_receipt = {
            "status": provider_status,
            "error": error_code,
            "message": str(receipt.get("message") or "")[:240],
        }
        cur.execute(
            """
            UPDATE expo_push_tickets
            SET status=?, error_code=?, receipt_json=?, attempts=COALESCE(attempts,0)+1, checked_at=?
            WHERE id=?
            """,
            (final_status, error_code, json.dumps(safe_receipt), now, local_id),
        )
        if notification_id:
            cur.execute(
                """
                UPDATE pulse_notification_deliveries
                SET status=?, error_message=?, provider_response=?, sent_at=COALESCE(sent_at, ?)
                WHERE notification_id=? AND channel='push'
                """,
                (final_status, error_code, json.dumps(safe_receipt), now, int(notification_id)),
            )
        if final_status == "provider_confirmed":
            confirmed += 1
        else:
            failed += 1
        if final_status == "invalid":
            _deactivate_subscription(cur, subscription_id)
            invalidated += 1
        _trace(
            "provider_receipt",
            trace_id=trace_id,
            user_id=int(user_id or 0),
            subscription_id=int(subscription_id or 0),
            ticket_hash=_endpoint_hash(ticket_id),
            status=final_status,
            provider_error=error_code,
        )
    conn.commit()
    conn.close()
    return {"ok": True, "checked": len(rows), "confirmed": confirmed, "failed": failed, "invalidated": invalidated}
