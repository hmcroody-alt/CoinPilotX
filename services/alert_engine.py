"""Production alert engine for CoinPlotXAI.

Alert rules are stored in the database, evaluated by a worker/manual endpoint,
and dispatched through the centralized notification services with delivery logs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import requests

from . import db as db_service, email_service, live_market_service, notification_service, pulsesoc_notification_system, push_service, sms_service, user_context


SUPPORTED_ALERT_TYPES = {
    "coin_price",
    "price",
    "move_24h",
    "volatility",
    "news",
    "scam_keyword",
    "prediction",
    "arena",
}

PRICE_ALERT_TYPES = {"coin_price", "price"}
CHANGE_ALERT_TYPES = {"move_24h", "volatility"}
DEFAULT_COOLDOWN_SECONDS = int(os.getenv("ALERT_DEFAULT_COOLDOWN_SECONDS", "900"))
_ALERT_SCHEMA_READY = False
_ALERT_SCHEMA_LOCK = threading.Lock()


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _utcnow():
    return datetime.utcnow()


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _json_loads(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_to_dict(row):
    return user_context.row_to_dict(row) or {}


def _sql_error_is_duplicate_column(exc):
    message = str(exc or "").lower()
    return "duplicate column" in message or "already exists" in message or "exists" in message


def _table_exists(cur, table_name):
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=? LIMIT 1", (table_name,))
        else:
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _table_columns(cur, table_name):
    if not _table_exists(cur, table_name):
        return set()
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
            (table_name,),
        )
        return {str(row[0]) for row in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table_name})")
    return {str(row[1]) for row in cur.fetchall()}


def _add_columns_if_missing(cur, table_name, columns):
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
            (table_name,),
        )
        existing = {str(row[0]) for row in cur.fetchall()}
    else:
        cur.execute(f"PRAGMA table_info({table_name})")
        existing = {str(row[1]) for row in cur.fetchall()}
    for column_name, definition in columns:
        if column_name in existing:
            continue
        try:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
            existing.add(column_name)
        except Exception as exc:
            if not _sql_error_is_duplicate_column(exc):
                raise


def ensure_alert_schema(conn=None):
    global _ALERT_SCHEMA_READY
    if _ALERT_SCHEMA_READY:
        return {"ok": True}
    with _ALERT_SCHEMA_LOCK:
        if _ALERT_SCHEMA_READY:
            return {"ok": True}
        result = _ensure_alert_schema_impl(conn)
        _ALERT_SCHEMA_READY = True
        return result


def _ensure_alert_schema_impl(conn=None):
    """Keep the alert engine schema additive and shared by workers + dashboards."""
    owns_connection = conn is None
    conn = conn or user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            alert_type TEXT,
            symbol TEXT,
            target TEXT,
            condition TEXT,
            threshold_value REAL,
            target_value REAL,
            channels_json TEXT,
            channels TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1,
            cooldown_seconds INTEGER DEFAULT 900,
            last_checked_at TEXT,
            last_triggered_at TEXT,
            trigger_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'user_created',
            source_ref TEXT,
            metadata TEXT,
            deleted_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _add_columns_if_missing(
        cur,
        "alert_rules",
        [
            ("target", "TEXT"),
            ("threshold_value", "REAL"),
            ("target_value", "REAL"),
            ("channels_json", "TEXT"),
            ("channels", "TEXT"),
            ("status", "TEXT DEFAULT 'active'"),
            ("active", "INTEGER DEFAULT 1"),
            ("cooldown_seconds", "INTEGER DEFAULT 900"),
            ("last_checked_at", "TEXT"),
            ("last_triggered_at", "TEXT"),
            ("trigger_count", "INTEGER DEFAULT 0"),
            ("source", "TEXT DEFAULT 'user_created'"),
            ("source_ref", "TEXT"),
            ("metadata", "TEXT"),
            ("deleted_at", "TEXT"),
            # Edge-trigger latch state. See `evaluate_alert_rule`.
            ("condition_state", "TEXT"),
            ("trigger_seq", "INTEGER DEFAULT 0"),
            ("last_observed_value", "REAL"),
            ("state_changed_at", "TEXT"),
            # Persistent-alert state. See `evaluate_alert_rule`: the value the
            # user was last actually notified about (distinct from the value
            # last *observed*), and the per-rule repeat policy measured against
            # it.
            ("last_notified_value", "REAL"),
            ("repeat_mode", "TEXT DEFAULT 'progress'"),
            # No column DEFAULT: NULL means "use DEFAULT_REPEAT_STEP_PERCENT".
            # A column default would bake today's policy into every existing row
            # at ALTER time, so changing the policy later would silently apply to
            # new rules only. Nothing in the product writes this column, so a
            # non-NULL value here is always an explicit operator override.
            ("repeat_step_percent", "REAL"),
            # Premium advanced alerts. `advanced_conditions` holds the JSON
            # payload {"operator":"AND"|"OR","conditions":[...]} (NULL for
            # every basic rule, which keeps the free path untouched),
            # `match_mode` mirrors the operator as "all"/"any", and
            # `advanced_state` persists per-condition last-observed values for
            # crossing semantics plus the last evaluation status — restart-safe
            # because it lives in the database, not in the worker process.
            ("advanced_conditions", "TEXT"),
            ("match_mode", "TEXT"),
            ("advanced_state", "TEXT"),
        ],
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_rule_id INTEGER,
            user_id INTEGER,
            watch_rule_id INTEGER,
            symbol TEXT,
            alert_type TEXT,
            condition TEXT,
            threshold_value REAL,
            observed_value REAL,
            title TEXT,
            body TEXT,
            status TEXT,
            message TEXT,
            metadata TEXT,
            notification_id INTEGER,
            delivery_job_id INTEGER,
            delivery_status TEXT,
            created_at TEXT
        )
        """
    )
    _add_columns_if_missing(
        cur,
        "alert_events",
        [
            ("alert_rule_id", "INTEGER"),
            ("symbol", "TEXT"),
            ("alert_type", "TEXT"),
            ("condition", "TEXT"),
            ("threshold_value", "REAL"),
            ("observed_value", "REAL"),
            ("message", "TEXT"),
            ("metadata", "TEXT"),
            ("notification_id", "INTEGER"),
            ("delivery_job_id", "INTEGER"),
            ("delivery_status", "TEXT"),
            # Stable per-crossing identity: "<rule_id>:<trigger_seq>". Unique across
            # retries, restarts and duplicate workers so one crossing can only ever
            # own one triggered event (and therefore one push).
            ("trigger_key", "TEXT"),
        ],
    )
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_events_trigger_key "
            "ON alert_events (trigger_key)"
        )
    except Exception:
        # A pre-existing database may hold duplicate rows from the level-triggered
        # era; the application-level guard in `_create_event` still holds.
        logging.warning("alert_events.trigger_key unique index could not be created.", exc_info=True)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_delivery_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            user_id INTEGER,
            channel TEXT,
            status TEXT,
            provider TEXT,
            provider_message_id TEXT,
            error_message TEXT,
            attempts INTEGER DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT,
            sent_at TEXT
        )
        """
    )
    conn.commit()
    _retire_legacy_repeat_step_default(conn)
    if owns_connection:
        conn.close()
    return {"ok": True}


def _retire_legacy_repeat_step_default(conn):
    """Clear the short-lived ``repeat_step_percent DEFAULT 0.25``.

    Environments that created the column while it still carried that default had
    every row backfilled with a materiality floor no user ever chose and no UI or
    API can set — the very gate that kept small qualifying moves silent. Resetting
    those rows to NULL hands them back to ``DEFAULT_REPEAT_STEP_PERCENT``.

    Idempotent, and narrow enough that any other value (a deliberate operator
    override) survives untouched. Runs after the schema DDL has been committed
    and rolls itself back on failure: on PostgreSQL a failed statement aborts the
    surrounding transaction, so a swallowed error here would otherwise take every
    later statement down with it.
    """
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE alert_rules SET repeat_step_percent=NULL WHERE repeat_step_percent=0.25"
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.warning(
            "alert_rules.repeat_step_percent legacy default could not be cleared.",
            exc_info=True,
        )


def _normalize_symbol(symbol):
    return (symbol or "BTC").strip().upper()[:24]


def _normalize_alert_type(alert_type):
    value = (alert_type or "coin_price").strip().lower()
    if value == "price":
        return "coin_price"
    if value not in SUPPORTED_ALERT_TYPES:
        return "coin_price"
    return value


def _normalize_condition(condition):
    value = (condition or "above").strip().lower()
    aliases = {
        "over": "above",
        "greater_than": "above",
        "under": "below",
        "less_than": "below",
        "changes": "volatility_above",
        "change": "volatility_above",
        "percent_change": "volatility_above",
        "percentage_change": "volatility_above",
        "volume_spike": "volatility_above",
        "market_cap_change": "volatility_above",
        "24h_move_above": "volatility_above",
    }
    return aliases.get(value, value)


def _normalize_channels(channels):
    if isinstance(channels, dict):
        normalized = {str(key): bool(value) for key, value in channels.items()}
    elif isinstance(channels, list):
        normalized = {str(channel): True for channel in channels if channel}
    elif channels:
        normalized = {str(channels): True}
    else:
        normalized = {"in_app": True}
    for key in ("in_app", "email", "push", "sms", "telegram"):
        normalized.setdefault(key, False)
    if not any(normalized.get(key) for key in ("in_app", "email", "push", "sms", "telegram")):
        normalized["in_app"] = True
    return {key: bool(normalized.get(key)) for key in ("in_app", "email", "push", "sms", "telegram")}


def _format_money(value):
    try:
        number = float(value)
    except Exception:
        return str(value or "")
    if number >= 100:
        return f"${number:,.0f}"
    return f"${number:,.4f}".rstrip("0").rstrip(".")


def _condition_label(condition):
    return {
        "above": "crossed above",
        "below": "crossed below",
        "moves_up_percent": "moved up more than",
        "moves_down_percent": "moved down more than",
        "volatility_above": "volatility crossed",
    }.get(condition, condition.replace("_", " "))


def _user_record(user_id):
    return user_context.get_user_by_id(user_id) or {}


def _telegram_token():
    return os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")


def _sms_provider_ready():
    return sms_service.is_sms_configured()


def _web_push_provider_ready():
    return bool(
        (os.getenv("WEB_PUSH_PUBLIC_KEY") or os.getenv("VAPID_PUBLIC_KEY"))
        and (os.getenv("WEB_PUSH_PRIVATE_KEY") or os.getenv("VAPID_PRIVATE_KEY"))
    )


def _apns_provider_ready():
    return all(os.getenv(key) for key in ("APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID"))


def _fcm_provider_ready():
    return bool(
        os.getenv("FCM_SERVER_KEY")
        or all(os.getenv(key) for key in ("FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY"))
    )


def _active_filter(columns):
    active_columns = [column for column in ("is_active", "active", "enabled") if column in columns]
    filters = [f"COALESCE({column},1)=1" for column in active_columns]
    if "deleted_at" in columns:
        filters.append("deleted_at IS NULL")
    return " AND ".join(filters)


def _push_delivery_inventory(user_id=None):
    """Return registered push routes without assuming every route is Web Push."""
    inventory = {"expo": 0, "web_push": 0, "apns": 0, "fcm": 0, "registered": 0}
    if not user_id:
        return inventory
    identities = set()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        subscription_columns = _table_columns(cur, "push_subscriptions")
        if "user_id" in subscription_columns and "endpoint" in subscription_columns:
            selected = ["endpoint"]
            if "subscription_json" in subscription_columns:
                selected.append("subscription_json")
            active_filter = _active_filter(subscription_columns)
            cur.execute(
                f"SELECT {', '.join(selected)} FROM push_subscriptions WHERE user_id=?"
                + (f" AND {active_filter}" if active_filter else ""),
                (int(user_id),),
            )
            for row in cur.fetchall():
                record = _row_to_dict(row)
                endpoint = str(record.get("endpoint") or "")
                subscription = _json_loads(record.get("subscription_json"), {}) or {}
                token = str(subscription.get("expo_push_token") or subscription.get("token") or endpoint)
                identity = token or endpoint
                if identity:
                    identities.add(identity)
                if token.startswith(("ExponentPushToken[", "ExpoPushToken[")):
                    inventory["expo"] += 1
                elif endpoint:
                    inventory["web_push"] += 1
        device_columns = _table_columns(cur, "notification_device_tokens")
        selected = [column for column in ("platform", "push_provider", "push_token", "endpoint") if column in device_columns]
        if "user_id" in device_columns and selected:
            active_filter = _active_filter(device_columns)
            cur.execute(
                f"SELECT {', '.join(selected)} FROM notification_device_tokens WHERE user_id=?"
                + (f" AND {active_filter}" if active_filter else ""),
                (int(user_id),),
            )
            for row in cur.fetchall():
                record = _row_to_dict(row)
                provider = str(record.get("push_provider") or "").strip().lower()
                platform = str(record.get("platform") or "").strip().lower()
                token = str(record.get("push_token") or record.get("endpoint") or "")
                if token:
                    identities.add(token)
                if provider == "expo" or token.startswith(("ExponentPushToken[", "ExpoPushToken[")):
                    inventory["expo"] += 1
                elif provider == "fcm" or (platform == "android" and provider not in {"web_push", "webpush"}):
                    inventory["fcm"] += 1
                elif provider == "apns" or (platform == "ios" and provider not in {"web_push", "webpush"}):
                    inventory["apns"] += 1
                elif token:
                    inventory["web_push"] += 1
    finally:
        conn.close()
    inventory["registered"] = len(identities)
    return inventory


def _push_provider_ready(user_id=None, inventory=None):
    inventory = inventory or _push_delivery_inventory(user_id)
    return bool(
        inventory["expo"]
        or (inventory["web_push"] and _web_push_provider_ready())
        or (inventory["apns"] and _apns_provider_ready())
        or (inventory["fcm"] and _fcm_provider_ready())
    )


def _push_subscription_count(user_id=None):
    conn = user_context.connect()
    cur = conn.cursor()
    columns = _table_columns(cur, "push_subscriptions")
    if not columns:
        conn.close()
        return 0
    active_filter = _active_filter(columns)
    where = []
    params = []
    if user_id and "user_id" in columns:
        where.append("user_id=?")
        params.append(int(user_id))
    if active_filter:
        where.append(active_filter)
    cur.execute("SELECT COUNT(*) AS total FROM push_subscriptions" + (" WHERE " + " AND ".join(where) if where else ""), tuple(params))
    row = _row_to_dict(cur.fetchone())
    conn.close()
    return int((row or {}).get("total") or 0)


def _telegram_connected_count():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM users WHERE telegram_chat_id IS NOT NULL")
    row = _row_to_dict(cur.fetchone())
    conn.close()
    return int((row or {}).get("total") or 0)


def _status_payload(ready, status, label, message, setup_url=""):
    return {
        "ready": bool(ready),
        "status": status,
        "label": label,
        "message": message,
        "setup_url": setup_url,
    }


def _recent_successful_delivery(user_id, channel, hours=168):
    conn = user_context.connect()
    cur = conn.cursor()
    since = (_utcnow() - timedelta(hours=hours)).isoformat()
    cur.execute(
        """
        SELECT created_at
        FROM notification_delivery_logs
        WHERE user_id=? AND channel=? AND status IN ('sent', 'created') AND created_at>=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, channel, since),
    )
    row = _row_to_dict(cur.fetchone())
    conn.close()
    return row


def _require_recent_test(payload, user_id, channel):
    if not payload.get("ready"):
        return payload
    recent = _recent_successful_delivery(user_id, channel)
    if recent:
        payload["last_success_at"] = recent.get("created_at")
        return payload
    payload.update({
        "ready": False,
        "status": "test_required",
        "label": "Test required",
        "message": f"{channel.replace('_', ' ').title()} needs a successful delivery test before it is marked Ready.",
    })
    return payload


def channel_readiness(user_id, browser_permission=None, require_recent_test=True):
    user = _user_record(user_id)
    push_inventory = _push_delivery_inventory(user_id)
    push_subscriptions = push_inventory["registered"]
    push_provider_ready = _push_provider_ready(user_id, push_inventory)
    sms_ready = _sms_provider_ready()
    telegram_token_ready = bool(_telegram_token())
    permission = (browser_permission or "").strip().lower()
    if permission == "denied":
        push = _status_payload(False, "permission_denied", "Failed", "Browser push permission is denied. Enable notifications in browser settings.", "/notifications")
    elif push_subscriptions <= 0:
        push = _status_payload(False, "not_configured", "Needs setup", "Enable Push Notifications before using push alerts.", "/notifications")
    elif not push_provider_ready:
        push = _status_payload(False, "not_configured", "Needs setup", "The registered push provider is not configured yet.", "/notifications")
    else:
        push = _status_payload(True, "ready", "Ready", "Push alerts are ready.", "/notifications")
    phone = user.get("phone_number") or user.get("phone")
    if not sms_ready:
        sms = _status_payload(False, "not_configured", "Needs setup", "SMS provider is not configured.", "/account/settings")
    elif not phone:
        sms = _status_payload(False, "not_configured", "Needs setup", "Add a phone number for SMS alerts.", "/account/settings")
    elif not user.get("phone_verified"):
        sms = _status_payload(False, "not_configured", "Needs setup", "Phone verification required.", "/account/settings")
    elif int(user.get("sms_opt_in") or 0) != 1:
        sms = _status_payload(False, "not_configured", "Needs setup", "Turn on SMS opt-in before using text alerts.", "/account/settings")
    else:
        sms = _status_payload(True, "ready", "Ready", "SMS alerts are ready.", "/account/settings")
    if not telegram_token_ready:
        telegram = _status_payload(False, "not_configured", "Needs setup", "Telegram bot token is not configured.", "/account/settings")
    elif not user.get("telegram_chat_id"):
        telegram = _status_payload(False, "not_configured", "Needs setup", "Connect Telegram Companion before using Telegram alerts.", "/account/settings")
    else:
        telegram = _status_payload(True, "ready", "Ready", "Telegram Companion is connected.", "/account/settings")
    email_ready = bool(user.get("email") and os.getenv("BREVO_API_KEY"))
    readiness = {
        "in_app": _status_payload(True, "ready", "Ready", "In-app alerts are always available.", "/notifications"),
        "email": _status_payload(email_ready, "ready" if email_ready else "not_configured", "Ready" if email_ready else "Needs setup", "Email alerts are ready." if email_ready else "Email provider or account email is missing.", "/account/settings"),
        "push": {
            **push,
            "subscription_count": push_subscriptions,
            "provider_configured": push_provider_ready,
            "vapid_configured": _web_push_provider_ready(),
            "routes": push_inventory,
        },
        "sms": {**sms, "provider_configured": sms_ready, "phone_configured": bool(phone), "phone_verified": bool(user.get("phone_verified")), "sms_opt_in": bool(int(user.get("sms_opt_in") or 0))},
        "telegram": {**telegram, "bot_configured": telegram_token_ready, "connected": bool(user.get("telegram_chat_id"))},
    }
    if require_recent_test:
        for channel in ("email", "push", "sms", "telegram"):
            readiness[channel] = _require_recent_test(readiness[channel], user_id, channel)
    return readiness


def validate_requested_channels(user_id, channels, browser_permission=None):
    channel_map = _normalize_channels(channels)
    readiness = channel_readiness(user_id, browser_permission=browser_permission)
    blocked = []
    for channel in ("push", "sms", "telegram"):
        if channel_map.get(channel) and not readiness[channel].get("ready"):
            blocked.append({"channel": channel, **readiness[channel]})
    if blocked:
        message = " ".join(item.get("message") or f"{item['channel']} needs setup." for item in blocked)
        return {"ok": False, "message": message, "blocked_channels": blocked, "channel_readiness": readiness}
    return {"ok": True, "channels": channel_map, "channel_readiness": readiness}


def _quiet_hours_active(user_id):
    prefs = notification_service.get_preferences(user_id).get("experience") or {}
    if not prefs.get("quiet_hours_enabled"):
        return False
    try:
        start_hour, start_min = [int(part) for part in str(prefs.get("quiet_hours_start") or "22:00").split(":")[:2]]
        end_hour, end_min = [int(part) for part in str(prefs.get("quiet_hours_end") or "07:00").split(":")[:2]]
        now = datetime.now().time()
        start = now.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
        end = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end
    except Exception:
        return False


def _public_rule(row):
    rule = dict(row or {})
    channels = _json_loads(rule.get("channels_json"), None)
    if channels is None:
        raw_channels = rule.get("channels")
        channels = _normalize_channels(raw_channels if isinstance(raw_channels, (dict, list)) else (raw_channels or "").split(",") if raw_channels else None)
    rule["channels"] = channels
    rule["threshold_value"] = rule.get("threshold_value") if rule.get("threshold_value") is not None else rule.get("target_value")
    rule["threshold"] = rule["threshold_value"]
    rule["target_value"] = rule["threshold_value"]
    rule["asset_symbol"] = _normalize_symbol(rule.get("symbol") or rule.get("asset_symbol") or rule.get("target"))
    rule["condition"] = _normalize_condition(rule.get("condition") or rule.get("condition_type"))
    rule["condition_type"] = rule["condition"]
    rule["notify_in_app"] = 1 if channels.get("in_app") else 0
    rule["notify_push"] = 1 if channels.get("push") else 0
    rule["notify_email"] = 1 if channels.get("email") else 0
    rule["notify_sms"] = 1 if channels.get("sms") else 0
    rule["notify_telegram"] = 1 if channels.get("telegram") else 0
    rule["source"] = rule.get("source") or "user_created"
    rule["source_ref"] = rule.get("source_ref") or ""
    rule["deleted_at"] = rule.get("deleted_at") or ""
    rule["active"] = 1 if (rule.get("status") or "active") == "active" else 0
    return rule


def create_alert_rule(
    user_id,
    alert_type="coin_price",
    symbol="BTC",
    condition="above",
    threshold=None,
    channels=None,
    target="",
    cooldown_seconds=None,
    source="user_created",
    source_ref="",
    metadata=None,
    connection=None,
    schema_ready=False,
):
    if not schema_ready:
        ensure_alert_schema(connection)
    alert_type = _normalize_alert_type(alert_type)
    symbol = _normalize_symbol(symbol or target)
    condition = _normalize_condition(condition)
    try:
        threshold_value = float(threshold)
    except Exception:
        return {"ok": False, "message": "Enter a valid alert threshold."}
    channel_map = _normalize_channels(channels)
    cooldown = int(cooldown_seconds or DEFAULT_COOLDOWN_SECONDS)
    now = _now()
    owns_connection = connection is None
    conn = connection or user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (user_id, alert_type, symbol, target, condition, threshold_value, target_value, channels_json, channels,
             status, active, cooldown_seconds, trigger_count, source, source_ref, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                alert_type,
                symbol,
                target or symbol,
                condition,
                threshold_value,
                threshold_value,
                json.dumps(channel_map),
                ",".join([channel for channel, enabled in channel_map.items() if enabled]),
                cooldown,
                str(source or "user_created")[:80],
                str(source_ref or "")[:160],
                json.dumps(metadata or {})[:4000],
                now,
                now,
            ),
        )
        alert_id = cur.lastrowid
        if owns_connection:
            conn.commit()
        cur.execute("SELECT * FROM alert_rules WHERE id=? AND user_id=? LIMIT 1", (alert_id, user_id))
        rule = _public_rule(_row_to_dict(cur.fetchone()))
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()
    return {"ok": True, "alert_id": alert_id, "alert": rule, "message": "Alert activated.", "warnings": channel_warnings(user_id, channel_map)}


def list_alert_rules(user_id, limit=100, include_deleted=False, symbol=None):
    """The caller's alert rules, newest and active first, capped at ``limit``.

    ``symbol`` narrows the query rather than the result, and the difference is the
    whole reason it exists. Callers used to read a page and filter it in memory, which
    is correct only while the page holds everything: an account with more rules than
    the cap would have its Bitcoin alerts filtered out of a window that never contained
    them. ``undx_agent_runtime.resolve_alert_reference`` decides "exactly one of your
    alerts matches" from this call, and asking the narrow question here is what lets it
    answer about a narrow set instead of refusing about a wide one.

    Optional and defaulted, so every existing caller keeps the behaviour it had.
    """
    ensure_alert_schema()
    reconcile_legacy_alerts(user_id=user_id)
    conn = user_context.connect()
    cur = conn.cursor()
    status_clause = "" if include_deleted else "AND COALESCE(status, 'active')!='deleted' AND deleted_at IS NULL"
    symbol_clause = ""
    parameters = [user_id]
    wanted = str(symbol or "").strip().upper()
    if wanted:
        symbol_clause = "AND UPPER(COALESCE(symbol, ''))=?"
        parameters.append(wanted)
    parameters.append(int(limit))
    cur.execute(
        f"""
        SELECT * FROM alert_rules
        WHERE user_id=? {status_clause} {symbol_clause}
        ORDER BY CASE WHEN COALESCE(status, 'active')='active' THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT ?
        """,
        tuple(parameters),
    )
    rows = [_public_rule(_row_to_dict(row)) for row in cur.fetchall()]
    conn.close()
    _attach_delivery_statuses(user_id, rows)
    return {"ok": True, "alerts": rows}


def _attach_delivery_statuses(user_id, rules):
    rule_ids = [int(rule.get("id") or 0) for rule in rules if rule.get("id")]
    if not rule_ids:
        return rules
    placeholders = ",".join(["?"] * len(rule_ids))
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT alert_rule_id, channel, status, error_message, created_at
        FROM notification_delivery_logs
        WHERE user_id=? AND alert_rule_id IN ({placeholders})
        ORDER BY id DESC
        """,
        (user_id, *rule_ids),
    )
    latest = {}
    for row in cur.fetchall():
        item = _row_to_dict(row)
        key = (item.get("alert_rule_id"), item.get("channel"))
        if key not in latest:
            latest[key] = item
    conn.close()
    for rule in rules:
        rule["delivery_statuses"] = {
            channel: latest.get((rule.get("id"), channel), {})
            for channel in ("in_app", "email", "push", "sms", "telegram")
        }
    return rules


def get_alert_rule(alert_id, user_id=None):
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    if user_id is None:
        cur.execute("SELECT * FROM alert_rules WHERE id=? LIMIT 1", (alert_id,))
    else:
        cur.execute("SELECT * FROM alert_rules WHERE id=? AND user_id=? LIMIT 1", (alert_id, user_id))
    row = _row_to_dict(cur.fetchone())
    conn.close()
    return _public_rule(row) if row else None


def pause_alert(rule_id, user_id):
    return _set_rule_status(rule_id, user_id, "paused", 0, "Alert paused.")


def resume_alert(rule_id, user_id):
    return _set_rule_status(rule_id, user_id, "active", 1, "Alert resumed.")


def delete_alert(rule_id, user_id):
    return _set_rule_status(rule_id, user_id, "deleted", 0, "Alert deleted.")


def _set_rule_status(rule_id, user_id, status, active, message):
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    deleted_at = _now() if status == "deleted" else None
    cur.execute(
        "UPDATE alert_rules SET status=?, active=?, deleted_at=?, updated_at=? WHERE id=? AND user_id=?",
        (status, active, deleted_at, _now(), rule_id, user_id),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(changed), "message": message if changed else "Alert not found.", "alert_id": rule_id, "status": status}


def update_alert_rule(rule_id, user_id, payload):
    ensure_alert_schema()
    rule = get_alert_rule(rule_id, user_id)
    if not rule:
        return {"ok": False, "message": "Alert not found."}
    symbol = _normalize_symbol(payload.get("assetSymbol") or payload.get("asset_symbol") or payload.get("symbol") or rule.get("symbol"))
    condition = _normalize_condition(payload.get("condition") or payload.get("condition_type") or rule.get("condition"))
    try:
        threshold = float(payload.get("targetValue") if payload.get("targetValue") not in (None, "") else payload.get("target_value") if payload.get("target_value") not in (None, "") else rule.get("threshold_value"))
    except Exception:
        return {"ok": False, "message": "Use a valid target value."}
    if threshold <= 0:
        return {"ok": False, "message": "Target value must be greater than zero."}
    channels = _normalize_channels(payload.get("channels") or {
        "push": bool(payload.get("notifyPush", rule.get("notify_push"))),
        "email": bool(payload.get("notifyEmail", rule.get("notify_email"))),
        "sms": bool(payload.get("notifySMS", rule.get("notify_sms"))),
        "in_app": bool(payload.get("notifyInApp", True if rule.get("notify_in_app") in (None, "") else rule.get("notify_in_app"))),
        "telegram": bool(payload.get("notifyTelegram", rule.get("notify_telegram"))),
    })
    alert_type = _normalize_alert_type(payload.get("alert_type") or ("move_24h" if condition in {"moves_up_percent", "moves_down_percent", "volatility_above"} else "coin_price"))
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE alert_rules
        SET alert_type=?, symbol=?, target=?, condition=?, threshold_value=?, target_value=?,
            channels_json=?, channels=?, metadata=?, updated_at=?
        WHERE id=? AND user_id=? AND COALESCE(status, 'active')!='deleted'
        """,
        (
            alert_type,
            symbol,
            symbol,
            condition,
            threshold,
            threshold,
            json.dumps(channels),
            ",".join([channel for channel, enabled in channels.items() if enabled]),
            json.dumps({"edited_from_dashboard": True, "previous_source": rule.get("source") or ""})[:4000],
            _now(),
            rule_id,
            user_id,
        ),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(changed), "message": "Alert updated." if changed else "Alert not found.", "alert_id": rule_id}


def duplicate_alert_rule(rule_id, user_id):
    ensure_alert_schema()
    rule = get_alert_rule(rule_id, user_id)
    if not rule or (rule.get("status") or "active") == "deleted":
        return {"ok": False, "message": "Alert not found."}
    result = create_alert_rule(
        user_id,
        alert_type=rule.get("alert_type") or "coin_price",
        symbol=rule.get("symbol") or rule.get("asset_symbol"),
        condition=rule.get("condition"),
        threshold=rule.get("threshold_value"),
        channels=rule.get("channels"),
        target=rule.get("target") or rule.get("symbol"),
        cooldown_seconds=rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS,
        source="duplicated",
        source_ref=f"alert_rules:{rule_id}",
        metadata={"duplicated_from": rule_id},
    )
    if result.get("ok"):
        result["message"] = "Alert duplicated."
    return result


def list_alert_events(user_id, limit=50, alert_id=None):
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    if alert_id:
        cur.execute(
            """
            SELECT e.*, r.channels_json
            FROM alert_events e
            LEFT JOIN alert_rules r ON r.id=e.alert_rule_id
            WHERE e.user_id=? AND e.alert_rule_id=?
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT ?
            """,
            (user_id, int(alert_id), int(limit)),
        )
    else:
        cur.execute(
            """
            SELECT e.*, r.channels_json
            FROM alert_events e
            LEFT JOIN alert_rules r ON r.id=e.alert_rule_id
            WHERE e.user_id=?
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
    rows = [_row_to_dict(row) for row in cur.fetchall()]
    for row in rows:
        row["channels"] = _json_loads(row.get("channels_json"), {})
    conn.close()
    return {"ok": True, "events": rows}


def _legacy_crypto_condition(value):
    condition = _normalize_condition(value)
    if condition in {"above", "below", "moves_up_percent", "moves_down_percent", "volatility_above"}:
        return condition
    return "above"


def _legacy_crypto_alert_type(condition):
    return "move_24h" if _normalize_condition(condition) in {"moves_up_percent", "moves_down_percent", "volatility_above"} else "coin_price"


def _legacy_channels(row):
    return _normalize_channels({
        "push": bool(int((row or {}).get("notify_push") if (row or {}).get("notify_push") not in (None, "") else 1)),
        "email": bool(int((row or {}).get("notify_email") or 0)),
        "sms": bool(int((row or {}).get("notify_sms") or 0)),
        "in_app": bool(int((row or {}).get("notify_in_app") if (row or {}).get("notify_in_app") not in (None, "") else 1)),
    })


def reconcile_legacy_alerts(user_id=None, limit=1000):
    """Import legacy dashboard crypto alerts into alert_rules without duplicating."""
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    imported = 0
    skipped = 0
    disabled = 0
    findings = {
        "source_of_truth": "alert_rules",
        "crypto_alerts_table_found": False,
        "user_alerts_table_found": False,
        "imported_crypto_alerts": 0,
        "skipped_existing": 0,
        "legacy_user_alerts_active": 0,
        "disabled_unmappable": 0,
    }
    try:
        if _table_exists(cur, "crypto_alerts"):
            findings["crypto_alerts_table_found"] = True
            where = "WHERE user_id=?" if user_id is not None else ""
            params = (int(user_id), int(limit)) if user_id is not None else (int(limit),)
            cur.execute(
                f"""
                SELECT * FROM crypto_alerts
                {where}
                ORDER BY id ASC
                LIMIT ?
                """,
                params,
            )
            for legacy in [_row_to_dict(row) for row in cur.fetchall()]:
                legacy_id = int(legacy.get("id") or 0)
                owner_id = int(legacy.get("user_id") or 0)
                symbol = _normalize_symbol(legacy.get("asset_symbol"))
                try:
                    threshold = float(legacy.get("target_value"))
                except Exception:
                    disabled += 1
                    continue
                if not legacy_id or not owner_id or not symbol or threshold <= 0:
                    disabled += 1
                    continue
                source_ref = f"crypto_alerts:{legacy_id}"
                cur.execute("SELECT id FROM alert_rules WHERE source_ref=? LIMIT 1", (source_ref,))
                if cur.fetchone():
                    skipped += 1
                    continue
                status = str(legacy.get("status") or "active").lower()
                active = 1 if status == "active" else 0
                condition = _legacy_crypto_condition(legacy.get("condition_type"))
                channels = _legacy_channels(legacy)
                now = _now()
                cur.execute(
                    """
                    INSERT INTO alert_rules
                    (user_id, alert_type, symbol, target, condition, threshold_value, target_value,
                     channels_json, channels, status, active, cooldown_seconds, last_triggered_at,
                     trigger_count, source, source_ref, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner_id,
                        _legacy_crypto_alert_type(condition),
                        symbol,
                        symbol,
                        condition,
                        threshold,
                        threshold,
                        json.dumps(channels),
                        ",".join([channel for channel, enabled in channels.items() if enabled]),
                        status if status in {"active", "paused", "expired", "triggered", "deleted"} else "active",
                        active,
                        DEFAULT_COOLDOWN_SECONDS,
                        legacy.get("last_triggered_at"),
                        "migrated_crypto_alerts",
                        source_ref,
                        json.dumps({"legacy_table": "crypto_alerts", "legacy_id": legacy_id, "note": legacy.get("note") or ""})[:4000],
                        legacy.get("created_at") or now,
                        now,
                    ),
                )
                imported += 1
        if _table_exists(cur, "user_alerts"):
            findings["user_alerts_table_found"] = True
            where = "WHERE user_id=? AND COALESCE(active, 1)=1" if user_id is not None else "WHERE COALESCE(active, 1)=1"
            params = (int(user_id),) if user_id is not None else ()
            cur.execute(f"SELECT COUNT(*) AS total FROM user_alerts {where}", params)
            findings["legacy_user_alerts_active"] = int((_row_to_dict(cur.fetchone()) or {}).get("total") or 0)
        conn.commit()
    finally:
        conn.close()
    findings["imported_crypto_alerts"] = imported
    findings["skipped_existing"] = skipped
    findings["disabled_unmappable"] = disabled
    return findings


def current_observed_value(rule):
    alert_type = _normalize_alert_type(rule.get("alert_type"))
    symbol = _normalize_symbol(rule.get("symbol") or rule.get("target"))
    if alert_type not in PRICE_ALERT_TYPES and alert_type not in CHANGE_ALERT_TYPES:
        return {"ok": False, "status": "skipped", "message": f"{alert_type.replace('_', ' ').title()} alerts are scaffolded and not monitored by the live price worker yet."}
    quote = live_market_service.get_crypto_quote(symbol)
    _record_observation_from_quote(quote)
    asset = quote.get("asset") or {}
    if not quote.get("ok") and alert_type in PRICE_ALERT_TYPES:
        return {"ok": False, "status": "error", "message": quote.get("message") or f"{symbol} quote unavailable."}
    if alert_type in CHANGE_ALERT_TYPES or _normalize_condition(rule.get("condition")) in {"moves_up_percent", "moves_down_percent", "volatility_above"}:
        value = asset.get("change_24h")
        metric = "24h_change_percent"
    else:
        value = asset.get("price")
        metric = "price"
    try:
        return {"ok": True, "symbol": symbol, "metric": metric, "value": float(value), "quote": quote}
    except Exception:
        return {"ok": False, "status": "error", "message": f"{symbol} live value is unavailable."}


def condition_matches(condition, observed_value, threshold_value):
    condition = _normalize_condition(condition)
    observed = float(observed_value)
    threshold = float(threshold_value)
    if condition == "above":
        return observed >= threshold
    if condition == "below":
        return observed <= threshold
    if condition == "moves_up_percent":
        return observed >= abs(threshold)
    if condition == "moves_down_percent":
        return observed <= -abs(threshold)
    if condition == "volatility_above":
        return abs(observed) >= abs(threshold)
    return False


#: Latch states persisted on ``alert_rules.condition_state``.
STATE_ARMED = "armed"
STATE_LATCHED = "latched"

#: Repeat policy persisted on ``alert_rules.repeat_mode``.
#:
#: ``progress`` (the default) keeps a latched rule monitoring and re-notifies
#: when the market moves materially *further* into the breached region.
#: ``once`` is the strict edge-trigger of the original latch: one notification
#: per crossing and nothing until the condition clears and re-crosses.
REPEAT_MODE_PROGRESS = "progress"
REPEAT_MODE_ONCE = "once"
DEFAULT_REPEAT_MODE = os.getenv("ALERT_DEFAULT_REPEAT_MODE", REPEAT_MODE_PROGRESS).strip().lower()

#: Minimum move, as a percent of the last notified value, before a latched rule
#: speaks again. ``0`` (the default) means *any* strictly-further move is news.
#:
#: This was briefly 0.25, which reads as a sensible noise filter but is not what
#: a price alert promises: at BTC ~$100,000 it silently demanded a ~$250 move, so
#: a user watching a threshold got nothing for a $1, $10 or $100 climb. The
#: anti-duplicate guarantee does not depend on this number — it comes from
#: ``_repeat_is_further`` requiring the value to be *strictly* further than the
#: one already notified, so re-observing an identical price is silent at any
#: setting. Operators who want a noise floor back can set it per environment.
DEFAULT_REPEAT_STEP_PERCENT = float(os.getenv("ALERT_DEFAULT_REPEAT_STEP_PERCENT", "0"))

#: Floor between two *repeat* notifications on one latched rule, in seconds.
#:
#: Deliberately separate from the per-rule ``cooldown_seconds``. That column is
#: user-facing and is set to 600-900s on live rules, where it means "do not
#: re-notify me every time this threshold is re-crossed" — a rate limit on
#: *crossings*. Reusing it for repeats made a 15-minute window swallow every
#: qualifying move in between, which is the behaviour this default exists to
#: avoid. ``0`` leaves the worker poll interval as the natural rate limit.
DEFAULT_REPEAT_MIN_SECONDS = max(0, int(os.getenv("ALERT_REPEAT_MIN_SECONDS", "0")))


def _repeat_is_further(condition, value, reference):
    """Is ``value`` deeper into the breached region than ``reference``?

    Only movement that worsens the breach counts. A rule for "BTC above
    $61,000" that notified at $61,500 should speak again at $64,000, but drifting
    back down to $61,100 is the market returning toward the threshold — the user
    already knows they are above it, so that is not news.
    """
    condition = _normalize_condition(condition)
    if condition in {"above", "moves_up_percent"}:
        return value > reference
    if condition in {"below", "moves_down_percent"}:
        return value < reference
    if condition == "volatility_above":
        return abs(value) > abs(reference)
    return False


def alert_repeat_progressed(condition, value, last_notified, step_percent):
    """Does ``value`` justify a repeat notification after ``last_notified``?

    Direction (``_repeat_is_further``) is the guard that always applies: it stops
    the alert from narrating a retreat back toward the threshold, and — because
    it demands *strictly* further — it is also what makes re-observing an
    already-notified value silent. That is the anti-duplicate rule.

    ``step_percent`` is an optional noise floor on top, ``0`` by default, in
    which case every strictly-further value qualifies. When set it is measured
    relative to ``last_notified`` because these are prices spanning many orders
    of magnitude, so a percentage means the same thing for a $0.42 altcoin and a
    $61,000 BTC where a fixed dollar step would not.
    """
    if last_notified is None:
        return False
    try:
        value = float(value)
        last_notified = float(last_notified)
        step_percent = float(step_percent)
    except (TypeError, ValueError):
        return False
    if not _repeat_is_further(condition, value, last_notified):
        return False
    if step_percent <= 0:
        # No noise floor configured: any strictly-further value is a new
        # observation and therefore news.
        return True
    magnitude = abs(value - last_notified)
    scale = abs(last_notified)
    if scale <= 0:
        # Percentages are meaningless around zero (a percent-change rule can sit
        # at exactly 0.00%), so fall back to comparing against the step directly.
        return magnitude >= step_percent
    return (magnitude / scale) * 100.0 >= step_percent


def _claim_repeat(rule_id, observed_value, expected_seq):
    """Atomically claim a repeat notification for an already-latched rule.

    The same concurrency problem as ``_claim_crossing``: several workers may
    evaluate one rule at once and each see the same qualifying progression. The
    guard here is optimistic concurrency on ``trigger_seq`` — the caller passes
    the sequence it read, and only the evaluator whose compare-and-set lands
    first advances it and owns the notification.

    ``trigger_seq`` is used rather than a NULL-safe compare on
    ``last_notified_value`` deliberately: it is a plain integer, so the
    predicate is identical on SQLite and PostgreSQL. (``IS`` vs
    ``IS NOT DISTINCT FROM`` differs between the two engines, and this codebase
    has already shipped one production outage from SQL that only ran on SQLite.)
    """
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_rules
            SET trigger_seq=COALESCE(trigger_seq, 0)+1,
                last_notified_value=?,
                last_observed_value=?,
                state_changed_at=?,
                updated_at=?
            WHERE id=? AND COALESCE(trigger_seq, 0)=?
            """,
            (observed_value, observed_value, _now(), _now(), rule_id, int(expected_seq or 0)),
        )
        claimed = int(getattr(cur, "rowcount", 0) or 0) > 0
        conn.commit()
        if not claimed:
            return None
        return int(expected_seq or 0) + 1
    finally:
        conn.close()


def _claim_crossing(rule_id, observed_value):
    """Atomically claim a false->true crossing for ``rule_id``.

    The alert worker can run in several processes at once (and a dashboard
    request can evaluate the same rule concurrently). Latching with a plain
    read-then-write would let two evaluators both observe ``armed`` and both
    fire. Instead we move ``armed -> latched`` with a single conditional UPDATE
    and let the database decide the winner: exactly one caller sees a non-zero
    rowcount, and only that caller sends the notification.

    Returns the claimed ``trigger_seq`` on success, or ``None`` if another
    evaluator already owns this crossing.
    """
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_rules
            SET condition_state=?,
                trigger_seq=COALESCE(trigger_seq, 0)+1,
                last_observed_value=?,
                last_notified_value=?,
                state_changed_at=?,
                updated_at=?
            WHERE id=? AND COALESCE(condition_state, '')<>?
            """,
            (STATE_LATCHED, observed_value, observed_value, _now(), _now(), rule_id, STATE_LATCHED),
        )
        claimed = int(getattr(cur, "rowcount", 0) or 0) > 0
        conn.commit()
        if not claimed:
            return None
        cur.execute("SELECT trigger_seq FROM alert_rules WHERE id=? LIMIT 1", (rule_id,))
        row = cur.fetchone()
        return int((row[0] if row else 0) or 0)
    finally:
        conn.close()


def _set_last_notified_value(rule_id, observed_value):
    """Seed the repeat baseline without notifying.

    Used for rules that were already latched before ``last_notified_value``
    existed, so a schema migration never manifests as a surprise notification.
    """
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE alert_rules SET last_notified_value=?, last_observed_value=?, updated_at=? WHERE id=?",
            (observed_value, observed_value, _now(), rule_id),
        )
        conn.commit()
    finally:
        conn.close()


def _set_condition_state(rule_id, state, observed_value):
    """Persist a non-firing state transition (arming / re-arming)."""
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_rules
            SET condition_state=?, last_observed_value=?, state_changed_at=?, updated_at=?
            WHERE id=?
            """,
            (state, observed_value, _now(), _now(), rule_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Premium advanced conditions (Premium Crypto Intelligence)
#
# Advanced rules live in the SAME alert_rules table and run through the SAME
# evaluator entry point (`evaluate_alert_rule`) and worker as basic rules —
# there is deliberately no second engine. A rule is "advanced" when its
# `advanced_conditions` column carries {"operator":"AND"|"OR","conditions":[..]}.
# Basic above/below rules keep the exact code path they had.
# ---------------------------------------------------------------------------

ADVANCED_MAX_CONDITIONS = 5
ADVANCED_WINDOW_MIN_MINUTES = 15
ADVANCED_WINDOW_MAX_MINUTES = 1440

ADVANCED_MARKET_CONDITION_TYPES = {
    "price_above",
    "price_below",
    "price_crosses_above",
    "price_crosses_below",
    "price_move_pct",
    "price_move_abs",
    "volume_above",
    "volume_below",
    "volume_move_pct",
    "market_cap_above",
    "market_cap_below",
    "market_cap_move_pct",
}
ADVANCED_PORTFOLIO_CONDITION_TYPES = {
    "portfolio_value_above",
    "portfolio_value_below",
    "portfolio_move_pct",
    "allocation_above",
}
ADVANCED_CONDITION_TYPES = ADVANCED_MARKET_CONDITION_TYPES | ADVANCED_PORTFOLIO_CONDITION_TYPES
#: Windowed (move) types: they evaluate against a real market_observations
#: sample near the window start, or record insufficient_data — never a guess.
ADVANCED_WINDOWED_TYPES = {
    "price_move_pct",
    "price_move_abs",
    "volume_move_pct",
    "market_cap_move_pct",
    "portfolio_move_pct",
}
ADVANCED_CROSSING_TYPES = {"price_crosses_above", "price_crosses_below"}
ADVANCED_DIRECTIONS = {"up", "down", "any"}
ADVANCED_FREQUENCIES = {"once", "every_crossing", "recurring"}
DEFAULT_ADVANCED_FREQUENCY = "every_crossing"


def validate_advanced_conditions(payload):
    """Strictly validate the advanced-conditions payload. NO eval(), unknown
    condition types rejected, at most :data:`ADVANCED_MAX_CONDITIONS` entries.

    Accepts ``{"operator": "AND"|"OR", "conditions": [...]}`` (a flat list) and
    returns ``{"ok": True, "operator": ..., "match": "all"|"any",
    "conditions": [normalized...]}`` or ``{"ok": False, "message": ...}``.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Advanced conditions must be an object."}
    operator = str(payload.get("operator") or "AND").strip().upper()
    if operator not in {"AND", "OR"}:
        return {"ok": False, "message": "Operator must be AND or OR."}
    raw_conditions = payload.get("conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        return {"ok": False, "message": "At least one condition is required."}
    if len(raw_conditions) > ADVANCED_MAX_CONDITIONS:
        return {"ok": False, "message": f"At most {ADVANCED_MAX_CONDITIONS} conditions per alert."}
    normalized = []
    for entry in raw_conditions:
        if not isinstance(entry, dict):
            return {"ok": False, "message": "Each condition must be an object."}
        condition_type = str(entry.get("type") or "").strip().lower()
        if condition_type not in ADVANCED_CONDITION_TYPES:
            return {"ok": False, "message": f"Unknown condition type: {condition_type or '(missing)'}."}
        try:
            threshold = float(entry.get("threshold"))
        except (TypeError, ValueError):
            return {"ok": False, "message": f"Condition {condition_type} needs a numeric threshold."}
        if threshold != threshold or threshold in (float("inf"), float("-inf")):
            return {"ok": False, "message": f"Condition {condition_type} needs a finite threshold."}
        item = {"type": condition_type, "threshold": threshold}
        if condition_type in ADVANCED_WINDOWED_TYPES:
            try:
                window_minutes = int(entry.get("window_minutes"))
            except (TypeError, ValueError):
                return {"ok": False, "message": f"Condition {condition_type} needs window_minutes."}
            if not ADVANCED_WINDOW_MIN_MINUTES <= window_minutes <= ADVANCED_WINDOW_MAX_MINUTES:
                return {
                    "ok": False,
                    "message": (
                        f"window_minutes must be between {ADVANCED_WINDOW_MIN_MINUTES} "
                        f"and {ADVANCED_WINDOW_MAX_MINUTES}."
                    ),
                }
            item["window_minutes"] = window_minutes
            direction = str(entry.get("direction") or "any").strip().lower()
            if direction not in ADVANCED_DIRECTIONS:
                return {"ok": False, "message": f"direction must be one of {sorted(ADVANCED_DIRECTIONS)}."}
            item["direction"] = direction
        elif entry.get("window_minutes") not in (None, ""):
            return {"ok": False, "message": f"Condition {condition_type} does not take window_minutes."}
        normalized.append(item)
    return {
        "ok": True,
        "operator": operator,
        "match": "all" if operator == "AND" else "any",
        "conditions": normalized,
    }


def _is_advanced_rule(rule):
    return bool(_json_loads((rule or {}).get("advanced_conditions"), None))


def _advanced_payload(rule):
    """The validated advanced payload for a stored rule, or None."""
    stored = _json_loads(rule.get("advanced_conditions"), None)
    if not stored:
        return None
    validated = validate_advanced_conditions(stored)
    return validated if validated.get("ok") else None


def _rule_metadata(rule):
    return _json_loads((rule or {}).get("metadata"), None) or {}


def _advanced_frequency(rule):
    frequency = str(_rule_metadata(rule).get("frequency") or "").strip().lower()
    return frequency if frequency in ADVANCED_FREQUENCIES else DEFAULT_ADVANCED_FREQUENCY


def _load_advanced_state(rule):
    state = _json_loads((rule or {}).get("advanced_state"), None)
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("last_values"), dict):
        state["last_values"] = {}
    return state


def _save_advanced_state(rule_id, state):
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE alert_rules SET advanced_state=?, updated_at=? WHERE id=?",
            (json.dumps(state)[:4000], _now(), rule_id),
        )
        conn.commit()
    finally:
        conn.close()


def _note_advanced_status(rule, status, state=None):
    """Record the last evaluation status on the rule (restart-safe, no event
    spam): skipped rules are recorded, never deleted."""
    state = state if state is not None else _load_advanced_state(rule)
    state["last_status"] = status
    state["last_status_at"] = _now()
    _save_advanced_state(rule.get("id"), state)
    return state


def _observations_module():
    try:
        from . import market_observations

        return market_observations
    except ImportError:
        return None


def _record_observation_from_quote(quote):
    """Append a market observation from the worker's existing price fetch.

    Best-effort by design: observation recording must never break alert
    evaluation, and it fabricates nothing (record_quote refuses quotes without
    a real price).
    """
    module = _observations_module()
    if module is None:
        return
    try:
        module.record_quote(quote)
    except Exception:
        logging.debug("Market observation recording skipped.", exc_info=True)


def _advanced_alerts_capability(user_id):
    """Premium gate for advanced alerts. ImportError => no capability."""
    try:
        from . import crypto_premium_gate
    except ImportError:
        return False
    try:
        return bool(
            crypto_premium_gate.has_crypto_capability(
                user_id, crypto_premium_gate.CAP_CRYPTO_ADVANCED_ALERTS
            )
        )
    except Exception:
        return False


def _portfolio_valuation_for_alerts(user_id):
    """Current portfolio valuation via the sibling service, or None.

    ImportError / ok:False / any failure => None, and the caller records
    insufficient_data and skips.
    """
    try:
        from . import portfolio_intelligence
    except ImportError:
        return None
    try:
        result = portfolio_intelligence.compute_portfolio_valuation(user_id)
    except Exception:
        logging.info("Portfolio valuation unavailable for alert evaluation.", exc_info=True)
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return result


def _window_baseline(symbol, window_minutes, field):
    """The ``field`` value of the real observation nearest the window start
    (within the +/-20% tolerance), or None."""
    module = _observations_module()
    if module is None:
        return None
    try:
        observation = module.window_start_observation(symbol, window_minutes)
    except Exception:
        return None
    if not observation:
        return None
    value = observation.get(field)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _move_matches(current, baseline, threshold, direction, as_percent):
    """Shared windowed-move comparison. Returns True/False, or None when the
    percentage cannot be computed honestly (zero baseline)."""
    delta = current - baseline
    if as_percent:
        scale = abs(baseline)
        if scale <= 0:
            return None
        delta = (delta / scale) * 100.0
    magnitude = abs(float(threshold))
    if direction == "up":
        return delta >= magnitude
    if direction == "down":
        return delta <= -magnitude
    return abs(delta) >= magnitude


def _condition_summary(condition, matched, observed):
    label = condition["type"].replace("_", " ")
    threshold = condition.get("threshold")
    parts = f"{label} {threshold}"
    if condition.get("window_minutes"):
        parts += f" over {condition['window_minutes']}m"
    if observed is not None:
        try:
            parts += f" (observed {round(float(observed), 6)})"
        except (TypeError, ValueError):
            pass
    return parts


def _evaluate_advanced_condition(rule, index, condition, context, state):
    """Evaluate one advanced condition.

    Returns ``(matched, observed_value)`` where ``matched`` is True/False or
    None for insufficient data — an unknown never counts as a match and never
    counts as a miss.
    """
    condition_type = condition["type"]
    threshold = float(condition["threshold"])
    symbol = context["symbol"]
    asset = context.get("asset") or {}

    def _metric(name):
        value = asset.get(name)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if condition_type in ADVANCED_MARKET_CONDITION_TYPES:
        if not context.get("quote_ok"):
            return None, None
        if condition_type.startswith("price"):
            current = _metric("price")
        elif condition_type.startswith("volume"):
            current = _metric("volume_24h")
        else:
            current = _metric("market_cap")
        if current is None:
            return None, None
        if condition_type in {"price_above", "volume_above", "market_cap_above"}:
            return current >= threshold, current
        if condition_type in {"price_below", "volume_below", "market_cap_below"}:
            return current <= threshold, current
        if condition_type in ADVANCED_CROSSING_TYPES:
            key = str(index)
            previous = state["last_values"].get(key)
            state["last_values"][key] = current
            context["state_dirty"] = True
            try:
                previous = float(previous)
            except (TypeError, ValueError):
                # First observation of this condition: arm it, never fire.
                return False, current
            if condition_type == "price_crosses_above":
                return previous <= threshold and current > threshold, current
            return previous >= threshold and current < threshold, current
        # Windowed market move types.
        field = {
            "price_move_pct": "price",
            "price_move_abs": "price",
            "volume_move_pct": "volume_24h",
            "market_cap_move_pct": "market_cap",
        }[condition_type]
        baseline = _window_baseline(symbol, condition.get("window_minutes"), field)
        if baseline is None:
            return None, current
        matched = _move_matches(
            current,
            baseline,
            threshold,
            condition.get("direction") or "any",
            as_percent=condition_type != "price_move_abs",
        )
        return matched, current

    # Portfolio-backed conditions.
    portfolio = context.get("portfolio")
    if portfolio is None:
        return None, None
    try:
        total_value = float(portfolio.get("total_value"))
    except (TypeError, ValueError):
        return None, None
    if condition_type == "portfolio_value_above":
        return total_value >= threshold, total_value
    if condition_type == "portfolio_value_below":
        return total_value <= threshold, total_value
    if condition_type == "allocation_above":
        allocation = 0.0
        for holding in portfolio.get("holdings") or []:
            if _normalize_symbol(holding.get("symbol")) == symbol:
                try:
                    allocation = float(holding.get("allocation_pct") or 0.0)
                except (TypeError, ValueError):
                    allocation = 0.0
                break
        return allocation >= threshold, allocation
    # portfolio_move_pct: reconstruct the window-start portfolio value from
    # real per-asset observations — every priced holding must have one, else
    # the answer is honestly unknown.
    holdings = portfolio.get("holdings") or []
    if not holdings:
        return None, total_value
    baseline_total = 0.0
    for holding in holdings:
        holding_symbol = _normalize_symbol(holding.get("symbol"))
        try:
            amount = float(holding.get("amount"))
        except (TypeError, ValueError):
            return None, total_value
        if not holding_symbol:
            return None, total_value
        baseline_price = _window_baseline(holding_symbol, condition.get("window_minutes"), "price")
        if baseline_price is None:
            return None, total_value
        baseline_total += amount * baseline_price
    matched = _move_matches(
        total_value,
        baseline_total,
        threshold,
        condition.get("direction") or "any",
        as_percent=True,
    )
    return matched, total_value


def _combine_advanced_matches(match_mode, outcomes):
    """AND/OR combination where None (insufficient data) stays honest:
    - all: any False => False; else any None => None; else True.
    - any: any True => True; else any None => None; else False.
    """
    if match_mode == "any":
        if any(outcome is True for outcome in outcomes):
            return True
        if any(outcome is None for outcome in outcomes):
            return None
        return False
    if any(outcome is False for outcome in outcomes):
        return False
    if any(outcome is None for outcome in outcomes):
        return None
    return True


def _complete_once_rule(rule_id, user_id):
    """A frequency="once" advanced rule that has fired is completed — kept
    (never deleted) but no longer evaluated."""
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE alert_rules SET status='completed', active=0, updated_at=? WHERE id=? AND user_id=?",
            (_now(), rule_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _evaluate_advanced_rule(rule):
    """Advanced-rule evaluation inside the one canonical evaluator.

    Reuses the basic rules' armed/latched edge-trigger state machine, cooldown
    and trigger-key dedup; only the condition matching differs.
    """
    user_id = rule.get("user_id")
    rule_id = rule.get("id")
    if not _advanced_alerts_capability(user_id):
        _mark_checked(rule_id)
        _note_advanced_status(rule, "premium_required")
        return {
            "ok": True,
            "triggered": False,
            "skipped": True,
            "status": "premium_required",
            "message": "Advanced alert skipped: this account no longer has PulseSoc Premium. The rule is kept and will resume when Premium returns.",
        }
    payload = _advanced_payload(rule)
    if payload is None:
        _mark_checked(rule_id)
        _note_advanced_status(rule, "invalid_conditions")
        return {
            "ok": False,
            "triggered": False,
            "status": "invalid_conditions",
            "message": "Advanced alert conditions failed validation and were skipped.",
        }
    conditions = payload["conditions"]
    match_mode = str(rule.get("match_mode") or payload.get("match") or "all").strip().lower()
    if match_mode not in {"all", "any"}:
        match_mode = payload.get("match") or "all"
    symbol = _normalize_symbol(rule.get("symbol") or rule.get("target"))
    state = _load_advanced_state(rule)
    context = {"symbol": symbol, "asset": None, "quote_ok": False, "portfolio": None, "state_dirty": False}
    if any(condition["type"] in ADVANCED_MARKET_CONDITION_TYPES for condition in conditions):
        quote = live_market_service.get_crypto_quote(symbol)
        _record_observation_from_quote(quote)
        context["quote_ok"] = bool(quote.get("ok"))
        context["asset"] = quote.get("asset") or {}
    if any(condition["type"] in ADVANCED_PORTFOLIO_CONDITION_TYPES for condition in conditions):
        context["portfolio"] = _portfolio_valuation_for_alerts(user_id)

    outcomes = []
    observed_primary = None
    summaries = []
    for index, condition in enumerate(conditions):
        matched, observed = _evaluate_advanced_condition(rule, index, condition, context, state)
        outcomes.append(matched)
        if observed is not None and observed_primary is None:
            observed_primary = observed
        if matched:
            summaries.append(_condition_summary(condition, matched, observed))
    matched = _combine_advanced_matches(match_mode, outcomes)
    _mark_checked(rule_id)

    if matched is None:
        # Insufficient data: record and skip. Never guess a windowed baseline,
        # never treat an unavailable portfolio or quote as zero.
        state = _note_advanced_status(rule, "insufficient_data", state=state)
        return {
            "ok": True,
            "triggered": False,
            "skipped": True,
            "status": "insufficient_data",
            "observed_value": observed_primary,
            "message": "Advanced alert skipped: not enough real observed data to evaluate the window/conditions.",
        }

    state["last_status"] = "evaluated"
    state["last_status_at"] = _now()
    _save_advanced_state(rule_id, state)

    previous_state = str(rule.get("condition_state") or "").strip().lower()
    if not matched:
        if previous_state != STATE_ARMED:
            _set_condition_state(rule_id, STATE_ARMED, observed_primary)
        return {
            "ok": True,
            "triggered": False,
            "observed_value": observed_primary,
            "state": STATE_ARMED,
            "rearmed": previous_state == STATE_LATCHED,
            "message": "Advanced conditions not met." if previous_state != STATE_LATCHED else "Advanced conditions cleared; alert re-armed.",
        }

    frequency = _advanced_frequency(rule)
    message = f"{symbol} advanced alert: " + "; ".join(summaries or ["conditions met"])

    if previous_state == STATE_LATCHED:
        if frequency != "recurring":
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": observed_primary,
                "state": STATE_LATCHED,
                "message": "Advanced conditions still met from the already-notified crossing.",
            }
        last_triggered = _parse_dt(rule.get("last_triggered_at"))
        cooldown = int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
        if last_triggered and _utcnow() - last_triggered < timedelta(seconds=cooldown):
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "cooldown": True,
                "observed_value": observed_primary,
                "state": STATE_LATCHED,
                "message": f"Advanced conditions still met; recurring alert cooling down for {cooldown} seconds.",
            }
        repeat_seq = _claim_repeat(rule_id, observed_primary, rule.get("trigger_seq"))
        if repeat_seq is None:
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": observed_primary,
                "state": STATE_LATCHED,
                "message": "Recurring notification already claimed by a concurrent evaluation.",
            }
        return trigger_alert(rule, observed_primary, trigger_seq=repeat_seq, repeat=True, message=message)

    if previous_state != STATE_ARMED:
        # First observation: arm, never fire on creation.
        _set_condition_state(rule_id, STATE_ARMED, observed_primary)
        return {
            "ok": True,
            "triggered": False,
            "armed": True,
            "observed_value": observed_primary,
            "state": STATE_ARMED,
            "message": "Advanced alert armed on first observation; it will notify on the next genuine crossing.",
        }

    last_triggered = _parse_dt(rule.get("last_triggered_at"))
    cooldown = int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    if last_triggered and _utcnow() - last_triggered < timedelta(seconds=cooldown):
        return {
            "ok": True,
            "triggered": False,
            "cooldown": True,
            "observed_value": observed_primary,
            "state": previous_state,
            "message": f"Advanced conditions met, skipped during the {cooldown} second cooldown.",
        }
    trigger_seq = _claim_crossing(rule_id, observed_primary)
    if trigger_seq is None:
        return {
            "ok": True,
            "triggered": False,
            "latched": True,
            "observed_value": observed_primary,
            "state": STATE_LATCHED,
            "message": "Crossing already claimed by a concurrent evaluation; no duplicate notification sent.",
        }
    result = trigger_alert(rule, observed_primary, trigger_seq=trigger_seq, message=message)
    if result.get("triggered") and frequency == "once":
        _complete_once_rule(rule_id, user_id)
        result["completed"] = True
    return result


def evaluate_alert_rule(rule):
    """Evaluate one rule, firing at most once per genuine threshold crossing.

    This is edge-triggered, not level-triggered. The previous implementation
    fired whenever the condition *was* true and the cooldown had lapsed, so a
    rule like "BTC above $61,000" kept re-notifying every cooldown window for as
    long as BTC stayed above $61,000 — each push carrying a freshly observed
    price. On the device that reads as a banner that never goes away.

    The state machine, mirroring services/business_os/crypto/alerts.py:

      * No recorded state (a brand new rule, or one migrated from the
        level-triggered era) only *arms*. It never fires on its first
        observation, so creating "BTC above $61,000" while BTC already trades at
        $64,446 no longer produces an immediate, self-perpetuating alert.
      * ``armed`` + condition true  -> a real crossing: fire exactly once, latch.
      * ``latched`` + condition true -> still in region: notify again on every
        value strictly further into the breach than the last notified one, and
        stay silent on anything else (a repeat of the same observation, or a
        retreat back toward the threshold).
      * condition false             -> re-arm, ready for the next crossing.

    The per-rule ``cooldown_seconds`` rate limits *crossings*, not the repeats
    above: it stops a rule that oscillates across its threshold from notifying on
    every flap. Repeats are bounded by ``DEFAULT_REPEAT_MIN_SECONDS`` (0 by
    default) because their duplicate protection is a value comparison, not a
    timer.
    """
    if not rule:
        return {"ok": False, "triggered": False, "message": "Alert rule missing."}
    rule = _public_rule(rule)
    if (rule.get("status") or "active") != "active":
        return {"ok": True, "triggered": False, "message": "Alert is not active."}
    if _is_advanced_rule(rule):
        # Premium advanced rules share this entry point (one engine, one
        # worker) but branch here; the basic path below is untouched.
        return _evaluate_advanced_rule(rule)
    observed = current_observed_value(rule)
    if not observed.get("ok"):
        # A missing/failed quote must not disturb latch state, otherwise a single
        # provider blip would re-arm a latched rule and let it fire again.
        _mark_checked(rule["id"], status_message=observed.get("message"))
        if observed.get("status") == "error":
            _create_event(rule, None, "error", observed.get("message") or "Alert evaluation failed.")
        return {"ok": observed.get("status") != "error", "triggered": False, "message": observed.get("message") or "Alert skipped."}
    threshold = rule.get("threshold_value")
    value = observed["value"]
    matched = condition_matches(rule.get("condition"), value, threshold)
    _mark_checked(rule["id"])
    previous_state = str(rule.get("condition_state") or "").strip().lower()

    if not matched:
        if previous_state != STATE_ARMED:
            _set_condition_state(rule["id"], STATE_ARMED, value)
        return {
            "ok": True,
            "triggered": False,
            "observed_value": value,
            "state": STATE_ARMED,
            "rearmed": previous_state == STATE_LATCHED,
            "message": "Condition not met." if previous_state != STATE_LATCHED else "Condition cleared; alert re-armed for the next crossing.",
        }

    if previous_state == STATE_LATCHED:
        # Still inside the breached region. The rule stays active and keeps
        # monitoring: a latched rule is not a finished rule. It speaks again on
        # every observation that is strictly further into the breach than the
        # value the user was last notified about.
        #
        # "Strictly further than the last *notified* value" is the whole
        # anti-duplicate mechanism, and it is a comparison rather than a timer:
        # re-observing a price that has already been reported is silent no matter
        # how often the worker polls or how many workers poll at once, while any
        # genuinely new value is news. Rate limiting is deliberately not doing
        # this job — a time window cannot tell a repeated observation apart from
        # a real move, so it suppresses both.
        repeat_mode = str(rule.get("repeat_mode") or DEFAULT_REPEAT_MODE).strip().lower()
        if repeat_mode == REPEAT_MODE_ONCE:
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": value,
                "state": STATE_LATCHED,
                "message": "Condition still met from the already-notified crossing; this alert is set to notify once per crossing.",
            }
        step_percent = rule.get("repeat_step_percent")
        if step_percent is None:
            step_percent = DEFAULT_REPEAT_STEP_PERCENT
        # Rules latched before this column existed have no notified value to
        # measure against; adopt the current observation as the baseline so the
        # next genuine move is judged against something real rather than firing
        # immediately on migration.
        last_notified = rule.get("last_notified_value")
        if last_notified is None:
            _set_last_notified_value(rule["id"], value)
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": value,
                "state": STATE_LATCHED,
                "message": "Condition still met; repeat baseline recorded for this already-latched alert.",
            }
        if not alert_repeat_progressed(rule.get("condition"), value, last_notified, step_percent):
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": value,
                "state": STATE_LATCHED,
                "message": "Condition still met, but the value has not moved materially further since the last notification.",
            }
        repeat_min = DEFAULT_REPEAT_MIN_SECONDS
        if repeat_min > 0:
            last_triggered = _parse_dt(rule.get("last_triggered_at"))
            if last_triggered and _utcnow() - last_triggered < timedelta(seconds=repeat_min):
                return {
                    "ok": True,
                    "triggered": False,
                    "latched": True,
                    "cooldown": True,
                    "observed_value": value,
                    "state": STATE_LATCHED,
                    "message": f"Value moved further, skipped because repeats are rate limited to one per {repeat_min} seconds.",
                }
        repeat_seq = _claim_repeat(rule["id"], value, rule.get("trigger_seq"))
        if repeat_seq is None:
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": value,
                "state": STATE_LATCHED,
                "message": "Repeat already claimed by a concurrent evaluation; no duplicate notification sent.",
            }
        return trigger_alert(rule, value, trigger_seq=repeat_seq, repeat=True)

    if previous_state != STATE_ARMED:
        # First observation of this rule: record where the market sits without
        # firing, so only a subsequent genuine crossing notifies.
        _set_condition_state(rule["id"], STATE_ARMED, value)
        return {
            "ok": True,
            "triggered": False,
            "armed": True,
            "observed_value": value,
            "state": STATE_ARMED,
            "message": "Alert armed on first observation; it will notify on the next threshold crossing.",
        }

    last_triggered = _parse_dt(rule.get("last_triggered_at"))
    cooldown = int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    if last_triggered and _utcnow() - last_triggered < timedelta(seconds=cooldown):
        message = f"Condition met, skipped because alert is cooling down for {cooldown} seconds."
        return {"ok": True, "triggered": False, "cooldown": True, "observed_value": value, "state": previous_state, "message": message}

    trigger_seq = _claim_crossing(rule["id"], value)
    if trigger_seq is None:
        # Another worker/request won the same crossing; it owns the notification.
        return {
            "ok": True,
            "triggered": False,
            "latched": True,
            "observed_value": value,
            "state": STATE_LATCHED,
            "message": "Crossing already claimed by a concurrent evaluation; no duplicate notification sent.",
        }
    return trigger_alert(rule, value, trigger_seq=trigger_seq)


def _mark_checked(rule_id, status_message=""):
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE alert_rules SET last_checked_at=?, updated_at=? WHERE id=?", (_now(), _now(), rule_id))
    conn.commit()
    conn.close()


def _create_event(rule, observed_value, status, message, trigger_seq=None):
    """Record an alert event.

    When ``trigger_seq`` is supplied the event carries a stable ``trigger_key``
    of ``"<rule_id>:<trigger_seq>"`` — one key per genuine crossing, unchanged
    across retries, worker restarts and duplicate provider callbacks. If an
    event for that key already exists it is returned as-is rather than inserted
    again, so a replayed dispatch reuses the same event id (and therefore the
    same downstream dedupe key) instead of producing a second banner.

    The old key was a wall-clock bucket (``rule_id:now//cooldown``), which
    rotated on its own and so identified a *time window* rather than an event.
    """
    ensure_alert_schema()
    now = _now()
    trigger_key = f"{rule.get('id')}:{int(trigger_seq)}" if trigger_seq is not None else None
    conn = user_context.connect()
    cur = conn.cursor()
    if trigger_key:
        cur.execute("SELECT * FROM alert_events WHERE trigger_key=? LIMIT 1", (trigger_key,))
        existing = _row_to_dict(cur.fetchone())
        if existing:
            conn.close()
            existing["trigger_bucket"] = trigger_key
            existing["trigger_key"] = trigger_key
            existing["replayed"] = True
            return existing
    cur.execute(
        """
        INSERT INTO alert_events
        (alert_rule_id, user_id, symbol, alert_type, condition, threshold_value, observed_value,
         status, message, title, body, metadata, trigger_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule.get("id"),
            rule.get("user_id"),
            rule.get("symbol"),
            rule.get("alert_type"),
            rule.get("condition"),
            rule.get("threshold_value"),
            observed_value,
            status,
            message[:2000],
            f"{rule.get('symbol')} alert {status}",
            message[:2000],
            json.dumps({"rule_id": rule.get("id"), "observed_value": observed_value, "trigger_bucket": trigger_key})[:4000],
            trigger_key,
            now,
        ),
    )
    event_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM alert_events WHERE id=? LIMIT 1", (event_id,))
    event = _row_to_dict(cur.fetchone())
    if event:
        event["trigger_bucket"] = trigger_key or str(event.get("id") or "")
        event["trigger_key"] = trigger_key or ""
    conn.close()
    return event


def trigger_alert(rule, observed_value, trigger_seq=None, repeat=False, message=None):
    symbol = _normalize_symbol(rule.get("symbol"))
    condition = _normalize_condition(rule.get("condition"))
    threshold = rule.get("threshold_value")
    # "Value at crossing", not "live observed value": the notification describes a
    # single past event and is never refreshed with later prices, so wording that
    # implies a live feed would be misleading. A repeat is a different event —
    # the threshold was crossed earlier and the market has since moved further —
    # so it is labelled as a continuing move rather than a fresh crossing.
    moment = "Still moving" if repeat else "Value at crossing"
    if message:
        # Advanced rules describe themselves (multi-condition summaries); the
        # basic wording below stays exactly as it was.
        message = str(message)[:2000]
    elif condition in {"above", "below"}:
        message = f"{symbol} {_condition_label(condition)} {_format_money(threshold)}. {moment}: {_format_money(observed_value)}."
    else:
        message = f"{symbol} {_condition_label(condition)} {threshold}%. {moment}: {round(float(observed_value), 2)}%."
    if trigger_seq is None:
        # Direct/manual invocation (tests, admin replays) still gets a stable key.
        trigger_seq = _claim_crossing(rule.get("id"), observed_value)
        if trigger_seq is None:
            return {"ok": True, "triggered": False, "latched": True, "observed_value": observed_value, "message": "Crossing already claimed."}
    event = _create_event(rule, observed_value, "triggered", message, trigger_seq=trigger_seq)
    if event.get("replayed"):
        # The crossing was already recorded and dispatched; do not send again.
        return {"ok": True, "triggered": False, "deduped": True, "event": event, "observed_value": observed_value, "message": "Crossing already notified; duplicate suppressed."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE alert_rules
        SET last_triggered_at=?, trigger_count=COALESCE(trigger_count, 0)+1, updated_at=?
        WHERE id=?
        """,
        (_now(), _now(), rule.get("id")),
    )
    conn.commit()
    conn.close()
    delivery = dispatch_alert_event(event, rule)
    return {"ok": True, "triggered": True, "event": event, "delivery": delivery, "observed_value": observed_value, "message": message}


def _delivery_status_from_result(result):
    if not result:
        return "failed"
    if result.get("status"):
        return result.get("status")
    return "sent" if result.get("ok") else "failed"


def _central_crypto_alert_type(rule):
    alert_type = _normalize_alert_type((rule or {}).get("alert_type"))
    condition = _normalize_condition((rule or {}).get("condition"))
    if alert_type in PRICE_ALERT_TYPES:
        return "price_target_reached"
    if alert_type in CHANGE_ALERT_TYPES or condition in {"moves_up_percent", "moves_down_percent", "volatility_above"}:
        return "large_market_movement"
    if alert_type == "prediction":
        return "bot_signal"
    if alert_type == "arena":
        return "portfolio_milestone"
    if alert_type in {"news", "scam_keyword"}:
        return "critical_market_alert"
    return "price_target_reached"


def _central_crypto_priority(rule, channels):
    alert_type = _central_crypto_alert_type(rule)
    if alert_type == "critical_market_alert" or channels.get("sms"):
        return "urgent"
    return "high"


def _central_crypto_channels(channels, priority):
    requested = ["in_app"]
    if channels.get("push"):
        requested.append("push")
    if channels.get("email"):
        requested.append("email")
    if channels.get("sms") and priority == "urgent":
        requested.append("sms")
    return requested


def _central_status_for_channel(result, channel):
    if result.get("suppressed"):
        return "skipped_by_preference" if result.get("reason") == "channel_preferences_disabled" else result.get("reason") or "suppressed"
    if result.get("deduped"):
        return "duplicate"
    for job in result.get("delivery_jobs") or []:
        if job.get("channel") == channel:
            return job.get("status") or "queued"
    return "failed" if not result.get("ok") else "skipped_by_preference"


def _log_delivery(user_id, channel, status, provider="", provider_response="", error_message="", notification_id=None, alert_rule_id=None, alert_event_id=None):
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    setup_status = status in {"not_configured", "permission_denied", "disabled", "config_missing", "skipped_by_preference", "skipped_no_device", "suppressed", "duplicate"}
    if setup_status and alert_rule_id:
        cutoff = (_utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
        cur.execute(
            """
            SELECT id FROM notification_delivery_logs
            WHERE alert_rule_id=? AND channel=? AND status=? AND created_at>=?
            ORDER BY id DESC LIMIT 1
            """,
            (alert_rule_id, channel, status, cutoff),
        )
        if cur.fetchone():
            conn.close()
            return {"ok": True, "duplicate": True, "reason": "setup_status_throttled"}
    if alert_event_id:
        cur.execute(
            """
            SELECT id FROM notification_delivery_logs
            WHERE alert_event_id=? AND channel=?
            ORDER BY id DESC LIMIT 1
            """,
            (alert_event_id, channel),
        )
        if cur.fetchone():
            conn.close()
            return {"ok": True, "duplicate": True}
    retryable_job = not setup_status and status != "skipped"
    if alert_rule_id and retryable_job:
        cur.execute(
            """
            INSERT INTO alert_delivery_jobs
            (alert_id, user_id, channel, status, provider, provider_message_id, error_message,
             attempts, next_retry_at, created_at, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
            """,
            (
                alert_rule_id,
                user_id,
                channel,
                status,
                provider,
                "",
                str(error_message or "")[:1200],
                _now(),
                _now() if status in {"sent", "created", "skipped", "not_configured", "queued"} else None,
            ),
        )
    cur.execute(
        """
        INSERT INTO notification_delivery_logs
        (user_id, notification_id, alert_rule_id, alert_event_id, channel, status, provider,
         provider_response, error_message, retry_count, created_at, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            user_id,
            notification_id,
            alert_rule_id,
            alert_event_id,
            channel,
            status,
            provider,
            json.dumps(provider_response)[:4000] if isinstance(provider_response, (dict, list)) else str(provider_response or "")[:4000],
            str(error_message or "")[:1200],
            _now(),
            _now() if status in {"sent", "created", "skipped", "not_configured", "queued"} else None,
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "duplicate": False}


def _update_event_delivery(alert_event_id, notification_id, delivery):
    if not alert_event_id:
        return {"ok": False, "message": "Missing alert event."}
    statuses = []
    for channel, status in ((delivery or {}).get("channels") or {}).items():
        statuses.append(f"{channel}:{status}")
    summary = ",".join(statuses)[:500] or "created"
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE alert_events
        SET notification_id=?, delivery_status=?
        WHERE id=?
        """,
        (notification_id, summary, alert_event_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "delivery_status": summary}


def _telegram_send(user, title, body, metadata=None):
    token = _telegram_token()
    chat_id = (user or {}).get("telegram_chat_id")
    if not token:
        return {"ok": False, "status": "not_configured", "message": "Telegram bot token is not configured."}
    if not chat_id:
        return {"ok": False, "status": "not_configured", "message": "Telegram companion is not linked."}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{title}\n\n{body}"[:3500], "disable_web_page_preview": True},
            timeout=12,
        )
        ok = 200 <= response.status_code < 300
        return {"ok": ok, "status": "sent" if ok else "failed", "status_code": response.status_code, "response": response.text[:1000]}
    except Exception as exc:
        return {"ok": False, "status": "failed", "message": str(exc)}


def _crypto_intelligence_push_copy(event, rule, body):
    symbol = _normalize_symbol((rule or {}).get("symbol") or (event or {}).get("symbol"))
    signal = {
        "stream_key": "crypto_pulse",
        "asset_symbol": symbol,
        "headline": f"{symbol} crossed {_format_money((rule or {}).get('threshold_value'))}" if symbol else "Crypto signal",
        "summary": body or "A configured crypto alert was triggered.",
        "priority": "high",
        "metadata": {
            "asset_symbol": symbol,
            "status_card": {"asset": symbol} if symbol else {},
            "source": "crypto_alert",
        },
    }
    try:
        from services.pulsesoc_intelligence_engine import normalize_intelligence_alert_copy

        copy = normalize_intelligence_alert_copy(signal)
    except Exception:
        headline = f"{symbol} MARKET SIGNAL" if symbol else "CRYPTO MARKET SIGNAL"
        copy = {
            "lock_title": "PULSESOC ALERT",
            "lock_headline": headline,
            "lock_body": body or "A configured crypto alert was triggered.",
            "card_label": "PULSESOC ALERT",
            "card_category": "Crypto Signal",
            "card_priority_badge": "HIGH PRIORITY",
            "card_headline": headline,
            "card_summary": body or "A configured crypto alert was triggered.",
            "card_icon": "crypto",
            "accent": "gold",
            "ask_ai_prompts": ["Explain this", "Why does it matter?", "What should I do?"],
        }
    copy["lock_title"] = "PULSESOC ALERT"
    copy["lock_headline"] = str(copy.get("lock_headline") or "CRYPTO MARKET SIGNAL").strip().upper()[:64]
    if body:
        copy["lock_body"] = str(body).strip()[:180]
        copy["card_summary"] = copy["lock_body"]
    else:
        copy["lock_body"] = str(copy.get("lock_body") or "A configured crypto alert was triggered.").strip()[:180]
    return copy


def dispatch_alert_event(event, rule=None):
    ensure_alert_schema()
    event = dict(event or {})
    rule = _public_rule(rule or get_alert_rule(event.get("alert_rule_id")) or {})
    user_id = event.get("user_id") or rule.get("user_id")
    user = _user_record(user_id)
    channels = _normalize_channels(rule.get("channels") or _json_loads(rule.get("channels_json"), {}))
    body = event.get("message") or "Your PulseSoc alert condition was met."
    alert_copy = _crypto_intelligence_push_copy(event, rule, body)
    title = alert_copy["lock_title"]
    push_body = alert_copy["lock_body"]
    alert_id = rule.get("id") or event.get("alert_rule_id") or event.get("id") or ""
    event_id = event.get("id") or ""
    trigger_window = str(event.get("trigger_bucket") or event_id or alert_id or _now())
    dashboard_link = f"/dashboard/crypto/alerts?alert_id={alert_id}" if alert_id not in (None, "") else "/dashboard/crypto/alerts"
    metadata = {
        "url": dashboard_link,
        "deep_link": dashboard_link,
        "mobile_deep_link": f"pulse://alerts/{alert_id}".rstrip("/"),
        "push_type": "market_alert",
        "event_type": "crypto_alert_triggered",
        "alert_rule_id": rule.get("id"),
        "alert_event_id": event_id,
        "symbol": rule.get("symbol"),
        "observed_value": event.get("observed_value"),
        "target_price": rule.get("threshold_value"),
        "trigger_window": trigger_window,
        "type": "intelligence_pulse",
        "notification_type": "intelligence_pulse",
        "category": "intelligence",
        "source_type": "crypto_alert",
        "source_id": str(alert_id),
        "headline": alert_copy["lock_headline"],
        "alert_copy": alert_copy,
        "card_label": alert_copy.get("card_label"),
        "card_category": alert_copy.get("card_category"),
        "priority_badge": alert_copy.get("card_priority_badge"),
        "card_summary": alert_copy.get("card_summary"),
        "card_icon": alert_copy.get("card_icon"),
        "accent": alert_copy.get("accent"),
        "ask_ai_prompts": alert_copy.get("ask_ai_prompts"),
        "show_on_lock_screen": True,
        "badge": True,
    }
    delivery = {"ok": True, "channels": {}}
    notification_id = None
    alert_type = _central_crypto_alert_type(rule)
    priority = _central_crypto_priority(rule, channels)
    metadata["sound_key"] = "alert" if priority == "urgent" else "pulse_signal"
    metadata["vibration"] = "strong" if priority == "urgent" else "standard"
    central_channels = _central_crypto_channels(channels, priority)
    if central_channels:
        created = pulsesoc_notification_system.notify_crypto_alert(
            int(user_id),
            alert_id,
            title,
            push_body,
            _normalize_symbol(rule.get("symbol")),
            critical=priority == "urgent",
            metadata=metadata,
            alert_type=alert_type,
            trigger_price=event.get("observed_value"),
            target_price=rule.get("threshold_value"),
            direction=_normalize_condition(rule.get("condition")),
            priority=priority,
            deep_link=metadata["deep_link"],
            channels=central_channels,
            trigger_window=trigger_window,
        )
        notification_id = created.get("notification_id")
        delivery["central_notification"] = {
            "ok": bool(created.get("ok")),
            "notification_id": notification_id,
            "deduped": bool(created.get("deduped")),
            "suppressed": bool(created.get("suppressed")),
            "reason": created.get("reason") or "",
        }
        for channel in central_channels:
            status = _central_status_for_channel(created, channel)
            delivery["channels"][channel] = "created" if channel == "in_app" and status == "ready" else status
            _log_delivery(
                user_id,
                channel,
                delivery["channels"][channel],
                "pulsesoc_notification_system",
                created,
                created.get("message") or created.get("reason") or "",
                notification_id,
                rule.get("id"),
                event.get("id"),
            )
    external_sent = any(status in {"sent", "queued", "ready", "created", "duplicate", "scheduled"} for status in delivery["channels"].values())
    external_attempted = any(channels.get(channel) for channel in ("email", "push", "sms", "telegram"))
    readiness = channel_readiness(user_id)
    for channel in ("telegram",):
        if not channels.get(channel):
            continue
        external_attempted = True
        if not readiness["telegram"].get("ready"):
            result = {"ok": False, "status": "not_configured", "message": readiness["telegram"].get("message")}
            status = "not_configured"
        else:
            result = _telegram_send(user, title, body, metadata)
            status = _delivery_status_from_result(result)
        delivery["channels"][channel] = status
        external_sent = external_sent or status == "sent"
        _log_delivery(user_id, channel, status, "telegram", result, result.get("message"), notification_id, rule.get("id"), event.get("id"))
    if external_attempted and not external_sent and not channels.get("in_app"):
        created = pulsesoc_notification_system.notify_crypto_alert(
            int(user_id),
            alert_id,
            title,
            f"{push_body}\n\nSelected external channels need setup, so this in-app copy was created.",
            _normalize_symbol(rule.get("symbol")),
            metadata=metadata,
            alert_type=alert_type,
            trigger_price=event.get("observed_value"),
            target_price=rule.get("threshold_value"),
            direction=_normalize_condition(rule.get("condition")),
            priority=priority,
            deep_link=metadata["deep_link"],
            channels=["in_app"],
            trigger_window=trigger_window,
        )
        notification_id = created.get("notification_id")
        delivery["channels"]["in_app_fallback"] = "created" if created.get("ok") else "failed"
        _log_delivery(user_id, "in_app", delivery["channels"]["in_app_fallback"], "pulsesoc_notification_system", created, created.get("message"), notification_id, rule.get("id"), event.get("id"))
    _update_event_delivery(event.get("id"), notification_id, delivery)
    return delivery


def evaluate_all_active_alerts(limit=500, worker_name="alert_worker"):
    ensure_alert_schema()
    reconcile_legacy_alerts()
    module = _observations_module()
    if module is not None:
        try:
            # Retention for the observation samples recorded by this very
            # cycle's price fetches — cheap, throttled inside the module, and
            # requiring no second worker.
            module.maybe_prune_observations()
        except Exception:
            logging.debug("Market observation prune skipped.", exc_info=True)
    start = time.time()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM alert_rules
        WHERE COALESCE(status, CASE WHEN COALESCE(active, 1)=1 THEN 'active' ELSE 'paused' END)='active'
        ORDER BY CASE WHEN last_checked_at IS NULL THEN 0 ELSE 1 END, last_checked_at ASC, id ASC
        LIMIT ?
        """,
        (int(limit),),
    )
    rules = [_public_rule(_row_to_dict(row)) for row in cur.fetchall()]
    conn.close()
    checked = 0
    triggered = 0
    errors = 0
    last_error = ""
    for rule in rules:
        checked += 1
        try:
            result = evaluate_alert_rule(rule)
            if result.get("triggered"):
                triggered += 1
            if not result.get("ok"):
                errors += 1
                last_error = result.get("message", "")
        except Exception as exc:
            errors += 1
            last_error = str(exc)
            logging.exception("Alert rule evaluation failed rule_id=%s", rule.get("id"))
    record_worker_heartbeat(worker_name, checked, triggered, errors, last_error)
    return {
        "ok": True,
        "checked_count": checked,
        "triggered_count": triggered,
        "error_count": errors,
        "last_error": last_error,
        "latency_ms": round((time.time() - start) * 1000, 2),
    }


def send_test_alert(rule_id, user_id):
    rule = get_alert_rule(rule_id, user_id)
    if not rule:
        return {"ok": False, "message": "Alert not found."}
    value = rule.get("threshold_value")
    event = _create_event(rule, value, "triggered", f"Test alert for {_normalize_symbol(rule.get('symbol'))}. Delivery path check only; no market crossing required.")
    delivery = dispatch_alert_event(event, rule)
    return {"ok": True, "message": "Test alert sent.", "event": event, "delivery": delivery}


def test_delivery_channel(user_id, channel, client_state=None):
    channel = (channel or "").strip().lower()
    client_state = client_state or {}
    title = f"CoinPlotXAI {channel.title()} alert test"
    body = "This is a setup test for CoinPlotXAI alert delivery."
    metadata = {"url": "/notifications", "test": True, "channel": channel}
    readiness = channel_readiness(user_id, browser_permission=client_state.get("permission"), require_recent_test=False)
    user = _user_record(user_id)
    provider = channel
    if channel == "push":
        provider = "web_push"
        if not readiness["push"].get("ready"):
            status = "permission_denied" if readiness["push"].get("status") == "permission_denied" else "not_configured"
            result = {"ok": False, "status": status, "message": readiness["push"].get("message")}
        else:
            result = push_service.send_push(user_id, title, body, metadata, push_type="market_alert")
            status = _delivery_status_from_result(result)
            if status == "skipped":
                status = "not_configured"
    elif channel == "sms":
        provider = "brevo_sms"
        if not readiness["sms"].get("ready"):
            status = "not_configured"
            result = {"ok": False, "status": status, "message": readiness["sms"].get("message")}
        else:
            result = sms_service.send_test_sms(user_id)
            status = _delivery_status_from_result(result)
            if status == "skipped":
                status = "not_configured"
    elif channel == "telegram":
        provider = "telegram"
        if not readiness["telegram"].get("ready"):
            status = "not_configured"
            result = {"ok": False, "status": status, "message": readiness["telegram"].get("message")}
        else:
            result = _telegram_send(user, title, body, metadata)
            status = _delivery_status_from_result(result)
    elif channel == "email":
        provider = "brevo"
        if not readiness["email"].get("ready"):
            status = "not_configured"
            result = {"ok": False, "status": status, "message": readiness["email"].get("message")}
        else:
            result = email_service.send_email(user.get("email"), title, f"<p>{body}</p>", body, email_type="market_alerts", user_id=user_id)
            status = "sent" if result.get("ok") else "failed"
    elif channel == "in_app":
        provider = "database"
        result = notification_service.queue_notification(user_id, title, body, "market_alerts", metadata)
        status = "created" if result.get("ok") else "failed"
    else:
        return {"ok": False, "status": "failed", "message": "Unsupported channel."}
    _log_delivery(user_id, channel, status, provider, result, result.get("error") or result.get("message"))
    return {
        "ok": status in {"sent", "created"},
        "channel": channel,
        "status": status,
        "message": result.get("message") or readiness.get(channel, {}).get("message") or status,
        "delivery": result,
        "channel_readiness": channel_readiness(user_id, browser_permission=client_state.get("permission")),
    }


def record_worker_heartbeat(worker_name="alert_worker", checked_count=0, triggered_count=0, error_count=0, last_error=""):
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alert_worker_heartbeat
        (worker_name, last_run_at, last_success_at, checked_count, triggered_count, error_count, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(worker_name) DO UPDATE SET
            last_run_at=excluded.last_run_at,
            last_success_at=CASE WHEN excluded.error_count=0 THEN excluded.last_success_at ELSE alert_worker_heartbeat.last_success_at END,
            checked_count=excluded.checked_count,
            triggered_count=excluded.triggered_count,
            error_count=excluded.error_count,
            last_error=excluded.last_error
        """,
        (worker_name, now, now if not error_count else None, checked_count, triggered_count, error_count, str(last_error or "")[:1200]),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "worker_name": worker_name, "last_run_at": now}


def worker_health():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alert_worker_heartbeat ORDER BY last_run_at DESC LIMIT 20")
    rows = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    heartbeat = rows[0] if rows else {}
    last_run = _parse_dt(heartbeat.get("last_run_at"))
    stale = True
    age_seconds = None
    if last_run:
        age_seconds = int((_utcnow() - last_run).total_seconds())
        stale = age_seconds > int(os.getenv("ALERT_WORKER_STALE_SECONDS", "180"))
    return {"ok": True, "heartbeat": heartbeat, "heartbeats": rows, "stale": stale, "age_seconds": age_seconds}


def provider_status():
    return {
        "brevo_email": email_service.provider_status(),
        "brevo_sms": {"provider": "brevo_sms", "ready": _sms_provider_ready()},
        "vapid_push": {"ready": _web_push_provider_ready(), "active_subscriptions": _push_subscription_count()},
        "telegram": {"ready": bool(_telegram_token()), "connected_users": _telegram_connected_count()},
        "live_market_provider": live_market_service.health().get("providers", {}).get("coingecko_or_fallback", {}),
    }


def channel_warnings(user_id, channels):
    readiness = channel_readiness(user_id)
    warnings = []
    for channel in ("push", "sms", "telegram"):
        if channels.get(channel) and not readiness[channel].get("ready"):
            warnings.append(readiness[channel].get("message") or f"{channel} needs setup.")
    return warnings


def admin_summary():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM alert_rules WHERE COALESCE(status, 'active')='active'")
    active = (_row_to_dict(cur.fetchone()) or {}).get("total") or 0
    cur.execute("SELECT COUNT(*) AS total FROM alert_events WHERE status='triggered' AND created_at >= ?", (_utcnow().date().isoformat(),))
    triggered_today = (_row_to_dict(cur.fetchone()) or {}).get("total") or 0
    cur.execute("SELECT status, COUNT(*) AS total FROM notification_delivery_logs GROUP BY status ORDER BY total DESC")
    delivery_statuses = [_row_to_dict(row) for row in cur.fetchall()]
    cur.execute("SELECT channel, status, COUNT(*) AS total FROM notification_delivery_logs GROUP BY channel, status ORDER BY channel, total DESC")
    channel_statuses = [_row_to_dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT user_id, channel, status, provider, error_message, created_at
        FROM notification_delivery_logs
        WHERE status IN ('failed', 'not_configured', 'permission_denied')
        ORDER BY created_at DESC, id DESC
        LIMIT 30
        """
    )
    recent_delivery_errors = [_row_to_dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM alert_events ORDER BY created_at DESC, id DESC LIMIT 20")
    recent_events = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    return {
        "ok": True,
        "active_alert_count": int(active),
        "triggered_today": int(triggered_today),
        "delivery_statuses": delivery_statuses,
        "channel_statuses": channel_statuses,
        "recent_delivery_errors": recent_delivery_errors,
        "recent_events": recent_events,
        "worker": worker_health(),
        "providers": provider_status(),
    }


# ---------------------------------------------------------------------------
# Mobile Premium Crypto Intelligence API layer (/api/mobile/crypto/alerts)
#
# Maps the mobile Alert JSON contract onto the EXISTING alert_rules storage —
# basic above/below rules round-trip through this API too, so there is one
# store and one engine behind both surfaces. The flask routes in bot.py are
# thin wrappers over these functions; keeping the logic here keeps it
# unit-testable with stdlib only.
# ---------------------------------------------------------------------------

MOBILE_FREE_BASIC_RULE_LIMIT = 5
MOBILE_PREMIUM_TOTAL_RULE_LIMIT = 100
#: Symbols that mean "every asset on my watchlist" — premium only.
WATCHLIST_WIDE_SYMBOLS = {"*", "ALL", "WATCHLIST"}
#: The only condition types a free basic rule may use.
MOBILE_BASIC_CONDITION_TYPES = {"price_above", "price_below"}
_MOBILE_DEFAULT_CHANNELS = {"in_app": True, "push": True}


def _mobile_premium_required(capability_name="advanced_alerts"):
    try:
        from . import crypto_premium_gate

        return crypto_premium_gate.premium_required_response(
            crypto_premium_gate.CAP_CRYPTO_ADVANCED_ALERTS
        )
    except ImportError:
        return {
            "ok": False,
            "code": "premium_required",
            "capability": capability_name,
            "message": "Advanced crypto alerts are a PulseSoc Premium feature.",
        }


def _mobile_basic_conditions(rule):
    """The mobile conditions[] rendering of one legacy/basic rule."""
    condition = _normalize_condition(rule.get("condition"))
    threshold = rule.get("threshold_value")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = 0.0
    if condition == "above":
        return [{"type": "price_above", "threshold": threshold}]
    if condition == "below":
        return [{"type": "price_below", "threshold": threshold}]
    if condition == "moves_up_percent":
        return [{"type": "price_move_pct", "threshold": threshold, "direction": "up", "window_minutes": 1440}]
    if condition == "moves_down_percent":
        return [{"type": "price_move_pct", "threshold": threshold, "direction": "down", "window_minutes": 1440}]
    if condition == "volatility_above":
        return [{"type": "price_move_pct", "threshold": threshold, "direction": "any", "window_minutes": 1440}]
    return [{"type": "price_above", "threshold": threshold}]


def mobile_alert_json(rule):
    """One rule in the exact mobile Alert JSON contract shape."""
    rule = _public_rule(rule)
    metadata = _rule_metadata(rule)
    advanced = _is_advanced_rule(rule)
    symbol = _normalize_symbol(rule.get("symbol"))
    if advanced:
        payload = _advanced_payload(rule) or {"conditions": [], "match": "all"}
        conditions = payload.get("conditions") or []
        match = str(rule.get("match_mode") or payload.get("match") or "all").lower()
        frequency = _advanced_frequency(rule)
    else:
        conditions = _mobile_basic_conditions(rule)
        match = "all"
        repeat_mode = str(rule.get("repeat_mode") or DEFAULT_REPEAT_MODE).strip().lower()
        frequency = str(metadata.get("frequency") or "").strip().lower()
        if frequency not in ADVANCED_FREQUENCIES:
            frequency = "every_crossing" if repeat_mode == REPEAT_MODE_ONCE else "recurring"
    status = str(rule.get("status") or "active")
    return {
        "id": int(rule.get("id") or 0),
        "asset_id": metadata.get("asset_id") if metadata.get("asset_id") not in (None, "") else symbol.lower(),
        "symbol": symbol,
        "name": str(metadata.get("name") or symbol),
        "rule_type": "advanced" if advanced else "basic",
        "conditions": conditions,
        "match": match if match in {"all", "any"} else "all",
        "frequency": frequency,
        "cooldown_seconds": int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS),
        "enabled": status == "active",
        "status": status,
        "last_evaluated_at": rule.get("last_checked_at") or None,
        "last_triggered_at": rule.get("last_triggered_at") or None,
        "premium": advanced,
    }


def _mobile_rule_counts(user_id):
    """(basic_count, total_count) of the caller's non-deleted rules."""
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN advanced_conditions IS NULL OR advanced_conditions='' THEN 1 ELSE 0 END) AS basic_total
            FROM alert_rules
            WHERE user_id=? AND COALESCE(status, 'active')!='deleted' AND deleted_at IS NULL
            """,
            (user_id,),
        )
        row = _row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()
    return int(row.get("basic_total") or 0), int(row.get("total") or 0)


def list_mobile_crypto_alerts(user_id, limit=200):
    """{ok, items:[Alert]} — capabilities are attached by the route."""
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM alert_rules
            WHERE user_id=? AND COALESCE(status, 'active')!='deleted' AND deleted_at IS NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        rows = [_row_to_dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return {"ok": True, "items": [mobile_alert_json(row) for row in rows]}


def _mobile_normalize_frequency(value, default=DEFAULT_ADVANCED_FREQUENCY):
    frequency = str(value or "").strip().lower()
    return frequency if frequency in ADVANCED_FREQUENCIES else default


def _mobile_symbol(payload):
    raw = str((payload or {}).get("symbol") or "").strip()
    return raw


def _is_watchlist_wide(symbol_raw):
    return symbol_raw.strip().upper() in WATCHLIST_WIDE_SYMBOLS if symbol_raw else False


def _store_advanced_columns(rule_id, user_id, validated, metadata):
    """Persist the advanced JSON + match_mode + metadata on an existing row."""
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_rules
            SET advanced_conditions=?, match_mode=?, metadata=?,
                condition_state=NULL, advanced_state=NULL, updated_at=?
            WHERE id=? AND user_id=?
            """,
            (
                json.dumps({"operator": validated["operator"], "conditions": validated["conditions"]})[:4000],
                validated["match"],
                json.dumps(metadata)[:4000],
                _now(),
                rule_id,
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_repeat_mode(rule_id, user_id, frequency):
    """Map the mobile frequency onto the existing repeat-mode state machine:
    once/every_crossing => one notification per crossing; recurring => keep
    speaking while the move progresses."""
    repeat_mode = REPEAT_MODE_PROGRESS if frequency == "recurring" else REPEAT_MODE_ONCE
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE alert_rules SET repeat_mode=?, updated_at=? WHERE id=? AND user_id=?",
            (repeat_mode, _now(), rule_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def create_mobile_crypto_alert(user_id, payload, has_premium):
    """POST /api/mobile/crypto/alerts.

    Free users: up to MOBILE_FREE_BASIC_RULE_LIMIT basic (single price_above/
    price_below) rules. Premium: advanced rules, watchlist-wide rules, and up
    to MOBILE_PREMIUM_TOTAL_RULE_LIMIT rules total. Premium denials return the
    canonical premium_required payload (served with HTTP 200 by the route).
    """
    payload = payload or {}
    symbol_raw = _mobile_symbol(payload)
    if not symbol_raw:
        return {"ok": False, "code": "invalid_symbol", "message": "A symbol is required."}
    watchlist_wide = _is_watchlist_wide(symbol_raw)
    conditions_raw = payload.get("conditions")
    if not isinstance(conditions_raw, list) or not conditions_raw:
        return {"ok": False, "code": "invalid_conditions", "message": "At least one condition is required."}
    match = str(payload.get("match") or "all").strip().lower()
    validated = validate_advanced_conditions(
        {"operator": "OR" if match == "any" else "AND", "conditions": conditions_raw}
    )
    if not validated.get("ok"):
        return {"ok": False, "code": "invalid_conditions", "message": validated.get("message")}
    conditions = validated["conditions"]
    requested_type = str(payload.get("rule_type") or "").strip().lower()
    is_basic = (
        requested_type != "advanced"
        and not watchlist_wide
        and len(conditions) == 1
        and conditions[0]["type"] in MOBILE_BASIC_CONDITION_TYPES
        and match != "any"
    )
    if not is_basic and not has_premium:
        return _mobile_premium_required()
    if watchlist_wide and not has_premium:
        return _mobile_premium_required()
    basic_count, total_count = _mobile_rule_counts(user_id)
    if has_premium:
        if total_count >= MOBILE_PREMIUM_TOTAL_RULE_LIMIT:
            return {
                "ok": False,
                "code": "limit_reached",
                "message": f"You can keep at most {MOBILE_PREMIUM_TOTAL_RULE_LIMIT} alert rules.",
            }
    elif basic_count >= MOBILE_FREE_BASIC_RULE_LIMIT:
        return {
            "ok": False,
            "code": "limit_reached",
            "message": (
                f"Free accounts can keep {MOBILE_FREE_BASIC_RULE_LIMIT} basic alerts. "
                "Upgrade to Premium for more."
            ),
        }
    frequency = _mobile_normalize_frequency(payload.get("frequency"))
    raw_cooldown = payload.get("cooldown_seconds")
    if raw_cooldown in (None, ""):
        raw_cooldown = DEFAULT_COOLDOWN_SECONDS
    try:
        cooldown = max(0, min(int(raw_cooldown), 86400 * 7))
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN_SECONDS
    symbol = _normalize_symbol(symbol_raw) if not watchlist_wide else "WATCHLIST"
    metadata = {
        "created_via": "mobile_crypto_api",
        "rule_type": "advanced" if not is_basic else "basic",
        "frequency": frequency,
        "name": str(payload.get("name") or "")[:120],
        "asset_id": str(payload.get("asset_id") or "")[:60],
        "watchlist_wide": watchlist_wide,
    }
    if is_basic:
        condition = "above" if conditions[0]["type"] == "price_above" else "below"
        created = create_alert_rule(
            user_id,
            alert_type="coin_price",
            symbol=symbol,
            condition=condition,
            threshold=conditions[0]["threshold"],
            channels=dict(_MOBILE_DEFAULT_CHANNELS),
            cooldown_seconds=cooldown,
            source="mobile_crypto_api",
            metadata=metadata,
        )
        if not created.get("ok"):
            return created
        alert_id = created["alert_id"]
        _apply_repeat_mode(alert_id, user_id, frequency)
    else:
        created = create_alert_rule(
            user_id,
            alert_type="coin_price",
            symbol=symbol,
            condition="advanced",
            threshold=conditions[0]["threshold"],
            channels=dict(_MOBILE_DEFAULT_CHANNELS),
            cooldown_seconds=cooldown,
            source="mobile_crypto_api",
            metadata=metadata,
        )
        if not created.get("ok"):
            return created
        alert_id = created["alert_id"]
        _store_advanced_columns(alert_id, user_id, validated, metadata)
        _apply_repeat_mode(alert_id, user_id, frequency)
    rule = get_alert_rule(alert_id, user_id)
    return {"ok": True, "item": mobile_alert_json(rule), "message": "Alert created."}


def update_mobile_crypto_alert(user_id, alert_id, payload, has_premium):
    """PATCH /api/mobile/crypto/alerts/<id> — any subset of the create payload
    plus `enabled` for pause/resume."""
    payload = payload or {}
    rule = get_alert_rule(alert_id, user_id)
    if not rule or (rule.get("status") or "active") == "deleted":
        return {"ok": False, "code": "not_found", "message": "Alert not found."}
    is_advanced = _is_advanced_rule(rule)
    structural_keys = {"conditions", "match", "symbol", "rule_type"}
    wants_structural = any(key in payload for key in structural_keys)
    if (is_advanced and (wants_structural or "frequency" in payload)) and not has_premium:
        return _mobile_premium_required()
    if "enabled" in payload:
        if payload.get("enabled"):
            resume_alert(alert_id, user_id)
        else:
            pause_alert(alert_id, user_id)
    if wants_structural or "frequency" in payload or "cooldown_seconds" in payload or "name" in payload:
        current = mobile_alert_json(rule)
        merged = {
            "symbol": payload.get("symbol", current["symbol"]),
            "asset_id": payload.get("asset_id", current["asset_id"]),
            "name": payload.get("name", current["name"]),
            "rule_type": payload.get("rule_type", current["rule_type"]),
            "conditions": payload.get("conditions", current["conditions"]),
            "match": payload.get("match", current["match"]),
            "frequency": payload.get("frequency", current["frequency"]),
            "cooldown_seconds": payload.get("cooldown_seconds", current["cooldown_seconds"]),
        }
        symbol_raw = _mobile_symbol(merged)
        watchlist_wide = _is_watchlist_wide(symbol_raw)
        match = str(merged.get("match") or "all").strip().lower()
        validated = validate_advanced_conditions(
            {"operator": "OR" if match == "any" else "AND", "conditions": merged.get("conditions")}
        )
        if not validated.get("ok"):
            return {"ok": False, "code": "invalid_conditions", "message": validated.get("message")}
        conditions = validated["conditions"]
        is_basic = (
            str(merged.get("rule_type") or "").strip().lower() != "advanced"
            and not watchlist_wide
            and len(conditions) == 1
            and conditions[0]["type"] in MOBILE_BASIC_CONDITION_TYPES
            and match != "any"
        )
        if (not is_basic or watchlist_wide) and not has_premium:
            return _mobile_premium_required()
        frequency = _mobile_normalize_frequency(merged.get("frequency"))
        raw_cooldown = merged.get("cooldown_seconds")
        if raw_cooldown in (None, ""):
            raw_cooldown = DEFAULT_COOLDOWN_SECONDS
        try:
            cooldown = max(0, min(int(raw_cooldown), 86400 * 7))
        except (TypeError, ValueError):
            cooldown = DEFAULT_COOLDOWN_SECONDS
        symbol = _normalize_symbol(symbol_raw) if not watchlist_wide else "WATCHLIST"
        metadata = _rule_metadata(rule)
        metadata.update(
            {
                "rule_type": "advanced" if not is_basic else "basic",
                "frequency": frequency,
                "name": str(merged.get("name") or "")[:120],
                "asset_id": str(merged.get("asset_id") or "")[:60],
                "watchlist_wide": watchlist_wide,
            }
        )
        ensure_alert_schema()
        conn = user_context.connect()
        try:
            cur = conn.cursor()
            if is_basic:
                condition = "above" if conditions[0]["type"] == "price_above" else "below"
                cur.execute(
                    """
                    UPDATE alert_rules
                    SET symbol=?, target=?, condition=?, threshold_value=?, target_value=?,
                        cooldown_seconds=?, metadata=?, advanced_conditions=NULL, match_mode=NULL,
                        advanced_state=NULL, condition_state=NULL, updated_at=?
                    WHERE id=? AND user_id=? AND COALESCE(status, 'active')!='deleted'
                    """,
                    (
                        symbol,
                        symbol,
                        condition,
                        conditions[0]["threshold"],
                        conditions[0]["threshold"],
                        cooldown,
                        json.dumps(metadata)[:4000],
                        _now(),
                        alert_id,
                        user_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE alert_rules
                    SET symbol=?, target=?, condition='advanced', threshold_value=?, target_value=?,
                        cooldown_seconds=?, metadata=?, advanced_conditions=?, match_mode=?,
                        advanced_state=NULL, condition_state=NULL, updated_at=?
                    WHERE id=? AND user_id=? AND COALESCE(status, 'active')!='deleted'
                    """,
                    (
                        symbol,
                        symbol,
                        conditions[0]["threshold"],
                        conditions[0]["threshold"],
                        cooldown,
                        json.dumps(metadata)[:4000],
                        json.dumps({"operator": validated["operator"], "conditions": conditions})[:4000],
                        validated["match"],
                        _now(),
                        alert_id,
                        user_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        _apply_repeat_mode(alert_id, user_id, frequency)
    updated = get_alert_rule(alert_id, user_id)
    if not updated:
        return {"ok": False, "code": "not_found", "message": "Alert not found."}
    return {"ok": True, "item": mobile_alert_json(updated), "message": "Alert updated."}


def delete_mobile_crypto_alert(user_id, alert_id):
    """DELETE /api/mobile/crypto/alerts/<id> — soft delete, same as the web."""
    result = delete_alert(alert_id, user_id)
    if not result.get("ok"):
        return {"ok": False, "code": "not_found", "message": "Alert not found."}
    return {"ok": True, "message": "Alert deleted."}


def list_mobile_alert_history(user_id, limit=30, offset=0, alert_id=None):
    """GET /api/mobile/crypto/alerts/history — triggered events, newest first,
    with a real has_more computed by over-fetching one row."""
    ensure_alert_schema()
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 30
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    clauses = ["user_id=?", "status='triggered'"]
    params = [user_id]
    if alert_id:
        clauses.append("alert_rule_id=?")
        params.append(int(alert_id))
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, alert_rule_id, symbol, condition, threshold_value, observed_value,
                   message, delivery_status, created_at
            FROM alert_events
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit + 1, offset),
        )
        rows = [_row_to_dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    has_more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        items.append(
            {
                "alert_id": int(row.get("alert_rule_id") or 0),
                "symbol": _normalize_symbol(row.get("symbol")),
                "condition_summary": str(row.get("message") or row.get("condition") or "")[:500],
                "observed_value": row.get("observed_value"),
                "triggered_at": row.get("created_at") or "",
                "notification_result": str(row.get("delivery_status") or "")[:80],
            }
        )
    return {"ok": True, "items": items, "has_more": has_more}
