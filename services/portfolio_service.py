import json
from datetime import datetime

from . import market_data, pro_access, user_context

SAFETY = "Portfolio tracker is educational only. CoinPilotXAI Inc. does not hold funds or provide financial advice."
FREE_LIMITS = {"holdings": 3, "watchlist": 5, "alerts": 2}


def _now():
    return datetime.now().isoformat()


def _row_dict(row):
    return dict(row) if row else None


def _rows(cur):
    return [dict(row) for row in cur.fetchall()]


def get_live_price(symbol):
    item = market_data.get_symbol((symbol or "").upper())
    if not item:
        return None
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "price": item.get("price"),
        "change_24h": item.get("change_24h"),
        "volume_24h": item.get("volume_24h"),
        "market_cap": item.get("market_cap"),
        "image": item.get("image") or "",
    }


def user_has_pro(user_id):
    row = user_context.get_user_by_id(user_id)
    return pro_access.has_pro_access(row or {})


def _count_table(user_id, table, active_only=False):
    conn = user_context.connect()
    cur = conn.cursor()
    if active_only:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=? AND COALESCE(active, 1)=1", (user_id,))
    else:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def _limit_check(user_id, kind):
    if user_has_pro(user_id):
        return True, ""
    count = _count_table(user_id, {"holdings": "portfolio_items", "watchlist": "watchlist_items", "alerts": "user_alerts"}[kind], active_only=(kind == "alerts"))
    limit = FREE_LIMITS[kind]
    if count >= limit:
        return False, f"Free accounts can save up to {limit} {kind}. Upgrade Pro for higher limits."
    return True, ""


def add_portfolio_item(user_id, symbol, coin_name="", amount=0, average_buy_price=0, notes=""):
    ok, message = _limit_check(user_id, "holdings")
    if not ok:
        return {"ok": False, "message": message}
    symbol = (symbol or "").upper().strip()[:16]
    if not symbol:
        return {"ok": False, "message": "Enter a coin symbol like BTC, ETH, or SOL."}
    amount = float(amount or 0)
    average_buy_price = float(average_buy_price or 0)
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, average_buy_price, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, symbol, coin_name or symbol, amount, average_buy_price, notes[:500], _now(), _now()),
    )
    conn.commit()
    conn.close()
    log_activity(user_id, "portfolio_item_added", symbol, {"amount": amount})
    return {"ok": True, "message": "Holding added."}


def update_portfolio_item(user_id, item_id, data):
    fields = []
    values = []
    for key in ("symbol", "coin_name", "amount", "average_buy_price", "notes"):
        if key in data:
            fields.append(f"{key}=?")
            value = data[key]
            if key == "symbol":
                value = str(value).upper().strip()[:16]
            if key in {"amount", "average_buy_price"}:
                value = float(value or 0)
            if key == "notes":
                value = str(value)[:500]
            values.append(value)
    if not fields:
        return {"ok": False, "message": "No changes provided."}
    fields.append("updated_at=?")
    values.append(_now())
    values.extend([item_id, user_id])
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(f"UPDATE portfolio_items SET {', '.join(fields)} WHERE id=? AND user_id=?", values)
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if changed:
        log_activity(user_id, "portfolio_item_updated", str(item_id), {})
    return {"ok": bool(changed), "message": "Holding updated." if changed else "Holding not found."}


def delete_portfolio_item(user_id, item_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM portfolio_items WHERE id=? AND user_id=?", (item_id, user_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if changed:
        log_activity(user_id, "portfolio_item_deleted", str(item_id), {})
    return {"ok": bool(changed), "message": "Holding deleted." if changed else "Holding not found."}


def add_watchlist_item(user_id, symbol, coin_name=""):
    ok, message = _limit_check(user_id, "watchlist")
    if not ok:
        return {"ok": False, "message": message}
    symbol = (symbol or "").upper().strip()[:16]
    if not symbol:
        return {"ok": False, "message": "Enter a coin symbol like BTC, ETH, or SOL."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO watchlist_items (user_id, symbol, coin_name, created_at) VALUES (?, ?, ?, ?)",
        (user_id, symbol, coin_name or symbol, _now()),
    )
    conn.commit()
    conn.close()
    log_activity(user_id, "watchlist_item_added", symbol, {})
    return {"ok": True, "message": "Watchlist coin saved."}


def delete_watchlist_item(user_id, item_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist_items WHERE id=? AND user_id=?", (item_id, user_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return {"ok": bool(changed), "message": "Watchlist item deleted." if changed else "Watchlist item not found."}


def create_price_alert(user_id, alert_type, symbol, target_value, condition="above", channel="telegram"):
    ok, message = _limit_check(user_id, "alerts")
    if not ok:
        return {"ok": False, "message": message}
    symbol = (symbol or "").upper().strip()[:16]
    if not symbol:
        return {"ok": False, "message": "Enter a coin symbol."}
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_alerts (user_id, alert_type, symbol, target_value, condition, channel, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (user_id, alert_type or "price", symbol, float(target_value or 0), condition or "above", channel or "telegram", _now(), _now()),
    )
    conn.commit()
    conn.close()
    log_activity(user_id, "alert_created", symbol, {"condition": condition, "target_value": target_value})
    return {"ok": True, "message": "Alert created."}


def delete_alert(user_id, alert_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_alerts WHERE id=? AND user_id=?", (alert_id, user_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return {"ok": bool(changed), "message": "Alert deleted." if changed else "Alert not found."}


def calculate_user_portfolio(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM portfolio_items WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    holdings = _rows(cur)
    conn.close()
    enriched = []
    total_value = 0.0
    total_cost = 0.0
    warning = ""
    for item in holdings:
        live = get_live_price(item.get("symbol"))
        if not live or live.get("price") is None:
            warning = "Live price feed temporarily unavailable."
            price = None
            value = 0.0
            change = None
        else:
            price = float(live.get("price") or 0)
            value = float(item.get("amount") or 0) * price
            change = live.get("change_24h")
        cost = float(item.get("amount") or 0) * float(item.get("average_buy_price") or 0)
        pnl = value - cost
        pnl_percent = (pnl / cost * 100) if cost else 0
        total_value += value
        total_cost += cost
        enriched.append({**item, "price": price, "value": value, "cost": cost, "pnl_value": pnl, "pnl_percent": pnl_percent, "change_24h": change})
    total_pnl = total_value - total_cost
    total_pnl_percent = (total_pnl / total_cost * 100) if total_cost else 0
    top_gainer = max(enriched, key=lambda x: x.get("pnl_percent", -999999), default=None)
    top_loser = min(enriched, key=lambda x: x.get("pnl_percent", 999999), default=None)
    return {
        "holdings": enriched,
        "total_value": total_value,
        "total_cost": total_cost,
        "pnl_value": total_pnl,
        "pnl_percent": total_pnl_percent,
        "top_gainer": top_gainer,
        "top_loser": top_loser,
        "warning": warning,
    }


def get_watchlist(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM watchlist_items WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = _rows(cur)
    conn.close()
    enriched = []
    for item in rows:
        live = get_live_price(item.get("symbol"))
        enriched.append({**item, **(live or {}), "trend": "up" if live and (live.get("change_24h") or 0) > 0 else "down" if live and (live.get("change_24h") or 0) < 0 else "mixed"})
    return enriched


def get_alerts(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_alerts WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = _rows(cur)
    conn.close()
    return rows


def log_activity(user_id, event_type, event_label="", metadata=None):
    try:
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_activity (user_id, event_type, event_label, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, event_type, event_label, json.dumps(metadata or {})[:4000], _now()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def activity_timeline(user_id, limit=20):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_activity WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
    rows = _rows(cur)
    conn.close()
    return rows


def portfolio_insight(portfolio, watchlist, pro=False):
    holdings = portfolio.get("holdings", [])
    if not holdings:
        return "Add your first holding to unlock portfolio insight. " + SAFETY
    total = portfolio.get("total_value") or 0
    largest = max(holdings, key=lambda x: x.get("value") or 0)
    concentration = ((largest.get("value") or 0) / total * 100) if total else 0
    risk = "Elevated" if concentration >= 65 else "Medium" if concentration >= 40 else "Balanced"
    movement = sorted(holdings, key=lambda x: abs(x.get("change_24h") or 0), reverse=True)[:3]
    moved = ", ".join(f"{item.get('symbol')} {float(item.get('change_24h') or 0):+.2f}%" for item in movement if item.get("change_24h") is not None) or "24h moves unavailable"
    detail = "Pro view includes deeper concentration, volatility, alert, and watchlist context." if pro else "Free view includes basic concentration and movement context."
    return (
        f"Risk Reminder: {SAFETY}\n\n"
        f"Portfolio concentration: {largest.get('symbol')} is about {concentration:.1f}% of tracked value.\n"
        f"Today's Risk Level: {risk}\n"
        f"What moved today: {moved}\n"
        f"What to watch: Review concentration, 24h volatility, and whether any alert thresholds are close.\n"
        f"{detail}"
    )


def get_user_dashboard_data(user_id):
    user = user_context.get_user_by_id(user_id) or {}
    pro = pro_access.has_pro_access(user)
    status = (user.get("subscription_status") or "inactive").lower()
    paid_pro = (
        pro
        and status == "active"
        and bool(user.get("stripe_subscription_id") or user.get("stripe_customer_id"))
    )
    trialing = pro and status == "trialing" and not paid_pro
    portfolio = calculate_user_portfolio(user_id)
    watchlist = get_watchlist(user_id)
    alerts = get_alerts(user_id)
    market = market_data.live_market_board(limit=12)
    data = {
        "ok": True,
        "user": {
            "name": user.get("full_name") or user.get("display_name") or "CoinPilotX user",
            "email": user_context.mask_email(user.get("email")),
            "plan": "Paid Pro" if paid_pro else "Pro Trial" if trialing else "Pro" if pro else "Free",
            "subscription_status": user.get("subscription_status") or "inactive",
            "has_pro_access": pro,
            "is_paid_pro": paid_pro,
            "is_trialing": trialing,
            "pro_expires_at": user.get("pro_expires_at") or (user.get("trial_end_date") if trialing else "") or "",
            "trial_end_date": user.get("trial_end_date") or "",
            "stripe_subscription_id": user.get("stripe_subscription_id") or "",
            "telegram_linked": bool(user.get("telegram_user_id")),
            "telegram_username": user.get("telegram_username") or "",
        },
        "limits": {"pro": pro, **({} if pro else FREE_LIMITS)},
        "portfolio": portfolio,
        "watchlist": watchlist,
        "alerts": alerts,
        "market": market,
        "ai_insight": portfolio_insight(portfolio, watchlist, pro=pro),
        "activity": activity_timeline(user_id),
        "safety": SAFETY,
    }
    save_snapshot(user_id, portfolio)
    return data


def save_snapshot(user_id, portfolio):
    try:
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO portfolio_snapshots (user_id, total_value, total_cost, pnl_value, pnl_percent, holdings_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                portfolio.get("total_value") or 0,
                portfolio.get("total_cost") or 0,
                portfolio.get("pnl_value") or 0,
                portfolio.get("pnl_percent") or 0,
                json.dumps(portfolio.get("holdings", []))[:8000],
                _now(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def check_alerts():
    return {"ok": True, "checked_at": _now(), "triggered": []}


def send_telegram_alert(*_args, **_kwargs):
    return {"ok": False, "message": "Telegram alert sending is handled by the bot runtime."}
