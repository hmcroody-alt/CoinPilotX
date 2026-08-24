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

from . import crypto_alert_conditions as conditions, db as db_service, email_service, live_market_service, market_observations, notification_service, premium_crypto_access, pulsesoc_notification_system, push_service, sms_service, user_context


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
            # Advanced (Premium) conditions. NULL on every rule that existed
            # before this column and on every basic rule created since, and the
            # evaluator branches on NULL, so the free single-threshold path is
            # untouched rather than reimplemented on top of the spec.
            ("condition_spec", "TEXT"),
            # The metrics this rule read last cycle, as JSON. A crossing needs a
            # prior reading to cross *from*, and each clause needs its own — a
            # single scalar cannot carry the previous price and the previous
            # volume at once.
            ("last_observations", "TEXT"),
            # Set when the rule watches every asset on a watchlist instead of the
            # single ``symbol``. Membership is read fresh each cycle rather than
            # copied here, so adding an asset to the watchlist extends the rule
            # and removing one stops it — which is what "watch this list" means.
            ("watchlist_id", "INTEGER"),
            # Set when the rule watches everything the member currently holds.
            # A flag rather than an id because a member has exactly one
            # portfolio; membership is read fresh each cycle for the same reason
            # as ``watchlist_id``, so buying an asset extends the rule and
            # selling out of one stops it.
            ("portfolio_scope", "INTEGER DEFAULT 0"),
        ],
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_rule_symbol_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            condition_state TEXT,
            trigger_seq INTEGER DEFAULT 0,
            last_observed_value REAL,
            last_notified_value REAL,
            last_observations TEXT,
            last_triggered_at TEXT,
            state_changed_at TEXT,
            updated_at TEXT
        )
        """
    )
    # One latch per (rule, symbol). A watchlist rule cannot share the columns on
    # its own row: "BTC crossed" and "ETH crossed" are separate events, and a
    # single latch would let whichever fired first silence every other asset on
    # the list until it re-armed.
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_rule_symbol_state_rule_symbol "
        "ON alert_rule_symbol_state (rule_id, symbol)"
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


def _measure_windows(symbol, spec):
    """Measure exactly the windows this rule depends on, and nothing else.

    Each entry is the full reading from ``market_observations.window_reading``,
    including the readings the series could not answer — the condition library
    turns those into an undecidable clause, and the copy below quotes the age of
    the baseline that was actually compared rather than implying the requested
    window was measured to the minute.
    """
    readings = {}
    for metric, minutes in conditions.required_windows(spec):
        try:
            readings[conditions.window_key(metric, minutes)] = (
                market_observations.window_reading(symbol, metric, minutes))
        except Exception as exc:
            # A series failure is undecidable, not false: leaving the key absent
            # is exactly how the library reads "this window has no answer".
            logging.warning("Window reading failed for %s %s/%sm: %s",
                            symbol, metric, minutes, exc)
    return readings


def _describe_window(clause, observations, windows):
    """One windowed clause's reading, named by the interval actually compared.

    The requested window is what the member asked for; ``baseline_age_seconds``
    is what the series could measure. Quoting the first as though it were the
    second would be a precision claim the sampler does not support.
    """
    metric = clause["metric"]
    minutes = conditions.clause_window(clause)
    subject = f"{conditions.metric_label(metric)} over {conditions.window_label(minutes)}"
    value = (observations or {}).get(conditions.clause_key(clause))
    if value is None:
        return f"{subject} unavailable"
    reading = (windows or {}).get(conditions.window_key(metric, minutes)) or {}
    age = reading.get("baseline_age_seconds")
    measured = f" (measured over {round(float(age) / 60.0)}m)" if age else ""
    return f"{subject} {round(float(value), 2)}%{measured}"


def _describe_observations(spec, observations, windows=None):
    """The readings behind a compound alert, in the order the member wrote them.

    A metric the market source did not publish is reported as unavailable rather
    than omitted. The rule still fired — an OR settles on one clause — and the
    member is owed the reason it does not see a number for the other.
    """
    observations = observations or {}
    parts = []
    for clause in spec.get("clauses") or ():
        if conditions.clause_window(clause):
            parts.append(_describe_window(clause, observations, windows))
            continue
        metric = clause["metric"]
        value = observations.get(conditions.clause_key(clause))
        if value is None:
            parts.append(f"{conditions.metric_label(metric)} unavailable")
        elif conditions.is_percent_metric(metric):
            parts.append(f"{conditions.metric_label(metric)} {round(float(value), 2)}%")
        else:
            parts.append(f"{conditions.metric_label(metric)} {_format_money(value)}")
    return ", ".join(parts)


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
    spec = _json_loads(rule.get("condition_spec"), None)
    # Re-validated on read, not trusted from storage. A spec that no longer
    # validates (a metric retired, a row hand-edited) must fall back to the basic
    # single-threshold rule the row also carries rather than be evaluated
    # half-understood — the alternative is a rule that silently watches fewer
    # conditions than the member set.
    if isinstance(spec, dict):
        try:
            spec = conditions.validate_spec(spec)
        except conditions.ConditionError:
            logging.warning("alert_rules.condition_spec on rule %s is no longer valid; "
                            "falling back to the basic condition.", rule.get("id"))
            spec = None
    else:
        spec = None
    rule["condition_spec"] = spec
    rule["is_advanced"] = bool(spec)
    # Rendered once, server-side. A compound rule's description has to agree with
    # what the engine evaluates and with what the notification says, and three
    # independent renderers (web, native, notification copy) would eventually
    # disagree about a rule the member cannot otherwise inspect.
    rule["condition_summary"] = conditions.describe_spec(spec, rule["asset_symbol"]) if spec else ""
    observations = _json_loads(rule.get("last_observations"), None)
    rule["last_observations"] = observations if isinstance(observations, dict) else {}
    rule["watchlist_id"] = int(rule["watchlist_id"]) if rule.get("watchlist_id") else None
    rule["is_watchlist_rule"] = bool(rule["watchlist_id"])
    rule["portfolio_scope"] = 1 if rule.get("portfolio_scope") else 0
    rule["is_portfolio_rule"] = bool(rule["portfolio_scope"])
    if (rule["is_watchlist_rule"] or rule["is_portfolio_rule"]) and not rule.get("_state_scope"):
        # ``_normalize_symbol`` above filled this with its BTC default. A rule
        # that watches a list is about no single asset, and saying "BTC" would be
        # read as one. A *member* of that list is about exactly one asset, which
        # is why the scope check is here: this function runs again on each member
        # and would otherwise erase the symbol it is being evaluated for.
        rule["asset_symbol"] = ""
        rule["symbol"] = ""
        rule["condition_summary"] = conditions.describe_spec(spec, "") if spec else ""
    return rule


#: How many assets one watchlist rule will evaluate per cycle.
#:
#: Not a performance guess: each member costs three small UPDATEs a cycle and
#: the quotes themselves come from one cached board, so the real limit is how
#: many simultaneous notifications a person can read. A list past this is
#: refused at creation rather than silently trimmed.
WATCHLIST_RULE_MAX_SYMBOLS = int(os.getenv("ALERT_WATCHLIST_MAX_SYMBOLS", "25"))


def _watchlist_symbols(user_id, watchlist_id, limit=None, connection=None):
    """The assets on one member's watchlist, in the order they arranged them.

    Queried directly rather than through ``dashboard_crypto_command_center``,
    which imports this module — the tables are read-only here and only two
    column names are borrowed.

    Scoped by ``user_id`` as well as ``watchlist_id`` so a rule can never be
    pointed at somebody else's list by editing an id.

    Reuses the caller's connection when it has one: alert creation runs inside an
    open transaction, and a second connection reading the same database would sit
    behind that write lock rather than answering.
    """
    limit = int(limit or WATCHLIST_RULE_MAX_SYMBOLS)
    owns_connection = connection is None
    conn = connection or user_context.connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT asset_symbol FROM crypto_watchlist_assets "
                "WHERE watchlist_id=? AND user_id=? ORDER BY position ASC, id ASC LIMIT ?",
                (int(watchlist_id), int(user_id), limit + 1),
            )
            rows = cur.fetchall() or []
        except Exception:
            # The crypto command centre creates these tables on first use, so a
            # database that has never opened it simply has no watchlists yet.
            logging.warning("Watchlist assets unavailable for watchlist %s", watchlist_id, exc_info=True)
            return [], False
    finally:
        if owns_connection:
            conn.close()
    symbols = []
    for row in rows:
        symbol = _normalize_symbol(row[0] if not isinstance(row, dict) else row.get("asset_symbol"))
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols[:limit], len(symbols) > limit


def _ensure_symbol_state(rule_id, symbol):
    """Return this (rule, symbol)'s latch row, creating it empty if new.

    An empty row is the same starting point a brand-new rule has: no recorded
    state, so the first observation arms and cannot fire. That is what makes an
    asset added to the watchlist today behave like a rule created today, rather
    than firing immediately on a level it has been sitting at for a week.
    """
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO alert_rule_symbol_state (rule_id, symbol, updated_at) VALUES (?, ?, ?)",
            (int(rule_id), symbol, _now()),
        )
        conn.commit()
        cur.execute(
            "SELECT * FROM alert_rule_symbol_state WHERE rule_id=? AND symbol=? LIMIT 1",
            (int(rule_id), symbol),
        )
        return _row_to_dict(cur.fetchone()) or {}
    finally:
        conn.close()


#: Columns the latch machine reads and writes. A watchlist member's evaluation
#: reads them from its own state row instead of the rule row; everything else
#: about the rule (threshold, spec, channels, cooldown) is shared.
_SCOPED_STATE_COLUMNS = (
    "condition_state", "trigger_seq", "last_observed_value",
    "last_notified_value", "last_triggered_at", "state_changed_at",
)


def _member_rule(rule, symbol):
    """One watchlist member seen as the rule the engine already knows how to run.

    Everything downstream — the latch, the notification copy, the event row —
    is written against a rule with one symbol, so a member is presented as
    exactly that. ``_state_scope`` is what routes the state writes back to the
    per-symbol row instead of the shared one.
    """
    state = _ensure_symbol_state(rule["id"], symbol)
    member = dict(rule)
    member["symbol"] = symbol
    member["asset_symbol"] = symbol
    member["_state_scope"] = symbol
    for column in _SCOPED_STATE_COLUMNS:
        member[column] = state.get(column)
    member["last_observations"] = state.get("last_observations")
    return member


def _state_table(scope):
    """Where this evaluation's latch lives, and how to address its row."""
    if scope:
        return "alert_rule_symbol_state", "rule_id=? AND symbol=?"
    return "alert_rules", "id=?"


def _state_key(rule_id, scope):
    return (rule_id, scope) if scope else (rule_id,)


def _validate_watchlist_rule(user_id, watchlist_id, connection=None):
    """Can this member point a rule at this list, today?

    Refuses an oversized list rather than trimming it. A rule that quietly
    watches 25 of 40 assets is one the member believes covers all 40, and they
    would only discover otherwise by not being told about the other fifteen.
    """
    if not premium_crypto_access.allowed_for_user_id(user_id, premium_crypto_access.ADVANCED_ALERTS):
        return {"ok": False, "code": "premium_required",
                "capability": premium_crypto_access.ADVANCED_ALERTS,
                "message": "Watching a whole list with one alert is part of PulseSoc Premium."}
    try:
        watchlist_id = int(watchlist_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "invalid_watchlist", "message": "Choose a watchlist to watch."}
    owns_connection = connection is None
    conn = connection or user_context.connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT name FROM crypto_watchlists WHERE id=? AND user_id=? LIMIT 1",
                        (watchlist_id, int(user_id)))
            row = cur.fetchone()
        except Exception:
            logging.warning("Watchlist lookup failed for %s", watchlist_id, exc_info=True)
            row = None
    finally:
        if owns_connection:
            conn.close()
    if not row:
        # Same answer for "does not exist" and "belongs to somebody else", so the
        # error cannot be used to probe which watchlist ids are real.
        return {"ok": False, "code": "watchlist_not_found",
                "message": "That watchlist could not be found."}
    symbols, truncated = _watchlist_symbols(user_id, watchlist_id, connection=connection)
    if not symbols:
        return {"ok": False, "code": "watchlist_empty",
                "message": "Add at least one asset to that watchlist first."}
    if truncated:
        return {"ok": False, "code": "watchlist_too_large",
                "message": (f"One alert can watch up to {WATCHLIST_RULE_MAX_SYMBOLS} assets. "
                            "Split this list, or create the alert on a shorter one.")}
    name = row[0] if not isinstance(row, dict) else row.get("name")
    return {"ok": True, "watchlist_id": watchlist_id, "watchlist_name": name or "", "symbols": symbols}


def watchlist_rule_preflight(user_id, watchlist_id, connection=None):
    """Would rule creation accept this list right now, and if not, why?

    Deliberately the same function the gate runs rather than a second reading of
    the same rules. A creation form that offers a list creation would refuse is
    worse than one that offers nothing: the member picks it, fills the rest of
    the form in, and only then is told no.
    """
    return _validate_watchlist_rule(user_id, watchlist_id, connection=connection)


#: How many holdings one portfolio rule will evaluate per cycle.
#:
#: Same ceiling and same reasoning as ``WATCHLIST_RULE_MAX_SYMBOLS``, with its
#: own environment variable because the two scopes are sized by different
#: things: a watchlist is as long as somebody chose to make it, a portfolio is
#: as long as their holdings happen to be.
PORTFOLIO_RULE_MAX_SYMBOLS = int(os.getenv("ALERT_PORTFOLIO_MAX_SYMBOLS", "25"))


def _portfolio_symbols(user_id, limit=None, connection=None):
    """The assets this member currently holds, newest position first.

    Read from ``portfolio_items`` — the table :mod:`services.portfolio_service`
    writes and the one behind ``/api/portfolio``. The Business OS crypto ledger
    is deliberately not consulted: it is dark behind ``BUSINESS_OS_CRYPTO``, and
    a rule that watched a different set of assets than the portfolio screen
    shows would be impossible for the member to reconcile.

    Only the symbol is taken. Amount, cost basis and anything derived from them
    stay out of this function on purpose — the scope decides *which* assets a
    rule watches, and the conditions it watches them with are the ordinary
    market ones. Nothing here computes a gain.

    Deduplicated because ``portfolio_items`` has no uniqueness constraint on
    ``(user_id, symbol)``: two lots of the same asset are two rows, and
    evaluating BTC twice in one cycle would race its own latch.

    Reuses the caller's connection for the same reason ``_watchlist_symbols``
    does — creation runs inside an open transaction.
    """
    limit = int(limit or PORTFOLIO_RULE_MAX_SYMBOLS)
    owns_connection = connection is None
    conn = connection or user_context.connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT symbol FROM portfolio_items WHERE user_id=? ORDER BY created_at DESC, id DESC",
                (int(user_id),),
            )
            rows = cur.fetchall() or []
        except Exception:
            logging.warning("Portfolio holdings unavailable for user %s", user_id, exc_info=True)
            return [], False
    finally:
        if owns_connection:
            conn.close()
    symbols = []
    for row in rows:
        symbol = _normalize_symbol(row[0] if not isinstance(row, dict) else row.get("symbol"))
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    # Sliced after deduplication, not by SQL LIMIT: two lots of the same asset
    # would otherwise consume two of the member's allowance and truncate a
    # portfolio that is comfortably inside it.
    return symbols[:limit], len(symbols) > limit


def _validate_portfolio_rule(user_id, connection=None):
    """Can this member point a rule at their holdings, today?

    Refuses an oversized portfolio rather than trimming it, for the reason
    ``_validate_watchlist_rule`` records. The exposure is worse here: a
    watchlist only grows when the member edits it, while a portfolio grows the
    moment they add a holding on an entirely different screen. That is why the
    truncation is also reported at evaluation time rather than only at creation.
    """
    if not premium_crypto_access.allowed_for_user_id(user_id, premium_crypto_access.ADVANCED_ALERTS):
        return {"ok": False, "code": "premium_required",
                "capability": premium_crypto_access.ADVANCED_ALERTS,
                "message": "Watching your whole portfolio with one alert is part of PulseSoc Premium."}
    symbols, truncated = _portfolio_symbols(user_id, connection=connection)
    if not symbols:
        return {"ok": False, "code": "portfolio_empty",
                "message": "Add at least one holding to your portfolio first."}
    if truncated:
        return {"ok": False, "code": "portfolio_too_large",
                "message": (f"One alert can watch up to {PORTFOLIO_RULE_MAX_SYMBOLS} holdings. "
                            "Create the alert on a watchlist instead.")}
    return {"ok": True, "portfolio_scope": 1, "symbols": symbols}


def portfolio_rule_preflight(user_id, connection=None):
    """Would rule creation accept this member's portfolio right now, and if not, why?

    The same function the gate runs, for the reason
    :func:`watchlist_rule_preflight` gives.
    """
    return _validate_portfolio_rule(user_id, connection=connection)


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
    condition_spec=None,
    watchlist_id=None,
    portfolio_scope=False,
):
    if not schema_ready:
        ensure_alert_schema(connection)
    alert_type = _normalize_alert_type(alert_type)
    condition = _normalize_condition(condition)
    portfolio_scope = bool(portfolio_scope)
    if watchlist_id and portfolio_scope:
        # Refused rather than resolved by precedence. Both are a complete answer
        # to "which assets", and silently honouring one would give the member a
        # rule watching a set they did not ask for.
        return {"ok": False, "code": "conflicting_scope",
                "message": "An alert watches one watchlist or your portfolio, not both."}
    if watchlist_id:
        gate = _validate_watchlist_rule(user_id, watchlist_id, connection)
        if not gate["ok"]:
            return gate
        watchlist_id = gate["watchlist_id"]
        # Left empty on purpose. ``_normalize_symbol`` defaults to BTC, and a rule
        # that watches a whole list must not carry a ticker that says otherwise.
        symbol = ""
    elif portfolio_scope:
        gate = _validate_portfolio_rule(user_id, connection)
        if not gate["ok"]:
            return gate
        watchlist_id = None
        symbol = ""
    else:
        watchlist_id = None
        symbol = _normalize_symbol(symbol or target)
    spec = None
    if condition_spec:
        # Gated here rather than only in the route, because the worker, UNDX and
        # the admin tools all create rules through this function. A gate that
        # lives in one HTTP handler is a gate on one door of several.
        if not premium_crypto_access.allowed_for_user_id(user_id, premium_crypto_access.ADVANCED_ALERTS):
            return {"ok": False, "code": "premium_required",
                    "capability": premium_crypto_access.ADVANCED_ALERTS,
                    "message": "Advanced alert conditions are part of PulseSoc Premium."}
        try:
            spec = conditions.validate_spec(condition_spec)
        except conditions.ConditionError as exc:
            return {"ok": False, "code": "invalid_condition", "message": str(exc)}
        # The advanced rule still carries a basic condition + threshold, taken
        # from its first clause. Every existing reader — the dashboard, the
        # legacy Telegram surfaces, `_central_crypto_alert_type` — reads those
        # columns, and leaving them empty would make an advanced rule look
        # malformed to code that predates this feature.
        primary = spec["clauses"][0]
        condition = "above" if primary["comparator"] in {"above", "crosses_above"} else "below"
        threshold = primary["value"]
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
             status, active, cooldown_seconds, trigger_count, source, source_ref, metadata, condition_spec,
             watchlist_id, portfolio_scope, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(spec) if spec else None,
                watchlist_id,
                1 if portfolio_scope else 0,
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
        # Without this a duplicated advanced rule silently becomes the basic
        # single-threshold rule its legacy columns describe — the copy would
        # watch less than the original and look identical in the list. The
        # entitlement is re-checked on the way through, so a lapsed member
        # cannot mint new advanced rules by duplicating an old one.
        condition_spec=rule.get("condition_spec"),
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


def evaluate_rule_condition(rule):
    """Did this rule's condition match? One answer for basic and advanced rules.

    Both kinds go through the same state machine below, so both must produce the
    same shape: ``{ok, matched, value, metric, observations}``. ``value`` is the
    single scalar the latch, the repeat comparison and the notification copy are
    all written in terms of, which is why an advanced rule still names a primary
    metric rather than trying to latch on a tuple.

    A rule without a ``condition_spec`` takes the original path unchanged —
    ``current_observed_value`` then ``condition_matches`` — so no existing rule's
    behaviour depends on any of the advanced code being correct.

    ``ok=False`` with ``status="skipped"`` is the *undecidable* answer: the
    market source did not publish a metric the rule reads, or a crossing clause
    has no earlier reading to compare against. The caller treats it exactly like
    a failed quote — check the rule, leave the latch alone, notify nobody —
    because a rule that cannot be evaluated has not been observed to be false.
    """
    spec = rule.get("condition_spec")
    if not spec:
        observed = current_observed_value(rule)
        if not observed.get("ok"):
            return observed
        observed["matched"] = condition_matches(
            rule.get("condition"), observed["value"], rule.get("threshold_value"))
        observed["observations"] = {}
        return observed

    symbol = _normalize_symbol(rule.get("symbol") or rule.get("target"))
    quote = live_market_service.get_crypto_quote(symbol)
    asset = quote.get("asset") or {}
    if not asset:
        return {"ok": False, "status": "error", "observations": {},
                "message": quote.get("message") or f"{symbol} quote unavailable."}

    windows = _measure_windows(symbol, spec)
    result = conditions.evaluate_spec(asset, spec, rule.get("last_observations"), windows)
    observations = result["observations"]
    primary_clause = spec["clauses"][0]
    primary = primary_clause["metric"]
    primary_key = conditions.clause_key(primary_clause)
    if not result["ok"]:
        # Still carries the observations: recording what we *did* see is what
        # gives the next cycle a reading for a crossing to compare against, so a
        # crossing rule arms itself rather than staying undecidable forever.
        return {"ok": False, "status": "skipped", "observations": observations,
                "symbol": symbol, "metric": primary,
                "value": observations.get(primary_key),
                "message": result["message"] or "Alert conditions could not be evaluated."}

    value = observations.get(primary_key)
    if value is None:
        # The primary metric was unavailable but the rule was still decided (an
        # OR another clause already answered). Report the value that decided it.
        value = next((c["observed"] for c in result["clauses"]
                      if c.get("observed") is not None), None)
    return {"ok": True, "symbol": symbol, "metric": primary, "value": float(value),
            "matched": bool(result["matched"]), "observations": observations,
            "windows": windows, "quote": quote, "spec_result": result}


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
    # ``crosses_above``/``crosses_below`` only ever arrive from an advanced
    # rule's primary clause; no stored basic rule carries them, so adding them
    # here cannot change how an existing rule repeats. They matter for a latched
    # OR rule, where a second clause can hold the latch open after the crossing
    # itself has stopped being true.
    if condition in {"above", "moves_up_percent", "crosses_above"}:
        return value > reference
    if condition in {"below", "moves_down_percent", "crosses_below"}:
        return value < reference
    if condition == "volatility_above":
        return abs(value) > abs(reference)
    return False


def _repeat_direction(rule):
    """Which way is "further into the breach" for this rule?

    An advanced rule's latch follows its primary (first) clause, because that is
    the metric ``value`` carries. A compound rule that is latched on "price above
    61,000 and volume above 30B" repeats when the *price* moves further up, which
    is the clause the member led with and the number the notification quotes.
    """
    spec = rule.get("condition_spec") or {}
    clauses = spec.get("clauses") or ()
    return clauses[0]["comparator"] if clauses else rule.get("condition")


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


def _claim_repeat(rule_id, observed_value, expected_seq, scope=None):
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
        table, where = _state_table(scope)
        cur.execute(
            f"""
            UPDATE {table}
            SET trigger_seq=COALESCE(trigger_seq, 0)+1,
                last_notified_value=?,
                last_observed_value=?,
                state_changed_at=?,
                updated_at=?
            WHERE {where} AND COALESCE(trigger_seq, 0)=?
            """,
            (observed_value, observed_value, _now(), _now(),
             *_state_key(rule_id, scope), int(expected_seq or 0)),
        )
        claimed = int(getattr(cur, "rowcount", 0) or 0) > 0
        conn.commit()
        if not claimed:
            return None
        return int(expected_seq or 0) + 1
    finally:
        conn.close()


def _claim_crossing(rule_id, observed_value, scope=None):
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
        table, where = _state_table(scope)
        cur.execute(
            f"""
            UPDATE {table}
            SET condition_state=?,
                trigger_seq=COALESCE(trigger_seq, 0)+1,
                last_observed_value=?,
                last_notified_value=?,
                state_changed_at=?,
                updated_at=?
            WHERE {where} AND COALESCE(condition_state, '')<>?
            """,
            (STATE_LATCHED, observed_value, observed_value, _now(), _now(),
             *_state_key(rule_id, scope), STATE_LATCHED),
        )
        claimed = int(getattr(cur, "rowcount", 0) or 0) > 0
        conn.commit()
        if not claimed:
            return None
        cur.execute(f"SELECT trigger_seq FROM {table} WHERE {where} LIMIT 1", _state_key(rule_id, scope))
        row = cur.fetchone()
        return int((row[0] if row else 0) or 0)
    finally:
        conn.close()


def _set_last_notified_value(rule_id, observed_value, scope=None):
    """Seed the repeat baseline without notifying.

    Used for rules that were already latched before ``last_notified_value``
    existed, so a schema migration never manifests as a surprise notification.
    """
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        table, where = _state_table(scope)
        cur.execute(
            f"UPDATE {table} SET last_notified_value=?, last_observed_value=?, updated_at=? WHERE {where}",
            (observed_value, observed_value, _now(), *_state_key(rule_id, scope)),
        )
        conn.commit()
    finally:
        conn.close()


def _set_last_observations(rule_id, observations, scope=None):
    """Persist the metrics this rule read, for the next cycle to compare against.

    Separate from ``last_observed_value`` rather than replacing it: that column
    is the single scalar the latch and the repeat comparison run on, and every
    existing rule, dashboard and test reads it. This one answers a question the
    scalar cannot — "what was the volume last time?" — for rules that watch more
    than one metric.
    """
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        table, where = _state_table(scope)
        cur.execute(
            f"UPDATE {table} SET last_observations=?, updated_at=? WHERE {where}",
            (json.dumps(observations)[:4000], _now(), *_state_key(rule_id, scope)),
        )
        conn.commit()
    finally:
        conn.close()


def _set_condition_state(rule_id, state, observed_value, scope=None):
    """Persist a non-firing state transition (arming / re-arming)."""
    ensure_alert_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        table, where = _state_table(scope)
        cur.execute(
            f"""
            UPDATE {table}
            SET condition_state=?, last_observed_value=?, state_changed_at=?, updated_at=?
            WHERE {where}
            """,
            (state, observed_value, _now(), _now(), *_state_key(rule_id, scope)),
        )
        conn.commit()
    finally:
        conn.close()


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
    if not rule.get("_state_scope"):
        if rule.get("watchlist_id"):
            return _evaluate_watchlist_rule(rule)
        if rule.get("portfolio_scope"):
            return _evaluate_portfolio_rule(rule)
    scope = rule.get("_state_scope")
    observed = evaluate_rule_condition(rule)
    # Deliberately in-memory only. A window reading describes the two moments
    # this cycle compared; persisting it would let a later notification quote a
    # baseline age that was never measured for it.
    rule["last_windows"] = observed.get("windows") or {}
    if observed.get("observations"):
        # Written before the outcome is acted on, and on the undecidable path
        # too: this is the reading the *next* cycle's crossing compares against,
        # so skipping it when we could not decide would leave a crossing rule
        # permanently unable to see its first edge.
        _set_last_observations(rule["id"], observed["observations"], scope)
        # The in-memory rule was loaded before this cycle, so it still holds the
        # previous reading — which `evaluate_rule_condition` needed and nothing
        # after this point does. Notification copy quotes these values, so leaving
        # them stale would report the wrong numbers on the alert that just fired.
        rule["last_observations"] = observed["observations"]
    if not observed.get("ok"):
        # A missing/failed quote must not disturb latch state, otherwise a single
        # provider blip would re-arm a latched rule and let it fire again. An
        # undecidable advanced rule is the same situation for the same reason.
        _mark_checked(rule["id"], status_message=observed.get("message"))
        if observed.get("status") == "error":
            _create_event(rule, None, "error", observed.get("message") or "Alert evaluation failed.")
        elif (observed.get("value") is not None
              and str(rule.get("condition_state") or "").strip().lower() != STATE_LATCHED):
            # We read the market but could not decide the rule. Arming here is
            # what makes a crossing rule work at all: its first cycle is *always*
            # undecidable — there is nothing to cross from — so if that cycle did
            # not count as the arming observation, the rule would spend its second
            # cycle arming and never fire on the edge it just saw.
            #
            # Restricted to rules that are not latched, which is what preserves
            # the guarantee above: a provider gap on a latched rule still leaves
            # the latch exactly where it was.
            _set_condition_state(rule["id"], STATE_ARMED, observed.get("value"), scope)
        return {"ok": observed.get("status") != "error", "triggered": False, "message": observed.get("message") or "Alert skipped."}
    value = observed["value"]
    matched = observed["matched"]
    _mark_checked(rule["id"])
    previous_state = str(rule.get("condition_state") or "").strip().lower()

    if not matched:
        if previous_state != STATE_ARMED:
            _set_condition_state(rule["id"], STATE_ARMED, value, scope)
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
            _set_last_notified_value(rule["id"], value, scope)
            return {
                "ok": True,
                "triggered": False,
                "latched": True,
                "observed_value": value,
                "state": STATE_LATCHED,
                "message": "Condition still met; repeat baseline recorded for this already-latched alert.",
            }
        if not alert_repeat_progressed(_repeat_direction(rule), value, last_notified, step_percent):
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
        repeat_seq = _claim_repeat(rule["id"], value, rule.get("trigger_seq"), scope)
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
        _set_condition_state(rule["id"], STATE_ARMED, value, scope)
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

    trigger_seq = _claim_crossing(rule["id"], value, scope)
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


def _evaluate_watchlist_rule(rule):
    """Run one watchlist rule once per asset currently on the list.

    Membership is read here, every cycle, rather than frozen at creation. An
    asset added to the watchlist starts with no recorded state and therefore
    arms before it can fire; an asset removed simply stops being evaluated, and
    its state row is left in place so that re-adding it does not present a level
    it has been sitting at for days as a fresh crossing.
    """
    symbols, truncated = _watchlist_symbols(rule.get("user_id"), rule["watchlist_id"])
    return _evaluate_scoped_rule(
        rule, symbols, truncated,
        cap=WATCHLIST_RULE_MAX_SYMBOLS,
        empty_status="Watchlist is empty.",
        empty_message="This alert watches a list that has no assets on it yet.",
        checked_noun="assets on this watchlist",
        truncated_message=(f"Only the first {WATCHLIST_RULE_MAX_SYMBOLS} are watched; "
                           "the list has grown past that."),
    )


def _evaluate_portfolio_rule(rule):
    """Run one portfolio rule once per asset the member currently holds.

    Holdings are read every cycle for the same reason watchlist membership is:
    an asset bought today starts with no recorded state and so arms before it
    can fire, and one sold out of simply stops being evaluated. Freezing the set
    at creation would leave a member's newest position unwatched by an alert
    that says it watches their portfolio.

    Nothing here reads amounts or cost basis. The scope answers "which assets",
    and each one is then evaluated by the ordinary market conditions — so this
    can say "BTC crossed $61,000 and you hold BTC" without claiming anything
    about what the position is worth or what it has earned.
    """
    symbols, truncated = _portfolio_symbols(rule.get("user_id"))
    return _evaluate_scoped_rule(
        rule, symbols, truncated,
        cap=PORTFOLIO_RULE_MAX_SYMBOLS,
        empty_status="Portfolio is empty.",
        empty_message="This alert watches your portfolio, which has no holdings in it yet.",
        checked_noun="holdings in your portfolio",
        truncated_message=(f"Only the first {PORTFOLIO_RULE_MAX_SYMBOLS} are watched; "
                           "your portfolio has grown past that."),
    )


def _evaluate_scoped_rule(rule, symbols, truncated, cap, empty_status, empty_message,
                          checked_noun, truncated_message):
    """Fan one multi-asset rule out over its current members.

    Each asset is evaluated as an ordinary single-symbol rule with its own latch,
    so everything the engine already guarantees — arm on first observation, one
    notification per crossing, restart-safe dedup — holds per asset rather than
    once for the whole set.

    Shared by both scopes rather than copied: a second fan-out would be a second
    place for "did this asset already fire" to be decided, and the two would
    drift the first time either was fixed.
    """
    if not symbols:
        _mark_checked(rule["id"], status_message=empty_status)
        return {"ok": True, "triggered": False, "symbols": [], "message": empty_message}
    results = []
    triggered = 0
    errors = 0
    last_error = ""
    for symbol in symbols:
        try:
            result = evaluate_alert_rule(_member_rule(rule, symbol))
        except Exception as exc:
            # One asset's failure is not the set's failure: the remaining assets
            # are independently decidable and stopping here would silence them.
            errors += 1
            last_error = str(exc)
            logging.exception("Scoped alert member failed rule_id=%s symbol=%s", rule["id"], symbol)
            continue
        results.append({"symbol": symbol, **result})
        if result.get("triggered"):
            triggered += 1
        if not result.get("ok"):
            errors += 1
            last_error = result.get("message") or last_error
    message = f"Checked {len(results)} of {len(symbols)} {checked_noun}."
    if truncated:
        message += f" {truncated_message}"
    return {"ok": errors == 0, "triggered": triggered > 0, "triggered_count": triggered,
            "symbols": symbols, "truncated": truncated, "results": results,
            "message": last_error or message, "cap": cap}


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
    # A watchlist member's sequence counts that asset's crossings, so the rule id
    # alone no longer identifies an event — BTC's first crossing and ETH's first
    # crossing would both be "12:1" and the unique index would drop the second.
    scope = rule.get("_state_scope")
    if trigger_seq is None:
        trigger_key = None
    elif scope:
        trigger_key = f"{rule.get('id')}:{scope}:{int(trigger_seq)}"
    else:
        trigger_key = f"{rule.get('id')}:{int(trigger_seq)}"
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


def trigger_alert(rule, observed_value, trigger_seq=None, repeat=False):
    symbol = _normalize_symbol(rule.get("symbol"))
    condition = _normalize_condition(rule.get("condition"))
    threshold = rule.get("threshold_value")
    # "Value at crossing", not "live observed value": the notification describes a
    # single past event and is never refreshed with later prices, so wording that
    # implies a live feed would be misleading. A repeat is a different event —
    # the threshold was crossed earlier and the market has since moved further —
    # so it is labelled as a continuing move rather than a fresh crossing.
    moment = "Still moving" if repeat else "Value at crossing"
    spec = rule.get("condition_spec")
    if spec:
        # A compound rule must restate every condition it fired on. Naming only
        # the threshold that happens to sit in ``threshold_value`` would describe
        # a different, simpler alert than the one the member created.
        message = (f"{conditions.describe_spec(spec, symbol)}. {moment}: "
                   f"{_describe_observations(spec, rule.get('last_observations'), rule.get('last_windows'))}.")
    elif condition in {"above", "below"}:
        message = f"{symbol} {_condition_label(condition)} {_format_money(threshold)}. {moment}: {_format_money(observed_value)}."
    else:
        message = f"{symbol} {_condition_label(condition)} {threshold}%. {moment}: {round(float(observed_value), 2)}%."
    scope = rule.get("_state_scope")
    if trigger_seq is None:
        # Direct/manual invocation (tests, admin replays) still gets a stable key.
        trigger_seq = _claim_crossing(rule.get("id"), observed_value, scope)
        if trigger_seq is None:
            return {"ok": True, "triggered": False, "latched": True, "observed_value": observed_value, "message": "Crossing already claimed."}
    event = _create_event(rule, observed_value, "triggered", message, trigger_seq=trigger_seq)
    if event.get("replayed"):
        # The crossing was already recorded and dispatched; do not send again.
        return {"ok": True, "triggered": False, "deduped": True, "event": event, "observed_value": observed_value, "message": "Crossing already notified; duplicate suppressed."}
    conn = user_context.connect()
    cur = conn.cursor()
    if scope:
        # Cooldown rate limits one asset's crossings, so it is measured per asset.
        # A shared timestamp would mean a busy asset muted the rest of the list.
        cur.execute(
            "UPDATE alert_rule_symbol_state SET last_triggered_at=?, updated_at=? WHERE rule_id=? AND symbol=?",
            (_now(), _now(), rule.get("id"), scope),
        )
    # The count on the rule row stays the rule's own total across every asset,
    # which is what the member sees next to the rule they created.
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
