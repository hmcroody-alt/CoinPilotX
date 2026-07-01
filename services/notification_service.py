import json
import logging
import os
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta

from . import user_context
from . import email_service
from . import push_service
from . import sms_service
from . import db as db_service


def _now():
    return datetime.now().isoformat()


def _dispatch_command_center_async(method_name, *args, **kwargs):
    try:
        from . import command_center_client

        if not command_center_client.is_enabled():
            return False
        method = getattr(command_center_client, method_name, None)
        if not callable(method):
            return False

        def run_dispatch():
            try:
                result = method(*args, **kwargs)
                if not result.get("ok"):
                    logging.info("NOTIFICATION_COMMAND_CENTER_DISPATCH_FAILED method=%s reason=%s", method_name, result.get("reason") or "unknown")
            except Exception as exc:
                logging.info("NOTIFICATION_COMMAND_CENTER_DISPATCH_SKIPPED method=%s error=%s", method_name, exc.__class__.__name__)

        threading.Thread(target=run_dispatch, name=f"notification-{method_name}", daemon=True).start()
        return True
    except Exception as exc:
        logging.info("NOTIFICATION_COMMAND_CENTER_UNAVAILABLE method=%s error=%s", method_name, exc.__class__.__name__)
        return False


def _message_like_notification(note_type="", entity_type="", deep_link=""):
    normalized_type = str(note_type or "").strip().lower()
    normalized_entity = str(entity_type or "").strip().lower()
    normalized_link = str(deep_link or "").strip().lower()
    return (
        normalized_type in MESSAGE_NOTIFICATION_TYPES
        or normalized_entity in {"message", "messages", "chat", "conversation", "pulse_message", "pulse_conversation", "comm_v2_message"}
        or normalized_link.startswith(("/pulse/messages", "/messages", "/chat"))
    )


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


def _preferred_language_for_user(user_id):
    try:
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute("SELECT preferred_language FROM users WHERE user_id=? LIMIT 1", (int(user_id or 0),))
        row = cur.fetchone()
        conn.close()
        value = ""
        if row is not None:
            value = row["preferred_language"] if hasattr(row, "keys") else row[0]
        value = str(value or "en").strip().lower().replace("_", "-")[:16]
        return value if value in {"en", "es", "fr", "ht", "pt", "de", "it", "ar"} else "en"
    except Exception:
        return "en"


PULSE_NOTIFICATION_CATEGORIES = {
    "chat_message": {"in_app": True, "push": True, "email": False, "sms": False},
    "group_message": {"in_app": True, "push": True, "email": False, "sms": False},
    "room_message": {"in_app": True, "push": True, "email": False, "sms": False},
    "comment": {"in_app": True, "push": True, "email": False, "sms": False},
    "reply": {"in_app": True, "push": True, "email": False, "sms": False},
    "reaction": {"in_app": True, "push": True, "email": False, "sms": False},
    "follow": {"in_app": True, "push": True, "email": False, "sms": False},
    "status_view": {"in_app": True, "push": True, "email": False, "sms": False},
    "live_invite": {"in_app": True, "push": True, "email": False, "sms": False},
    "marketplace_order": {"in_app": True, "push": True, "email": True, "sms": False},
    "teacher_order": {"in_app": True, "push": True, "email": True, "sms": False},
    "system_security": {"in_app": True, "push": True, "email": True, "sms": False},
    "account": {"in_app": True, "push": True, "email": True, "sms": False},
    "premium": {"in_app": True, "push": True, "email": True, "sms": False},
    "social": {"in_app": True, "push": True, "email": False, "sms": False},
    "live": {"in_app": True, "push": True, "email": False, "sms": False},
    "status": {"in_app": True, "push": True, "email": False, "sms": False},
    "marketplace": {"in_app": True, "push": True, "email": True, "sms": False},
    "purchase": {"in_app": True, "push": True, "email": True, "sms": False},
    "payments": {"in_app": True, "push": True, "email": True, "sms": False},
    "crypto": {"in_app": True, "push": True, "email": False, "sms": False},
    "security": {"in_app": True, "push": True, "email": True, "sms": False},
    "admin_security": {"in_app": True, "push": True, "email": True, "sms": False},
    "marketing": {"in_app": False, "push": False, "email": False, "sms": False},
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
    "chat_message": "chat_message",
    "group_message": "group_message",
    "room_message": "room_message",
    "like": "likes",
    "post_like": "social",
    "reaction": "reaction",
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
    "message": "chat_message",
    "private_message": "chat_message",
    "voice_message": "chat_message",
    "group_invite": "group_message",
    "community_invite": "social",
    "room_invite": "room_message",
    "status_view": "status_view",
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
    "live_invite": "live_invite",
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
    "security_alert": "system_security",
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
    "marketplace_order": "marketplace_order",
    "teacher_order": "teacher_order",
    "order_accepted": "marketplace",
    "order_shipped": "marketplace",
    "order_delivered": "marketplace",
    "refund_issued": "marketplace",
    "payment_received": "marketplace",
    "purchase": "purchase",
    "purchase_completed": "purchase",
    "purchase_failed": "payments",
    "payment_success": "payments",
    "payment_failure": "payments",
    "payment_refunded": "payments",
    "subscription_renewal": "premium",
    "premium_subscription": "premium",
    "premium_purchase": "premium",
    "crypto_price_alert": "crypto",
    "crypto_alert_triggered": "crypto",
    "crypto_percentage_alert": "crypto",
    "crypto_volatility_alert": "crypto",
    "crypto_security_alert": "crypto",
    "portfolio_alert": "crypto",
    "watchlist_alert": "crypto",
    "major_market_movement": "crypto",
    "custom_crypto_alert": "crypto",
    "cohost_request": "live",
    "cohost_request_received": "live",
    "cohost_accepted": "live",
    "cohost_denied": "live",
    "cohost_removed": "live",
    "guest_removed": "live",
    "live_highlight_ready": "live",
    "admin_security_event": "admin_security",
    "account_locked": "security",
    "password_reset_requested": "security",
    "marketing_update": "marketing",
}

MESSAGE_NOTIFICATION_TYPES = {
    "message",
    "chat_message",
    "voice_message",
    "group_message",
    "room_message",
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
        "subject": "Welcome to PulseSoc",
        "headline": "Welcome to PulseSoc",
        "body": "Your PulseSoc account is ready. Start connecting, creating, and discovering on PulseSoc.com.",
    },
    "founder_premium_activated": {
        "subject": "Founder Premium is active",
        "headline": "Founder Premium is active",
        "body": "Your Founder Premium membership is active. Your Founder benefits are now available in PulseSoc.",
    },
    "payment_receipt": {
        "subject": "PulseSoc payment receipt",
        "headline": "Payment received",
        "body": "We received your PulseSoc payment. Your account has been updated.",
    },
    "password_reset": {
        "subject": "Reset your PulseSoc password",
        "headline": "Reset your PulseSoc password",
        "body": "Use the secure reset link to update your PulseSoc password. Ignore this message if you did not request it.",
    },
    "new_follower": {
        "subject": "You have a new PulseSoc follower",
        "headline": "New follower",
        "body": "Someone new followed you on PulseSoc.",
    },
    "new_message": {
        "subject": "New PulseSoc message",
        "headline": "New message",
        "body": "You received a new direct message on PulseSoc.",
    },
    "crypto_alert": {
        "subject": "PulseSoc crypto alert",
        "headline": "Crypto alert",
        "body": "A crypto alert you enabled was triggered.",
    },
    "security_alert": {
        "subject": "PulseSoc security alert",
        "headline": "Security alert",
        "body": "We detected an important security event on your PulseSoc account.",
    },
    "live_alert": {
        "subject": "PulseSoc Live update",
        "headline": "Live update",
        "body": "There is an update for a PulseSoc Live session.",
    },
    "social_alert": {
        "subject": "PulseSoc social update",
        "headline": "Social update",
        "body": "There is new activity on PulseSoc.",
    },
    "marketplace_alert": {
        "subject": "PulseSoc marketplace update",
        "headline": "Marketplace update",
        "body": "There is an update for your PulseSoc marketplace activity.",
    },
}

UNIVERSAL_NOTIFICATION_EVENTS = {
    "signup_welcome": {"type": "user_signup", "title": "Welcome to PulseSoc", "body": "Your PulseSoc account is ready.", "channels": ["in_app", "email"], "entity_type": "account"},
    "email_confirmation": {"type": "email_verification", "title": "Verify your PulseSoc email", "body": "Confirm your email to protect your account.", "channels": ["in_app", "email"], "entity_type": "account"},
    "password_reset": {"type": "password_reset", "title": "PulseSoc password reset", "body": "A password reset was requested for your account.", "channels": ["in_app", "email", "sms"], "priority": "high", "entity_type": "security"},
    "password_reset_requested": {"type": "password_reset_requested", "title": "PulseSoc password reset requested", "body": "A password reset was requested for your account.", "channels": ["in_app", "email", "sms"], "priority": "high", "entity_type": "security"},
    "password_changed": {"type": "password_changed", "title": "PulseSoc password changed", "body": "Your PulseSoc password was changed.", "channels": ["in_app", "email", "push", "sms"], "priority": "critical", "entity_type": "security"},
    "account_login": {"type": "account_login", "title": "PulseSoc login", "body": "Your PulseSoc account was accessed.", "channels": ["in_app", "email", "push"], "priority": "high", "entity_type": "security"},
    "new_device": {"type": "new_device", "title": "New device login", "body": "A new device signed in to your PulseSoc account.", "channels": ["in_app", "email", "push", "sms"], "priority": "critical", "entity_type": "security"},
    "suspicious_login": {"type": "suspicious_login", "title": "Suspicious login blocked", "body": "PulseSoc detected suspicious login activity.", "channels": ["in_app", "email", "push", "sms"], "priority": "critical", "entity_type": "security"},
    "account_locked": {"type": "account_locked", "title": "PulseSoc account locked", "body": "Your account was locked for protection.", "channels": ["in_app", "email", "push", "sms"], "priority": "critical", "entity_type": "security"},
    "email_changed": {"type": "email_changed", "title": "PulseSoc email changed", "body": "Your account email was changed.", "channels": ["in_app", "email", "push"], "priority": "critical", "entity_type": "security"},
    "phone_changed": {"type": "phone_changed", "title": "PulseSoc phone changed", "body": "Your account phone number was changed.", "channels": ["in_app", "email", "push", "sms"], "priority": "critical", "entity_type": "security"},
    "chat_message": {"type": "chat_message", "title": "New PulseSoc message", "body": "You received a new message.", "channels": ["in_app", "push"], "entity_type": "conversation"},
    "message": {"type": "chat_message", "title": "New PulseSoc message", "body": "You received a new message.", "channels": ["in_app", "push"], "entity_type": "conversation"},
    "reaction": {"type": "reaction", "title": "New reaction", "body": "Someone reacted to your PulseSoc activity.", "channels": ["in_app", "push"], "entity_type": "post"},
    "comment": {"type": "comment", "title": "New comment", "body": "Someone commented on your PulseSoc post.", "channels": ["in_app", "push"], "entity_type": "post"},
    "reply": {"type": "reply", "title": "New reply", "body": "Someone replied to your PulseSoc comment.", "channels": ["in_app", "push"], "entity_type": "comment"},
    "repost": {"type": "post_repost", "title": "New repost", "body": "Someone reposted your PulseSoc post.", "channels": ["in_app", "push"], "entity_type": "post"},
    "save": {"type": "save", "title": "Post saved", "body": "Someone saved your PulseSoc post.", "channels": ["in_app"], "entity_type": "post"},
    "follow": {"type": "follow", "title": "New follower", "body": "Someone followed you on PulseSoc.", "channels": ["in_app", "push"], "entity_type": "profile"},
    "mention": {"type": "mention", "title": "You were mentioned", "body": "Someone mentioned you on PulseSoc.", "channels": ["in_app", "push"], "entity_type": "mention"},
    "tag": {"type": "mention", "title": "You were tagged", "body": "Someone tagged you on PulseSoc.", "channels": ["in_app", "push"], "entity_type": "mention"},
    "crypto_alert": {"type": "crypto_price_alert", "title": "PulseSoc crypto alert", "body": "A crypto alert you enabled was triggered.", "channels": ["in_app", "push", "email", "sms"], "priority": "high", "entity_type": "crypto_alert"},
    "crypto_alert_triggered": {"type": "crypto_alert_triggered", "title": "PulseSoc crypto alert", "body": "A crypto alert you enabled was triggered.", "channels": ["in_app", "push", "email", "sms"], "priority": "high", "entity_type": "crypto_alert"},
    "live_started": {"type": "live_started", "title": "PulseSoc Live started", "body": "A creator you follow is live.", "channels": ["in_app", "push"], "entity_type": "live"},
    "cohost_request": {"type": "cohost_request", "title": "Co-host request received", "body": "A viewer requested to co-host your Live.", "channels": ["in_app", "push"], "priority": "high", "entity_type": "live"},
    "cohost_request_received": {"type": "cohost_request_received", "title": "Co-host request received", "body": "A viewer requested to co-host your Live.", "channels": ["in_app", "push"], "priority": "high", "entity_type": "live"},
    "cohost_accepted": {"type": "cohost_accepted", "title": "Co-host request accepted", "body": "The host approved your co-host request.", "channels": ["in_app", "push"], "priority": "high", "entity_type": "live"},
    "cohost_denied": {"type": "cohost_denied", "title": "Co-host request denied", "body": "The host denied your co-host request.", "channels": ["in_app", "push"], "entity_type": "live"},
    "guest_removed": {"type": "guest_removed", "title": "Removed from Live", "body": "You were removed from the Live co-host seat.", "channels": ["in_app", "push"], "priority": "high", "entity_type": "live"},
    "live_ended": {"type": "live_ended", "title": "PulseSoc Live ended", "body": "The Live session ended.", "channels": ["in_app", "push"], "entity_type": "live"},
    "live_replay_ready": {"type": "live_replay_ready", "title": "Live replay ready", "body": "Your PulseSoc Live replay is ready.", "channels": ["in_app", "push", "email"], "entity_type": "live"},
    "live_highlight_ready": {"type": "live_highlight_ready", "title": "Live highlight ready", "body": "A PulseSoc Live highlight is ready.", "channels": ["in_app", "push"], "entity_type": "live"},
    "purchase": {"type": "purchase", "title": "Payment complete", "body": "Your PulseSoc purchase was completed.", "channels": ["in_app", "push", "email"], "priority": "high", "entity_type": "purchase"},
    "payment_succeeded": {"type": "payment_succeeded", "title": "Payment complete", "body": "Your PulseSoc payment was completed.", "channels": ["in_app", "push", "email"], "priority": "high", "entity_type": "payment"},
    "payment_failed": {"type": "payment_failed", "title": "Payment failed", "body": "Your PulseSoc payment did not complete.", "channels": ["in_app", "push", "email"], "priority": "high", "entity_type": "payment"},
    "premium": {"type": "premium_alert", "title": "PulseSoc Premium update", "body": "There is a Premium account update.", "channels": ["in_app", "push", "email"], "entity_type": "premium"},
    "marketplace_order": {"type": "marketplace_order", "title": "Marketplace order update", "body": "There is an update for your PulseSoc marketplace order.", "channels": ["in_app", "push", "email"], "entity_type": "marketplace_order"},
    "admin_security_event": {"type": "admin_security_event", "title": "PulseSoc security event", "body": "An admin/security event needs review.", "channels": ["in_app", "push", "email"], "priority": "critical", "entity_type": "admin_security"},
}

UNIVERSAL_EVENT_ALIASES = {
    "signup": "signup_welcome",
    "welcome": "signup_welcome",
    "email_confirm": "email_confirmation",
    "email_verification": "email_confirmation",
    "password_reset_alert": "password_reset_requested",
    "login": "account_login",
    "new_login": "account_login",
    "login_new_device": "new_device",
    "security_alert": "suspicious_login",
    "new_message": "chat_message",
    "private_message": "chat_message",
    "new_reaction": "reaction",
    "like": "reaction",
    "post_like": "reaction",
    "reel_reaction": "reaction",
    "status_reaction": "reaction",
    "feed_post_reaction": "reaction",
    "new_comment": "comment",
    "post_comment": "comment",
    "comment_reply": "reply",
    "repost": "repost",
    "post_repost": "repost",
    "reposts": "repost",
    "saves": "save",
    "follows": "follow",
    "mentions": "mention",
    "tags": "tag",
    "crypto_price_alert": "crypto_alert",
    "price_trigger": "crypto_alert",
    "percentage_trigger": "crypto_alert",
    "volatility_trigger": "crypto_alert",
    "scam_security_alert": "crypto_alert",
    "live_cohost_request": "cohost_request",
    "live_cohost_request_created": "cohost_request",
    "live_cohost_request_accepted": "cohost_accepted",
    "live_cohost_request_denied": "cohost_denied",
    "cohost_request_accepted": "cohost_accepted",
    "cohost_request_denied": "cohost_denied",
    "guest_removed_from_live": "guest_removed",
    "replay_available": "live_replay_ready",
    "purchase_payment": "purchase",
    "purchase_completed": "purchase",
    "payment_complete": "payment_succeeded",
    "premium_subscription": "premium",
    "subscription": "premium",
    "subscription_renewed": "premium",
    "marketplace_purchase": "marketplace_order",
    "marketplace_order_update": "marketplace_order",
    "admin_alert": "admin_security_event",
}

SELF_NOTIFICATION_ALLOWED_TYPES = {
    "user_signup",
    "email_verification",
    "phone_verification",
    "password_reset",
    "password_reset_requested",
    "password_changed",
    "account_login",
    "new_device",
    "suspicious_login",
    "account_locked",
    "email_changed",
    "phone_changed",
    "payment_succeeded",
    "payment_failed",
    "purchase",
    "premium_alert",
    "marketplace_order",
    "admin_security_event",
}

RATE_WINDOWS = {"email": 60, "sms": 300, "in_app": 20}
RATE_LIMITS = {"email": 12, "sms": 4, "in_app": 20}
MEMORY_DELIVERY_RATE = {}
EMAIL_QUEUE_PROCESSOR_LOCK = threading.Lock()


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
    if category in {"security", "admin_security"}:
        return "security_alert"
    if category == "live":
        return "live_alert"
    if category in {"social", "status", "comments", "likes", "mentions", "follows"}:
        return "social_alert"
    if category in {"marketplace", "marketplace_order", "teacher_order", "purchase", "payments"}:
        return "marketplace_alert"
    return "welcome"


def _branded_html(headline, body, deep_link="/pulse/notifications"):
    headline = str(headline or "PulseSoc notification")[:180]
    body = str(body or "")[:2000]
    link = str(deep_link or "https://pulsesoc.com/pulse/notifications")[:700]
    if link.startswith("/"):
        link = f"https://pulsesoc.com{link}"
    return (
        "<div style='font-family:Inter,Arial,sans-serif;line-height:1.55;color:#0f172a'>"
        "<p style='font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#2563eb'>PulseSoc</p>"
        f"<h1 style='font-size:24px;margin:0 0 12px'>{headline}</h1>"
        f"<p>{body}</p>"
        f"<p><a href='{link}' style='color:#2563eb'>Open PulseSoc</a></p>"
        "<p style='font-size:12px;color:#64748b'>PulseSoc is operated by CoinPlotXAI Inc. Visit PulseSoc.com.</p>"
        "</div>"
    )


def _notification_trace_id(metadata=None):
    metadata = metadata or {}
    return str(metadata.get("trace_id") or metadata.get("push_trace_id") or secrets.token_hex(8))[:120]


def _normalize_channels(channels):
    if channels is None:
        return []
    if isinstance(channels, str):
        channels = [part.strip() for part in channels.split(",")]
    allowed = {"in_app", "push", "email", "sms", "telegram"}
    normalized = []
    for channel in channels or []:
        value = str(channel or "").strip().lower()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return normalized


def _event_spec(event_type):
    event_type = str(event_type or "").strip().lower()
    event_type = UNIVERSAL_EVENT_ALIASES.get(event_type, event_type)
    return UNIVERSAL_NOTIFICATION_EVENTS.get(event_type) or {
        "type": event_type if event_type in PULSE_TYPE_TO_CATEGORY else "message",
        "title": "PulseSoc notification",
        "body": "There is new PulseSoc activity.",
        "channels": ["in_app", "push"],
        "entity_type": "",
    }


def _deep_link_for_event(event_type, content_id="", deep_link="", metadata=None):
    metadata = metadata or {}
    if deep_link:
        return str(deep_link)[:700]
    event_type = str(event_type or "").strip().lower()
    content_id = str(content_id or metadata.get("content_id") or metadata.get("entity_id") or "").strip()
    if metadata.get("deep_link") or metadata.get("target_url") or metadata.get("url"):
        return str(metadata.get("deep_link") or metadata.get("target_url") or metadata.get("url"))[:700]
    conversation_id = metadata.get("conversation_id") or metadata.get("conversationId")
    post_id = metadata.get("post_id") or metadata.get("postId")
    status_id = metadata.get("status_id") or metadata.get("statusId")
    profile_id = metadata.get("profile_user_id") or metadata.get("followed_user_id") or metadata.get("actor_user_id") or (content_id if event_type in {"follow", "new_follower", "follow_request"} else "")
    live_id = metadata.get("live_id") or metadata.get("liveId") or (content_id if "live" in event_type or "cohost" in event_type else "")
    order_id = metadata.get("order_id") or metadata.get("purchase_id") or metadata.get("payment_id") or (content_id if event_type in {"purchase", "payment_succeeded", "payment_failed", "marketplace_order", "order_accepted", "order_shipped", "order_delivered"} else "")
    alert_id = metadata.get("alert_id") or metadata.get("alert_rule_id") or (content_id if event_type.startswith("crypto") else "")
    if conversation_id:
        return f"/pulse/messages/{conversation_id}"
    if status_id:
        return f"/pulse/status/{status_id}"
    if post_id:
        return f"/pulse/post/{post_id}"
    if profile_id:
        return f"/pulse/profile/{profile_id}"
    if live_id:
        return f"/pulse/live/{live_id}"
    if alert_id:
        return f"/pulse/alerts/{alert_id}"
    if order_id:
        return f"/pulse/purchases/{order_id}"
    if event_type in {"account_login", "new_device", "suspicious_login", "password_changed", "password_reset", "password_reset_requested", "email_changed", "phone_changed", "account_locked"}:
        return "/account/security"
    if event_type in {"premium", "premium_subscription", "premium_purchase"}:
        return "/pulse/premium"
    if event_type in {"marketplace_order", "order_accepted", "order_shipped", "order_delivered"}:
        return "/pulse/marketplace/orders"
    return "/pulse/notifications"


def _mobile_link_for_event(event_type, deep_link="", content_id="", metadata=None):
    metadata = metadata or {}
    explicit = metadata.get("mobile_deep_link") or metadata.get("native_url") or metadata.get("app_url") or metadata.get("deepLink")
    if explicit:
        return str(explicit)[:700]
    conversation_id = metadata.get("conversation_id") or metadata.get("conversationId")
    post_id = metadata.get("post_id") or metadata.get("postId")
    status_id = metadata.get("status_id") or metadata.get("statusId")
    comment_id = metadata.get("comment_id") or metadata.get("commentId")
    profile_id = metadata.get("profile_user_id") or metadata.get("followed_user_id") or metadata.get("actor_user_id") or (content_id if str(event_type or "") in {"follow", "new_follower", "follow_request"} else "")
    live_id = metadata.get("live_id") or metadata.get("liveId") or (content_id if "live" in str(event_type or "") or "cohost" in str(event_type or "") else "")
    alert_id = metadata.get("alert_id") or metadata.get("alert_rule_id") or (content_id if str(event_type or "").startswith("crypto") else "")
    order_id = metadata.get("order_id") or metadata.get("purchase_id") or metadata.get("payment_id") or (content_id if str(event_type or "") in {"purchase", "payment_succeeded", "payment_failed", "marketplace_order", "order_accepted", "order_shipped", "order_delivered"} else "")
    if conversation_id:
        return f"pulse://pulse/messages-v2?conversation={conversation_id}"
    if post_id:
        if comment_id:
            return f"pulse://post/{post_id}/comment/{comment_id}"
        return f"pulse://post/{post_id}"
    if status_id:
        return f"pulse://status/{status_id}"
    if profile_id:
        return f"pulse://pulse/profile/{profile_id}"
    if live_id:
        return "pulse://pulse/live/studio" if "cohost_request" in str(event_type or "") else f"pulse://live/{live_id}"
    if alert_id:
        return f"pulse://alerts/{alert_id}"
    if order_id:
        return f"pulse://purchase/{order_id}"
    if str(event_type or "") in {"account_login", "new_device", "suspicious_login", "password_changed", "password_reset", "password_reset_requested", "email_changed", "phone_changed", "account_locked"}:
        return "pulse://account/security"
    if deep_link and str(deep_link).startswith("/"):
        return f"pulse://{str(deep_link).lstrip('/')}"
    return str(deep_link or "pulse://pulse/notifications")[:700]


def _quiet_hours_active(user_id):
    try:
        experience = get_preferences(user_id).get("experience") or {}
        if not experience.get("quiet_hours_enabled"):
            return False
        def minutes(value, fallback):
            raw = str(value or fallback)
            parts = raw.split(":")
            return max(0, min(1439, int(parts[0]) * 60 + int(parts[1])))
        start = minutes(experience.get("quiet_hours_start"), "22:00")
        end = minutes(experience.get("quiet_hours_end"), "07:00")
        now = datetime.now()
        current = now.hour * 60 + now.minute
        return start <= end and start <= current <= end or start > end and (current >= start or current <= end)
    except Exception:
        return False


def _ensure_failed_email_queue(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS failed_email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            recipient_email TEXT,
            email_type TEXT,
            subject TEXT,
            html_body TEXT,
            text_body TEXT,
            metadata TEXT,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            last_error TEXT,
            next_retry_at TEXT,
            trace_id TEXT,
            idempotency_key TEXT,
            provider_status_code INTEGER,
            provider_message_id TEXT,
            processed_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_failed_email_queue_due ON failed_email_queue(status, next_retry_at, id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_failed_email_queue_idempotency ON failed_email_queue(idempotency_key)")


def _queue_email_job(user_id, to_email, subject, html_body, text_body="", email_type="transactional", metadata=None, notification_id=0):
    metadata = metadata or {}
    trace_id = _notification_trace_id(metadata)
    if not to_email:
        return {"ok": False, "status": "skipped", "provider": "brevo", "message": "Recipient email missing.", "trace_id": trace_id}
    now = _now()
    digest = hashlib.sha256(
        json.dumps(
            {
                "user_id": int(user_id or 0),
                "to_email": str(to_email or "").lower(),
                "email_type": str(email_type or "transactional"),
                "notification_id": int(notification_id or 0),
                "subject": str(subject or "")[:180],
                "event_type": metadata.get("event_type") or metadata.get("legacy_alert_type") or metadata.get("type") or "",
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:32]
    idempotency_key = str(metadata.get("email_idempotency_key") or f"notification-email:{digest}")[:240]
    conn = user_context.connect()
    cur = conn.cursor()
    _ensure_failed_email_queue(cur)
    cur.execute("SELECT id, status, trace_id FROM failed_email_queue WHERE idempotency_key=? ORDER BY id DESC LIMIT 1", (idempotency_key,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        existing_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
        existing_status = existing[1] if not hasattr(existing, "keys") else existing["status"]
        existing_trace = existing[2] if not hasattr(existing, "keys") else existing["trace_id"]
        return {"ok": True, "status": existing_status or "queued", "provider": "brevo", "queue_id": int(existing_id or 0), "trace_id": existing_trace or trace_id, "duplicate": True}
    next_retry_at = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO failed_email_queue
        (user_id, recipient_email, email_type, subject, html_body, text_body, metadata, status,
         retry_count, max_attempts, last_error, next_retry_at, trace_id, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, 5, '', ?, ?, ?, ?, ?)
        """,
        (
            int(user_id or 0),
            str(to_email or "")[:320],
            str(email_type or "transactional")[:80],
            str(subject or "")[:240],
            str(html_body or "")[:12000],
            str(text_body or "")[:4000],
            json.dumps({**metadata, "notification_id": int(notification_id or 0), "trace_id": trace_id}, default=str)[:4000],
            next_retry_at,
            trace_id,
            idempotency_key,
            now,
            now,
        ),
    )
    queue_id = int(getattr(cur, "lastrowid", 0) or 0)
    conn.commit()
    conn.close()
    logging.info("PULSE_EMAIL_JOB_QUEUED user_id=%s notification_id=%s queue_id=%s trace_id=%s", user_id, notification_id, queue_id, trace_id)
    schedule_email_queue_processing(reason="notification_email_queued")
    return {"ok": True, "status": "queued", "provider": "brevo", "queue_id": queue_id, "trace_id": trace_id}


def _email_retry_at(attempts):
    delay_seconds = min(3600, 30 * (2 ** max(0, int(attempts or 1) - 1)))
    return (datetime.utcnow() + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")


def process_queued_email_notifications(limit=10, provider_send=None):
    provider_send = provider_send or email_service.send_email
    limit = max(1, min(int(limit or 10), 50))
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    _ensure_failed_email_queue(cur)
    cur.execute(
        """
        SELECT *
        FROM failed_email_queue
        WHERE status IN ('pending','failed','retry_ready')
          AND COALESCE(retry_count,0) < COALESCE(max_attempts,5)
          AND (next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?)
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (now, limit),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    sent = retry = dead_letter = 0
    for row in rows:
        queue_id = int(row.get("id") or 0)
        attempts = int(row.get("retry_count") or 0) + 1
        claim_conn = user_context.connect()
        claim_cur = claim_conn.cursor()
        claim_cur.execute(
            "UPDATE failed_email_queue SET status='processing', retry_count=?, updated_at=? WHERE id=? AND status IN ('pending','failed','retry_ready')",
            (attempts, datetime.utcnow().isoformat(timespec="seconds"), queue_id),
        )
        claimed = int(getattr(claim_cur, "rowcount", 0) or 0) > 0
        claim_conn.commit()
        claim_conn.close()
        if not claimed:
            continue
        try:
            result = provider_send(
                row.get("recipient_email") or "",
                row.get("subject") or "PulseSoc notification",
                row.get("html_body") or row.get("text_body") or "",
                text_body=row.get("text_body") or "",
                email_type=row.get("email_type") or "transactional",
                user_id=row.get("user_id") or 0,
            ) or {}
        except Exception as exc:
            result = {"ok": False, "error": exc.__class__.__name__, "status_code": None, "response": {}}
        ok = bool(result.get("ok"))
        max_attempts = int(row.get("max_attempts") or 5)
        final_status = "sent" if ok else "dead_letter" if attempts >= max_attempts else "retry_ready"
        if ok:
            sent += 1
        elif final_status == "dead_letter":
            dead_letter += 1
        else:
            retry += 1
        response_body = result.get("response")
        response_message = response_body.get("message") if isinstance(response_body, dict) else response_body
        safe_error = "" if ok else str(result.get("error") or response_message or "Email provider unavailable.")[:1000]
        update_conn = user_context.connect()
        update_cur = update_conn.cursor()
        update_cur.execute(
            """
            UPDATE failed_email_queue
            SET status=?, last_error=?, next_retry_at=?, provider_status_code=?, provider_message_id=?,
                processed_at=?, updated_at=?
            WHERE id=?
            """,
            (
                final_status,
                safe_error,
                "" if final_status in {"sent", "dead_letter"} else _email_retry_at(attempts),
                result.get("status_code"),
                str(result.get("message_id") or "")[:240],
                datetime.utcnow().isoformat(timespec="seconds") if final_status in {"sent", "dead_letter"} else "",
                datetime.utcnow().isoformat(timespec="seconds"),
                queue_id,
            ),
        )
        update_conn.commit()
        update_conn.close()
        logging.info("PULSE_EMAIL_JOB_PROCESSED queue_id=%s trace_id=%s status=%s attempts=%s", queue_id, row.get("trace_id") or "", final_status, attempts)
    return {"ok": True, "attempted": len(rows), "sent": sent, "retry": retry, "dead_letter": dead_letter}


def schedule_email_queue_processing(reason="enqueue"):
    if os.getenv("EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return {"ok": True, "scheduled": False, "reason": "disabled"}
    if not EMAIL_QUEUE_PROCESSOR_LOCK.acquire(blocking=False):
        return {"ok": True, "scheduled": False, "reason": "already_running"}

    def run():
        try:
            result = process_queued_email_notifications(limit=int(os.getenv("EMAIL_OPPORTUNISTIC_PROCESSOR_LIMIT", "10") or 10))
            if result.get("attempted"):
                logging.info("PULSE_EMAIL_QUEUE_PROCESSOR_COMPLETE reason=%s result=%s", reason, result)
        except Exception as exc:
            logging.warning("PULSE_EMAIL_QUEUE_PROCESSOR_FAILED reason=%s error=%s", reason, exc.__class__.__name__)
        finally:
            try:
                EMAIL_QUEUE_PROCESSOR_LOCK.release()
            except RuntimeError:
                pass

    timer = threading.Timer(0.5, run)
    timer.daemon = True
    timer.name = "pulse-email-queue-processor"
    timer.start()
    return {"ok": True, "scheduled": True, "reason": reason}


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
        return f"pulse://pulse/messages-v2?conversation={conversation_id}"
    if status_id:
        if reply_id or comment_id:
            return f"pulse://status/{status_id}/reply/{reply_id or comment_id}"
        return f"pulse://status/{status_id}"
    if post_id:
        if comment_id:
            return f"pulse://post/{post_id}/comment/{comment_id}"
        return f"pulse://post/{post_id}"
    if note_type == "message" and item.get("entity_id"):
        return "pulse://pulse/messages-v2"
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
    metadata = dict(metadata or {})
    recipient_language = metadata.get("preferred_language") or metadata.get("language") or _preferred_language_for_user(user_id)
    metadata.setdefault("preferred_language", recipient_language)
    metadata.setdefault("language", recipient_language)
    existing_id = _recent_duplicate(user_id, note_type, entity_type, entity_id, deep_link, body)
    if existing_id:
        _log_pulse_delivery(existing_id, user_id, "in_app", "pulse", "duplicate_suppressed", {"dedupe": True}, "")
        logging.info(
            "PUSH_TRACE stage=notification_duplicate %s",
            json.dumps({"user_id": int(user_id or 0), "notification_id": int(existing_id or 0), "note_type": note_type, "entity_type": entity_type, "entity_id": entity_id, "push_trace_id": metadata.get("push_trace_id")}, default=str, sort_keys=True)[:1200],
        )
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
            str(title or "PulseSoc notification")[:180],
            str(body or "")[:2000],
            str(entity_type or "")[:80],
            str(entity_id or "")[:120],
            str(deep_link or "/pulse")[:700],
            str(deep_link or "/pulse")[:700],
            str(delivery_status or "created")[:60],
                json.dumps(metadata)[:4000],
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
    if not _message_like_notification(note_type, entity_type, deep_link):
        _dispatch_command_center_async(
            "enqueue_notification_event",
            int(user_id),
            str(note_type or "notification")[:80],
            str(title or "PulseSoc notification")[:180],
            str(body or "")[:2000],
            int(actor_user_id or 0) or None,
            {
                **metadata,
                "local_notification_id": int(notification_id or 0),
                "entity_type": str(entity_type or "")[:80],
                "entity_id": str(entity_id or "")[:120],
                "deep_link": str(deep_link or "/pulse")[:700],
            },
            "in_app",
            f"pulse-note-{int(notification_id or 0)}",
        )
    logging.info(
        "PUSH_TRACE stage=notification_created %s",
        json.dumps(
            {
                "user_id": int(user_id or 0),
                "actor_user_id": int(actor_user_id or 0),
                "notification_id": int(notification_id or 0),
                "note_type": note_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "deep_link": deep_link,
                "push_trace_id": metadata.get("push_trace_id"),
                "suppress_push": bool(metadata.get("suppress_push")),
            },
            default=str,
            sort_keys=True,
        )[:1600],
    )
    push_result = {"status": "skipped"}
    try:
        category = _pulse_category(note_type)
        defaults = PULSE_NOTIFICATION_CATEGORIES.get(category, PULSE_NOTIFICATION_CATEGORIES.get("messages", {}))
        suppress_push = bool((metadata or {}).get("suppress_push"))
        if suppress_push:
            push_result = {"ok": True, "status": "skipped", "message": "Push suppressed by notification policy."}
            _log_pulse_delivery(
                notification_id,
                user_id,
                "push",
                "web_expo_push",
                "skipped",
                push_result,
                push_result["message"],
            )
        elif defaults.get("push"):
            is_message_like = _message_like_notification(note_type, entity_type, deep_link)
            push_type = str(metadata.get("push_type") or ("chat_message" if is_message_like else metadata.get("type")) or category or note_type or "notification")[:80]
            notification_type = str(metadata.get("type") or note_type or push_type)[:80]
            payload_type = push_type if is_message_like or push_type in {"chat_message", "private_message", "group_message"} else notification_type
            try:
                badge_counts = pulse_badge_counts(user_id)
                badge_count = int(badge_counts.get("chat_unread_count") if is_message_like else badge_counts.get("alert_unread_count") or 0)
            except Exception:
                badge_count = int(metadata.get("badge") or 0)
            push_metadata = {
                **metadata,
                "url": metadata.get("url") or deep_link or "/pulse",
                "web_url": metadata.get("web_url") or (f"https://pulsesoc.com{deep_link}" if str(deep_link or "").startswith("/") else deep_link or "/pulse"),
                "deepLink": metadata.get("deepLink") or metadata.get("native_url") or metadata.get("mobile_deep_link") or metadata.get("deep_link") or deep_link or "/pulse",
                "type": payload_type,
                "notification_type": notification_type,
                "push_type": push_type,
                "category": metadata.get("category") or category,
                "sound": metadata.get("sound") or "default",
                "priority": metadata.get("priority") or ("high" if category in {"security", "system_security", "admin_security", "crypto", "live", "marketplace"} else "normal"),
                "badge": int(metadata.get("badge") if metadata.get("badge") is not None else badge_count),
                "vibration": metadata.get("vibration") or [200, 100, 200],
                "channel_id": metadata.get("channel_id") or metadata.get("channelId") or ("pulse-messages-v2" if is_message_like else "default"),
                "channelId": metadata.get("channelId") or metadata.get("channel_id") or ("pulse-messages-v2" if is_message_like else "default"),
                "notification_id": int(notification_id),
            }
            push_result = send_push_alert(
                int(user_id),
                str(title or "PulseSoc notification"),
                str(body or ""),
                push_metadata,
            )
            _log_pulse_delivery(
                notification_id,
                user_id,
                "push",
                "web_expo_push",
                push_result.get("status") or ("sent" if push_result.get("ok") else "failed"),
                push_result,
                push_result.get("message") or "; ".join(push_result.get("failures") or []),
            )
    except Exception as exc:
        logging.warning("PULSE_PUSH_DELIVERY_FAILED notification_id=%s user_id=%s error=%s", notification_id, user_id, type(exc).__name__)
    logging.info(
        "PUSH_TRACE stage=notification_push_result %s",
        json.dumps(
            {
                "user_id": int(user_id or 0),
                "notification_id": int(notification_id or 0),
                "note_type": note_type,
                "push_trace_id": metadata.get("push_trace_id") or push_result.get("trace_id"),
                "push_status": push_result.get("status"),
                "push_ok": bool(push_result.get("ok")),
                "sent": push_result.get("sent"),
                "invalidated": push_result.get("invalidated"),
            },
            default=str,
            sort_keys=True,
        )[:1600],
    )
    return {"ok": True, "notification_id": notification_id, "push": push_result}


def list_pulse_notifications(user_id, limit=50, category="all", unread_only=False):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    clauses = ["user_id=?"]
    params = [int(user_id)]
    if unread_only:
        clauses.append("(is_read=0 OR read_at IS NULL)")
    normalized_category = str(category or "all").lower()
    category_types = {
        "priority": [
            "security_alert", "account_login", "new_device", "password_changed", "email_changed", "phone_changed",
            "suspicious_login", "account_locked", "crypto_price_alert", "crypto_alert_triggered", "crypto_security_alert",
            "cohost_request", "cohost_request_received", "payment_failed", "admin_security_event",
        ],
        "social": [
            "like", "reaction", "comment", "reply", "save", "share", "repost", "mention", "tag", "status_mention",
            "follow", "follow_request", "follow_accept", "status_view", "status_reaction", "status_reply", "reel_like",
            "reel_comment", "reel_mention", "reel_share", "video_like", "video_comment", "video_mention", "video_share",
            "video_save",
        ],
        "live": [
            "live_started", "live_reminder", "live_invite", "live_ended", "live_ended_summary", "live_replay_ready",
            "live_highlight_ready", "replay_available", "cohost_request", "cohost_request_received", "cohost_accepted",
            "cohost_denied", "guest_removed",
        ],
        "crypto": [
            "crypto_price_alert", "crypto_alert_triggered", "crypto_percentage_alert", "crypto_volatility_alert",
            "crypto_security_alert", "scam_security_alert", "scam_alert",
        ],
        "security": [
            "security_alert", "account_login", "new_device", "password_changed", "email_changed", "phone_changed",
            "suspicious_login", "account_locked", "two_factor_enabled", "two_factor_disabled", "admin_security_event",
        ],
        "marketplace": [
            "marketplace_update", "marketplace_order", "teacher_order", "order_accepted", "order_shipped",
            "order_delivered", "refund_issued", "payment_received", "purchase", "payment_succeeded", "payment_failed",
            "premium_alert", "founder_premium_activated", "subscription_renewed", "subscription_canceled",
        ],
        "system": [
            "system", "system_update", "system_announcement", "system_security", "security_alert", "admin_security_event",
            "ai_alert", "ai_update", "account", "user_signup", "email_verification", "phone_verification",
        ],
    }.get(normalized_category)
    if normalized_category in {"messages", "message", "chat"}:
        clauses.append(f"({_message_notification_where_clause()})")
        params.extend(_message_notification_params())
    elif category_types:
        clauses.append("type IN (%s)" % ",".join("?" for _ in category_types))
        params.extend(category_types)
    else:
        clauses.append(f"NOT ({_message_notification_where_clause()})")
        params.extend(_message_notification_params())
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


def get_pulse_notification(user_id, notification_id):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_notifications WHERE id=? AND user_id=? LIMIT 1",
        (int(notification_id or 0), int(user_id)),
    )
    row = cur.fetchone()
    item = _pulse_row(row) if row else None
    conn.close()
    return item


def _message_notification_where_clause():
    type_placeholders = ",".join("?" for _ in MESSAGE_NOTIFICATION_TYPES)
    return (
        f"LOWER(COALESCE(type,'')) IN ({type_placeholders}) "
        "OR LOWER(COALESCE(entity_type,'')) IN ('message','messages','chat','conversation','pulse_message','pulse_conversation','comm_v2_message') "
        "OR LOWER(COALESCE(deep_link,'')) LIKE '/pulse/messages%' "
        "OR LOWER(COALESCE(deep_link,'')) LIKE '/messages%' "
        "OR LOWER(COALESCE(deep_link,'')) LIKE '/chat%' "
        "OR LOWER(COALESCE(target_url,'')) LIKE '/pulse/messages%' "
        "OR LOWER(COALESCE(target_url,'')) LIKE '/messages%' "
        "OR LOWER(COALESCE(target_url,'')) LIKE '/chat%'"
    )


def _message_notification_params():
    return sorted(MESSAGE_NOTIFICATION_TYPES)


def _table_exists(cur, table_name):
    table_name = str(table_name or "")
    if db_service.ENGINE_NAME == "postgresql":
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=? LIMIT 1",
            (table_name,),
        )
    else:
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,))
    return bool(cur.fetchone())


def pulse_badge_counts(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    params = [int(user_id), *_message_notification_params()]
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM pulse_notifications
        WHERE user_id=?
          AND (is_read=0 OR read_at IS NULL)
          AND NOT ({_message_notification_where_clause()})
        """,
        tuple(params),
    )
    alert_count = int(cur.fetchone()[0] or 0)

    chat_count = 0
    if _table_exists(cur, "pulse_conversation_participants"):
        cur.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN COALESCE(unread_count,0) > 0 THEN unread_count ELSE 0 END),0)
            FROM pulse_conversation_participants
            WHERE user_id=? AND COALESCE(left_at,'')=''
            """,
            (int(user_id),),
        )
        chat_count += int(cur.fetchone()[0] or 0)
    if _table_exists(cur, "comm_v2_participants"):
        cur.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN COALESCE(unread_count,0) > 0 THEN unread_count ELSE 0 END),0)
            FROM comm_v2_participants
            WHERE user_id=? AND COALESCE(membership_state,'active')='active' AND COALESCE(left_at,'')=''
            """,
            (int(user_id),),
        )
        chat_count += int(cur.fetchone()[0] or 0)
    if _table_exists(cur, "conversations") and _table_exists(cur, "conversation_members") and _table_exists(cur, "private_messages"):
        cur.execute(
            """
            SELECT COUNT(*)
            FROM private_messages pm
            JOIN conversation_members cm ON cm.conversation_id=pm.conversation_id AND cm.user_id=?
            WHERE pm.sender_user_id != ?
              AND pm.deleted_at IS NULL
              AND pm.created_at > COALESCE(cm.last_read_at, '')
            """,
            (int(user_id), int(user_id)),
        )
        chat_count += int(cur.fetchone()[0] or 0)
    conn.close()
    return {
        "ok": True,
        "alert_unread_count": alert_count,
        "chat_unread_count": chat_count,
        "total_unread_count": alert_count + chat_count,
        "count": alert_count,
        "unread_count": alert_count,
    }


def pulse_unread_count(user_id):
    return pulse_badge_counts(user_id)


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
    if changed:
        _dispatch_command_center_async("mark_notification_read", int(user_id), f"pulse-note-{int(notification_id or 0)}", False)
    counts = pulse_badge_counts(user_id)
    return {"ok": True, "updated": changed, "badge_counts": counts, **counts}


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
    if changed:
        _dispatch_command_center_async("mark_notification_read", int(user_id), "", True)
    counts = pulse_badge_counts(user_id)
    return {"ok": True, "updated": changed, "badge_counts": counts, **counts}


def delete_pulse_notification(user_id, notification_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_notifications WHERE id=? AND user_id=?", (int(notification_id or 0), int(user_id)))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    counts = pulse_badge_counts(user_id)
    return {"ok": True, "deleted": changed, "badge_counts": counts, **counts}


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
    logging.info(
        "PUSH_TRACE stage=device_registered %s",
        json.dumps(
            {
                "user_id": int(user_id or 0),
                "provider": provider,
                "device_type": device_type,
                "endpoint_hash": push_service._endpoint_hash(endpoint),
                "subscription_ok": bool(result.get("ok")),
                "subscription_status": result.get("status") or ("ok" if result.get("ok") else "failed"),
            },
            default=str,
            sort_keys=True,
        )[:1200],
    )
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
    result = _queue_email_job(
        user_id,
        user.get("email"),
        subject,
        _branded_html(headline, copy, deep_link),
        copy,
        email_type=notification_type,
        metadata={**(metadata or {}), "event_type": notification_type, "category": category, "deep_link": deep_link},
        notification_id=notification_id,
    )
    status = result.get("status") or "queued"
    _log_delivery(user_id, notification_id, "email", status, result, result.get("message"))
    _log_pulse_delivery(notification_id, user_id, "email", "brevo", status, result, result.get("message"))
    return {"ok": result.get("ok"), "status": status, "provider": "brevo", "queue_id": result.get("queue_id") or 0, "trace_id": result.get("trace_id") or ""}


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
    normalized_channels = _normalize_channels(channels)
    requested = set(normalized_channels)
    security = _pulse_category(_pulse_type_for_alert(notification_type)) == "security" or notification_type in SECURITY_NOTIFICATION_TYPES
    priority = str(metadata.get("priority") or "normal").lower()
    quiet = _quiet_hours_active(user_id) and not security and priority not in {"high", "critical", "urgent"}
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
        if quiet:
            result["email"] = "skipped"
            _log_delivery(user_id, notification_id, "email", "skipped", "", "Quiet hours are active.")
            _log_pulse_delivery(notification_id, user_id, "email", "brevo", "skipped", {}, "Quiet hours are active.")
        elif prefs.get("email") or security or "email" in normalized_channels:
            email_result = send_email_notification(user_id, title, body, notification_type, metadata, notification_id)
            result["email"] = email_result.get("status", "failed")
        else:
            result["email"] = "skipped"
            _log_delivery(user_id, notification_id, "email", "skipped", "", "Email alerts disabled.")
            _log_pulse_delivery(notification_id, user_id, "email", "brevo", "skipped", {}, "Email alerts disabled.")
    if "sms" in requested:
        if quiet:
            result["sms"] = "skipped"
            _log_delivery(user_id, notification_id, "sms", "skipped", "", "Quiet hours are active.")
            _log_pulse_delivery(notification_id, user_id, "sms", "brevo_sms", "skipped", {}, "Quiet hours are active.")
        elif prefs.get("sms") or "sms" in normalized_channels:
            sms_result = send_sms_notification(user_id, title, body, notification_type, metadata, notification_id)
            result["sms"] = sms_result.get("status", "failed")
        else:
            result["sms"] = "skipped"
            _log_delivery(user_id, notification_id, "sms", "skipped", "", "SMS alerts disabled.")
            _log_pulse_delivery(notification_id, user_id, "sms", "brevo_sms", "skipped", {}, "SMS alerts disabled.")
    if "push" in requested:
        if quiet:
            result["push"] = "skipped"
            _log_delivery(user_id, notification_id, "push", "skipped", "", "Quiet hours are active.")
            _log_pulse_delivery(notification_id, user_id, "push", "web_push", "skipped", {}, "Quiet hours are active.")
        else:
            category = _pulse_category(_pulse_type_for_alert(notification_type))
            try:
                badge_count = int((pulse_badge_counts(user_id) or {}).get("alert_unread_count") or 0)
            except Exception:
                badge_count = int(metadata.get("badge") or 0)
            push_metadata = {
                **metadata,
                "notification_id": int(notification_id or metadata.get("notification_id") or 0),
                "category": metadata.get("category") or category,
                "type": metadata.get("type") or notification_type,
                "notification_type": notification_type,
                "sound": metadata.get("sound") or "default",
                "priority": metadata.get("priority") or ("high" if category in {"security", "system_security", "admin_security", "crypto", "live", "marketplace"} else "normal"),
                "badge": int(metadata.get("badge") if metadata.get("badge") is not None else badge_count),
                "vibration": metadata.get("vibration") or [200, 100, 200],
            }
            push_result = send_push_alert(user_id, title, body, push_metadata)
            result["push"] = push_result.get("status", "failed")
            _log_delivery(user_id, notification_id, "push", result["push"], push_result, push_result.get("message"))
            _log_pulse_delivery(notification_id, user_id, "push", "web_push", result["push"], push_result, push_result.get("message"))
    if "telegram" in requested:
        telegram_result = send_telegram_alert(_user_record(user_id), title, body)
        result["telegram"] = telegram_result.get("status", "failed")
        _log_delivery(user_id, notification_id, "telegram", result["telegram"], telegram_result, telegram_result.get("message"))
    return result


def dispatch_universal_notification(
    event_type,
    actor_user_id=0,
    recipient_user_id=0,
    content_id="",
    deep_link="",
    priority="normal",
    channels=None,
    metadata=None,
):
    """Central PulseSoc event-to-channel notification contract.

    The caller supplies only event facts. This service resolves category,
    defaults, deep links, preferences, duplicate suppression, and delivery logs.
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    recipient_user_id = int(recipient_user_id or 0)
    actor_user_id = int(actor_user_id or 0)
    if not recipient_user_id:
        return {"ok": False, "status": "skipped", "error_code": "RECIPIENT_REQUIRED"}
    spec = _event_spec(event_type)
    notification_type = str(spec.get("type") or event_type or "message")[:80]
    if actor_user_id and actor_user_id == recipient_user_id and notification_type not in SELF_NOTIFICATION_ALLOWED_TYPES:
        _log_delivery(recipient_user_id, None, "orchestrator", "skipped", {"event_type": event_type}, "Self notification suppressed.")
        return {"ok": True, "status": "skipped", "reason": "self_notification_suppressed"}
    trace_id = _notification_trace_id(metadata)
    resolved_priority = str(priority or spec.get("priority") or "normal").lower()[:40]
    resolved_deep_link = _deep_link_for_event(event_type, content_id, deep_link, metadata)
    mobile_deep_link = _mobile_link_for_event(event_type, resolved_deep_link, content_id, metadata)
    explicit_channels = _normalize_channels(channels)
    default_channels = _normalize_channels(spec.get("channels") or [])
    title = str(metadata.get("title") or spec.get("title") or "PulseSoc notification")[:180]
    body = str(metadata.get("body") or metadata.get("message") or spec.get("body") or "There is new PulseSoc activity.")[:2000]
    enriched = {
        **metadata,
        "event_type": str(event_type or "")[:80],
        "notification_type": notification_type,
        "actor_user_id": actor_user_id,
        "recipient_user_id": recipient_user_id,
        "content_id": str(content_id or metadata.get("content_id") or metadata.get("entity_id") or "")[:120],
        "entity_type": metadata.get("entity_type") or spec.get("entity_type") or "",
        "entity_id": str(metadata.get("entity_id") or content_id or "")[:120],
        "priority": resolved_priority,
        "trace_id": trace_id,
        "push_trace_id": metadata.get("push_trace_id") or trace_id,
        "deep_link": resolved_deep_link,
        "target_url": resolved_deep_link,
        "url": resolved_deep_link,
        "web_url": metadata.get("web_url") or (f"https://pulsesoc.com{resolved_deep_link}" if resolved_deep_link.startswith("/") else resolved_deep_link),
        "mobile_deep_link": mobile_deep_link,
        "native_url": mobile_deep_link,
        "app_url": mobile_deep_link,
        "deepLink": mobile_deep_link,
        "category": _pulse_category(notification_type),
        "sound": metadata.get("sound") or "default",
        "vibration": metadata.get("vibration") or [200, 100, 200],
        "badge": metadata.get("badge"),
        "channels": explicit_channels or default_channels,
        "default_channels": default_channels,
    }
    result = send_multi_channel_notification(
        recipient_user_id,
        notification_type,
        title,
        body,
        enriched,
        channels=explicit_channels or None,
    )
    result.update({
        "event_type": str(event_type or "")[:80],
        "notification_type": notification_type,
        "recipient_user_id": recipient_user_id,
        "actor_user_id": actor_user_id,
        "trace_id": trace_id,
        "priority": resolved_priority,
        "deep_link": resolved_deep_link,
        "mobile_deep_link": mobile_deep_link,
        "channels": explicit_channels or default_channels,
        "explicit_channels": explicit_channels,
        "universal": True,
    })
    return result


def send_universal_notification(**kwargs):
    return dispatch_universal_notification(**kwargs)


class NotificationService:
    @staticmethod
    def send(user_id, notification_type, title, body, metadata=None, channels=None):
        return send_multi_channel_notification(user_id, notification_type, title, body, metadata, channels)

    @staticmethod
    def dispatch_event(event_type, actor_user_id=0, recipient_user_id=0, content_id="", deep_link="", priority="normal", channels=None, metadata=None):
        return dispatch_universal_notification(
            event_type,
            actor_user_id=actor_user_id,
            recipient_user_id=recipient_user_id,
            content_id=content_id,
            deep_link=deep_link,
            priority=priority,
            channels=channels,
            metadata=metadata,
        )


sendEmailNotification = send_email_notification
sendSmsNotification = send_sms_notification
sendInAppNotification = send_in_app_channel_notification
sendMultiChannelNotification = send_multi_channel_notification
sendUniversalNotification = dispatch_universal_notification


def send_push_alert(user_id, title, message, metadata=None):
    metadata = metadata or {}
    push_type = (metadata or {}).get("push_type") or (metadata or {}).get("type") or "general"
    if push_service._async_push_enabled():
        return push_service.enqueue_push(
            user_id,
            title,
            message,
            metadata,
            push_type=push_type,
            notification_id=int(metadata.get("notification_id") or 0),
        )
    return push_service.send_push(user_id, title, message, metadata, push_type=push_type)


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
        cur.execute("UPDATE push_subscriptions SET active=0, is_active=0, updated_at=? WHERE user_id=? AND endpoint=?", (_now(), user_id, endpoint))
        subscription_changed = cur.rowcount
        cur.execute("UPDATE pulse_notification_devices SET active=0, updated_at=? WHERE user_id=? AND endpoint=?", (_now(), user_id, endpoint))
        device_changed = cur.rowcount
        try:
            cur.execute("UPDATE user_device_tokens SET enabled=0, revoked_at=?, updated_at=? WHERE user_id=? AND push_token=?", (_now(), _now(), user_id, endpoint))
        except Exception:
            pass
    else:
        cur.execute("UPDATE push_subscriptions SET active=0, is_active=0, updated_at=? WHERE user_id=?", (_now(), user_id))
        subscription_changed = cur.rowcount
        cur.execute("UPDATE pulse_notification_devices SET active=0, updated_at=? WHERE user_id=?", (_now(), user_id))
        device_changed = cur.rowcount
        try:
            cur.execute("UPDATE user_device_tokens SET enabled=0, revoked_at=?, updated_at=? WHERE user_id=?", (_now(), _now(), user_id))
        except Exception:
            pass
    conn.commit()
    conn.close()
    logging.info(
        "PUSH_TRACE stage=device_unsubscribed %s",
        json.dumps(
            {
                "user_id": int(user_id or 0),
                "endpoint_hash": push_service._endpoint_hash(endpoint),
                "subscription_updated": int(subscription_changed or 0),
                "device_updated": int(device_changed or 0),
                "all_devices": not bool(endpoint),
            },
            default=str,
            sort_keys=True,
        )[:1200],
    )
    return {"ok": True, "updated": subscription_changed, "devices_updated": device_changed}


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
