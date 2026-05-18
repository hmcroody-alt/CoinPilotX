"""Persistent Auto Signals monitoring for CoinPilotXAI."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from . import alert_engine, live_market_service, user_context


MODES = {"conservative", "balanced", "aggressive"}
SENSITIVITY = {"conservative": 0.06, "balanced": 0.04, "aggressive": 0.025}
COOLDOWN = {"conservative": 1800, "balanced": 900, "aggressive": 600}
AUTO_SYMBOLS = ("BTC", "ETH", "SOL")
AUTO_TARGET_PREFIX = "auto_signal"


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _mode(value):
    value = (value or "balanced").strip().lower()
    return value if value in MODES else "balanced"


def _row(row):
    return user_context.row_to_dict(row) or {}


def _channels(user_id, browser_permission=None):
    readiness = alert_engine.channel_readiness(user_id, browser_permission=browser_permission)
    return {
        "in_app": True,
        "email": bool((readiness.get("email") or {}).get("ready")),
        "push": bool((readiness.get("push") or {}).get("ready")),
        "sms": False,
        "telegram": bool((readiness.get("telegram") or {}).get("ready")),
    }


def _price(symbol):
    quote = live_market_service.get_crypto_quote(symbol)
    if not quote.get("ok"):
        return None
    asset = quote.get("asset") or {}
    value = asset.get("price") or asset.get("current_price") or asset.get("usd")
    try:
        return float(value)
    except Exception:
        return None


def _find_existing_rule(cur, user_id, symbol, condition, mode):
    cur.execute(
        """
        SELECT * FROM alert_rules
        WHERE user_id=? AND symbol=? AND condition=? AND target=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, symbol, condition, f"{AUTO_TARGET_PREFIX}:{mode}:{symbol}:{condition}"),
    )
    return _row(cur.fetchone())


def _upsert_rule(user_id, symbol, condition, threshold, mode, channels):
    target = f"{AUTO_TARGET_PREFIX}:{mode}:{symbol}:{condition}"
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    existing = _find_existing_rule(cur, user_id, symbol, condition, mode)
    if existing:
        cur.execute(
            """
            UPDATE alert_rules
            SET threshold_value=?, target_value=?, channels_json=?, channels=?, status='active', active=1,
                cooldown_seconds=?, updated_at=?
            WHERE id=? AND user_id=?
            """,
            (
                float(threshold),
                float(threshold),
                json.dumps(channels),
                ",".join([name for name, enabled in channels.items() if enabled]),
                COOLDOWN[mode],
                now,
                existing["id"],
                user_id,
            ),
        )
        conn.commit()
        alert_id = existing["id"]
        action = "updated"
    else:
        result = alert_engine.create_alert_rule(
            user_id,
            alert_type="coin_price",
            symbol=symbol,
            condition=condition,
            threshold=round(float(threshold), 2),
            channels=channels,
            target=target,
            cooldown_seconds=COOLDOWN[mode],
        )
        conn.close()
        if not result.get("ok"):
            return {"ok": False, "symbol": symbol, "condition": condition, "message": result.get("message")}
        return {"ok": True, "action": "created", "alert_id": result.get("alert_id"), "symbol": symbol, "condition": condition, "threshold": round(float(threshold), 2)}
    conn.close()
    return {"ok": True, "action": action, "alert_id": alert_id, "symbol": symbol, "condition": condition, "threshold": round(float(threshold), 2)}


def maintain_user(user_id, mode="balanced", browser_permission=None):
    mode = _mode(mode)
    channel_map = _channels(user_id, browser_permission=browser_permission)
    sensitivity = SENSITIVITY[mode]
    maintained = []
    skipped = []
    for symbol in AUTO_SYMBOLS:
        price = _price(symbol)
        if price is None:
            skipped.append({"symbol": symbol, "reason": "live price unavailable"})
            continue
        for condition, threshold in (("above", price * (1 + sensitivity)), ("below", price * (1 - sensitivity))):
            result = _upsert_rule(user_id, symbol, condition, threshold, mode, channel_map)
            if result.get("ok"):
                maintained.append(result)
            else:
                skipped.append(result)
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET auto_signals_last_checked_at=?, updated_at=? WHERE user_id=?",
        (_now(), _now(), user_id),
    )
    conn.commit()
    conn.close()
    return {"ok": bool(maintained), "mode": mode, "alerts": maintained, "created": [r for r in maintained if r.get("action") == "created"], "maintained": maintained, "skipped": skipped, "channels": channel_map}


def activate(user_id, mode="balanced", browser_permission=None):
    mode = _mode(mode)
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET auto_signals_enabled=1, auto_signals_mode=?, auto_signals_started_at=COALESCE(auto_signals_started_at, ?),
            auto_signals_paused_at=NULL, auto_signals_stopped_at=NULL, updated_at=?
        WHERE user_id=?
        """,
        (mode, now, now, user_id),
    )
    conn.commit()
    conn.close()
    result = maintain_user(user_id, mode, browser_permission=browser_permission)
    result.update({"enabled": True, "message": f"Auto Signals active in {mode.title()} mode. Monitoring continues until you stop it."})
    return result


def stop(user_id):
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET auto_signals_enabled=0, auto_signals_stopped_at=?, updated_at=? WHERE user_id=?",
        (now, now, user_id),
    )
    cur.execute(
        """
        UPDATE alert_rules
        SET status='paused', active=0, updated_at=?
        WHERE user_id=? AND target LIKE ?
        """,
        (now, user_id, f"{AUTO_TARGET_PREFIX}:%"),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "enabled": False, "message": "Auto Signals stopped."}


def pause(user_id):
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET auto_signals_enabled=0, auto_signals_paused_at=?, updated_at=? WHERE user_id=?",
        (now, now, user_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "enabled": False, "message": "Auto Signals paused."}


def status(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT auto_signals_enabled, auto_signals_mode, auto_signals_started_at, auto_signals_last_checked_at,
               auto_signals_paused_at, auto_signals_stopped_at
        FROM users WHERE user_id=? LIMIT 1
        """,
        (user_id,),
    )
    user = _row(cur.fetchone())
    cur.execute(
        """
        SELECT COUNT(*) AS total FROM alert_rules
        WHERE user_id=? AND target LIKE ? AND COALESCE(status, 'active')='active'
        """,
        (user_id, f"{AUTO_TARGET_PREFIX}:%"),
    )
    active_count = int((_row(cur.fetchone()).get("total") or 0))
    cur.execute(
        """
        SELECT symbol, condition, threshold_value, last_triggered_at, updated_at FROM alert_rules
        WHERE user_id=? AND target LIKE ?
        ORDER BY COALESCE(last_triggered_at, updated_at, created_at) DESC, id DESC
        LIMIT 1
        """,
        (user_id, f"{AUTO_TARGET_PREFIX}:%"),
    )
    latest = _row(cur.fetchone())
    conn.close()
    last_checked = user.get("auto_signals_last_checked_at") or ""
    next_eta = ""
    if last_checked:
        try:
            next_eta = (datetime.fromisoformat(last_checked) + timedelta(seconds=45)).isoformat(timespec="seconds")
        except Exception:
            next_eta = ""
    last_signal = ""
    if latest:
        last_signal = f"{latest.get('symbol')} {latest.get('condition')} {latest.get('threshold_value')}"
    return {
        "ok": True,
        "enabled": bool(int(user.get("auto_signals_enabled") or 0)),
        "mode": _mode(user.get("auto_signals_mode")),
        "started_at": user.get("auto_signals_started_at") or "",
        "last_checked_at": last_checked,
        "paused_at": user.get("auto_signals_paused_at") or "",
        "stopped_at": user.get("auto_signals_stopped_at") or "",
        "active_alerts_count": active_count,
        "last_signal": last_signal,
        "next_check_eta": next_eta,
    }


def process_enabled_users(limit=200):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, auto_signals_mode FROM users
        WHERE COALESCE(auto_signals_enabled, 0)=1
        ORDER BY COALESCE(auto_signals_last_checked_at, auto_signals_started_at, created_at) ASC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = [_row(row) for row in cur.fetchall()]
    conn.close()
    checked = 0
    errors = 0
    maintained = 0
    last_error = ""
    for row in rows:
        try:
            checked += 1
            result = maintain_user(row.get("user_id"), row.get("auto_signals_mode") or "balanced")
            maintained += len(result.get("maintained") or [])
        except Exception as exc:
            errors += 1
            last_error = str(exc)
            logging.exception("Auto Signals worker maintenance failed user_id=%s", row.get("user_id"))
    return {"ok": True, "checked_users": checked, "maintained_rules": maintained, "error_count": errors, "last_error": last_error}
