"""Production alert engine for CoinPilotXAI.

Alert rules are stored in the database, evaluated by a worker/manual endpoint,
and dispatched through the centralized notification services with delivery logs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta

import requests

from . import email_service, live_market_service, notification_service, push_service, user_context


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
        channels = _normalize_channels((rule.get("channels") or "").split(",") if rule.get("channels") else None)
    rule["channels"] = channels
    rule["threshold_value"] = rule.get("threshold_value") if rule.get("threshold_value") is not None else rule.get("target_value")
    rule["threshold"] = rule["threshold_value"]
    rule["target_value"] = rule["threshold_value"]
    rule["active"] = 1 if (rule.get("status") or "active") == "active" else 0
    return rule


def create_alert_rule(user_id, alert_type="coin_price", symbol="BTC", condition="above", threshold=None, channels=None, target="", cooldown_seconds=None):
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
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alert_rules
        (user_id, alert_type, symbol, target, condition, threshold_value, target_value, channels_json, channels,
         status, active, cooldown_seconds, trigger_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, 0, ?, ?)
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
            now,
            now,
        ),
    )
    alert_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM alert_rules WHERE id=? AND user_id=? LIMIT 1", (alert_id, user_id))
    rule = _public_rule(_row_to_dict(cur.fetchone()))
    conn.close()
    return {"ok": True, "alert_id": alert_id, "alert": rule, "message": "Alert activated.", "warnings": channel_warnings(user_id, channel_map)}


def list_alert_rules(user_id, limit=100, include_deleted=False):
    conn = user_context.connect()
    cur = conn.cursor()
    status_clause = "" if include_deleted else "AND COALESCE(status, 'active')!='deleted'"
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
    return {"ok": True, "alerts": rows}


def get_alert_rule(alert_id, user_id=None):
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
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE alert_rules SET status=?, active=?, updated_at=? WHERE id=? AND user_id=?",
        (status, active, _now(), rule_id, user_id),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(changed), "message": message if changed else "Alert not found.", "alert_id": rule_id, "status": status}


def list_alert_events(user_id, limit=50):
    conn = user_context.connect()
    cur = conn.cursor()
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
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE alert_rules SET last_checked_at=?, updated_at=? WHERE id=?", (_now(), _now(), rule_id))
    conn.commit()
    conn.close()


def _create_event(rule, observed_value, status, message):
    now = _now()
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
            json.dumps({"rule_id": rule.get("id"), "observed_value": observed_value})[:4000],
            now,
        ),
    )
    event_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM alert_events WHERE id=? LIMIT 1", (event_id,))
    event = _row_to_dict(cur.fetchone())
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


def _log_delivery(user_id, channel, status, provider="", provider_response="", error_message="", notification_id=None, alert_rule_id=None, alert_event_id=None):
    conn = user_context.connect()
    cur = conn.cursor()
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


def _telegram_send(user, title, body, metadata=None):
    token = os.getenv("BOT_TOKEN")
    chat_id = (user or {}).get("telegram_chat_id")
    if not token:
        return {"ok": False, "status": "not_configured", "message": "BOT_TOKEN is not configured."}
    if not chat_id:
        return {"ok": False, "status": "skipped", "message": "Telegram companion is not linked."}
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
    event = dict(event or {})
    rule = _public_rule(rule or get_alert_rule(event.get("alert_rule_id")) or {})
    user_id = event.get("user_id") or rule.get("user_id")
    user = _user_record(user_id)
    channels = _normalize_channels(rule.get("channels") or _json_loads(rule.get("channels_json"), {}))
    title = f"CoinPilotXAI Alert: {_normalize_symbol(rule.get('symbol'))} crossed {_format_money(rule.get('threshold_value'))}"
    body = event.get("message") or "Your CoinPilotXAI alert condition was met."
    metadata = {
        "url": "/alerts",
        "push_type": "market_alert",
        "alert_rule_id": rule.get("id"),
        "alert_event_id": event.get("id"),
        "symbol": rule.get("symbol"),
        "observed_value": event.get("observed_value"),
    }
    delivery = {"ok": True, "channels": {}}
    notification_id = None
    if channels.get("in_app"):
        created = notification_service.queue_notification(user_id, title, body, "market_alerts", metadata)
        notification_id = created.get("notification_id")
        delivery["channels"]["in_app"] = "created" if created.get("ok") else "failed"
        _log_delivery(user_id, "in_app", "created" if created.get("ok") else "failed", "database", created, created.get("message"), notification_id, rule.get("id"), event.get("id"))
    quiet = _quiet_hours_active(user_id)
    external_sent = False
    external_attempted = False
    for channel in ("email", "push", "sms", "telegram"):
        if not channels.get(channel):
            continue
        external_attempted = True
        if quiet:
            delivery["channels"][channel] = "skipped"
            _log_delivery(user_id, channel, "skipped", channel, "", "Quiet hours are active.", notification_id, rule.get("id"), event.get("id"))
            continue
        if channel == "email":
            if not user.get("email") or not os.getenv("BREVO_API_KEY"):
                status = "not_configured"
                result = {"ok": False, "message": "Email address or BREVO_API_KEY is missing."}
            else:
                html = f"<p>{body}</p><p><a href='https://coinpilotx.app/alerts'>Open Alerts Command Center</a></p>"
                result = email_service.send_email(user.get("email"), title, html, body, email_type="market_alerts", user_id=user_id)
                status = "sent" if result.get("ok") else "failed"
            delivery["channels"][channel] = status
            external_sent = external_sent or status == "sent"
            _log_delivery(user_id, channel, status, "brevo", result, result.get("error") or result.get("message"), notification_id, rule.get("id"), event.get("id"))
        elif channel == "push":
            result = push_service.send_push(user_id, title, body, metadata, push_type="market_alert")
            status = _delivery_status_from_result(result)
            delivery["channels"][channel] = status
            external_sent = external_sent or status == "sent"
            _log_delivery(user_id, channel, status, "web_push", result, result.get("message"), notification_id, rule.get("id"), event.get("id"))
        elif channel == "sms":
            result = notification_service.send_sms_alert(user, title, body, notification_id)
            status = _delivery_status_from_result(result)
            delivery["channels"][channel] = status
            external_sent = external_sent or status == "sent"
            _log_delivery(user_id, channel, status, "twilio", result, result.get("message"), notification_id, rule.get("id"), event.get("id"))
        elif channel == "telegram":
            result = _telegram_send(user, title, body, metadata)
            status = _delivery_status_from_result(result)
            delivery["channels"][channel] = status
            external_sent = external_sent or status == "sent"
            _log_delivery(user_id, channel, status, "telegram", result, result.get("message"), notification_id, rule.get("id"), event.get("id"))
    if external_attempted and not external_sent and not channels.get("in_app"):
        created = notification_service.queue_notification(user_id, title, f"{body}\n\nSelected external channels need setup, so this in-app copy was created.", "market_alerts", metadata)
        notification_id = created.get("notification_id")
        delivery["channels"]["in_app_fallback"] = "created" if created.get("ok") else "failed"
        _log_delivery(user_id, "in_app", "created" if created.get("ok") else "failed", "database", created, created.get("message"), notification_id, rule.get("id"), event.get("id"))
    return delivery


def evaluate_all_active_alerts(limit=500, worker_name="alert_worker"):
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
        "brevo_sms": {"provider": "twilio", "ready": bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN") and os.getenv("TWILIO_FROM_NUMBER"))},
        "vapid_push": {"ready": bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY"))},
        "telegram": {"ready": bool(os.getenv("BOT_TOKEN"))},
        "live_market_provider": live_market_service.health().get("providers", {}).get("coingecko_or_fallback", {}),
    }


def channel_warnings(user_id, channels):
    user = _user_record(user_id)
    warnings = []
    if channels.get("sms") and (not user.get("phone") or int(user.get("sms_opt_in") or 0) != 1):
        warnings.append("SMS is not configured for your account yet.")
    if channels.get("push"):
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM push_subscriptions WHERE user_id=? AND COALESCE(is_active, active, 1)=1", (user_id,))
        row = _row_to_dict(cur.fetchone())
        conn.close()
        if not int((row or {}).get("total") or 0):
            warnings.append("Enable push notifications in Settings to receive browser/PWA alerts.")
    if channels.get("telegram") and not user.get("telegram_chat_id"):
        warnings.append("Telegram companion is not connected yet.")
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
    cur.execute("SELECT * FROM alert_events ORDER BY created_at DESC, id DESC LIMIT 20")
    recent_events = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    return {
        "ok": True,
        "active_alert_count": int(active),
        "triggered_today": int(triggered_today),
        "delivery_statuses": delivery_statuses,
        "channel_statuses": channel_statuses,
        "recent_events": recent_events,
        "worker": worker_health(),
        "providers": provider_status(),
    }
