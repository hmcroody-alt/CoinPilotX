import json
import logging
import os
import time
from datetime import datetime

from . import user_context
from . import email_service
from . import push_service
from . import sms_service


def _now():
    return datetime.now().isoformat()


def _json(value, default=None):
    if value in (None, ""):
        return default or {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else (default or {})
    except Exception:
        return default or {}


PULSE_NOTIFICATION_CATEGORIES = {
    "account": {"in_app": True, "push": True, "email": True, "sms": False},
    "premium": {"in_app": True, "push": True, "email": True, "sms": False},
    "social": {"in_app": True, "push": True, "email": False, "sms": False},
    "live": {"in_app": True, "push": True, "email": False, "sms": False},
    "status": {"in_app": True, "push": True, "email": False, "sms": False},
    "marketplace": {"in_app": True, "push": True, "email": True, "sms": False},
    "crypto": {"in_app": True, "push": True, "email": False, "sms": False},
    "security": {"in_app": True, "push": True, "email": True, "sms": False},
    # Legacy UI categories preserved for older saved preferences.
    "messages": {"in_app": True, "push": True, "email": False, "sms": False},
    "comments": {"in_app": True, "push": True, "email": False, "sms": False},
    "likes": {"in_app": True, "push": False, "email": False, "sms": False},
    "mentions": {"in_app": True, "push": True, "email": False, "sms": False},
    "follows": {"in_app": True, "push": True, "email": False, "sms": False},
    "lives": {"in_app": True, "push": True, "email": False, "sms": False},
    "roast_battle": {"in_app": True, "push": True, "email": False, "sms": False},
}

PULSE_TYPE_TO_CATEGORY = {
    "like": "likes",
    "post_like": "social",
    "comment": "comments",
    "post_comment": "social",
    "reply": "comments",
    "comment_reply": "social",
    "save": "likes",
    "share": "likes",
    "post_repost": "social",
    "mention": "mentions",
    "status_mention": "mentions",
    "follow": "follows",
    "follow_accept": "follows",
    "message": "messages",
    "voice_message": "messages",
    "group_invite": "messages",
    "community_invite": "social",
    "room_invite": "messages",
    "status_view": "likes",
    "status_reaction": "status",
    "status_reply": "status",
    "reel_like": "likes",
    "reel_comment": "comments",
    "reel_mention": "mentions",
    "reel_share": "likes",
    "video_like": "likes",
    "video_comment": "comments",
    "video_mention": "mentions",
    "video_share": "likes",
    "video_save": "likes",
    "live_started": "live",
    "live_reminder": "live",
    "live_invite": "live",
    "live_ended": "live",
    "live_ended_summary": "live",
    "live_replay_ready": "live",
    "replay_available": "live",
    "roast_battle_invite": "roast_battle",
    "roast_battle_result": "roast_battle",
    "premium_alert": "premium",
    "founder_premium_activated": "premium",
    "subscription_renewed": "premium",
    "payment_succeeded": "premium",
    "payment_failed": "premium",
    "subscription_canceled": "premium",
    "founder_number_assigned": "premium",
    "founder_badge_granted": "premium",
    "user_signup": "account",
    "email_verification": "account",
    "phone_verification": "account",
    "password_reset": "account",
    "account_recovery": "account",
    "security_alert": "security",
    "account_login": "security",
    "suspicious_login": "security",
    "new_device": "security",
    "password_changed": "security",
    "email_changed": "security",
    "phone_changed": "security",
    "two_factor_enabled": "security",
    "two_factor_disabled": "security",
    "teacher_update": "messages",
    "student_update": "messages",
    "marketplace_update": "marketplace",
    "marketplace_order": "marketplace",
    "order_accepted": "marketplace",
    "order_shipped": "marketplace",
    "order_delivered": "marketplace",
    "refund_issued": "marketplace",
    "payment_received": "marketplace",
    "crypto_price_alert": "crypto",
    "portfolio_alert": "crypto",
    "watchlist_alert": "crypto",
    "major_market_movement": "crypto",
    "custom_crypto_alert": "crypto",
}

SECURITY_NOTIFICATION_TYPES = {
    "security_alert",
    "account_login",
    "suspicious_login",
    "new_device",
    "password_changed",
    "email_changed",
    "phone_changed",
    "two_factor_enabled",
    "two_factor_disabled",
}

BREVO_NOTIFICATION_TEMPLATES = {
    "welcome": {
        "subject": "Welcome to Pulse",
        "headline": "Welcome to Pulse",
        "body": "Your Pulse account is ready. Start connecting, creating, and discovering on PulseSoc.com.",
    },
    "founder_premium_activated": {
        "subject": "Founder Premium is active",
        "headline": "Founder Premium is active",
        "body": "Your Founder Premium membership is active. Your Founder benefits are now available in Pulse.",
    },
    "payment_receipt": {
        "subject": "Pulse payment receipt",
        "headline": "Payment received",
        "body": "We received your Pulse payment. Your account has been updated.",
    },
    "password_reset": {
        "subject": "Reset your Pulse password",
        "headline": "Reset your Pulse password",
        "body": "Use the secure reset link to update your Pulse password. Ignore this message if you did not request it.",
    },
    "new_follower": {
        "subject": "You have a new Pulse follower",
        "headline": "New follower",
        "body": "Someone new followed you on Pulse.",
    },
    "new_message": {
        "subject": "New Pulse message",
        "headline": "New message",
        "body": "You received a new direct message on Pulse.",
    },
    "crypto_alert": {
        "subject": "Pulse crypto alert",
        "headline": "Crypto alert",
        "body": "A crypto alert you enabled was triggered.",
    },
    "security_alert": {
        "subject": "Pulse security alert",
        "headline": "Security alert",
        "body": "We detected an important security event on your Pulse account.",
    },
}

RATE_WINDOWS = {"email": 60, "sms": 300, "in_app": 20}
RATE_LIMITS = {"email": 12, "sms": 4, "in_app": 20}
MEMORY_DELIVERY_RATE = {}


def _pulse_category(note_type):
    return PULSE_TYPE_TO_CATEGORY.get(str(note_type or "").strip(), "messages")


def _truthy_env(name, default=False):
    value = os.getenv(name)
    if value is None or value == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _email_enabled():
    return _truthy_env("BREVO_EMAIL_ENABLED", True)


def _sms_enabled():
    return _truthy_env("BREVO_SMS_ENABLED", False)


def _rate_allowed(user_id, channel):
    channel = str(channel or "in_app")
    now = time.time()
    window = RATE_WINDOWS.get(channel, 60)
    limit = RATE_LIMITS.get(channel, 20)
    key = (int(user_id or 0), channel)
    bucket = [ts for ts in MEMORY_DELIVERY_RATE.get(key, []) if now - ts < window]
    if len(bucket) >= limit:
        MEMORY_DELIVERY_RATE[key] = bucket
        return False
    bucket.append(now)
    MEMORY_DELIVERY_RATE[key] = bucket
    return True


def _dedupe_key(note_type, entity_type="", entity_id="", deep_link="", body=""):
    return "|".join(str(value or "").strip()[:180] for value in [note_type, entity_type, entity_id, deep_link, body[:120]])


def _recent_duplicate(user_id, note_type, entity_type="", entity_id="", deep_link="", body="", seconds=45):
    conn = user_context.connect()
    cur = conn.cursor()
    cutoff = datetime.fromtimestamp(time.time() - max(1, int(seconds or 45))).isoformat()
    cur.execute(
        """
        SELECT id FROM pulse_notifications
        WHERE user_id=? AND type=? AND COALESCE(entity_type,'')=? AND COALESCE(entity_id,'')=?
          AND COALESCE(deep_link,'')=? AND COALESCE(body,'')=? AND created_at>=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            int(user_id),
            str(note_type or "message")[:80],
            str(entity_type or "")[:80],
            str(entity_id or "")[:120],
            str(deep_link or "/pulse")[:700],
            str(body or "")[:2000],
            cutoff,
        ),
    )
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else 0


def _log_pulse_delivery(notification_id, user_id, channel, provider, status, provider_response=None, error_message=""):
    if not notification_id:
        return
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    response = json.dumps(provider_response or {})[:4000] if isinstance(provider_response, (dict, list)) else str(provider_response or "")[:4000]
    cur.execute(
        """
        INSERT INTO pulse_notification_deliveries
        (notification_id, user_id, channel, provider, status, error_message, provider_response, created_at, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(notification_id),
            int(user_id or 0),
            str(channel or "")[:40],
            str(provider or "")[:80],
            str(status or "")[:80],
            str(error_message or "")[:1200],
            response,
            now,
            now if status in {"sent", "created", "skipped", "not_configured", "rate_limited", "duplicate_suppressed"} else None,
        ),
    )
    conn.commit()
    conn.close()


def _template_key(note_type, category):
    note_type = str(note_type or "").strip()
    if note_type in {"user_signup"}:
        return "welcome"
    if note_type in {"founder_premium_activated", "founder_number_assigned", "founder_badge_granted"}:
        return "founder_premium_activated"
    if note_type in {"payment_succeeded", "subscription_renewed", "payment_received"}:
        return "payment_receipt"
    if note_type in {"password_reset", "account_recovery"}:
        return "password_reset"
    if note_type in {"follow", "new_follower"}:
        return "new_follower"
    if note_type in {"message", "voice_message"}:
        return "new_message"
    if category == "crypto":
        return "crypto_alert"
    if category == "security":
        return "security_alert"
    return "welcome"


def _branded_html(headline, body, deep_link="/pulse/notifications"):
    headline = str(headline or "Pulse notification")[:180]
    body = str(body or "")[:2000]
    link = str(deep_link or "https://pulsesoc.com/pulse/notifications")[:700]
    if link.startswith("/"):
        link = f"https://pulsesoc.com{link}"
    return (
        "<div style='font-family:Inter,Arial,sans-serif;line-height:1.55;color:#0f172a'>"
        "<p style='font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#2563eb'>Pulse</p>"
        f"<h1 style='font-size:24px;margin:0 0 12px'>{headline}</h1>"
        f"<p>{body}</p>"
        f"<p><a href='{link}' style='color:#2563eb'>Open Pulse</a></p>"
        "<p style='font-size:12px;color:#64748b'>Pulse is operated by CoinPilotXAI Inc. Visit PulseSoc.com.</p>"
        "</div>"
    )


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
    metadata = _json(item.get("metadata_json"), {})
    item["metadata"] = metadata
    item["actor_name"] = metadata.get("actor_name") or metadata.get("sender_name") or ""
    item["actor_avatar"] = metadata.get("actor_avatar") or metadata.get("sender_avatar") or ""
    item["preview_text"] = metadata.get("preview_text") or metadata.get("reply_preview") or metadata.get("comment_preview") or metadata.get("message_preview") or item.get("body") or ""
    item["original_preview"] = metadata.get("original_preview") or metadata.get("post_preview") or metadata.get("status_preview") or ""
    item["postId"] = metadata.get("post_id") or metadata.get("postId") or ""
    item["statusId"] = metadata.get("status_id") or metadata.get("statusId") or ""
    item["commentId"] = metadata.get("comment_id") or metadata.get("commentId") or ""
    item["replyId"] = metadata.get("reply_id") or metadata.get("replyId") or ""
    item["conversationId"] = metadata.get("conversation_id") or metadata.get("conversationId") or ""
    item["content_type"] = metadata.get("content_type") or metadata.get("source_type") or item.get("entity_type") or item.get("type") or ""
    item["mobile_deep_link"] = metadata.get("mobile_deep_link") or _mobile_deep_link(item)
    item["deepLink"] = item["mobile_deep_link"]
    if item["actor_user_id"] and (not item["actor_name"] or not item["actor_avatar"]):
        try:
            conn = user_context.connect()
            cur = conn.cursor()
            cur.execute("SELECT display_name, full_name, username, avatar_url FROM users WHERE user_id=? LIMIT 1", (item["actor_user_id"],))
            actor = user_context.row_to_dict(cur.fetchone()) or {}
            conn.close()
            item["actor_name"] = item["actor_name"] or actor.get("display_name") or actor.get("full_name") or actor.get("username") or ""
            item["actor_avatar"] = item["actor_avatar"] or actor.get("avatar_url") or ""
        except Exception:
            item["actor_name"] = item["actor_name"] or ""
    return item


def _mobile_deep_link(item):
    metadata = item.get("metadata") or {}
    post_id = metadata.get("post_id") or metadata.get("postId")
    status_id = metadata.get("status_id") or metadata.get("statusId")
    comment_id = metadata.get("comment_id") or metadata.get("commentId")
    reply_id = metadata.get("reply_id") or metadata.get("replyId")
    conversation_id = metadata.get("conversation_id") or metadata.get("conversationId")
    note_type = str(item.get("type") or "")
    if conversation_id:
        return f"pulse://messages/{conversation_id}"
    if status_id:
        if reply_id or comment_id:
            return f"pulse://status/{status_id}/reply/{reply_id or comment_id}"
        return f"pulse://status/{status_id}"
    if post_id:
        if comment_id:
            return f"pulse://post/{post_id}/comment/{comment_id}"
        return f"pulse://post/{post_id}"
    if note_type == "message" and item.get("entity_id"):
        return f"pulse://messages/{item.get('entity_id')}"
    return item.get("deep_link") or item.get("target_url") or "pulse://pulse/notifications"


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
    existing_id = _recent_duplicate(user_id, note_type, entity_type, entity_id, deep_link, body)
    if existing_id:
        _log_pulse_delivery(existing_id, user_id, "in_app", "pulse", "duplicate_suppressed", {"dedupe": True}, "")
        return {"ok": True, "notification_id": existing_id, "duplicate_suppressed": True}
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
            values = {**values, "in_app": True, "email": True}
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
    category = _pulse_category(_pulse_type_for_alert(alert_type))
    pulse_prefs = pulse_preferences(user_id).get("preferences", {})
    if category in pulse_prefs:
        values = {**pulse_prefs[category], "telegram": False}
    else:
        prefs = get_preferences(user_id).get("preferences", {})
        values = prefs.get(alert_type) or prefs.get("market_alerts") or {"in_app": True, "push": False, "email": False, "sms": False, "telegram": False}
    if category == "security" or str(alert_type or "") in SECURITY_NOTIFICATION_TYPES:
        values = {**values, "in_app": True, "email": True}
    return values


def send_sms_alert(user, title, message, notification_id=None):
    user = user or {}
    if not _sms_enabled() or not sms_service.is_sms_configured():
        return {"ok": False, "status": "not_configured", "provider": "brevo_sms", "message": "Brevo SMS is not configured."}
    phone = user.get("phone_number") or user.get("phone") or ""
    if not phone or int(user.get("sms_opt_in") or 0) != 1 or int(user.get("phone_verified") or 0) != 1:
        return {"ok": False, "status": "not_configured", "message": "User has not opted in to SMS alerts or has no phone."}
    return sms_service.send_sms(phone, f"{title}\n\n{message}", purpose="notification", user_id=int(user.get("user_id") or user.get("id") or 0))

def send_email_notification(user_id, title, body, notification_type="message", metadata=None, notification_id=None):
    user = _user_record(user_id)
    category = _pulse_category(_pulse_type_for_alert(notification_type))
    if not _email_enabled():
        _log_delivery(user_id, notification_id, "email", "skipped", "", "Brevo email notifications are disabled.")
        _log_pulse_delivery(notification_id, user_id, "email", "brevo", "skipped", {}, "Brevo email notifications are disabled.")
        return {"ok": False, "status": "skipped", "provider": "brevo", "message": "Email notifications are disabled."}
    if not _rate_allowed(user_id, "email"):
        _log_delivery(user_id, notification_id, "email", "rate_limited", "", "Email rate limit exceeded.")
        _log_pulse_delivery(notification_id, user_id, "email", "brevo", "rate_limited", {}, "Email rate limit exceeded.")
        return {"ok": False, "status": "rate_limited", "provider": "brevo", "message": "Email rate limit exceeded."}
    template = BREVO_NOTIFICATION_TEMPLATES.get(_template_key(notification_type, category), BREVO_NOTIFICATION_TEMPLATES["welcome"])
    deep_link = (metadata or {}).get("deep_link") or (metadata or {}).get("target_url") or "/pulse/notifications"
    subject = str(title or template["subject"])[:180]
    headline = str(title or template["headline"])[:180]
    copy = str(body or template["body"])[:2000]
    result = email_service.send_email(
        user.get("email"),
        subject,
        _branded_html(headline, copy, deep_link),
        copy,
        email_type=notification_type,
    )
    status = "sent" if result.get("ok") else "failed"
    _log_delivery(user_id, notification_id, "email", status, result.get("provider_response") or result, result.get("error"))
    _log_pulse_delivery(notification_id, user_id, "email", "brevo", status, result.get("provider_response") or result, result.get("error"))
    return {"ok": result.get("ok"), "status": status, "provider": "brevo", "message_id": result.get("message_id") or ""}


def send_sms_notification(user_id, title, body, notification_type="message", metadata=None, notification_id=None):
    if not _rate_allowed(user_id, "sms"):
        _log_delivery(user_id, notification_id, "sms", "rate_limited", "", "SMS rate limit exceeded.")
        _log_pulse_delivery(notification_id, user_id, "sms", "brevo_sms", "rate_limited", {}, "SMS rate limit exceeded.")
        return {"ok": False, "status": "rate_limited", "provider": "brevo_sms", "message": "SMS rate limit exceeded."}
    result = send_sms_alert(_user_record(user_id), title, body, notification_id)
    status = result.get("status", "failed")
    _log_delivery(user_id, notification_id, "sms", status, result, result.get("message"))
    _log_pulse_delivery(notification_id, user_id, "sms", "brevo_sms", status, result, result.get("message"))
    return {"ok": result.get("ok"), "status": status, "provider": "brevo_sms"}


def send_in_app_channel_notification(user_id, title, body, notification_type="message", metadata=None):
    metadata = metadata or {}
    note_type = _pulse_type_for_alert(notification_type)
    deep_link = metadata.get("deep_link") or metadata.get("target_url") or metadata.get("next_url") or metadata.get("url") or metadata.get("href") or "/pulse/notifications"
    return create_pulse_notification(
        user_id,
        note_type,
        title,
        body,
        actor_user_id=metadata.get("actor_user_id") or metadata.get("sender_user_id") or metadata.get("from_user_id") or 0,
        entity_type=metadata.get("entity_type") or metadata.get("content_type") or "",
        entity_id=metadata.get("entity_id") or metadata.get("post_id") or metadata.get("message_id") or metadata.get("conversation_id") or "",
        deep_link=deep_link,
        delivery_status="created",
        metadata=metadata,
    )


def send_multi_channel_notification(user_id, notification_type, title, body, metadata=None, channels=None):
    metadata = metadata or {}
    prefs = _category_prefs(user_id, notification_type)
    requested = set(channels or [])
    security = _pulse_category(_pulse_type_for_alert(notification_type)) == "security" or notification_type in SECURITY_NOTIFICATION_TYPES
    if not requested:
        requested = {"in_app"}
        for key in ("email", "sms", "push", "telegram"):
            if prefs.get(key):
                requested.add(key)
    if security:
        requested.update({"in_app", "email"})
    in_app = send_in_app_channel_notification(user_id, title, body, notification_type, metadata)
    notification_id = in_app.get("notification_id")
    result = {"ok": True, "notification_id": notification_id, "pulse_notification_id": notification_id, "in_app": "created"}
    _log_delivery(user_id, notification_id, "in_app", result["in_app"], {"notification_id": notification_id}, "")
    if in_app.get("duplicate_suppressed"):
        result["in_app"] = "duplicate_suppressed"
        _log_delivery(user_id, notification_id, "in_app", "duplicate_suppressed", {"notification_id": notification_id}, "")
        if not channels:
            result["ok"] = True
            return result
    if "email" in requested:
        if prefs.get("email") or security or "email" in (channels or []):
            email_result = send_email_notification(user_id, title, body, notification_type, metadata, notification_id)
            result["email"] = email_result.get("status", "failed")
        else:
            result["email"] = "skipped"
            _log_delivery(user_id, notification_id, "email", "skipped", "", "Email alerts disabled.")
            _log_pulse_delivery(notification_id, user_id, "email", "brevo", "skipped", {}, "Email alerts disabled.")
    if "sms" in requested:
        if prefs.get("sms") or "sms" in (channels or []):
            sms_result = send_sms_notification(user_id, title, body, notification_type, metadata, notification_id)
            result["sms"] = sms_result.get("status", "failed")
        else:
            result["sms"] = "skipped"
            _log_delivery(user_id, notification_id, "sms", "skipped", "", "SMS alerts disabled.")
            _log_pulse_delivery(notification_id, user_id, "sms", "brevo_sms", "skipped", {}, "SMS alerts disabled.")
    if "push" in requested:
        push_result = send_push_alert(user_id, title, body, metadata)
        result["push"] = push_result.get("status", "failed")
        _log_delivery(user_id, notification_id, "push", result["push"], push_result, push_result.get("message"))
        _log_pulse_delivery(notification_id, user_id, "push", "web_push", result["push"], push_result, push_result.get("message"))
    if "telegram" in requested:
        telegram_result = send_telegram_alert(_user_record(user_id), title, body)
        result["telegram"] = telegram_result.get("status", "failed")
        _log_delivery(user_id, notification_id, "telegram", result["telegram"], telegram_result, telegram_result.get("message"))
    return result


class NotificationService:
    @staticmethod
    def send(user_id, notification_type, title, body, metadata=None, channels=None):
        return send_multi_channel_notification(user_id, notification_type, title, body, metadata, channels)


sendEmailNotification = send_email_notification
sendSmsNotification = send_sms_notification
sendInAppNotification = send_in_app_channel_notification
sendMultiChannelNotification = send_multi_channel_notification


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
    created = queue_notification(user_id, title, body, alert_type, data)
    notification_id = created.get("notification_id")
    result = send_multi_channel_notification(
        user_id,
        alert_type,
        title,
        body,
        {**data, "legacy_notification_id": notification_id, "legacy_alert_type": alert_type},
        channels=channels,
    )
    result["notification_id"] = notification_id
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
    return send_in_app_channel_notification(user_id, title, message, notification_type, metadata)


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
