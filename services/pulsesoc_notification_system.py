"""PulseSoc notification operating-system foundation.

Phase 1 keeps delivery safe and queue-ready: events are normalized, user rules
are applied, in-app records are stored, delivery jobs are created, and push
provider adapters remain placeholders until Phase 2.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from services import db as db_service


PRIORITY_LEVELS = {"urgent", "high", "normal", "low"}
URGENCY_LEVELS = {"immediate", "standard", "deferred", "silent"}
DELIVERY_CHANNELS = {"in_app", "push", "email", "sms", "call", "system"}
PROVIDER_PLACEHOLDERS = {
    "push": "apns_fcm_web_push_phase2",
    "email": "brevo_email_phase2",
    "sms": "brevo_sms_twilio_phase2",
    "call": "callkit_android_call_phase2",
    "system": "internal",
    "in_app": "pulse_in_app",
}
NOISY_CHANNELS = {"push", "email", "sms", "call"}
SOCIAL_CATEGORIES = {"social", "messages", "comments", "mentions", "follows", "live"}
SENSITIVE_CATEGORIES = {"security", "payments", "billing"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


EVENT_DEFINITIONS: dict[str, dict[str, str]] = {
    "new_message": {"category": "messages", "priority": "high", "urgency": "immediate", "title": "New message"},
    "missed_call": {"category": "messages", "priority": "urgent", "urgency": "immediate", "title": "Missed call"},
    "incoming_call": {"category": "messages", "priority": "urgent", "urgency": "immediate", "title": "Incoming call"},
    "friend_request": {"category": "social", "priority": "normal", "urgency": "standard", "title": "New friend request"},
    "follow": {"category": "social", "priority": "normal", "urgency": "standard", "title": "New follower"},
    "like": {"category": "social", "priority": "normal", "urgency": "silent", "title": "New like"},
    "comment": {"category": "comments", "priority": "normal", "urgency": "standard", "title": "New comment"},
    "mention": {"category": "mentions", "priority": "high", "urgency": "immediate", "title": "You were mentioned"},
    "repost": {"category": "social", "priority": "normal", "urgency": "standard", "title": "New repost"},
    "quote": {"category": "social", "priority": "normal", "urgency": "standard", "title": "New quote"},
    "live_started": {"category": "live", "priority": "high", "urgency": "immediate", "title": "Live started"},
    "live_invite": {"category": "live", "priority": "high", "urgency": "immediate", "title": "Live invite"},
    "cohost_request": {"category": "live", "priority": "high", "urgency": "immediate", "title": "Co-host request"},
    "creator_payout": {"category": "creator", "priority": "high", "urgency": "standard", "title": "Creator payout update"},
    "verification_approved": {"category": "verification", "priority": "high", "urgency": "standard", "title": "Verification approved"},
    "verification_rejected": {"category": "verification", "priority": "high", "urgency": "standard", "title": "Verification update"},
    "subscription_renewal": {"category": "premium", "priority": "normal", "urgency": "standard", "title": "Subscription renewed"},
    "payment_failed": {"category": "payments", "priority": "urgent", "urgency": "immediate", "title": "Payment failed"},
    "founder_premium_activated": {"category": "premium", "priority": "high", "urgency": "standard", "title": "Founder Premium activated"},
    "security_login_alert": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Security login alert"},
    "new_device_login": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "New device login"},
    "password_changed": {"category": "security", "priority": "urgent", "urgency": "immediate", "title": "Password changed"},
    "crypto_price_alert": {"category": "crypto", "priority": "high", "urgency": "immediate", "title": "Crypto price alert"},
    "marketplace_order": {"category": "marketplace", "priority": "high", "urgency": "standard", "title": "Marketplace order"},
    "system_announcement": {"category": "system", "priority": "normal", "urgency": "standard", "title": "PulseSoc announcement"},
}

DEFAULT_CATEGORIES = sorted({definition["category"] for definition in EVENT_DEFINITIONS.values()} | {
    "system",
    "messages",
    "security",
    "social",
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
            push INTEGER DEFAULT 0,
            email INTEGER DEFAULT 0,
            telegram INTEGER DEFAULT 0,
            sms INTEGER DEFAULT 0,
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
            enable_push_notifications INTEGER DEFAULT 0,
            enable_notification_sound INTEGER DEFAULT 1,
            enable_notification_vibration INTEGER DEFAULT 1,
            notification_sound_type TEXT DEFAULT 'soft',
            updated_at TEXT,
            UNIQUE(user_id, category)
        )
        """
    )
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


def _preferences_from_rows(rows: list[Any]) -> dict[str, Any]:
    defaults = {
        category: {"in_app": True, "push": False, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True}
        for category in DEFAULT_CATEGORIES
    }
    experience = {
        "enable_push_notifications": False,
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
            "push": _bool(_row_get(row, "push", 0), False),
            "email": _bool(_row_get(row, "email", 0), False),
            "sms": _bool(_row_get(row, "sms", 0), False),
            "sound": _bool(_row_get(row, "sound", _row_get(row, "enable_notification_sound", 1)), True),
            "vibration": _bool(_row_get(row, "vibration", _row_get(row, "enable_notification_vibration", 1)), True),
            "lock_screen_preview": _bool(_row_get(row, "lock_screen_preview", 1), True),
        }
        if category != "global":
            defaults[category] = values
        if category == "global" or not experience.get("_loaded"):
            experience.update({
                "enable_push_notifications": _bool(_row_get(row, "enable_push_notifications", _row_get(row, "push", 0)), False),
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


def get_preferences(user_id: int) -> dict[str, Any]:
    conn = db_service.connect()
    ensure_schema(conn)
    cur = conn.cursor()
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
                normalize_category(str(category), "system_announcement"),
                1 if values.get("in_app", True) else 0,
                1 if values.get("push") else 0,
                1 if values.get("email") else 0,
                1 if values.get("sms") else 0,
                1 if values.get("sound", True) else 0,
                1 if values.get("vibration", True) else 0,
                1 if values.get("lock_screen_preview", True) else 0,
                now,
            ),
        )
    if experience:
        cur.execute(
            """
            INSERT INTO notification_preferences
            (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview,
             enable_push_notifications, enable_notification_sound, enable_notification_vibration,
             notification_sound_type, quiet_hours_enabled, quiet_hours_start, quiet_hours_end,
             muted_users_json, muted_conversations_json, blocked_users_json, updated_at)
            VALUES (?, 'global', 1, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if experience.get("enable_push_notifications") else 0,
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
    category_pref = (prefs.get("preferences") or {}).get(category) or {}
    requested = set(payload.get("channels") or ["in_app"])
    requested = {channel for channel in requested if channel in DELIVERY_CHANNELS} or {"in_app"}
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
                _json_dumps({"title": payload["title"], "body": payload["body"], "deep_link": payload["deep_link"], "metadata": payload["metadata"]}),
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
        prefs = _get_preferences_with_cursor(cur, recipient_user_id)
        rules = _rules_check(cur, payload, prefs)
        if not rules.get("allowed"):
            cur.execute(
                "UPDATE notification_events SET status='suppressed', suppression_reason=?, updated_at=? WHERE id=?",
                (rules.get("reason") or "suppressed", now_iso(), event_id),
            )
            conn.commit()
            return {"ok": True, "suppressed": True, "reason": rules.get("reason"), "event_id": event_id, "notification_id": 0}
        payload["event_id"] = event_id
        metadata_json = _json_dumps({**metadata, "event_id": event_id, "dedupe_key": payload["dedupe_key"]})
        cur.execute(
            """
            INSERT INTO notifications
            (user_id, notification_type, title, message, status, metadata, created_at, read_at,
             recipient_user_id, actor_user_id, type, category, priority, urgency, body, preview, deep_link,
             source_type, source_id, icon_url, avatar_url, metadata_json, seen_at, delivered_at, opened_at,
             failed_at, failure_reason, updated_at, deleted_at, dedupe_key, event_id, delivery_status)
            VALUES (?, ?, ?, ?, 'unread', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, '', ?, NULL, ?, ?, 'created')
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
                created_at,
                payload["dedupe_key"],
                event_id,
            ),
        )
        notification_id = _int(getattr(cur, "lastrowid", 0))
        payload["notification_id"] = notification_id
        jobs = _insert_delivery_jobs(cur, notification_id, payload, list(rules.get("channels") or ["in_app"]), bool(rules.get("quiet_hours")))
        pulse_id = _mirror_pulse_notification(cur, notification_id, payload)
        if pulse_id:
            metadata["pulse_notification_id"] = pulse_id
            cur.execute(
                "UPDATE notifications SET metadata_json=?, metadata=? WHERE id=?",
                (_json_dumps({**metadata, "event_id": event_id, "dedupe_key": payload["dedupe_key"], "pulse_notification_id": pulse_id}), _json_dumps(metadata), notification_id),
            )
        conn.commit()
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
    now = now_iso()
    token_hash = _token_hash(push_token, endpoint)
    cur.execute(
        """
        INSERT INTO notification_device_tokens
        (user_id, device_id, platform, push_token, endpoint, p256dh, auth, user_agent, app_version, enabled, token_hash, last_seen_at, created_at, updated_at, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)
        ON CONFLICT(user_id, device_id, platform) DO UPDATE SET
            push_token=excluded.push_token,
            endpoint=excluded.endpoint,
            p256dh=excluded.p256dh,
            auth=excluded.auth,
            user_agent=excluded.user_agent,
            app_version=excluded.app_version,
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
            token_hash,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    cur.execute(
        "SELECT id, platform, device_id, enabled, last_seen_at FROM notification_device_tokens WHERE user_id=? AND device_id=? AND platform=? LIMIT 1",
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


def delivery_router_status() -> dict[str, Any]:
    return {
        "ok": True,
        "channels": sorted(DELIVERY_CHANNELS),
        "in_app": "ready",
        "push": "phase2_adapter_placeholder",
        "email": "phase2_adapter_placeholder",
        "sms": "phase2_adapter_placeholder",
        "call": "phase2_adapter_placeholder",
        "providers_configured": {
            "apns": bool(os.getenv("APNS_KEY_ID") and os.getenv("APNS_TEAM_ID")),
            "fcm": bool(os.getenv("FCM_SERVER_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
            "web_push": bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY")),
            "brevo_email": bool(os.getenv("BREVO_API_KEY")),
            "brevo_sms": bool(os.getenv("BREVO_API_KEY") and os.getenv("BREVO_SMS_SENDER")),
            "twilio": bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN")),
        },
    }


def admin_simulate_notification(
    admin_user_id: int,
    recipient_user_id: int,
    event_type: str,
    title: str | None = None,
    body: str | None = None,
    deep_link: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    metadata.update({"admin_test": True, "admin_user_id": int(admin_user_id or 0)})
    return intake_event(
        event_type=event_type,
        recipient_user_id=int(recipient_user_id),
        actor_user_id=0,
        source_type="admin_test",
        source_id=str(admin_user_id or ""),
        title=title,
        body=body or "This is a PulseSoc notification foundation test.",
        deep_link=deep_link or "/pulse/notifications",
        metadata=metadata,
        channels=["in_app"],
        dedupe_key=f"admin-test-{admin_user_id}-{recipient_user_id}-{event_type}-{secrets.token_hex(4)}",
    )
