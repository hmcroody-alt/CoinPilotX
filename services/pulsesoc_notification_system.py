"""PulseSoc notification operating-system foundation and delivery adapters.

Phase 1 normalized events and created server-authoritative notification records.
Phase 2 keeps that intake model and adds real, queue-safe adapter routing for
Web Push/PWA, Brevo email/SMS, and honest APNs/FCM readiness states.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from typing import Any

import requests

from services import db as db_service
from services import email_service, push_service, sms_service


PRIORITY_LEVELS = {"urgent", "high", "normal", "low"}
URGENCY_LEVELS = {"immediate", "standard", "deferred", "silent"}
DELIVERY_CHANNELS = {"in_app", "push", "email", "sms", "call", "system"}
PROVIDER_PLACEHOLDERS = {
    "push": "pulse_push_router",
    "email": "brevo_email",
    "sms": "brevo_sms",
    "call": "callkit_android_call_phase2",
    "system": "internal",
    "in_app": "pulse_in_app",
}
ADAPTER_CHANNEL_ALIASES = {
    "web_push": "push",
    "pwa_push": "push",
    "fcm": "push",
    "apns": "push",
    "brevo_email": "email",
    "brevo_sms": "sms",
}
NOISY_CHANNELS = {"push", "email", "sms", "call"}
SOCIAL_CATEGORIES = {"social", "messages", "comments", "mentions", "follows", "live"}
SENSITIVE_CATEGORIES = {"security", "payments", "billing"}
LOCKED_DEVICE_PUSH_DEFAULT_CATEGORIES = {
    "messages",
    "calls",
    "comments",
    "mentions",
    "follows",
    "live",
    "security",
    "payments",
    "billing",
    "verification",
    "marketplace",
    "creator",
    "premium",
    "crypto",
    "intelligence",
    "system",
}
PREFERENCE_CONTROLLED_PUSH_CATEGORIES = {"social", "likes", "reposts", "suggestions", "digest", "marketing"}
EMAIL_DEFAULT_CATEGORIES = {"security", "payments", "billing", "verification", "marketplace", "creator", "premium"}
SMS_DEFAULT_CATEGORIES = {"security", "payments", "billing", "crypto", "system"}
TEMPORARY_FAILURE_STATUSES = {"failed", "timeout", "rate_limited", "provider_error", "temporary_failure"}
PERMANENT_SKIP_STATUSES = {
    "sent",
    "ready",
    "config_missing",
    "skipped",
    "skipped_by_preference",
    "skipped_no_device",
    "skipped_no_contact",
    "skipped_policy",
    "invalid_device",
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DELIVERY_LOCK = threading.Lock()


EVENT_DEFINITIONS: dict[str, dict[str, str]] = {
    "new_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "New message"},
    "group_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "New group message"},
    "image_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "Photo message"},
    "video_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "Video message"},
    "voice_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "Voice message"},
    "file_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "File message"},
    "missed_call": {"category": "calls", "priority": "urgent", "urgency": "immediate", "title": "Missed call"},
    "incoming_call": {"category": "calls", "priority": "urgent", "urgency": "immediate", "title": "Incoming call"},
    "friend_request": {"category": "follows", "priority": "normal", "urgency": "standard", "title": "New friend request"},
    "friend_request_accepted": {"category": "follows", "priority": "normal", "urgency": "standard", "title": "Friend request accepted"},
    "follow": {"category": "follows", "priority": "normal", "urgency": "standard", "title": "New follower"},
    "like": {"category": "likes", "priority": "normal", "urgency": "silent", "title": "New like"},
    "comment": {"category": "comments", "priority": "normal", "urgency": "standard", "title": "New comment"},
    "reply": {"category": "comments", "priority": "normal", "urgency": "standard", "title": "New reply"},
    "mention": {"category": "mentions", "priority": "high", "urgency": "immediate", "title": "You were mentioned"},
    "tag": {"category": "mentions", "priority": "high", "urgency": "immediate", "title": "You were tagged"},
    "repost": {"category": "reposts", "priority": "normal", "urgency": "standard", "title": "New repost"},
    "quote": {"category": "reposts", "priority": "normal", "urgency": "standard", "title": "New quote"},
    "live_started": {"category": "live", "priority": "high", "urgency": "immediate", "title": "Live started"},
    "live_invite": {"category": "live", "priority": "high", "urgency": "immediate", "title": "Live invite"},
    "cohost_request": {"category": "live", "priority": "high", "urgency": "immediate", "title": "Co-host request"},
    "live_ended": {"category": "live", "priority": "normal", "urgency": "standard", "title": "Live ended"},
    "creator_payout": {"category": "creator", "priority": "high", "urgency": "standard", "title": "Creator payout update"},
    "creator_payout_failed": {"category": "creator", "priority": "high", "urgency": "standard", "title": "Creator payout issue"},
    "verification_approved": {"category": "verification", "priority": "high", "urgency": "standard", "title": "Verification approved"},
    "verification_rejected": {"category": "verification", "priority": "high", "urgency": "standard", "title": "Verification update"},
    "verification_needs_info": {"category": "verification", "priority": "high", "urgency": "standard", "title": "Verification needs info"},
    "subscription_renewal": {"category": "premium", "priority": "normal", "urgency": "standard", "title": "Subscription renewed"},
    "subscription_canceled": {"category": "premium", "priority": "high", "urgency": "standard", "title": "Subscription canceled"},
    "payment_method_issue": {"category": "payments", "priority": "high", "urgency": "immediate", "title": "Payment method issue"},
    "payment_failed": {"category": "payments", "priority": "urgent", "urgency": "immediate", "title": "Payment failed"},
    "founder_premium_activated": {"category": "premium", "priority": "high", "urgency": "standard", "title": "Founder Premium activated"},
    "security_login_alert": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Security login alert"},
    "new_device_login": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "New device login"},
    "password_changed": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Password changed"},
    "email_changed": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Email changed"},
    "phone_changed": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Phone changed"},
    "suspicious_login": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Suspicious login"},
    "crypto_price_alert": {"category": "crypto", "priority": "high", "urgency": "immediate", "title": "Crypto price alert"},
    "crypto_alert_triggered": {"category": "crypto", "priority": "high", "urgency": "immediate", "title": "Crypto alert"},
    "intelligence_pulse": {"category": "intelligence", "priority": "normal", "urgency": "standard", "title": "Intelligence Pulse"},
    "intelligence_forecast": {"category": "intelligence", "priority": "normal", "urgency": "standard", "title": "Intelligence Forecast"},
    "intelligence_digest": {"category": "intelligence", "priority": "low", "urgency": "deferred", "title": "Intelligence Digest"},
    "marketplace_order": {"category": "marketplace", "priority": "high", "urgency": "standard", "title": "Marketplace order"},
    "admin_warning": {"category": "system", "priority": "high", "urgency": "immediate", "title": "Account notice"},
    "account_restriction": {"category": "system", "priority": "urgent", "urgency": "immediate", "title": "Account restriction"},
    "content_removed": {"category": "system", "priority": "high", "urgency": "standard", "title": "Content removed"},
    "system_announcement": {"category": "system", "priority": "normal", "urgency": "standard", "title": "PulseSoc announcement"},
}

DEFAULT_CATEGORIES = sorted({definition["category"] for definition in EVENT_DEFINITIONS.values()} | {
    "admin_security",
    "chat_message",
    "comments",
    "crypto",
    "group_message",
    "intelligence",
    "live",
    "live_invite",
    "market",
    "marketing",
    "marketplace",
    "marketplace_order",
    "payments",
    "premium",
    "purchase",
    "reaction",
    "reply",
    "roast_battle",
    "room_message",
    "status",
    "system",
    "messages",
    "calls",
    "security",
    "social",
    "likes",
    "reposts",
    "follows",
    "crypto",
    "marketplace",
    "premium",
})


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier or ""):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return identifier


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _json_loads(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return fallback if fallback is not None else {}
    try:
        return json.loads(str(value))
    except Exception:
        return fallback if fallback is not None else {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, default=str, sort_keys=True, separators=(",", ":"))[:10000]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _table_exists(cur: Any, table: str) -> bool:
    table = _safe_identifier(table)
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=? LIMIT 1",
                (table,),
            )
        else:
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _columns(cur: Any, table: str) -> set[str]:
    table = _safe_identifier(table)
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
                (table,),
            )
            return {str(_row_get(row, "column_name", row[0] if row else "")).lower() for row in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {str(_row_get(row, "name", "")).lower() for row in cur.fetchall()}
    except Exception:
        return set()


def _add_column_if_missing(cur: Any, table: str, column: str, definition: str) -> None:
    table = _safe_identifier(table)
    column = _safe_identifier(column)
    if column.lower() in _columns(cur, table):
        return
    if db_service.IS_POSTGRES:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    else:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_schema(conn: Any | None = None) -> None:
    owns_conn = conn is None
    if conn is None:
        conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            notification_type TEXT,
            title TEXT,
            message TEXT,
            status TEXT DEFAULT 'unread',
            metadata TEXT,
            created_at TEXT,
            read_at TEXT,
            recipient_user_id INTEGER,
            actor_user_id INTEGER,
            type TEXT,
            category TEXT,
            priority TEXT DEFAULT 'normal',
            urgency TEXT DEFAULT 'standard',
            body TEXT,
            preview TEXT,
            deep_link TEXT,
            source_type TEXT,
            source_id TEXT,
            icon_url TEXT,
            avatar_url TEXT,
            metadata_json TEXT,
            sound_key TEXT,
            vibration_json TEXT,
            seen_at TEXT,
            delivered_at TEXT,
            opened_at TEXT,
            failed_at TEXT,
            failure_reason TEXT,
            updated_at TEXT,
            deleted_at TEXT,
            dedupe_key TEXT,
            event_id INTEGER,
            delivery_status TEXT DEFAULT 'created'
        )
        """
    )
    for column, definition in [
        ("recipient_user_id", "INTEGER"),
        ("actor_user_id", "INTEGER"),
        ("type", "TEXT"),
        ("category", "TEXT"),
        ("priority", "TEXT DEFAULT 'normal'"),
        ("urgency", "TEXT DEFAULT 'standard'"),
        ("body", "TEXT"),
        ("preview", "TEXT"),
        ("deep_link", "TEXT"),
        ("source_type", "TEXT"),
        ("source_id", "TEXT"),
        ("icon_url", "TEXT"),
        ("avatar_url", "TEXT"),
        ("metadata_json", "TEXT"),
        ("sound_key", "TEXT"),
        ("vibration_json", "TEXT"),
        ("seen_at", "TEXT"),
        ("delivered_at", "TEXT"),
        ("opened_at", "TEXT"),
        ("failed_at", "TEXT"),
        ("failure_reason", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
        ("dedupe_key", "TEXT"),
        ("event_id", "INTEGER"),
        ("delivery_status", "TEXT DEFAULT 'created'"),
    ]:
        _add_column_if_missing(cur, "notifications", column, definition)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE,
            event_type TEXT,
            recipient_user_id INTEGER,
            actor_user_id INTEGER,
            source_type TEXT,
            source_id TEXT,
            payload_json TEXT,
            status TEXT DEFAULT 'accepted',
            suppression_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_delivery_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id INTEGER,
            user_id INTEGER,
            recipient_user_id INTEGER,
            channel TEXT,
            provider TEXT,
            status TEXT DEFAULT 'queued',
            dedupe_key TEXT UNIQUE,
            retry_count INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            scheduled_at TEXT,
            next_retry_at TEXT,
            failed_reason TEXT,
            failure_reason TEXT,
            attempted_at TEXT,
            failed_at TEXT,
            provider_response_json TEXT,
            provider_message_id TEXT,
            payload_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            sent_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            device_id TEXT,
            platform TEXT,
            push_token TEXT,
            endpoint TEXT,
            p256dh TEXT,
            auth TEXT,
            user_agent TEXT,
            app_version TEXT,
            push_provider TEXT,
            environment TEXT,
            enabled INTEGER DEFAULT 1,
            token_hash TEXT,
            last_seen_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT,
            UNIQUE(user_id, device_id, platform)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT,
            in_app INTEGER DEFAULT 1,
            push INTEGER DEFAULT 1,
            email INTEGER DEFAULT 1,
            telegram INTEGER DEFAULT 0,
            sms INTEGER DEFAULT 1,
            sound INTEGER DEFAULT 1,
            vibration INTEGER DEFAULT 1,
            lock_screen_preview INTEGER DEFAULT 1,
            quiet_hours_enabled INTEGER DEFAULT 0,
            quiet_hours_start TEXT DEFAULT '22:00',
            quiet_hours_end TEXT DEFAULT '07:00',
            muted_users_json TEXT,
            muted_conversations_json TEXT,
            blocked_users_json TEXT,
            category_rules_json TEXT,
            enable_push_notifications INTEGER DEFAULT 1,
            enable_notification_sound INTEGER DEFAULT 1,
            enable_notification_vibration INTEGER DEFAULT 1,
            notification_sound_type TEXT DEFAULT 'soft',
            updated_at TEXT,
            UNIQUE(user_id, category)
        )
        """
    )
    for column, definition in [
        ("failure_reason", "TEXT"),
        ("attempted_at", "TEXT"),
        ("failed_at", "TEXT"),
        ("provider_response_json", "TEXT"),
    ]:
        _add_column_if_missing(cur, "notification_delivery_jobs", column, definition)
    for column, definition in [
        ("sound_key", "TEXT"),
        ("vibration_json", "TEXT"),
    ]:
        _add_column_if_missing(cur, "notifications", column, definition)
    for column, definition in [
        ("push_provider", "TEXT"),
        ("environment", "TEXT"),
    ]:
        _add_column_if_missing(cur, "notification_device_tokens", column, definition)
    for column, definition in [
        ("sms", "INTEGER DEFAULT 0"),
        ("sound", "INTEGER DEFAULT 1"),
        ("vibration", "INTEGER DEFAULT 1"),
        ("lock_screen_preview", "INTEGER DEFAULT 1"),
        ("quiet_hours_enabled", "INTEGER DEFAULT 0"),
        ("quiet_hours_start", "TEXT DEFAULT '22:00'"),
        ("quiet_hours_end", "TEXT DEFAULT '07:00'"),
        ("muted_users_json", "TEXT"),
        ("muted_conversations_json", "TEXT"),
        ("blocked_users_json", "TEXT"),
        ("category_rules_json", "TEXT"),
        ("enable_push_notifications", "INTEGER DEFAULT 0"),
        ("enable_notification_sound", "INTEGER DEFAULT 1"),
        ("enable_notification_vibration", "INTEGER DEFAULT 1"),
        ("notification_sound_type", "TEXT DEFAULT 'soft'"),
    ]:
        _add_column_if_missing(cur, "notification_preferences", column, definition)
    if _table_exists(cur, "pulse_notifications"):
        _add_column_if_missing(cur, "pulse_notifications", "metadata_json", "TEXT")
    for statement in [
        "CREATE INDEX IF NOT EXISTS idx_notifications_recipient_read_created ON notifications(recipient_user_id, read_at, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_user_status_created ON notifications(user_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_category_priority ON notifications(category, priority, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key)",
        "CREATE INDEX IF NOT EXISTS idx_notification_events_recipient_type ON notification_events(recipient_user_id, event_type, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_status ON notification_delivery_jobs(status, scheduled_at, next_retry_at)",
        "CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_notification ON notification_delivery_jobs(notification_id, channel)",
        "CREATE INDEX IF NOT EXISTS idx_notification_device_tokens_user_enabled ON notification_device_tokens(user_id, enabled, last_seen_at)",
        "CREATE INDEX IF NOT EXISTS idx_notification_device_tokens_hash ON notification_device_tokens(token_hash)",
    ]:
        cur.execute(statement)
    conn.commit()
    if owns_conn:
        conn.close()


def _definition(event_type: str) -> dict[str, str]:
    normalized = normalize_type(event_type)
    return EVENT_DEFINITIONS.get(normalized, {"category": "system", "priority": "normal", "urgency": "standard", "title": "PulseSoc update"})


def normalize_type(event_type: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(event_type or "system_announcement").strip().lower()).strip("_")
    return normalized or "system_announcement"


def normalize_category(category: str | None, event_type: str) -> str:
    raw = str(category or _definition(event_type)["category"] or "system").strip().lower().replace("-", "_")
    return re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")[:80] or "system"


def normalize_priority(priority: str | None, event_type: str) -> str:
    value = str(priority or _definition(event_type)["priority"] or "normal").strip().lower()
    return value if value in PRIORITY_LEVELS else "normal"


def normalize_urgency(urgency: str | None, event_type: str, priority: str) -> str:
    value = str(urgency or _definition(event_type)["urgency"] or "").strip().lower()
    if value in URGENCY_LEVELS:
        return value
    return "immediate" if priority in {"urgent", "high"} else "standard"


def sanitize_deep_link(value: Any) -> str:
    link = str(value or "").strip()
    if not link or len(link) > 700 or re.search(r"[\r\n\t]", link):
        return "/pulse/notifications"
    if re.match(r"^(javascript|data|blob|file):", link, flags=re.I):
        return "/pulse/notifications"
    if link.startswith("//"):
        return "/pulse/notifications"
    if not link.startswith("/"):
        return "/pulse/notifications"
    if link.startswith(("/api/", "/static/", "/admin/")):
        return "/pulse/notifications"
    return link


def _env_value(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip()
    return ""


def _web_push_configured() -> bool:
    return bool(_env_value("WEB_PUSH_PUBLIC_KEY", "VAPID_PUBLIC_KEY") and _env_value("WEB_PUSH_PRIVATE_KEY", "VAPID_PRIVATE_KEY"))


def _fcm_configured() -> bool:
    return bool(
        _env_value("FCM_SERVER_KEY")
        or (
            _env_value("FCM_PROJECT_ID")
            and _env_value("FCM_CLIENT_EMAIL")
            and _env_value("FCM_PRIVATE_KEY")
        )
    )


def _apns_configured() -> bool:
    return bool(
        _env_value("APNS_TEAM_ID")
        and _env_value("APNS_KEY_ID")
        and _env_value("APNS_PRIVATE_KEY")
        and _env_value("APNS_BUNDLE_ID")
    )


def _brevo_email_configured() -> bool:
    return bool(email_service.provider_status().get("ready"))


def _brevo_sms_configured() -> bool:
    return bool(sms_service.is_sms_configured())


def _sound_key(category: str, priority: str, prefs: dict[str, Any] | None = None) -> str:
    prefs = prefs or {}
    if not _bool((prefs.get("experience") or {}).get("enable_notification_sound", True), True):
        return "silent"
    if category == "intelligence":
        return "alert" if priority == "urgent" else "pulse_signal"
    if priority == "urgent":
        return "pulse_urgent"
    return {
        "messages": "pulse_message",
        "calls": "pulse_call",
        "live": "pulse_live",
        "comments": "pulse_social",
        "mentions": "pulse_social",
        "follows": "pulse_social",
        "security": "pulse_security",
        "payments": "pulse_payment",
        "billing": "pulse_payment",
        "crypto": "pulse_crypto",
        "creator": "pulse_creator",
        "verification": "pulse_creator",
        "premium": "pulse_creator",
        "system": "pulse_system",
    }.get(category, "pulse_soft")


def _vibration_pattern(category: str, priority: str, prefs: dict[str, Any] | None = None) -> list[int]:
    prefs = prefs or {}
    if not _bool((prefs.get("experience") or {}).get("enable_notification_vibration", True), True):
        return []
    if category == "intelligence":
        return [240, 90, 240, 90, 320] if priority == "urgent" else [160, 80, 160]
    if priority == "urgent":
        return [240, 90, 240, 90, 320]
    if category in {"messages", "calls", "live"}:
        return [180, 80, 180]
    if category in {"security", "payments", "billing"}:
        return [220, 100, 260]
    return [120, 70, 120]


def _notification_public_preview(notification: dict[str, Any], prefs: dict[str, Any] | None = None) -> str:
    prefs = prefs or {}
    category = str(notification.get("category") or "")
    priority = str(notification.get("priority") or "normal")
    lock_screen_preview = True
    try:
        lock_screen_preview = bool((prefs.get("preferences") or {}).get(category, {}).get("lock_screen_preview", True))
    except Exception:
        lock_screen_preview = True
    if not lock_screen_preview or category in SENSITIVE_CATEGORIES or priority == "urgent":
        return "Open PulseSoc to review this secure alert."
    return str(notification.get("preview") or notification.get("body") or "Open PulseSoc for the latest update.")[:240]


def _provider_ready_summary() -> dict[str, Any]:
    email_status = email_service.provider_status()
    return {
        "web_push": {
            "ready": _web_push_configured(),
            "status": "ready" if _web_push_configured() else "config_missing",
            "required_env": ["WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "WEB_PUSH_SUBJECT"],
            "legacy_env_supported": ["VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"],
        },
        "fcm": {
            "ready": _fcm_configured(),
            "status": "ready" if _fcm_configured() else "config_missing",
            "required_env": ["FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY"],
            "legacy_env_supported": ["FCM_SERVER_KEY"],
        },
        "apns": {
            "ready": _apns_configured(),
            "status": "ready" if _apns_configured() else "config_missing",
            "required_env": ["APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID", "APNS_USE_SANDBOX"],
        },
        "brevo_email": {
            "ready": bool(email_status.get("ready")),
            "status": "ready" if email_status.get("ready") else "config_missing",
            "missing_fields": email_status.get("missing_fields") or [],
            "sender_email": email_status.get("sender_email") or "",
        },
        "brevo_sms": {
            "ready": _brevo_sms_configured(),
            "status": "ready" if _brevo_sms_configured() else "config_missing",
            "required_env": ["BREVO_API_KEY", "BREVO_SMS_SENDER", "SMS_SENDER_NAME"],
        },
    }


def make_dedupe_key(
    event_type: str,
    recipient_user_id: int,
    actor_user_id: int | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    explicit: str | None = None,
) -> str:
    if explicit:
        return hashlib.sha256(str(explicit).encode("utf-8")).hexdigest()
    basis = "|".join([
        normalize_type(event_type),
        str(int(recipient_user_id or 0)),
        str(int(actor_user_id or 0)),
        str(source_type or ""),
        str(source_id or ""),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _token_hash(token: str, endpoint: str = "") -> str:
    return hashlib.sha256(f"{token or ''}|{endpoint or ''}".encode("utf-8")).hexdigest()


def _read_existing_notification(cur: Any, user_id: int, dedupe_key: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT *
        FROM notifications
        WHERE recipient_user_id=? AND dedupe_key=? AND deleted_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(user_id), dedupe_key),
    )
    row = cur.fetchone()
    return format_notification(row) if row else None


def _create_event_record(cur: Any, event_key: str, payload: dict[str, Any]) -> int:
    now = now_iso()
    cur.execute("SELECT id FROM notification_events WHERE event_key=? LIMIT 1", (event_key,))
    existing = cur.fetchone()
    if existing:
        return _int(_row_get(existing, "id", existing[0] if existing else 0))
    cur.execute(
        """
        INSERT INTO notification_events
        (event_key, event_type, recipient_user_id, actor_user_id, source_type, source_id, payload_json, status, suppression_reason, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            payload["type"],
            payload["recipient_user_id"],
            payload.get("actor_user_id") or 0,
            payload.get("source_type") or "",
            payload.get("source_id") or "",
            _json_dumps(payload),
            payload.get("event_status") or "accepted",
            payload.get("suppression_reason") or "",
            now,
            now,
        ),
    )
    return _int(getattr(cur, "lastrowid", 0))


def _ids_from_json(value: Any) -> set[int]:
    data = _json_loads(value, [])
    if not isinstance(data, list):
        return set()
    result = set()
    for item in data:
        try:
            result.add(int(item))
        except Exception:
            continue
    return result


def _actor_blocked(cur: Any, recipient_user_id: int, actor_user_id: int) -> bool:
    if not actor_user_id:
        return False
    try:
        if _table_exists(cur, "blocked_users"):
            cols = _columns(cur, "blocked_users")
            if "user_id" in cols and "blocked_user_id" in cols:
                cur.execute(
                    "SELECT 1 FROM blocked_users WHERE user_id=? AND blocked_user_id=? LIMIT 1",
                    (recipient_user_id, actor_user_id),
                )
            elif "blocker_user_id" in cols and "blocked_user_id" in cols:
                cur.execute(
                    "SELECT 1 FROM blocked_users WHERE blocker_user_id=? AND blocked_user_id=? LIMIT 1",
                    (recipient_user_id, actor_user_id),
                )
            else:
                return False
            if cur.fetchone():
                return True
    except Exception:
        pass
    try:
        if _table_exists(cur, "comm_v2_blocks"):
            cur.execute(
                """
                SELECT 1 FROM comm_v2_blocks
                WHERE blocker_user_id=? AND blocked_user_id=? AND COALESCE(deleted_at,'')=''
                LIMIT 1
                """,
                (recipient_user_id, actor_user_id),
            )
            if cur.fetchone():
                return True
    except Exception:
        pass
    return False


def _default_category_preferences(category: str) -> dict[str, bool]:
    return {
        "in_app": True,
        "push": True,
        "email": True,
        "sms": True,
        "sound": True,
        "vibration": True,
        "lock_screen_preview": True,
    }


def _default_channels_for_event(
    event_type: str,
    category: str | None = None,
    priority: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    normalized = normalize_type(event_type or "system_announcement")
    resolved_category = normalize_category(category, normalized)
    resolved_priority = normalize_priority(priority, normalized)
    metadata = metadata if isinstance(metadata, dict) else {}
    channels = ["in_app"]
    wants_locked_push = (
        resolved_category in LOCKED_DEVICE_PUSH_DEFAULT_CATEGORIES
        or resolved_priority in {"urgent", "high"}
        or normalized in {
            "new_message",
            "group_message",
            "image_message",
            "video_message",
            "voice_message",
            "file_message",
            "chat_message",
            "message",
            "private_message",
            "missed_call",
            "incoming_call",
            "live_invite",
            "cohost_request",
            "security_login_alert",
            "new_device_login",
            "password_changed",
            "email_changed",
            "phone_changed",
            "suspicious_login",
            "payment_failed",
            "payment_method_issue",
            "verification_approved",
            "verification_rejected",
            "verification_needs_info",
            "creator_payout",
            "creator_payout_failed",
            "crypto_alert_triggered",
            "crypto_price_alert",
            "intelligence_pulse",
            "intelligence_forecast",
            "intelligence_digest",
            "system_announcement",
            "admin_warning",
            "account_restriction",
        }
    )
    push_requested = metadata.get("push_allowed") or metadata.get("push_enabled") or metadata.get("force_push")
    if wants_locked_push or push_requested or resolved_category in PREFERENCE_CONTROLLED_PUSH_CATEGORIES:
        channels.append("push")
    if resolved_category in EMAIL_DEFAULT_CATEGORIES or resolved_priority == "urgent" or metadata.get("email_allowed"):
        channels.append("email")
    if resolved_category in SMS_DEFAULT_CATEGORIES and (resolved_priority == "urgent" or metadata.get("sms_allowed")):
        channels.append("sms")
    return list(dict.fromkeys(channels))


def _preferences_from_rows(rows: list[Any]) -> dict[str, Any]:
    defaults = {category: _default_category_preferences(category) for category in DEFAULT_CATEGORIES}
    experience = {
        "enable_push_notifications": True,
        "enable_notification_sound": True,
        "enable_notification_vibration": True,
        "notification_sound_type": "soft",
        "quiet_hours_enabled": False,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "muted_user_ids": [],
        "muted_conversation_ids": [],
        "blocked_user_ids": [],
    }
    for row in rows:
        category = str(_row_get(row, "category", "") or "global")
        values = {
            "in_app": _bool(_row_get(row, "in_app", 1), True),
            "push": _bool(_row_get(row, "push", 1), True),
            "email": _bool(_row_get(row, "email", 1), True),
            "sms": _bool(_row_get(row, "sms", 1), True),
            "sound": _bool(_row_get(row, "sound", _row_get(row, "enable_notification_sound", 1)), True),
            "vibration": _bool(_row_get(row, "vibration", _row_get(row, "enable_notification_vibration", 1)), True),
            "lock_screen_preview": _bool(_row_get(row, "lock_screen_preview", 1), True),
        }
        if category != "global":
            defaults[category] = values
        if category == "global" or not experience.get("_loaded"):
            experience.update({
                "enable_push_notifications": _bool(_row_get(row, "enable_push_notifications", _row_get(row, "push", 1)), True),
                "enable_notification_sound": _bool(_row_get(row, "enable_notification_sound", _row_get(row, "sound", 1)), True),
                "enable_notification_vibration": _bool(_row_get(row, "enable_notification_vibration", _row_get(row, "vibration", 1)), True),
                "notification_sound_type": str(_row_get(row, "notification_sound_type", "soft") or "soft"),
                "quiet_hours_enabled": _bool(_row_get(row, "quiet_hours_enabled", 0), False),
                "quiet_hours_start": str(_row_get(row, "quiet_hours_start", "22:00") or "22:00"),
                "quiet_hours_end": str(_row_get(row, "quiet_hours_end", "07:00") or "07:00"),
                "muted_user_ids": sorted(_ids_from_json(_row_get(row, "muted_users_json", "[]"))),
                "muted_conversation_ids": sorted(_ids_from_json(_row_get(row, "muted_conversations_json", "[]"))),
                "blocked_user_ids": sorted(_ids_from_json(_row_get(row, "blocked_users_json", "[]"))),
                "_loaded": True,
            })
    experience.pop("_loaded", None)
    return {"ok": True, "preferences": defaults, "experience": experience}


def _get_preferences_with_cursor(cur: Any, user_id: int) -> dict[str, Any]:
    cur.execute("SELECT * FROM notification_preferences WHERE user_id=?", (int(user_id),))
    return _preferences_from_rows(list(cur.fetchall()))


def ensure_user_notification_defaults(user_id: int, conn: Any | None = None) -> dict[str, Any]:
    """Provision missing notification preference rows without overwriting user choices."""
    user_id = _int(user_id, 0)
    if user_id <= 0:
        return {"ok": False, "inserted": 0, "updated_nulls": 0}
    owns_conn = conn is None
    if conn is None:
        conn = db_service.connect()
        ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    cur.execute("SELECT category FROM notification_preferences WHERE user_id=?", (user_id,))
    existing = {str(_row_get(row, "category", row[0] if row else "") or "") for row in cur.fetchall()}
    inserted = 0
    if "global" not in existing:
        cur.execute(
            """
            INSERT INTO notification_preferences
            (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview,
             enable_push_notifications, enable_notification_sound, enable_notification_vibration,
             notification_sound_type, quiet_hours_enabled, quiet_hours_start, quiet_hours_end, updated_at)
            VALUES (?, 'global', 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'soft', 0, '22:00', '07:00', ?)
            ON CONFLICT(user_id, category) DO NOTHING
            """,
            (user_id, now),
        )
        inserted += 1
    for category in DEFAULT_CATEGORIES:
        if category in existing:
            continue
        cur.execute(
            """
            INSERT INTO notification_preferences
            (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview, updated_at)
            VALUES (?, ?, 1, 1, 1, 1, 1, 1, 1, ?)
            ON CONFLICT(user_id, category) DO NOTHING
            """,
            (user_id, category, now),
        )
        inserted += 1
    cur.execute(
        """
        UPDATE notification_preferences
        SET in_app=COALESCE(in_app, 1),
            push=COALESCE(push, 1),
            email=COALESCE(email, 1),
            sms=COALESCE(sms, 1),
            sound=COALESCE(sound, 1),
            vibration=COALESCE(vibration, 1),
            lock_screen_preview=COALESCE(lock_screen_preview, 1),
            enable_push_notifications=CASE
                WHEN category='global' THEN COALESCE(enable_push_notifications, 1)
                ELSE enable_push_notifications
            END,
            enable_notification_sound=COALESCE(enable_notification_sound, 1),
            enable_notification_vibration=COALESCE(enable_notification_vibration, 1),
            notification_sound_type=COALESCE(notification_sound_type, 'soft'),
            quiet_hours_enabled=COALESCE(quiet_hours_enabled, 0),
            quiet_hours_start=COALESCE(quiet_hours_start, '22:00'),
            quiet_hours_end=COALESCE(quiet_hours_end, '07:00')
        WHERE user_id=?
        """,
        (user_id,),
    )
    updated_nulls = int(getattr(cur, "rowcount", 0) or 0)
    if owns_conn:
        conn.commit()
        conn.close()
    return {"ok": True, "inserted": inserted, "updated_nulls": updated_nulls}


def backfill_notification_defaults(limit: int = 1000, conn: Any | None = None) -> dict[str, Any]:
    owns_conn = conn is None
    if conn is None:
        conn = db_service.connect()
        ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id
        FROM users u
        WHERE u.user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM notification_preferences np
              WHERE np.user_id=u.user_id AND np.category='global'
          )
        ORDER BY u.user_id
        LIMIT ?
        """,
        (max(1, int(limit or 1000)),),
    )
    rows = list(cur.fetchall())
    users_checked = 0
    rows_inserted = 0
    for row in rows:
        user_id = _int(_row_get(row, "user_id", row[0] if row else 0), 0)
        if user_id <= 0:
            continue
        result = ensure_user_notification_defaults(user_id, conn=conn)
        users_checked += 1
        rows_inserted += int(result.get("inserted") or 0)
    if owns_conn:
        conn.commit()
        conn.close()
    return {"ok": True, "users_checked": users_checked, "rows_inserted": rows_inserted}


def get_preferences(user_id: int) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    ensure_user_notification_defaults(user_id, conn=conn)
    conn.commit()
    result = _get_preferences_with_cursor(cur, user_id)
    conn.close()
    return result


def update_preferences(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    payload = payload or {}
    now = now_iso()
    experience = payload.get("experience") if isinstance(payload.get("experience"), dict) else {
        key: payload[key]
        for key in [
            "enable_push_notifications",
            "enable_notification_sound",
            "enable_notification_vibration",
            "notification_sound_type",
            "quiet_hours_enabled",
            "quiet_hours_start",
            "quiet_hours_end",
            "muted_user_ids",
            "muted_conversation_ids",
            "blocked_user_ids",
        ]
        if key in payload
    }
    category_preferences = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else {
        key: value for key, value in payload.items() if isinstance(value, dict)
    }
    for category, values in (category_preferences or {}).items():
        if not isinstance(values, dict):
            continue
        normalized_category = normalize_category(str(category), "system_announcement")
        defaults = _default_category_preferences(normalized_category)
        cur.execute(
            """
            INSERT INTO notification_preferences (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
                in_app=excluded.in_app,
                push=excluded.push,
                email=excluded.email,
                sms=excluded.sms,
                sound=excluded.sound,
                vibration=excluded.vibration,
                lock_screen_preview=excluded.lock_screen_preview,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id),
                normalized_category,
                1 if values.get("in_app", defaults["in_app"]) else 0,
                1 if values.get("push", defaults["push"]) else 0,
                1 if values.get("email", defaults["email"]) else 0,
                1 if values.get("sms", defaults["sms"]) else 0,
                1 if values.get("sound", True) else 0,
                1 if values.get("vibration", True) else 0,
                1 if values.get("lock_screen_preview", True) else 0,
                now,
            ),
        )
    if experience:
        cur.execute("SELECT * FROM notification_preferences WHERE user_id=? AND category='global' LIMIT 1", (int(user_id),))
        existing_global = cur.fetchone()
        if "enable_push_notifications" in experience:
            push_enabled = 1 if experience.get("enable_push_notifications") else 0
        else:
            push_enabled = 1 if _bool(
                _row_get(existing_global, "enable_push_notifications", _row_get(existing_global, "push", 1)),
                True,
            ) else 0
        cur.execute(
            """
            INSERT INTO notification_preferences
            (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview,
             enable_push_notifications, enable_notification_sound, enable_notification_vibration,
             notification_sound_type, quiet_hours_enabled, quiet_hours_start, quiet_hours_end,
             muted_users_json, muted_conversations_json, blocked_users_json, updated_at)
            VALUES (?, 'global', 1, 1, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
                sound=excluded.sound,
                vibration=excluded.vibration,
                lock_screen_preview=excluded.lock_screen_preview,
                enable_push_notifications=excluded.enable_push_notifications,
                enable_notification_sound=excluded.enable_notification_sound,
                enable_notification_vibration=excluded.enable_notification_vibration,
                notification_sound_type=excluded.notification_sound_type,
                quiet_hours_enabled=excluded.quiet_hours_enabled,
                quiet_hours_start=excluded.quiet_hours_start,
                quiet_hours_end=excluded.quiet_hours_end,
                muted_users_json=excluded.muted_users_json,
                muted_conversations_json=excluded.muted_conversations_json,
                blocked_users_json=excluded.blocked_users_json,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id),
                1 if experience.get("enable_notification_sound", True) else 0,
                1 if experience.get("enable_notification_vibration", True) else 0,
                1 if experience.get("lock_screen_preview", True) else 0,
                push_enabled,
                1 if experience.get("enable_notification_sound", True) else 0,
                1 if experience.get("enable_notification_vibration", True) else 0,
                str(experience.get("notification_sound_type") or "soft")[:40],
                1 if experience.get("quiet_hours_enabled") else 0,
                str(experience.get("quiet_hours_start") or "22:00")[:5],
                str(experience.get("quiet_hours_end") or "07:00")[:5],
                _json_dumps(experience.get("muted_user_ids") or []),
                _json_dumps(experience.get("muted_conversation_ids") or []),
                _json_dumps(experience.get("blocked_user_ids") or []),
                now,
            ),
        )
    conn.commit()
    conn.close()
    return get_preferences(user_id)


def _quiet_hours_active(experience: dict[str, Any], now: datetime | None = None) -> bool:
    if not experience.get("quiet_hours_enabled"):
        return False
    now = now or datetime.utcnow()
    def parse(value: str) -> int:
        try:
            hour, minute = str(value or "00:00").split(":", 1)
            return int(hour) * 60 + int(minute)
        except Exception:
            return 0
    current = now.hour * 60 + now.minute
    start = parse(experience.get("quiet_hours_start") or "22:00")
    end = parse(experience.get("quiet_hours_end") or "07:00")
    return start <= end and start <= current <= end or start > end and (current >= start or current <= end)


def _rules_check(cur: Any, payload: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any]:
    category = payload["category"]
    priority = payload["priority"]
    actor_user_id = _int(payload.get("actor_user_id"), 0)
    recipient_user_id = _int(payload.get("recipient_user_id"), 0)
    metadata = payload.get("metadata") or {}
    if actor_user_id and actor_user_id == recipient_user_id and not metadata.get("allow_self_notification"):
        return {"allowed": False, "reason": "self_notification_suppressed", "channels": []}
    if category in SOCIAL_CATEGORIES and _actor_blocked(cur, recipient_user_id, actor_user_id):
        return {"allowed": False, "reason": "blocked_actor", "channels": []}
    experience = prefs.get("experience") or {}
    if actor_user_id in set(experience.get("muted_user_ids") or []) and priority != "urgent":
        return {"allowed": False, "reason": "muted_actor", "channels": []}
    conversation_id = _int(metadata.get("conversation_id") or metadata.get("thread_id") or metadata.get("conversationId"), 0)
    if conversation_id and conversation_id in set(experience.get("muted_conversation_ids") or []) and priority != "urgent":
        return {"allowed": False, "reason": "muted_conversation", "channels": []}
    preference_map = prefs.get("preferences") or {}
    category_pref = preference_map.get(category) or {}
    if category in {"likes", "reposts"} and not category_pref.get("push") and (preference_map.get("social") or {}).get("push"):
        category_pref = {**category_pref, "push": True}
    requested = set()
    for raw_channel in payload.get("channels") or ["in_app"]:
        channel = ADAPTER_CHANNEL_ALIASES.get(str(raw_channel or "").strip().lower(), str(raw_channel or "").strip().lower())
        if channel in DELIVERY_CHANNELS:
            requested.add(channel)
    requested = requested or {"in_app"}
    if priority == "urgent":
        requested.add("in_app")
    allowed_channels = []
    for channel in requested:
        if channel == "in_app":
            if category_pref.get("in_app", True) or priority == "urgent":
                allowed_channels.append(channel)
            continue
        if channel == "push" and not experience.get("enable_push_notifications"):
            continue
        if category_pref.get(channel, False):
            allowed_channels.append(channel)
    if not allowed_channels and (category_pref.get("in_app", True) or priority == "urgent"):
        allowed_channels = ["in_app"]
    return {
        "allowed": bool(allowed_channels),
        "reason": "" if allowed_channels else "channel_preferences_disabled",
        "channels": allowed_channels,
        "quiet_hours": _quiet_hours_active(experience),
    }


def _privacy_safe_preview(category: str, body: str, preview: str, priority: str) -> str:
    text = preview or body or ""
    if category in SENSITIVE_CATEGORIES or priority == "urgent":
        return "Open PulseSoc to review this secure alert."
    return text[:240]


def _insert_delivery_jobs(cur: Any, notification_id: int, payload: dict[str, Any], channels: list[str], quiet_hours: bool) -> list[dict[str, Any]]:
    jobs = []
    now = now_iso()
    for channel in channels:
        provider = PROVIDER_PLACEHOLDERS.get(channel, channel)
        status = "ready" if channel == "in_app" else "queued"
        scheduled_at = now
        if quiet_hours and channel in NOISY_CHANNELS and payload["priority"] != "urgent":
            status = "scheduled"
            scheduled_at = (datetime.utcnow() + timedelta(hours=1)).replace(microsecond=0).isoformat() + "Z"
        dedupe = hashlib.sha256(f"{notification_id}|{channel}|{payload['dedupe_key']}".encode("utf-8")).hexdigest()
        cur.execute("SELECT id FROM notification_delivery_jobs WHERE dedupe_key=? LIMIT 1", (dedupe,))
        existing = cur.fetchone()
        if existing:
            jobs.append({"id": _int(_row_get(existing, "id", existing[0] if existing else 0)), "channel": channel, "status": "duplicate"})
            continue
        cur.execute(
            """
            INSERT INTO notification_delivery_jobs
            (notification_id, user_id, recipient_user_id, channel, provider, status, dedupe_key, retry_count,
             max_attempts, scheduled_at, next_retry_at, failed_reason, provider_message_id, payload_json, created_at, updated_at, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 3, ?, ?, '', '', ?, ?, ?, ?)
            """,
            (
                notification_id,
                payload["recipient_user_id"],
                payload["recipient_user_id"],
                channel,
                provider,
                status,
                dedupe,
                scheduled_at,
                scheduled_at if status == "scheduled" else "",
                _json_dumps({
                    "title": payload["title"],
                    "body": payload["body"],
                    "preview": payload["preview"],
                    "deep_link": payload["deep_link"],
                    "category": payload["category"],
                    "priority": payload["priority"],
                    "urgency": payload["urgency"],
                    "sound_key": payload.get("sound_key") or "",
                    "vibration": payload.get("vibration") or [],
                    "metadata": payload["metadata"],
                }),
                now,
                now,
                now if status == "ready" else "",
            ),
        )
        jobs.append({"id": _int(getattr(cur, "lastrowid", 0)), "channel": channel, "status": status, "provider": provider})
    return jobs


def _mirror_pulse_notification(cur: Any, notification_id: int, payload: dict[str, Any]) -> int:
    if not _table_exists(cur, "pulse_notifications"):
        return 0
    metadata = dict(payload.get("metadata") or {})
    metadata.update({
        "notification_os_id": notification_id,
        "category": payload["category"],
        "priority": payload["priority"],
        "urgency": payload["urgency"],
        "source_type": payload.get("source_type") or "",
        "source_id": payload.get("source_id") or "",
        "dedupe_key": payload["dedupe_key"],
    })
    cur.execute(
        """
        INSERT INTO pulse_notifications
        (user_id, actor_user_id, type, title, body, entity_type, entity_id, deep_link, target_url, is_read, read_at, delivery_status, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 'created', ?, ?)
        """,
        (
            payload["recipient_user_id"],
            payload.get("actor_user_id") or 0,
            payload["type"],
            payload["title"],
            payload["body"],
            payload.get("source_type") or "",
            str(payload.get("source_id") or ""),
            payload["deep_link"],
            payload["deep_link"],
            _json_dumps(metadata),
            payload["created_at"],
        ),
    )
    return _int(getattr(cur, "lastrowid", 0))


def intake_event(
    event_type: str,
    recipient_user_id: int,
    actor_user_id: int | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    title: str | None = None,
    body: str | None = None,
    preview: str | None = None,
    deep_link: str | None = None,
    metadata: dict[str, Any] | None = None,
    category: str | None = None,
    priority: str | None = None,
    urgency: str | None = None,
    channels: list[str] | tuple[str, ...] | None = None,
    dedupe_key: str | None = None,
    icon_url: str | None = None,
    avatar_url: str | None = None,
) -> dict[str, Any]:
    event_type = normalize_type(event_type)
    category = normalize_category(category, event_type)
    priority = normalize_priority(priority, event_type)
    urgency = normalize_urgency(urgency, event_type, priority)
    definition = _definition(event_type)
    metadata = dict(metadata or {})
    created_at = now_iso()
    recipient_user_id = int(recipient_user_id)
    actor_user_id = int(actor_user_id or 0)
    source_type = str(source_type or metadata.get("source_type") or "")[:80]
    source_id = str(source_id or metadata.get("source_id") or "")[:160]
    body = str(body or metadata.get("body") or "Open PulseSoc for the latest update.")[:800]
    payload = {
        "recipient_user_id": recipient_user_id,
        "actor_user_id": actor_user_id,
        "type": event_type,
        "category": category,
        "priority": priority,
        "urgency": urgency,
        "title": str(title or definition.get("title") or "PulseSoc update")[:240],
        "body": body,
        "preview": "",
        "deep_link": sanitize_deep_link(deep_link or metadata.get("deep_link") or metadata.get("url") or "/pulse/notifications"),
        "source_type": source_type,
        "source_id": source_id,
        "metadata": metadata,
        "channels": list(channels or ["in_app"]),
        "dedupe_key": make_dedupe_key(event_type, recipient_user_id, actor_user_id, source_type, source_id, dedupe_key or metadata.get("dedupe_key")),
        "icon_url": str(icon_url or metadata.get("icon_url") or "")[:700],
        "avatar_url": str(avatar_url or metadata.get("avatar_url") or "")[:700],
        "created_at": created_at,
    }
    payload["preview"] = _privacy_safe_preview(category, body, str(preview or metadata.get("preview") or "")[:240], priority)
    conn = db_service.connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        existing = _read_existing_notification(cur, recipient_user_id, payload["dedupe_key"])
        event_id = _create_event_record(cur, payload["dedupe_key"], payload)
        if existing:
            conn.commit()
            return {"ok": True, "deduped": True, "notification": existing, "notification_id": existing.get("id"), "event_id": event_id}
        ensure_user_notification_defaults(recipient_user_id, conn=conn)
        prefs = _get_preferences_with_cursor(cur, recipient_user_id)
        rules = _rules_check(cur, payload, prefs)
        if not rules.get("allowed"):
            cur.execute(
                "UPDATE notification_events SET status='suppressed', suppression_reason=?, updated_at=? WHERE id=?",
                (rules.get("reason") or "suppressed", now_iso(), event_id),
            )
            conn.commit()
            return {"ok": True, "suppressed": True, "reason": rules.get("reason"), "event_id": event_id, "notification_id": 0}
        payload["sound_key"] = _sound_key(category, priority, prefs)
        payload["vibration"] = _vibration_pattern(category, priority, prefs)
        payload["event_id"] = event_id
        metadata_json = _json_dumps({**metadata, "event_id": event_id, "dedupe_key": payload["dedupe_key"]})
        cur.execute(
            """
            INSERT INTO notifications
             (user_id, notification_type, title, message, status, metadata, created_at, read_at,
             recipient_user_id, actor_user_id, type, category, priority, urgency, body, preview, deep_link,
             source_type, source_id, icon_url, avatar_url, metadata_json, sound_key, vibration_json, seen_at, delivered_at, opened_at,
             failed_at, failure_reason, updated_at, deleted_at, dedupe_key, event_id, delivery_status)
            VALUES (?, ?, ?, ?, 'unread', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, '', ?, NULL, ?, ?, 'created')
            """,
            (
                recipient_user_id,
                event_type,
                payload["title"],
                payload["body"],
                metadata_json,
                created_at,
                recipient_user_id,
                actor_user_id,
                event_type,
                category,
                priority,
                urgency,
                payload["body"],
                payload["preview"],
                payload["deep_link"],
                source_type,
                source_id,
                payload["icon_url"],
                payload["avatar_url"],
                metadata_json,
                payload["sound_key"],
                _json_dumps(payload["vibration"]),
                created_at,
                payload["dedupe_key"],
                event_id,
            ),
        )
        notification_id = _int(getattr(cur, "lastrowid", 0))
        payload["notification_id"] = notification_id
        jobs = _insert_delivery_jobs(cur, notification_id, payload, list(rules.get("channels") or ["in_app"]), bool(rules.get("quiet_hours")))
        pulse_id = 0
        if not metadata.get("skip_pulse_legacy_mirror"):
            pulse_id = _mirror_pulse_notification(cur, notification_id, payload)
        if pulse_id:
            metadata["pulse_notification_id"] = pulse_id
            cur.execute(
                "UPDATE notifications SET metadata_json=?, metadata=? WHERE id=?",
                (_json_dumps({**metadata, "event_id": event_id, "dedupe_key": payload["dedupe_key"], "pulse_notification_id": pulse_id}), _json_dumps(metadata), notification_id),
            )
        conn.commit()
        schedule_delivery_processing(reason="notification_intake")
        notification = get_notification(recipient_user_id, notification_id) or {"id": notification_id}
        logging.info(
            "PULSESOC_NOTIFICATION_CREATED user_id=%s type=%s category=%s priority=%s notification_id=%s channels=%s",
            recipient_user_id,
            event_type,
            category,
            priority,
            notification_id,
            ",".join(rule.get("channel", "") for rule in jobs),
        )
        return {"ok": True, "notification_id": notification_id, "event_id": event_id, "notification": notification, "delivery_jobs": jobs, "pulse_notification_id": pulse_id}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("PULSESOC_NOTIFICATION_INTAKE_FAILED type=%s user_id=%s error=%s", event_type, recipient_user_id, exc)
        return {"ok": False, "error": "notification_intake_failed", "message": "Notification could not be created safely."}
    finally:
        conn.close()


def _compact_text(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _actor_name(actor_name: str | None = None) -> str:
    return _compact_text(actor_name or "Someone", 80) or "Someone"


def _coerce_channels(channels: list[str] | tuple[str, ...] | None, default: tuple[str, ...] = ("in_app",)) -> list[str]:
    normalized: list[str] = []
    for raw in channels or default:
        channel = ADAPTER_CHANNEL_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
        if channel in DELIVERY_CHANNELS and channel not in normalized:
            normalized.append(channel)
    return normalized or list(default)


def _event_metadata(base: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    metadata = dict(base or {})
    for key, value in extra.items():
        if value not in (None, ""):
            metadata[key] = value
    return metadata


def _message_event_type(conversation_type: str = "direct", media_type: str = "") -> str:
    media = str(media_type or "").strip().lower()
    if media in {"photo", "image", "jpeg", "jpg", "png", "webp"}:
        return "image_message"
    if media in {"video", "mp4", "webm", "mov"}:
        return "video_message"
    if media in {"voice", "audio", "audio_message", "webm_audio", "voice_message"}:
        return "voice_message"
    if media in {"file", "document", "attachment"}:
        return "file_message"
    if str(conversation_type or "").strip().lower() in {"group", "room", "community"}:
        return "group_message"
    return "new_message"


def _message_preview(body: str = "", media_type: str = "") -> str:
    text = _compact_text(body, 180)
    if text:
        return text
    media = str(media_type or "").strip().lower()
    if media in {"photo", "image", "jpeg", "jpg", "png", "webp"}:
        return "Sent you a photo"
    if media in {"video", "mp4", "webm", "mov"}:
        return "Sent you a video"
    if media in {"voice", "audio", "audio_message", "webm_audio", "voice_message"}:
        return "Sent you a voice message"
    if media in {"file", "document", "attachment"}:
        return "Sent you a file"
    return "Sent you a message"


def notify_new_message(
    recipient_user_id: int,
    actor_user_id: int,
    conversation_id: int,
    message_id: int,
    body: str = "",
    media_type: str = "",
    conversation_type: str = "direct",
    actor_name: str = "",
    channels: list[str] | tuple[str, ...] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the central notification for a real Messenger message event."""

    event_type = _message_event_type(conversation_type, media_type)
    preview = _message_preview(body, media_type)
    actor = _actor_name(actor_name)
    return intake_event(
        event_type=event_type,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="message",
        source_id=str(int(message_id or 0)),
        title=f"New message from {actor}",
        body=preview,
        preview=preview,
        deep_link=f"/pulse/messages/{int(conversation_id or 0)}",
        metadata=_event_metadata(
            metadata,
            conversation_id=int(conversation_id or 0),
            thread_id=int(conversation_id or 0),
            message_id=int(message_id or 0),
            media_type=str(media_type or ""),
            conversation_type=str(conversation_type or "direct"),
            actor_name=actor,
        ),
        category="messages",
        priority="high",
        urgency="immediate",
        channels=_coerce_channels(channels, ("in_app", "push")),
        dedupe_key=f"message:{int(conversation_id or 0)}:{int(message_id or 0)}:{int(recipient_user_id or 0)}",
    )


def notify_missed_call(
    recipient_user_id: int,
    actor_user_id: int,
    conversation_id: int,
    call_id: str | int,
    actor_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    return intake_event(
        event_type="missed_call",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type=str((metadata or {}).get("source_type") or "call"),
        source_id=str(call_id or ""),
        title="Missed Pulse",
        body=f"{actor} tried to reach you.",
        preview="Missed voice call",
        deep_link=f"/pulse/messages/{int(conversation_id or 0)}?tab=calls",
        metadata=_event_metadata(metadata, conversation_id=int(conversation_id or 0), call_id=str(call_id or ""), actor_name=actor),
        channels=["in_app", "push", "call"],
        dedupe_key=f"missed-call:{conversation_id}:{call_id}:{recipient_user_id}",
    )


def notify_live_started(
    recipient_user_id: int,
    actor_user_id: int,
    live_session_id: int | str,
    actor_name: str = "",
    title: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    body = _compact_text(title, 180) or f"{actor} is live now."
    return intake_event(
        event_type="live_started",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="live_session",
        source_id=str(live_session_id or ""),
        title=f"{actor} went live",
        body=body,
        preview=body,
        deep_link=f"/pulse/reels?live={live_session_id}",
        metadata=_event_metadata(metadata, live_session_id=str(live_session_id or ""), actor_name=actor),
        channels=["in_app", "push"],
        dedupe_key=f"live-started:{live_session_id}:{recipient_user_id}",
    )


def notify_live_invite(
    recipient_user_id: int,
    actor_user_id: int,
    live_session_id: int | str,
    actor_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    return intake_event(
        event_type="live_invite",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="live_session",
        source_id=str(live_session_id or ""),
        title=f"Live invite from {actor}",
        body="You were invited to join a PulseSoc Live.",
        preview="Live invite",
        deep_link=f"/pulse/reels?live={live_session_id}",
        metadata=_event_metadata(metadata, live_session_id=str(live_session_id or ""), actor_name=actor),
        channels=["in_app", "push"],
        dedupe_key=f"live-invite:{live_session_id}:{recipient_user_id}:{actor_user_id}",
    )


def notify_cohost_request(
    recipient_user_id: int,
    actor_user_id: int,
    live_session_id: int | str,
    actor_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    return intake_event(
        event_type="cohost_request",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="live_session",
        source_id=str(live_session_id or ""),
        title=f"Co-host request from {actor}",
        body="Review this PulseSoc Live co-host request.",
        preview="Co-host request",
        deep_link=f"/pulse/live/studio?live_id={live_session_id}&panel=backstage",
        metadata=_event_metadata(metadata, live_session_id=str(live_session_id or ""), actor_name=actor),
        channels=["in_app", "push"],
        dedupe_key=f"cohost-request:{live_session_id}:{recipient_user_id}:{actor_user_id}",
    )


def notify_follow(
    recipient_user_id: int,
    actor_user_id: int,
    actor_name: str = "",
    actor_profile_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    profile_link = f"/pulse/profile/{actor_profile_id}" if actor_profile_id else f"/pulse/user/{int(actor_user_id or 0)}"
    return intake_event(
        event_type="follow",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="profile",
        source_id=str(actor_user_id or ""),
        title="New follower",
        body=f"{actor} followed you.",
        preview=f"{actor} followed you.",
        deep_link=profile_link,
        metadata=_event_metadata(metadata, actor_name=actor, actor_profile_id=actor_profile_id),
        channels=["in_app", "push"],
        dedupe_key=f"follow:{int(recipient_user_id or 0)}:{int(actor_user_id or 0)}",
    )


def notify_post_like(
    recipient_user_id: int,
    actor_user_id: int,
    post_id: int,
    reaction_type: str = "like",
    actor_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    reaction = _compact_text(reaction_type or "like", 40)
    return intake_event(
        event_type="like",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="post",
        source_id=str(int(post_id or 0)),
        title="New reaction",
        body=f"{actor} reacted to your post.",
        preview=f"{actor} reacted to your post.",
        deep_link=f"/pulse/post/{int(post_id or 0)}",
        metadata=_event_metadata(metadata, post_id=int(post_id or 0), reaction_type=reaction, actor_name=actor),
        channels=["in_app", "push"],
        dedupe_key=f"post-reaction:{int(post_id or 0)}:{int(actor_user_id or 0)}:{reaction}:{int(recipient_user_id or 0)}",
    )


def notify_post_comment(
    recipient_user_id: int,
    actor_user_id: int,
    post_id: int,
    comment_id: int,
    body: str = "",
    parent_comment_id: int | None = None,
    actor_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_name(actor_name)
    is_reply = bool(parent_comment_id)
    event_type = "reply" if is_reply else "comment"
    preview = _compact_text(body, 180) or ("Replied to a comment." if is_reply else "Commented on your post.")
    return intake_event(
        event_type=event_type,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type="comment",
        source_id=str(int(comment_id or 0)),
        title="New reply" if is_reply else "New comment",
        body=f"{actor}: {preview}",
        preview=preview,
        deep_link=f"/pulse/post/{int(post_id or 0)}#comment-{int(comment_id or 0)}",
        metadata=_event_metadata(
            metadata,
            post_id=int(post_id or 0),
            comment_id=int(comment_id or 0),
            parent_comment_id=int(parent_comment_id or 0),
            actor_name=actor,
        ),
        channels=["in_app", "push"],
        dedupe_key=f"post-comment:{int(comment_id or 0)}:{int(recipient_user_id or 0)}",
    )


def notify_security_event(
    recipient_user_id: int,
    event_type: str = "security_login_alert",
    title: str = "Security alert",
    body: str = "Review recent account activity.",
    source_id: str | int = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_type(event_type or "security_login_alert")
    preview = "Open PulseSoc to review this secure alert."
    return intake_event(
        event_type=normalized,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="security",
        source_id=str(source_id or normalized),
        title=_compact_text(title, 200) or "Security alert",
        body=_compact_text(body, 300) or "Review recent account activity.",
        preview=preview,
        deep_link="/dashboard/security",
        metadata=_event_metadata(metadata, secure_preview=True),
        category="security",
        priority="urgent",
        urgency="immediate",
        channels=["in_app", "push", "email", "sms"],
        dedupe_key=f"security:{normalized}:{recipient_user_id}:{source_id or metadata or now_iso()}",
    )


def notify_payment_event(
    recipient_user_id: int,
    event_type: str,
    title: str,
    body: str,
    source_id: str | int = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_type(event_type or "payment_failed")
    urgent = normalized in {"payment_failed", "payment_method_issue"}
    return intake_event(
        event_type=normalized,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="billing",
        source_id=str(source_id or normalized),
        title=_compact_text(title, 200) or _definition(normalized).get("title") or "Billing update",
        body=_compact_text(body, 300) or "Review your PulseSoc billing status.",
        preview="Open PulseSoc to review this billing update.",
        deep_link="/pulse/premium?panel=billing",
        metadata=_event_metadata(metadata, secure_preview=True),
        category=normalize_category(None, normalized),
        priority="urgent" if urgent else normalize_priority(None, normalized),
        urgency="immediate" if urgent else "standard",
        channels=["in_app", "push", "email", "sms"] if urgent else ["in_app", "push", "email"],
        dedupe_key=f"payment:{normalized}:{recipient_user_id}:{source_id or metadata or now_iso()}",
    )


def notify_creator_event(
    recipient_user_id: int,
    event_type: str,
    title: str,
    body: str,
    source_id: str | int = "",
    deep_link: str = "/pulse/dashboard/creator",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_type(event_type or "creator_payout")
    return intake_event(
        event_type=normalized,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="creator",
        source_id=str(source_id or normalized),
        title=_compact_text(title, 200) or _definition(normalized).get("title") or "Creator update",
        body=_compact_text(body, 300) or "Review your creator update in PulseSoc.",
        preview=_compact_text(body, 180) or "Creator update",
        deep_link=deep_link,
        metadata=_event_metadata(metadata),
        category=normalize_category(None, normalized),
        priority=normalize_priority(None, normalized),
        urgency=normalize_urgency(None, normalized, normalize_priority(None, normalized)),
        channels=["in_app", "push", "email"],
        dedupe_key=f"creator:{normalized}:{recipient_user_id}:{source_id or metadata or now_iso()}",
    )


def notify_crypto_alert(
    recipient_user_id: int,
    alert_id: int | str,
    title: str = "",
    body: str = "",
    coin_symbol: str = "",
    critical: bool = False,
    metadata: dict[str, Any] | None = None,
    *,
    alert_type: str = "price_target_reached",
    trigger_price: float | str | None = None,
    target_price: float | str | None = None,
    direction: str = "",
    priority: str = "",
    deep_link: str = "",
    channels: list[str] | tuple[str, ...] | None = None,
    trigger_window: str = "",
) -> dict[str, Any]:
    symbol = _compact_text(coin_symbol, 20).upper()
    crypto_alert_type = normalize_type(alert_type or "price_target_reached")
    supported_alert_types = {
        "price_target_reached",
        "large_market_movement",
        "portfolio_milestone",
        "wallet_activity",
        "bot_signal",
        "critical_market_alert",
    }
    if crypto_alert_type not in supported_alert_types:
        crypto_alert_type = "price_target_reached"
    normalized_priority = normalize_priority(priority or ("urgent" if critical or crypto_alert_type == "critical_market_alert" else "high"), "crypto_alert_triggered")
    default_channels = ("in_app", "push", "sms") if normalized_priority == "urgent" or critical else ("in_app", "push")
    requested_channels = _coerce_channels(channels, default_channels)
    link = sanitize_deep_link(
        deep_link
        or (f"/dashboard/crypto/alerts?alert_id={alert_id}" if alert_id not in (None, "") else "")
        or (f"/pulse/crypto?asset={symbol}" if symbol else "/dashboard/crypto/alerts")
    )
    metadata_payload = _event_metadata(
        metadata,
        alert_id=str(alert_id or ""),
        alert_type=crypto_alert_type,
        coin_symbol=symbol,
        trigger_price=trigger_price,
        target_price=target_price,
        direction=_compact_text(direction, 80),
        user_configured=True,
    )
    dedupe_window = _compact_text(
        trigger_window
        or metadata_payload.get("alert_event_id")
        or metadata_payload.get("trigger_window")
        or metadata_payload.get("trigger_bucket")
        or now_iso()[:16],
        80,
    )
    return intake_event(
        event_type="crypto_alert_triggered",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="crypto_alert",
        source_id=str(alert_id or ""),
        title=_compact_text(title, 200) or "Crypto alert",
        body=_compact_text(body, 300) or "A configured crypto alert was triggered.",
        preview=_compact_text(body, 180) or "Crypto alert triggered.",
        deep_link=link,
        metadata=metadata_payload,
        category="crypto",
        priority=normalized_priority,
        urgency="immediate",
        channels=requested_channels,
        dedupe_key=f"crypto-alert:{recipient_user_id}:{alert_id}:{symbol}:{crypto_alert_type}:{dedupe_window}",
    )


def notify_system_announcement(
    recipient_user_id: int,
    title: str,
    body: str,
    announcement_id: int | str = "",
    deep_link: str = "/pulse/notifications",
    priority: str = "normal",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_priority = normalize_priority(priority, "system_announcement")
    return intake_event(
        event_type="system_announcement",
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="system_announcement",
        source_id=str(announcement_id or ""),
        title=_compact_text(title, 200) or "PulseSoc announcement",
        body=_compact_text(body, 500) or "Open PulseSoc for the latest update.",
        preview=_compact_text(body, 180) or "PulseSoc announcement",
        deep_link=deep_link,
        metadata=_event_metadata(metadata, announcement_id=str(announcement_id or "")),
        category="system",
        priority=normalized_priority,
        urgency="immediate" if normalized_priority in {"urgent", "high"} else "standard",
        channels=["in_app", "push"],
        dedupe_key=f"system:{recipient_user_id}:{announcement_id or hashlib.sha256((_compact_text(title, 120) + _compact_text(body, 120)).encode('utf-8')).hexdigest()}",
    )


def notify_legacy_event(
    recipient_user_id: int,
    note_type: str,
    title: str,
    body: str,
    deep_link: str = "/pulse/notifications",
    actor_user_id: int | None = None,
    source_type: str = "",
    source_id: str | int = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility bridge for older PulseSoc call sites.

    Legacy code may already have push/email side effects, but many older
    call sites only mirrored into the central OS. Infer safe delivery channels
    from the normalized event so locked-device push does not stop at in-app.
    """

    normalized = normalize_type(note_type or "system_announcement")
    metadata = metadata if isinstance(metadata, dict) else {}
    category = normalize_category(metadata.get("category"), normalized)
    priority = normalize_priority(metadata.get("priority"), normalized)
    channels = metadata.get("channels") or metadata.get("notification_channels")
    if isinstance(channels, str):
        channels = [part.strip() for part in channels.split(",") if part.strip()]
    if not isinstance(channels, (list, tuple, set)):
        channels = _default_channels_for_event(normalized, category, priority, metadata)
    return intake_event(
        event_type=normalized,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=int(actor_user_id or 0),
        source_type=source_type or metadata.get("source_type") or "",
        source_id=str(source_id or metadata.get("source_id") or ""),
        title=_compact_text(title, 200) or _definition(normalized).get("title") or "PulseSoc update",
        body=_compact_text(body, 500) or "Open PulseSoc for the latest update.",
        preview=_compact_text(body, 180),
        deep_link=deep_link,
        metadata=_event_metadata(metadata, legacy_notification=True, skip_pulse_legacy_mirror=True),
        category=category,
        priority=priority,
        urgency=normalize_urgency(metadata.get("urgency"), normalized, priority),
        channels=list(channels),
        dedupe_key=f"legacy:{recipient_user_id}:{normalized}:{source_type}:{source_id}:{hashlib.sha256((_compact_text(title, 120) + _compact_text(body, 120)).encode('utf-8')).hexdigest()}",
    )


def format_notification(row: Any) -> dict[str, Any]:
    metadata = _json_loads(_row_get(row, "metadata_json", None), None)
    if metadata is None:
        metadata = _json_loads(_row_get(row, "metadata", None), {})
    read_at = _row_get(row, "read_at", "")
    body = _row_get(row, "body", None) or _row_get(row, "message", "") or ""
    note_type = _row_get(row, "type", None) or _row_get(row, "notification_type", "system_announcement")
    return {
        "id": _int(_row_get(row, "id", 0)),
        "recipient_user_id": _int(_row_get(row, "recipient_user_id", _row_get(row, "user_id", 0))),
        "user_id": _int(_row_get(row, "recipient_user_id", _row_get(row, "user_id", 0))),
        "actor_user_id": _int(_row_get(row, "actor_user_id", 0)),
        "type": note_type,
        "notification_type": note_type,
        "category": _row_get(row, "category", "") or normalize_category("", note_type),
        "priority": _row_get(row, "priority", "") or normalize_priority(None, note_type),
        "urgency": _row_get(row, "urgency", "") or normalize_urgency(None, note_type, normalize_priority(None, note_type)),
        "title": _row_get(row, "title", "") or "PulseSoc update",
        "body": body,
        "message": body,
        "preview": _row_get(row, "preview", "") or body[:240],
        "preview_text": _row_get(row, "preview", "") or body[:240],
        "deep_link": _row_get(row, "deep_link", "") or "/pulse/notifications",
        "target_url": _row_get(row, "deep_link", "") or "/pulse/notifications",
        "source_type": _row_get(row, "source_type", "") or "",
        "entity_type": _row_get(row, "source_type", "") or "",
        "source_id": _row_get(row, "source_id", "") or "",
        "entity_id": _row_get(row, "source_id", "") or "",
        "metadata": metadata or {},
        "read": bool(read_at) or str(_row_get(row, "status", "")).lower() == "read",
        "is_read": 1 if (bool(read_at) or str(_row_get(row, "status", "")).lower() == "read") else 0,
        "read_at": read_at,
        "seen_at": _row_get(row, "seen_at", "") or "",
        "delivered_at": _row_get(row, "delivered_at", "") or "",
        "opened_at": _row_get(row, "opened_at", "") or "",
        "failed_at": _row_get(row, "failed_at", "") or "",
        "failure_reason": _row_get(row, "failure_reason", "") or "",
        "delivery_status": _row_get(row, "delivery_status", "") or "created",
        "sound_key": _row_get(row, "sound_key", "") or "",
        "vibration": _json_loads(_row_get(row, "vibration_json", ""), []),
        "created_at": _row_get(row, "created_at", "") or "",
        "updated_at": _row_get(row, "updated_at", "") or "",
        "dedupe_key": _row_get(row, "dedupe_key", "") or "",
    }


def list_notifications(user_id: int, limit: int = 50, category: str = "all", unread_only: bool = False) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    clauses = ["recipient_user_id=?", "deleted_at IS NULL"]
    params: list[Any] = [int(user_id)]
    normalized_category = str(category or "all").strip().lower()
    if unread_only:
        clauses.append("(read_at IS NULL OR status!='read')")
    if normalized_category and normalized_category != "all":
        if normalized_category == "priority":
            clauses.append("priority IN ('urgent','high')")
        else:
            clauses.append("category=?")
            params.append(normalize_category(normalized_category, "system_announcement"))
    params.append(max(1, min(int(limit or 50), 100)))
    cur.execute(
        f"""
        SELECT *
        FROM notifications
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    rows = [format_notification(row) for row in cur.fetchall()]
    conn.close()
    return {"ok": True, "notifications": rows, "count": len(rows)}


def get_notification(user_id: int, notification_id: int) -> dict[str, Any] | None:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notifications WHERE id=? AND recipient_user_id=? AND deleted_at IS NULL LIMIT 1",
        (int(notification_id or 0), int(user_id)),
    )
    row = cur.fetchone()
    conn.close()
    return format_notification(row) if row else None


def badge_counts(user_id: int, chat_unread_count: int = 0) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM notifications
        WHERE recipient_user_id=? AND deleted_at IS NULL AND (read_at IS NULL OR status!='read')
        """,
        (int(user_id),),
    )
    alert_count = _int((cur.fetchone() or [0])[0])
    conn.close()
    return {
        "ok": True,
        "alert_unread_count": alert_count,
        "chat_unread_count": int(chat_unread_count or 0),
        "total_unread_count": alert_count + int(chat_unread_count or 0),
        "count": alert_count,
        "unread_count": alert_count,
        "server_authoritative": True,
    }


def mark_read(user_id: int, notification_id: int) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    cur.execute(
        """
        UPDATE notifications
        SET read_at=COALESCE(read_at, ?), seen_at=COALESCE(seen_at, ?), status='read', updated_at=?
        WHERE id=? AND recipient_user_id=? AND deleted_at IS NULL
        """,
        (now, now, now, int(notification_id or 0), int(user_id)),
    )
    changed = max(0, int(getattr(cur, "rowcount", 0) or 0))
    conn.commit()
    conn.close()
    counts = badge_counts(user_id)
    return {"ok": True, "updated": changed, "badge_counts": counts, **counts}


def mark_all_read(user_id: int, category: str = "all") -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    if category and category != "all":
        cur.execute(
            """
            UPDATE notifications
            SET read_at=COALESCE(read_at, ?), seen_at=COALESCE(seen_at, ?), status='read', updated_at=?
            WHERE recipient_user_id=? AND category=? AND deleted_at IS NULL AND (read_at IS NULL OR status!='read')
            """,
            (now, now, now, int(user_id), normalize_category(category, "system_announcement")),
        )
    else:
        cur.execute(
            """
            UPDATE notifications
            SET read_at=COALESCE(read_at, ?), seen_at=COALESCE(seen_at, ?), status='read', updated_at=?
            WHERE recipient_user_id=? AND deleted_at IS NULL AND (read_at IS NULL OR status!='read')
            """,
            (now, now, now, int(user_id)),
        )
    changed = max(0, int(getattr(cur, "rowcount", 0) or 0))
    conn.commit()
    conn.close()
    counts = badge_counts(user_id)
    return {"ok": True, "updated": changed, "badge_counts": counts, **counts}


def delete_notification(user_id: int, notification_id: int) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    cur.execute(
        "UPDATE notifications SET deleted_at=?, updated_at=? WHERE id=? AND recipient_user_id=? AND deleted_at IS NULL",
        (now, now, int(notification_id or 0), int(user_id)),
    )
    changed = max(0, int(getattr(cur, "rowcount", 0) or 0))
    conn.commit()
    conn.close()
    counts = badge_counts(user_id)
    return {"ok": True, "deleted": changed, "badge_counts": counts, **counts}


def register_device_token(user_id: int, payload: dict[str, Any], user_agent: str = "") -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    payload = payload or {}
    platform = str(payload.get("platform") or payload.get("device_type") or "web").strip().lower()[:20]
    device_id = str(payload.get("device_id") or payload.get("deviceId") or payload.get("endpoint") or payload.get("token") or secrets.token_hex(8))[:160]
    push_token = str(payload.get("push_token") or payload.get("token") or payload.get("expo_push_token") or payload.get("expoPushToken") or "")[:1000]
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else {}
    endpoint = str(payload.get("endpoint") or subscription.get("endpoint") or "")[:1000]
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    provider = str(
        payload.get("push_provider")
        or payload.get("provider")
        or ("fcm" if platform == "android" and push_token else "apns" if platform == "ios" and push_token else "web_push" if endpoint else "push")
    ).strip().lower()[:40]
    environment = str(payload.get("environment") or os.getenv("PUSH_ENVIRONMENT") or os.getenv("RAILWAY_ENVIRONMENT_NAME") or "production")[:80]
    now = now_iso()
    token_hash = _token_hash(push_token, endpoint)
    cur.execute(
        """
        INSERT INTO notification_device_tokens
        (user_id, device_id, platform, push_token, endpoint, p256dh, auth, user_agent, app_version, push_provider, environment, enabled, token_hash, last_seen_at, created_at, updated_at, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)
        ON CONFLICT(user_id, device_id, platform) DO UPDATE SET
            push_token=excluded.push_token,
            endpoint=excluded.endpoint,
            p256dh=excluded.p256dh,
            auth=excluded.auth,
            user_agent=excluded.user_agent,
            app_version=excluded.app_version,
            push_provider=excluded.push_provider,
            environment=excluded.environment,
            enabled=1,
            token_hash=excluded.token_hash,
            last_seen_at=excluded.last_seen_at,
            updated_at=excluded.updated_at,
            deleted_at=NULL
        """,
        (
            int(user_id),
            device_id,
            platform,
            push_token,
            endpoint,
            str(payload.get("p256dh") or keys.get("p256dh") or "")[:1000],
            str(payload.get("auth") or keys.get("auth") or "")[:1000],
            str(user_agent or payload.get("user_agent") or "")[:1000],
            str(payload.get("app_version") or payload.get("appVersion") or "")[:80],
            provider,
            environment,
            token_hash,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    cur.execute(
        "SELECT id, platform, device_id, push_provider, enabled, last_seen_at FROM notification_device_tokens WHERE user_id=? AND device_id=? AND platform=? LIMIT 1",
        (int(user_id), device_id, platform),
    )
    row = cur.fetchone()
    conn.close()
    return {
        "ok": True,
        "device": {
            "id": _int(_row_get(row, "id", 0)),
            "platform": platform,
            "device_id": device_id,
            "provider": _row_get(row, "push_provider", provider) if row else provider,
            "enabled": True,
            "last_seen_at": _row_get(row, "last_seen_at", now) if row else now,
        },
        "device_token_foundation": "registered",
    }


def disable_device_token(user_id: int, endpoint_or_device_id: str) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    token = str(endpoint_or_device_id or "")
    cur.execute(
        """
        UPDATE notification_device_tokens
        SET enabled=0, deleted_at=?, updated_at=?
        WHERE user_id=? AND (endpoint=? OR device_id=? OR push_token=?)
        """,
        (now, now, int(user_id), token, token, token),
    )
    changed = max(0, int(getattr(cur, "rowcount", 0) or 0))
    conn.commit()
    conn.close()
    return {"ok": True, "disabled": changed}


def device_status(user_id: int) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM notification_device_tokens WHERE user_id=? AND enabled=1 AND deleted_at IS NULL", (int(user_id),))
    active = _int((cur.fetchone() or [0])[0])
    conn.close()
    return {"ok": True, "notification_os_active_devices": active, "device_token_foundation": True}


def _delivery_autoprocess_enabled() -> bool:
    return str(os.getenv("PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED", "1")).strip().lower() not in {"0", "false", "off", "no"}


def schedule_delivery_processing(reason: str = "queued") -> dict[str, Any]:
    if not _delivery_autoprocess_enabled():
        return {"ok": True, "scheduled": False, "reason": "disabled"}
    if not DELIVERY_LOCK.acquire(blocking=False):
        return {"ok": True, "scheduled": False, "reason": "already_running"}

    def _run() -> None:
        try:
            process_delivery_jobs(limit=_int(os.getenv("PULSESOC_NOTIFICATION_DELIVERY_BATCH_SIZE"), 20))
        except Exception:
            logging.exception("PULSESOC_NOTIFICATION_DELIVERY_PROCESSOR_FAILED reason=%s", reason)
        finally:
            try:
                DELIVERY_LOCK.release()
            except RuntimeError:
                pass

    threading.Timer(0.25, _run).start()
    return {"ok": True, "scheduled": True, "reason": reason}


def _delivery_retry_at(retry_count: int) -> str:
    delay = min(3600, 30 * (2 ** max(0, int(retry_count or 1) - 1)))
    return (datetime.utcnow() + timedelta(seconds=delay)).replace(microsecond=0).isoformat() + "Z"


def _get_user_contact(cur: Any, user_id: int) -> dict[str, Any]:
    if not _table_exists(cur, "users"):
        return {"user_id": int(user_id), "email": "", "phone": "", "phone_verified": False, "sms_opt_in": False}
    cols = _columns(cur, "users")
    if "user_id" in cols:
        id_col = "user_id"
    elif "id" in cols:
        id_col = "id"
    else:
        return {"user_id": int(user_id), "email": "", "phone": "", "phone_verified": False, "sms_opt_in": False}
    wanted = [
        "email",
        "phone",
        "phone_number",
        "phone_verified",
        "sms_opt_in",
        "display_name",
        "username",
    ]
    select_cols = [column for column in wanted if column in cols]
    cur.execute(f"SELECT {', '.join(select_cols) if select_cols else id_col} FROM users WHERE {id_col}=? LIMIT 1", (int(user_id),))
    row = cur.fetchone()
    return {
        "user_id": int(user_id),
        "email": _row_get(row, "email", "") or "",
        "phone": _row_get(row, "phone_number", "") or _row_get(row, "phone", "") or "",
        "phone_verified": _bool(_row_get(row, "phone_verified", 0), False),
        "sms_opt_in": _bool(_row_get(row, "sms_opt_in", 0), False),
        "display_name": _row_get(row, "display_name", "") or _row_get(row, "username", "") or "PulseSoc member",
    }


def _active_device_tokens(cur: Any, user_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT *
        FROM notification_device_tokens
        WHERE user_id=? AND COALESCE(enabled,1)=1 AND deleted_at IS NULL
        ORDER BY last_seen_at DESC, id DESC
        """,
        (int(user_id),),
    )
    return [dict(row) if hasattr(row, "keys") else {
        "id": row[0],
        "user_id": row[1],
        "device_id": row[2],
        "platform": row[3],
        "push_token": row[4],
        "endpoint": row[5],
    } for row in cur.fetchall()]


def _legacy_push_subscription_count(cur: Any, user_id: int) -> int:
    if not _table_exists(cur, "push_subscriptions"):
        return 0
    try:
        cur.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=? AND COALESCE(is_active, active, 1)=1",
            (int(user_id),),
        )
        return _int((cur.fetchone() or [0])[0])
    except Exception:
        return 0


def _push_payload(notification: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any]:
    category = str(notification.get("category") or "system")
    priority = str(notification.get("priority") or "normal")
    metadata = notification.get("metadata") if isinstance(notification.get("metadata"), dict) else {}
    deep_link = sanitize_deep_link(notification.get("deep_link") or metadata.get("deep_link") or "/pulse/notifications")
    body = str(notification.get("body") or notification.get("message") or notification.get("preview") or "New PulseSoc update.")
    badge_count = badge_counts(int(notification.get("recipient_user_id") or notification.get("user_id") or 0)).get("total_unread_count", 0)
    return {
        "notification_id": int(notification.get("id") or 0),
        "type": notification.get("type") or notification.get("notification_type") or "system_announcement",
        "category": category,
        "priority": priority,
        "urgency": notification.get("urgency") or "standard",
        "title": notification.get("title") or "PulseSoc Alert",
        "body": body,
        "message": body,
        "preview": notification.get("preview") or body,
        "deep_link": deep_link,
        "target_url": deep_link,
        "url": deep_link,
        "web_url": deep_link,
        "sound_key": notification.get("sound_key") or _sound_key(category, priority, prefs),
        "sound": notification.get("sound_key") or _sound_key(category, priority, prefs),
        "vibrate": notification.get("vibration") or _vibration_pattern(category, priority, prefs),
        "vibration": notification.get("vibration") or _vibration_pattern(category, priority, prefs),
        "badge": True,
        "badge_count": badge_count,
        "show_on_lock_screen": True,
        "lock_screen": True,
        **metadata,
    }


def _send_fcm_token(token: str, notification: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "status": "skipped_no_device", "message": "FCM token missing."}
    server_key = _env_value("FCM_SERVER_KEY")
    project_id = _env_value("FCM_PROJECT_ID")
    if server_key:
        try:
            response = requests.post(
                "https://fcm.googleapis.com/fcm/send",
                headers={"Authorization": f"key={server_key}", "Content-Type": "application/json"},
                json={
                    "to": token,
                    "priority": "high" if notification.get("priority") in {"urgent", "high"} else "normal",
                    "notification": {
                        "title": notification.get("title") or "PulseSoc",
                        "body": payload.get("body") or notification.get("preview") or "Open PulseSoc.",
                    },
                    "data": payload,
                },
                timeout=10,
            )
            data = response.json() if response.content else {}
            if response.ok and int(data.get("success") or 0) > 0:
                return {"ok": True, "status": "sent", "provider": "fcm", "provider_message_id": str(((data.get("results") or [{}])[0] or {}).get("message_id") or "")}
            error = str(((data.get("results") or [{}])[0] or {}).get("error") or data.get("error") or response.text[:300])
            if error in {"NotRegistered", "InvalidRegistration", "MismatchSenderId"}:
                return {"ok": False, "status": "invalid_device", "provider": "fcm", "message": error}
            return {"ok": False, "status": "failed", "provider": "fcm", "message": error, "http_status": response.status_code}
        except Exception as exc:
            return {"ok": False, "status": "failed", "provider": "fcm", "message": str(exc)[:300], "error_type": type(exc).__name__}
    if project_id and _env_value("FCM_CLIENT_EMAIL") and _env_value("FCM_PRIVATE_KEY"):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
            info = {
                "type": "service_account",
                "project_id": project_id,
                "client_email": _env_value("FCM_CLIENT_EMAIL"),
                "private_key": _env_value("FCM_PRIVATE_KEY").replace("\\n", "\n"),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
            credentials.refresh(Request())
            response = requests.post(
                f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
                headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json"},
                json={"message": {"token": token, "notification": {"title": notification.get("title") or "PulseSoc", "body": payload.get("body") or ""}, "data": {key: str(value) for key, value in payload.items() if value is not None}}},
                timeout=10,
            )
            data = response.json() if response.content else {}
            if response.ok:
                return {"ok": True, "status": "sent", "provider": "fcm", "provider_message_id": str(data.get("name") or "")}
            return {"ok": False, "status": "failed", "provider": "fcm", "message": data.get("error", {}).get("message") or response.text[:300], "http_status": response.status_code}
        except Exception as exc:
            return {"ok": False, "status": "config_missing", "provider": "fcm", "message": f"FCM v1 dependency/config error: {type(exc).__name__}"}
    return {"ok": False, "status": "config_missing", "provider": "fcm", "message": "FCM credentials are not configured."}


def _send_apns_token(token: str, notification: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "status": "skipped_no_device", "message": "APNs token missing."}
    if not _apns_configured():
        return {"ok": False, "status": "config_missing", "provider": "apns", "message": "APNs credentials are not configured."}
    try:
        import httpx
        import jwt
    except Exception as exc:
        return {"ok": False, "status": "config_missing", "provider": "apns", "message": f"APNs dependency missing: {type(exc).__name__}"}
    team_id = _env_value("APNS_TEAM_ID")
    key_id = _env_value("APNS_KEY_ID")
    private_key = _env_value("APNS_PRIVATE_KEY").replace("\\n", "\n")
    bundle_id = _env_value("APNS_BUNDLE_ID")
    sandbox = str(os.getenv("APNS_USE_SANDBOX", "")).strip().lower() in {"1", "true", "yes", "on"}
    host = "https://api.sandbox.push.apple.com" if sandbox else "https://api.push.apple.com"
    try:
        auth_token = jwt.encode({"iss": team_id, "iat": int(datetime.utcnow().timestamp())}, private_key, algorithm="ES256", headers={"kid": key_id})
        body = {
            "aps": {
                "alert": {
                    "title": notification.get("title") or "PulseSoc",
                    "subtitle": str(payload.get("headline") or "")[:80],
                    "body": payload.get("body") or "",
                },
                "sound": "default" if payload.get("sound_key") != "silent" else None,
                "badge": _int(payload.get("badge"), 0),
                "category": str(payload.get("category") or "system")[:80],
            },
            "deep_link": payload.get("deep_link") or "/pulse/notifications",
            "notification_id": str(notification.get("id") or ""),
        }
        if body["aps"]["sound"] is None:
            body["aps"].pop("sound", None)
        with httpx.Client(http2=True, timeout=10) as client:
            response = client.post(
                f"{host}/3/device/{token}",
                headers={"authorization": f"bearer {auth_token}", "apns-topic": bundle_id, "apns-push-type": "alert", "apns-priority": "10" if notification.get("priority") in {"urgent", "high"} else "5"},
                json=body,
            )
        if 200 <= response.status_code < 300:
            return {"ok": True, "status": "sent", "provider": "apns", "provider_message_id": response.headers.get("apns-id", "")}
        status = "invalid_device" if response.status_code in {400, 410} and "BadDeviceToken" in response.text or "Unregistered" in response.text else "failed"
        return {"ok": False, "status": status, "provider": "apns", "message": response.text[:300], "http_status": response.status_code}
    except Exception as exc:
        return {"ok": False, "status": "failed", "provider": "apns", "message": str(exc)[:300], "error_type": type(exc).__name__}


def _disable_invalid_push_token(cur: Any, user_id: int, device: dict[str, Any], reason: str) -> None:
    try:
        token = str(device.get("push_token") or "")
        endpoint = str(device.get("endpoint") or "")
        device_id = str(device.get("device_id") or "")
        cur.execute(
            """
            UPDATE notification_device_tokens
            SET enabled=0, deleted_at=?, updated_at=?
            WHERE user_id=? AND (push_token=? OR endpoint=? OR device_id=?)
            """,
            (now_iso(), now_iso(), int(user_id), token, endpoint, device_id),
        )
        logging.info(
            "PULSESOC_NOTIFICATION_INVALID_PUSH_TOKEN_DISABLED user_id=%s device_id=%s provider=%s reason=%s",
            user_id,
            device_id[:80],
            str(device.get("push_provider") or device.get("platform") or "")[:40],
            str(reason or "invalid_device")[:120],
        )
    except Exception:
        logging.exception("PULSESOC_NOTIFICATION_INVALID_PUSH_TOKEN_DISABLE_FAILED user_id=%s", user_id)


def _dispatch_push(cur: Any, notification: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any]:
    user_id = int(notification.get("recipient_user_id") or notification.get("user_id") or 0)
    devices = _active_device_tokens(cur, user_id)
    legacy_count = _legacy_push_subscription_count(cur, user_id)
    if not devices and not legacy_count:
        return {"ok": False, "status": "skipped_no_device", "provider": "push_router", "message": "No active push device or subscription."}
    payload = _push_payload(notification, prefs)
    preview_body = _notification_public_preview(notification, prefs)
    results = []
    if legacy_count:
        results.append(push_service.send_push(user_id, notification.get("title") or "PulseSoc", preview_body, payload, push_type=str(notification.get("type") or "notification")))
    fcm_tokens = [d for d in devices if (str(d.get("push_provider") or "").lower() == "fcm" or str(d.get("platform") or "").lower() == "android") and d.get("push_token")]
    apns_tokens = [d for d in devices if (str(d.get("push_provider") or "").lower() == "apns" or str(d.get("platform") or "").lower() == "ios") and d.get("push_token")]
    for device in fcm_tokens:
        result = _send_fcm_token(str(device.get("push_token") or ""), notification, {**payload, "body": preview_body})
        if result.get("status") == "invalid_device":
            _disable_invalid_push_token(cur, user_id, device, result.get("message") or "invalid_fcm_token")
        results.append(result)
    for device in apns_tokens:
        result = _send_apns_token(str(device.get("push_token") or ""), notification, {**payload, "body": preview_body})
        if result.get("status") == "invalid_device":
            _disable_invalid_push_token(cur, user_id, device, result.get("message") or "invalid_apns_token")
        results.append(result)
    if not results:
        return {"ok": False, "status": "skipped_no_device", "provider": "push_router", "message": "No deliverable push token was found."}
    if any(result.get("ok") for result in results):
        return {"ok": True, "status": "sent", "provider": "push_router", "provider_response": results, "sent": sum(1 for r in results if r.get("ok"))}
    statuses = {str(result.get("status") or "") for result in results}
    if statuses <= {"config_missing", "not_configured"}:
        return {"ok": False, "status": "config_missing", "provider": "push_router", "message": "Push providers are not configured.", "provider_response": results}
    if "invalid_device" in statuses or "invalid" in statuses:
        return {"ok": False, "status": "invalid_device", "provider": "push_router", "message": "One or more push tokens are invalid.", "provider_response": results}
    if "not_configured" in statuses and not _web_push_configured() and not _fcm_configured() and not _apns_configured():
        return {"ok": False, "status": "config_missing", "provider": "push_router", "message": "Push provider variables are missing.", "provider_response": results}
    return {"ok": False, "status": "failed", "provider": "push_router", "message": "Push delivery failed.", "provider_response": results}


def _notification_email_html(notification: dict[str, Any], body: str) -> str:
    title = html.escape(str(notification.get("title") or "PulseSoc update"))
    safe_body = html.escape(body).replace("\n", "<br>")
    link = sanitize_deep_link(notification.get("deep_link") or "/pulse/notifications")
    return (
        "<div style='font-family:Inter,system-ui,sans-serif;background:#050b14;color:#f2fbff;padding:24px'>"
        "<div style='max-width:620px;margin:auto;border:1px solid rgba(110,223,246,.28);border-radius:18px;padding:22px;background:#081323'>"
        f"<h1 style='margin:0 0 12px'>{title}</h1><p style='line-height:1.55;color:#cfe8f4'>{safe_body}</p>"
        f"<p><a href='https://pulsesoc.com{html.escape(link)}' style='display:inline-block;background:#36e58f;color:#041019;text-decoration:none;font-weight:800;padding:12px 16px;border-radius:12px'>Open PulseSoc</a></p>"
        "<p style='font-size:12px;color:#9fb5c0'>You received this because your PulseSoc notification settings allow this alert.</p>"
        "</div></div>"
    )


def _dispatch_email(cur: Any, notification: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any]:
    category = str(notification.get("category") or "system")
    priority = str(notification.get("priority") or "normal")
    metadata = notification.get("metadata") if isinstance(notification.get("metadata"), dict) else {}
    if category not in EMAIL_DEFAULT_CATEGORIES and priority not in {"urgent", "high"} and not metadata.get("email_allowed"):
        return {"ok": False, "status": "skipped_policy", "provider": "brevo_email", "message": "Email is limited to important notification categories."}
    if not _brevo_email_configured():
        return {"ok": False, "status": "config_missing", "provider": "brevo_email", "message": "Brevo email is not configured.", "provider_response": email_service.provider_status()}
    contact = _get_user_contact(cur, int(notification.get("recipient_user_id") or notification.get("user_id") or 0))
    if not contact.get("email"):
        return {"ok": False, "status": "skipped_no_contact", "provider": "brevo_email", "message": "Recipient email is missing."}
    body = _notification_public_preview(notification, prefs)
    result = email_service.send_email(
        contact["email"],
        str(notification.get("title") or "PulseSoc update")[:180],
        _notification_email_html(notification, body),
        body,
        email_type=str(notification.get("type") or "notification"),
        user_id=int(notification.get("recipient_user_id") or notification.get("user_id") or 0),
        metadata={"notification_id": notification.get("id"), "category": category},
        channel="security" if category == "security" else "transactional",
    )
    if result.get("ok"):
        return {"ok": True, "status": "sent", "provider": "brevo_email", "provider_message_id": result.get("message_id") or "", "provider_response": result}
    status = "config_missing" if result.get("error_code") == "brevo_not_configured" else "failed"
    return {"ok": False, "status": status, "provider": "brevo_email", "message": result.get("error") or "Brevo email failed.", "provider_response": result}


def _dispatch_sms(cur: Any, notification: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any]:
    category = str(notification.get("category") or "system")
    priority = str(notification.get("priority") or "normal")
    metadata = notification.get("metadata") if isinstance(notification.get("metadata"), dict) else {}
    if category not in SMS_DEFAULT_CATEGORIES and priority != "urgent" and not metadata.get("sms_allowed"):
        return {"ok": False, "status": "skipped_policy", "provider": "brevo_sms", "message": "SMS is limited to urgent or explicitly enabled notification categories."}
    contact = _get_user_contact(cur, int(notification.get("recipient_user_id") or notification.get("user_id") or 0))
    phone = sms_service.normalize_phone(contact.get("phone") or "")
    if not phone or not contact.get("phone_verified") or not contact.get("sms_opt_in"):
        return {"ok": False, "status": "skipped_no_contact", "provider": "brevo_sms", "message": "Verified SMS opt-in phone number is missing."}
    if not _brevo_sms_configured():
        return {"ok": False, "status": "config_missing", "provider": "brevo_sms", "message": "Brevo SMS is not configured."}
    text = f"PulseSoc: {_notification_public_preview(notification, prefs)} {sanitize_deep_link(notification.get('deep_link') or '/pulse/notifications')}"[:480]
    result = sms_service.send_sms(phone, text, purpose=str(notification.get("type") or "notification"), user_id=int(notification.get("recipient_user_id") or notification.get("user_id") or 0))
    if result.get("ok"):
        return {"ok": True, "status": "sent", "provider": "brevo_sms", "provider_response": result}
    status = result.get("status") or "failed"
    mapped = "config_missing" if status in {"not_configured"} else "skipped_no_contact" if status in {"invalid_phone"} else "failed"
    return {"ok": False, "status": mapped, "provider": "brevo_sms", "message": result.get("message") or "Brevo SMS failed.", "provider_response": result}


def _dispatch_job(cur: Any, job: dict[str, Any], notification: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any]:
    channel = str(job.get("channel") or "in_app")
    if channel == "in_app":
        return {"ok": True, "status": "sent", "provider": "pulse_in_app", "message": "In-app notification is available."}
    if channel == "push":
        return _dispatch_push(cur, notification, prefs)
    if channel == "email":
        return _dispatch_email(cur, notification, prefs)
    if channel == "sms":
        return _dispatch_sms(cur, notification, prefs)
    if channel == "system":
        return {"ok": True, "status": "sent", "provider": "internal", "message": "System notification recorded."}
    return {"ok": False, "status": "config_missing", "provider": PROVIDER_PLACEHOLDERS.get(channel, channel), "message": f"{channel} adapter is not enabled yet."}


def _update_notification_delivery_state(cur: Any, notification_id: int) -> None:
    cur.execute("SELECT status FROM notification_delivery_jobs WHERE notification_id=?", (int(notification_id),))
    statuses = [str(_row_get(row, "status", row[0] if row else "") or "") for row in cur.fetchall()]
    if not statuses:
        return
    remaining = [status for status in statuses if status in {"queued", "scheduled", "retry"}]
    sent = [status for status in statuses if status in {"sent", "ready"}]
    skipped = [status for status in statuses if status.startswith("skipped") or status == "config_missing"]
    failed = [status for status in statuses if status in {"failed", "invalid_device"}]
    if remaining:
        state = "queued"
    elif sent and not failed:
        state = "delivered"
    elif sent:
        state = "partial"
    elif skipped and not failed:
        state = "skipped"
    else:
        state = "failed"
    now = now_iso()
    cur.execute(
        """
        UPDATE notifications
        SET delivery_status=?, delivered_at=CASE WHEN ? IN ('delivered','partial') THEN COALESCE(delivered_at, ?) ELSE delivered_at END,
            failed_at=CASE WHEN ?='failed' THEN COALESCE(failed_at, ?) ELSE failed_at END,
            updated_at=?
        WHERE id=?
        """,
        (state, state, now, state, now, now, int(notification_id)),
    )


def _record_job_result(cur: Any, job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    now = now_iso()
    status = str(result.get("status") or ("sent" if result.get("ok") else "failed"))
    retry_count = _int(job.get("retry_count"), 0)
    max_attempts = max(1, _int(job.get("max_attempts"), 3))
    if status in TEMPORARY_FAILURE_STATUSES and retry_count + 1 < max_attempts:
        final_status = "retry"
        next_retry_at = _delivery_retry_at(retry_count + 1)
        failed_at = ""
    else:
        final_status = status
        next_retry_at = ""
        failed_at = now if not result.get("ok") and final_status not in {"sent", "ready"} else ""
    provider = str(result.get("provider") or job.get("provider") or PROVIDER_PLACEHOLDERS.get(str(job.get("channel") or ""), ""))[:80]
    provider_message_id = str(result.get("provider_message_id") or "")[:240]
    message = str(result.get("message") or result.get("error") or result.get("failure_reason") or "")[:1000]
    cur.execute(
        """
        UPDATE notification_delivery_jobs
        SET status=?, provider=?, retry_count=?, next_retry_at=?, attempted_at=?, sent_at=?,
            failed_at=?, failed_reason=?, failure_reason=?, provider_message_id=?, provider_response_json=?, updated_at=?
        WHERE id=?
        """,
        (
            final_status,
            provider,
            retry_count + (0 if final_status in {"sent", "ready"} else 1),
            next_retry_at,
            now,
            now if final_status == "sent" else "",
            failed_at,
            message,
            message,
            provider_message_id,
            _json_dumps(result),
            now,
            int(job.get("id") or 0),
        ),
    )
    _update_notification_delivery_state(cur, int(job.get("notification_id") or 0))
    return {"job_id": int(job.get("id") or 0), "channel": job.get("channel"), "status": final_status, "provider": provider}


def process_delivery_jobs(limit: int = 50, channels: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    normalized_channels = [ADAPTER_CHANNEL_ALIASES.get(str(channel).lower(), str(channel).lower()) for channel in (channels or [])]
    clauses = ["status IN ('queued','scheduled','retry')", "(scheduled_at IS NULL OR scheduled_at='' OR scheduled_at<=?)", "(next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?)"]
    params: list[Any] = [now, now]
    if normalized_channels:
        placeholders = ",".join(["?"] * len(normalized_channels))
        clauses.append(f"channel IN ({placeholders})")
        params.extend(normalized_channels)
    params.append(max(1, min(int(limit or 50), 100)))
    cur.execute(
        f"""
        SELECT *
        FROM notification_delivery_jobs
        WHERE {' AND '.join(clauses)}
        ORDER BY id ASC
        LIMIT ?
        """,
        tuple(params),
    )
    jobs = [dict(row) if hasattr(row, "keys") else {
        "id": row[0],
        "notification_id": row[1],
        "user_id": row[2],
        "recipient_user_id": row[3],
        "channel": row[4],
        "provider": row[5],
        "status": row[6],
        "retry_count": row[8],
        "max_attempts": row[9],
    } for row in cur.fetchall()]
    processed = []
    for job in jobs:
        cur.execute("SELECT * FROM notifications WHERE id=? AND recipient_user_id=? AND deleted_at IS NULL LIMIT 1", (int(job.get("notification_id") or 0), int(job.get("recipient_user_id") or job.get("user_id") or 0)))
        row = cur.fetchone()
        if not row:
            processed.append(_record_job_result(cur, job, {"ok": False, "status": "skipped", "provider": job.get("provider"), "message": "Notification no longer exists."}))
            conn.commit()
            continue
        notification = format_notification(row)
        pref_user_id = int(notification.get("recipient_user_id") or notification.get("user_id") or 0)
        ensure_user_notification_defaults(pref_user_id, conn=conn)
        prefs = _get_preferences_with_cursor(cur, pref_user_id)
        try:
            result = _dispatch_job(cur, job, notification, prefs)
        except Exception as exc:
            logging.exception("PULSESOC_NOTIFICATION_DELIVERY_JOB_FAILED job_id=%s", job.get("id"))
            result = {"ok": False, "status": "failed", "provider": job.get("provider"), "message": str(exc)[:300], "error_type": type(exc).__name__}
        processed.append(_record_job_result(cur, job, result))
        conn.commit()
    conn.close()
    counts: dict[str, int] = {}
    for item in processed:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"ok": True, "processed": len(processed), "results": processed, "counts": counts, "provider_status": _provider_ready_summary()}


def delivery_router_status() -> dict[str, Any]:
    return {
        "ok": True,
        "channels": sorted(DELIVERY_CHANNELS),
        "in_app": "ready",
        "push": "ready" if any(_provider_ready_summary()[provider]["ready"] for provider in ("web_push", "fcm", "apns")) else "config_missing",
        "email": "ready" if _provider_ready_summary()["brevo_email"]["ready"] else "config_missing",
        "sms": "ready" if _provider_ready_summary()["brevo_sms"]["ready"] else "config_missing",
        "call": "phase2_adapter_placeholder",
        "adapters": _provider_ready_summary(),
        "providers_configured": {key: value["ready"] for key, value in _provider_ready_summary().items()},
    }


def admin_simulate_notification(
    admin_user_id: int,
    recipient_user_id: int,
    event_type: str,
    title: str | None = None,
    body: str | None = None,
    deep_link: str | None = None,
    metadata: dict[str, Any] | None = None,
    channels: list[str] | tuple[str, ...] | None = None,
    deliver_now: bool = False,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    metadata.update({"admin_test": True, "admin_user_id": int(admin_user_id or 0)})
    result = intake_event(
        event_type=event_type,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="admin_test",
        source_id=str(admin_user_id or ""),
        title=title,
        body=body or "This is a PulseSoc notification foundation test.",
        deep_link=deep_link or "/pulse/notifications",
        metadata=metadata,
        channels=list(channels or ["in_app"]),
        dedupe_key=f"admin-test-{admin_user_id}-{recipient_user_id}-{event_type}-{secrets.token_hex(4)}",
    )
    if deliver_now:
        result["delivery_processing"] = process_delivery_jobs(limit=20)
    return result
