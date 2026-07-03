"""Production alert engine for CoinPilotXAI.

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
        ],
    )
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
    if owns_connection:
        conn.close()
    return {"ok": True}


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


def _push_provider_ready():
    return bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY"))


def _push_subscription_count(user_id=None):
    conn = user_context.connect()
    cur = conn.cursor()
    if user_id:
        cur.execute(
            "SELECT COUNT(*) AS total FROM push_subscriptions WHERE user_id=? AND COALESCE(is_active, active, 1)=1",
            (user_id,),
        )
    else:
        cur.execute("SELECT COUNT(*) AS total FROM push_subscriptions WHERE COALESCE(is_active, active, 1)=1")
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
    push_subscriptions = _push_subscription_count(user_id)
    vapid_ready = _push_provider_ready()
    sms_ready = _sms_provider_ready()
    telegram_token_ready = bool(_telegram_token())
    permission = (browser_permission or "").strip().lower()
    if permission == "denied":
        push = _status_payload(False, "permission_denied", "Failed", "Browser push permission is denied. Enable notifications in browser settings.", "/notifications")
    elif not vapid_ready:
        push = _status_payload(False, "not_configured", "Needs setup", "Push keys are not configured yet.", "/notifications")
    elif push_subscriptions <= 0:
        push = _status_payload(False, "not_configured", "Needs setup", "Enable Push Notifications before using push alerts.", "/notifications")
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
        "push": {**push, "subscription_count": push_subscriptions, "vapid_configured": vapid_ready},
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


def list_alert_rules(user_id, limit=100, include_deleted=False):
    ensure_alert_schema()
    reconcile_legacy_alerts(user_id=user_id)
    conn = user_context.connect()
    cur = conn.cursor()
    status_clause = "" if include_deleted else "AND COALESCE(status, 'active')!='deleted' AND deleted_at IS NULL"
    cur.execute(
        f"""
        SELECT * FROM alert_rules
        WHERE user_id=? {status_clause}
        ORDER BY CASE WHEN COALESCE(status, 'active')='active' THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
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


def evaluate_alert_rule(rule):
    if not rule:
        return {"ok": False, "triggered": False, "message": "Alert rule missing."}
    rule = _public_rule(rule)
    if (rule.get("status") or "active") != "active":
        return {"ok": True, "triggered": False, "message": "Alert is not active."}
    observed = current_observed_value(rule)
    if not observed.get("ok"):
        _mark_checked(rule["id"], status_message=observed.get("message"))
        if observed.get("status") == "error":
            _create_event(rule, None, "error", observed.get("message") or "Alert evaluation failed.")
        return {"ok": observed.get("status") != "error", "triggered": False, "message": observed.get("message") or "Alert skipped."}
    threshold = rule.get("threshold_value")
    matched = condition_matches(rule.get("condition"), observed["value"], threshold)
    _mark_checked(rule["id"])
    if not matched:
        return {"ok": True, "triggered": False, "observed_value": observed["value"], "message": "Condition not met."}
    last_triggered = _parse_dt(rule.get("last_triggered_at"))
    cooldown = int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    if last_triggered and _utcnow() - last_triggered < timedelta(seconds=cooldown):
        message = f"Condition met, skipped because alert is cooling down for {cooldown} seconds."
        return {"ok": True, "triggered": False, "cooldown": True, "observed_value": observed["value"], "message": message}
    return trigger_alert(rule, observed["value"])


def _mark_checked(rule_id, status_message=""):
    ensure_alert_schema()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE alert_rules SET last_checked_at=?, updated_at=? WHERE id=?", (_now(), _now(), rule_id))
    conn.commit()
    conn.close()


def _create_event(rule, observed_value, status, message):
    ensure_alert_schema()
    now = _now()
    cooldown = max(1, int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS))
    trigger_bucket = f"{rule.get('id')}:{int(time.time() // cooldown)}"
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alert_events
        (alert_rule_id, user_id, symbol, alert_type, condition, threshold_value, observed_value,
         status, message, title, body, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            json.dumps({"rule_id": rule.get("id"), "observed_value": observed_value, "trigger_bucket": trigger_bucket})[:4000],
            now,
        ),
    )
    event_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM alert_events WHERE id=? LIMIT 1", (event_id,))
    event = _row_to_dict(cur.fetchone())
    if event:
        event["trigger_bucket"] = trigger_bucket
    conn.close()
    return event


def trigger_alert(rule, observed_value):
    symbol = _normalize_symbol(rule.get("symbol"))
    condition = _normalize_condition(rule.get("condition"))
    threshold = rule.get("threshold_value")
    if condition in {"above", "below"}:
        message = f"{symbol} {_condition_label(condition)} {_format_money(threshold)}. Live observed value: {_format_money(observed_value)}."
    else:
        message = f"{symbol} {_condition_label(condition)} {threshold}%. Live observed value: {round(float(observed_value), 2)}%."
    event = _create_event(rule, observed_value, "triggered", message)
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


def dispatch_alert_event(event, rule=None):
    ensure_alert_schema()
    event = dict(event or {})
    rule = _public_rule(rule or get_alert_rule(event.get("alert_rule_id")) or {})
    user_id = event.get("user_id") or rule.get("user_id")
    user = _user_record(user_id)
    channels = _normalize_channels(rule.get("channels") or _json_loads(rule.get("channels_json"), {}))
    title = f"PulseSoc Alert: {_normalize_symbol(rule.get('symbol'))} crossed {_format_money(rule.get('threshold_value'))}"
    body = event.get("message") or "Your PulseSoc alert condition was met."
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
    }
    delivery = {"ok": True, "channels": {}}
    notification_id = None
    alert_type = _central_crypto_alert_type(rule)
    priority = _central_crypto_priority(rule, channels)
    central_channels = _central_crypto_channels(channels, priority)
    if central_channels:
        created = pulsesoc_notification_system.notify_crypto_alert(
            int(user_id),
            alert_id,
            title,
            body,
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
            f"{body}\n\nSelected external channels need setup, so this in-app copy was created.",
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
    title = f"CoinPilotXAI {channel.title()} alert test"
    body = "This is a setup test for CoinPilotXAI alert delivery."
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
        "vapid_push": {"ready": _push_provider_ready(), "active_subscriptions": _push_subscription_count()},
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
