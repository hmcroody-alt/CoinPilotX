import json
import logging
import os
from datetime import datetime

from . import user_context
from . import email_service
from . import push_service


def _now():
    return datetime.now().isoformat()


PULSE_NOTIFICATION_CATEGORIES = {
    "messages": {"in_app": True, "push": True, "email": False, "sms": False},
    "comments": {"in_app": True, "push": True, "email": False, "sms": False},
    "likes": {"in_app": True, "push": False, "email": False, "sms": False},
    "mentions": {"in_app": True, "push": True, "email": False, "sms": False},
    "follows": {"in_app": True, "push": True, "email": False, "sms": False},
    "lives": {"in_app": True, "push": True, "email": False, "sms": False},
    "roast_battle": {"in_app": True, "push": True, "email": False, "sms": False},
    "premium": {"in_app": True, "push": True, "email": True, "sms": False},
    "security": {"in_app": True, "push": True, "email": True, "sms": False},
}

PULSE_TYPE_TO_CATEGORY = {
    "like": "likes",
    "comment": "comments",
    "reply": "comments",
    "save": "likes",
    "share": "likes",
    "mention": "mentions",
    "status_mention": "mentions",
    "follow": "follows",
    "follow_accept": "follows",
    "message": "messages",
    "voice_message": "messages",
    "group_invite": "messages",
    "room_invite": "messages",
    "status_view": "likes",
    "status_reaction": "likes",
    "reel_like": "likes",
    "reel_comment": "comments",
    "reel_mention": "mentions",
    "reel_share": "likes",
    "video_like": "likes",
    "video_comment": "comments",
    "video_mention": "mentions",
    "video_share": "likes",
    "video_save": "likes",
    "live_started": "lives",
    "live_invite": "lives",
    "live_ended": "lives",
    "live_replay_ready": "lives",
    "replay_available": "lives",
    "roast_battle_invite": "roast_battle",
    "roast_battle_result": "roast_battle",
    "premium_alert": "premium",
    "security_alert": "security",
    "account_login": "security",
    "new_device": "security",
    "teacher_update": "messages",
    "student_update": "messages",
    "marketplace_update": "premium",
}


def _pulse_category(note_type):
    return PULSE_TYPE_TO_CATEGORY.get(str(note_type or "").strip(), "messages")


def _pulse_type_for_alert(alert_type):
    alert_type = str(alert_type or "").strip()
    direct = {
        "pulse": "message",
        "private_message": "message",
        "payment_confirmations": "premium_alert",
        "pro_activation": "premium_alert",
        "market_alerts": "premium_alert",
        "account_security": "security_alert",
        "password_changed": "security_alert",
        "email_changed": "security_alert",
        "new_login": "account_login",
        "login": "account_login",
        "device": "new_device",
        "subscription_renewal": "premium_alert",
        "payment_success": "premium_alert",
        "payment_failure": "premium_alert",
    }.get(alert_type)
    return direct or (alert_type if alert_type in PULSE_TYPE_TO_CATEGORY else "message")


def _pulse_row(row):
    item = user_context.row_to_dict(row) or {}
    item["id"] = int(item.get("id") or 0)
    item["actor_user_id"] = int(item.get("actor_user_id") or 0)
    item["read"] = bool(item.get("is_read") or item.get("read_at"))
    item["status"] = "read" if item["read"] else "unread"
    item["deep_link"] = item.get("deep_link") or item.get("target_url") or "/pulse"
    item["target_url"] = item["deep_link"]
    item["category"] = _pulse_category(item.get("type"))
    return item


def create_pulse_notification(
    user_id,
    note_type,
    title,
    body,
    actor_user_id=0,
    entity_type="",
    entity_id="",
    deep_link="/pulse",
    delivery_status="created",
    metadata=None,
):
    if not user_id:
        return {"ok": False, "message": "User required."}
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        """
        INSERT INTO pulse_notifications
        (user_id, actor_user_id, type, title, body, entity_type, entity_id, deep_link, target_url,
         is_read, delivery_status, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            int(user_id),
            int(actor_user_id or 0),
            str(note_type or "message")[:80],
            str(title or "Pulse notification")[:180],
            str(body or "")[:2000],
            str(entity_type or "")[:80],
            str(entity_id or "")[:120],
            str(deep_link or "/pulse")[:700],
            str(deep_link or "/pulse")[:700],
            str(delivery_status or "created")[:60],
            json.dumps(metadata or {})[:4000],
            now,
        ),
    )
    notification_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO pulse_notification_deliveries
        (notification_id, user_id, channel, provider, status, created_at, sent_at)
        VALUES (?, ?, 'in_app', 'pulse', ?, ?, ?)
        """,
        (notification_id, int(user_id), str(delivery_status or "created")[:60], now, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "notification_id": notification_id}


def list_pulse_notifications(user_id, limit=50, category="all", unread_only=False):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    clauses = ["user_id=?"]
    params = [int(user_id)]
    if unread_only:
        clauses.append("(is_read=0 OR read_at IS NULL)")
    category_types = {
        "messages": ["message", "voice_message", "group_invite", "room_invite", "teacher_update", "student_update"],
        "social": ["like", "comment", "reply", "save", "share", "mention", "status_mention", "follow", "follow_accept", "status_view", "status_reaction", "reel_like", "reel_comment", "reel_mention", "reel_share", "video_like", "video_comment", "video_mention", "video_share", "video_save"],
        "security": ["security_alert", "account_login", "new_device"],
        "premium": ["premium_alert", "marketplace_update"],
    }.get(str(category or "all").lower())
    if category_types:
        clauses.append("type IN (%s)" % ",".join("?" for _ in category_types))
        params.extend(category_types)
    params.append(max(1, min(int(limit or 50), 100)))
    cur.execute(
        f"""
        SELECT *
        FROM pulse_notifications
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    rows = [_pulse_row(row) for row in cur.fetchall()]
    conn.close()
    return {"ok": True, "notifications": rows}


def pulse_unread_count(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pulse_notifications WHERE user_id=? AND (is_read=0 OR read_at IS NULL)", (int(user_id),))
    count = int(cur.fetchone()[0] or 0)
    conn.close()
    return {"ok": True, "count": count, "unread_count": count}


def mark_pulse_read(user_id, notification_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_notifications SET is_read=1, read_at=? WHERE id=? AND user_id=?",
        (_now(), int(notification_id or 0), int(user_id)),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "updated": changed}


def mark_all_pulse_read(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_notifications SET is_read=1, read_at=? WHERE user_id=? AND (is_read=0 OR read_at IS NULL)",
        (_now(), int(user_id)),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "updated": changed}


def delete_pulse_notification(user_id, notification_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_notifications WHERE id=? AND user_id=?", (int(notification_id or 0), int(user_id)))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "deleted": changed}


def pulse_preferences(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT category, in_app, email, sms, push FROM pulse_notification_preferences WHERE user_id=?", (int(user_id),))
    existing = {
        row[0]: {"in_app": bool(row[1]), "email": bool(row[2]), "sms": bool(row[3]), "push": bool(row[4])}
        for row in cur.fetchall()
    }
    conn.close()
    return {
        "ok": True,
        "preferences": {
            category: existing.get(category, defaults.copy())
            for category, defaults in PULSE_NOTIFICATION_CATEGORIES.items()
        },
        "categories": list(PULSE_NOTIFICATION_CATEGORIES.keys()),
    }


def update_pulse_preferences(user_id, payload):
    payload = payload or {}
    prefs = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else payload
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    for category, values in (prefs or {}).items():
        if category not in PULSE_NOTIFICATION_CATEGORIES or not isinstance(values, dict):
            continue
        defaults = PULSE_NOTIFICATION_CATEGORIES[category]
        if category == "security":
            values = {**values, "in_app": True}
        cur.execute(
            """
            INSERT INTO pulse_notification_preferences (user_id, category, in_app, email, sms, push, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
              in_app=excluded.in_app, email=excluded.email, sms=excluded.sms, push=excluded.push, updated_at=excluded.updated_at
            """,
            (
                int(user_id),
                category,
                1 if values.get("in_app", defaults["in_app"]) else 0,
                1 if values.get("email", defaults["email"]) else 0,
                1 if values.get("sms", defaults["sms"]) else 0,
                1 if values.get("push", defaults["push"]) else 0,
                now,
            ),
        )
    conn.commit()
    conn.close()
    return pulse_preferences(user_id)


def save_pulse_device(user_id, subscription, user_agent=""):
    result = save_push_subscription(user_id, subscription, user_agent)
    endpoint = (subscription or {}).get("endpoint") or ""
    provider = str((subscription or {}).get("provider") or "web_push")[:40]
    requested_device_type = str((subscription or {}).get("device_type") or "").lower()
    ua_device_type = "mobile" if any(token in (user_agent or "").lower() for token in ["iphone", "android", "mobile"]) else "desktop"
    device_type = requested_device_type if requested_device_type in {"mobile", "desktop", "native"} else ua_device_type
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    token_preview = endpoint[-18:] if endpoint else ""
    cur.execute(
        """
        INSERT INTO pulse_notification_devices
        (user_id, device_type, provider, endpoint, token_preview, subscription_json, user_agent, active, created_at, updated_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
          user_id=excluded.user_id, device_type=excluded.device_type, provider=excluded.provider,
          subscription_json=excluded.subscription_json, user_agent=excluded.user_agent,
          active=1, updated_at=excluded.updated_at, last_seen_at=excluded.last_seen_at
        """,
        (
            int(user_id),
            device_type,
            provider,
            endpoint,
            token_preview,
            json.dumps(subscription or {})[:8000],
            str(user_agent or "")[:800],
            now,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return result


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
        "SELECT * FROM notifications WHERE user_id=? AND notification_type!='arena' ORDER BY created_at DESC LIMIT ?",
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
    global_defaults = {
        "enable_push_notifications": False,
        "enable_notification_sound": True,
        "enable_notification_vibration": True,
        "notification_sound_type": "soft",
        "quiet_hours_enabled": False,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
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
    try:
        cur.execute(
            """
            SELECT enable_push_notifications, enable_notification_sound, enable_notification_vibration,
                   notification_sound_type, quiet_hours_enabled, quiet_hours_start, quiet_hours_end
            FROM notification_preferences
            WHERE user_id=?
            ORDER BY CASE WHEN category='global' THEN 0 ELSE 1 END, id ASC
            LIMIT 1
            """,
            (user_id,),
        )
        global_row = cur.fetchone()
        if global_row:
            global_defaults.update({
                "enable_push_notifications": bool(global_row[0]),
                "enable_notification_sound": bool(global_row[1]),
                "enable_notification_vibration": bool(global_row[2]),
                "notification_sound_type": global_row[3] or "soft",
                "quiet_hours_enabled": bool(global_row[4]),
                "quiet_hours_start": global_row[5] or "22:00",
                "quiet_hours_end": global_row[6] or "07:00",
            })
    except Exception:
        pass
    conn.close()
    return {
        "ok": True,
        "preferences": {category: existing.get(category, {"in_app": True, "push": False, "email": False, "sms": False, "telegram": False}) for category in categories},
        "experience": global_defaults,
    }


def update_preferences(user_id, preferences):
    conn = user_context.connect()
    cur = conn.cursor()
    preferences = preferences or {}
    experience_keys = {
        "enable_push_notifications",
        "enable_notification_sound",
        "enable_notification_vibration",
        "notification_sound_type",
        "quiet_hours_enabled",
        "quiet_hours_start",
        "quiet_hours_end",
    }
    experience = preferences.get("experience") if isinstance(preferences.get("experience"), dict) else {key: preferences[key] for key in experience_keys if key in preferences}
    category_preferences = preferences.get("preferences") if isinstance(preferences.get("preferences"), dict) else {
        key: value for key, value in preferences.items() if key not in experience_keys and key != "experience"
    }
    for category, values in category_preferences.items():
        if not isinstance(values, dict):
            continue
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
    if experience:
        cur.execute(
            """
            INSERT INTO notification_preferences
            (user_id, category, in_app, push, email, telegram, sms, enable_push_notifications,
             enable_notification_sound, enable_notification_vibration, notification_sound_type,
             quiet_hours_enabled, quiet_hours_start, quiet_hours_end, updated_at)
            VALUES (?, 'global', 1, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
                enable_push_notifications=excluded.enable_push_notifications,
                enable_notification_sound=excluded.enable_notification_sound,
                enable_notification_vibration=excluded.enable_notification_vibration,
                notification_sound_type=excluded.notification_sound_type,
                quiet_hours_enabled=excluded.quiet_hours_enabled,
                quiet_hours_start=excluded.quiet_hours_start,
                quiet_hours_end=excluded.quiet_hours_end,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                1 if experience.get("enable_push_notifications") else 0,
                1 if experience.get("enable_notification_sound", True) else 0,
                1 if experience.get("enable_notification_vibration", True) else 0,
                str(experience.get("notification_sound_type") or "soft")[:40],
                1 if experience.get("quiet_hours_enabled") else 0,
                str(experience.get("quiet_hours_start") or "22:00")[:8],
                str(experience.get("quiet_hours_end") or "07:00")[:8],
                _now(),
            ),
        )
    conn.commit()
    conn.close()
    return get_preferences(user_id)


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
        return {"ok": False, "status": "not_configured", "message": "User has not opted in to SMS alerts or has no phone."}
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
    push_type = (metadata or {}).get("push_type") or (metadata or {}).get("type") or "general"
    return push_service.send_push(user_id, title, message, metadata or {}, push_type=push_type)


def send_telegram_alert(user, title, message):
    user = user or {}
    if not user.get("telegram_chat_id"):
        return {"ok": False, "status": "not_configured", "message": "Telegram companion is not linked."}
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
    pulse_link = data.get("deep_link") or data.get("target_url") or data.get("next_url") or data.get("url") or data.get("href") or "/pulse/notifications"
    pulse_created = create_pulse_notification(
        user_id,
        _pulse_type_for_alert(alert_type),
        title,
        body,
        actor_user_id=data.get("actor_user_id") or data.get("sender_user_id") or data.get("from_user_id") or 0,
        entity_type=data.get("entity_type") or data.get("content_type") or "",
        entity_id=data.get("entity_id") or data.get("post_id") or data.get("message_id") or data.get("conversation_id") or "",
        deep_link=pulse_link,
        delivery_status="created",
        metadata={**data, "legacy_notification_id": notification_id, "legacy_alert_type": alert_type},
    )
    result = {
        "ok": True,
        "notification_id": notification_id,
        "pulse_notification_id": pulse_created.get("notification_id"),
        "in_app": "created",
    }
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
    ua = (user_agent or "").lower()
    device_type = "mobile" if any(token in ua for token in ["iphone", "android", "mobile"]) else "desktop"
    browser = "Safari" if "safari" in ua and "chrome" not in ua else "Chrome" if "chrome" in ua else "Browser"
    return push_service.save_subscription(user_id, subscription, user_agent, device_type=device_type, browser=browser)


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
        return {"ok": False, "status": "not_configured", "message": "Telegram companion is not linked."}
    return {"ok": False, "status": "queued", "message": "Telegram delivery is handled by the bot runtime.", "title": title, "body": message}


def retry_failed_notification():
    return {"ok": True, "retried": 0}
