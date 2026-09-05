import json
import logging
from datetime import datetime

from . import market_data, portfolio_events, pro_access, user_context

SAFETY = "Portfolio tracker is educational only. CoinPlotXAI Inc. does not hold funds or provide financial advice."

#: What a free account may *add*. Premium removes the ceiling entirely.
#:
#: These numbers are not new. They were already published to every client in the
#: dashboard's ``limits`` block while :func:`_limit_check` returned "allowed"
#: unconditionally — so the product has been stating a rule it did not apply.
#: Enforcing them makes the existing statement true rather than inventing a
#: restriction, and it is what gives ``premium.crypto.portfolio`` a gate that
#: reads it instead of a grant that changes nothing.
#:
#: Two of the three ceilings this dict used to advertise are gone, because
#: neither described anything the product does.
#:
#: The alert ceiling applied to ``user_alerts``, the legacy table behind
#: :func:`create_price_alert`, which no live route reaches. Alerts run through
#: ``services.alert_engine`` and are gated on *capability* — compound and
#: watchlist rules are Premium, single-threshold rules are free and unlimited —
#: so a count described no shipping behaviour.
#:
#: The watchlist ceiling counted ``watchlist_items``, and PulseSoc has a second,
#: newer watchlist system at ``/api/crypto/watchlists`` which is the one the
#: native app uses, the one alert watchlist rules read, and entirely unlimited.
#: Capping the older table would have told one member two different numbers for
#: "your watchlist" depending on which screen they were looking at. Whether free
#: watchlists should have a size at all is a product question, and the answer
#: has to be given once, to both systems, not smuggled in against the one that
#: happened to have a limit constant lying around.
FREE_LIMITS = {"holdings": 3}

#: Which table each ceiling counts. Values are module constants, never input.
_LIMIT_TABLES = {"holdings": "portfolio_items"}

LIMIT_MESSAGES = {
    "holdings": (
        f"A free portfolio tracks up to {FREE_LIMITS['holdings']} holdings. "
        "PulseSoc Premium removes the limit."
    ),
}


def _now():
    return datetime.now().isoformat()


def _row_dict(row):
    return dict(row) if row else None


def _rows(cur):
    return [dict(row) for row in cur.fetchall()]


def _kick_projection(user_id):
    """Best-effort post-commit nudge to the Capital Graph projector.

    The durable leg is the outbox row already committed alongside the
    mutation; this call only makes the graph feel live. Any failure here is
    logged and absorbed — the projection converges on its next read.
    """
    if not portfolio_events.projection_enabled():
        return
    try:
        from services.private_office import portfolio_projection

        portfolio_projection.process_pending(user_id)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("portfolio_service").info(
            "PORTFOLIO_PROJECTION_KICK_FAILED user=%s error=%s", user_id, exc)


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


def has_premium_portfolio(user_id):
    """Does this account hold ``premium.crypto.portfolio``?

    Resolved through :mod:`services.premium_crypto_access` — the same reader the
    alert engine consults — so the portfolio and the alerts cannot end up with
    different opinions about who is a member. That module already owns the
    off/shadow/canonical precedence and the account-hold rule; this is a caller,
    not a fourth authority.

    An import failure is treated as *entitled*. This gate only ever removes a
    ceiling, so the two ways it can be wrong are not symmetric: failing open
    lets a free account add a fourth holding, failing closed tells a paying
    member they cannot touch their own portfolio. The first is recoverable.
    """
    try:
        from services import premium_crypto_access
        return bool(
            premium_crypto_access.allowed_for_user_id(
                user_id, premium_crypto_access.PORTFOLIO
            )
        )
    except Exception:  # noqa: BLE001 — a missing reader must not lock anyone out
        return True


#: The code a ceiling refusal carries, so a client can tell "you have reached
#: the free limit" apart from "that symbol is not a symbol" and open the right
#: surface. Mirrors how the alert engine answers a capability denial.
PREMIUM_REQUIRED = "premium_required"


def _limit_check(user_id, kind):
    """May this account add one more of ``kind``? ``(ok, message, code)``.

    Enforced on creation only, and deliberately never on read. An account that
    is already over the ceiling — because the ceiling was published for a long
    time without being applied — keeps every holding it has and continues to see
    all of them; it simply cannot add another until it removes one or
    subscribes. Trimming to make the limit true retroactively would delete a
    member's own records to settle a bookkeeping question.
    """
    ceiling = FREE_LIMITS.get(kind)
    table = _LIMIT_TABLES.get(kind)
    if not ceiling or not table:
        return True, "", ""
    if has_premium_portfolio(user_id):
        return True, "", ""
    try:
        current = _count_table(user_id, table)
    except Exception:  # noqa: BLE001
        # A table that cannot be counted is not evidence of an over-limit
        # account, and refusing on it would break adding for everyone.
        return True, "", ""
    if current < ceiling:
        return True, "", ""
    return False, LIMIT_MESSAGES[kind], PREMIUM_REQUIRED


def _limit_refusal(message, code):
    """The one shape a ceiling refusal takes, wherever it is raised.

    Deliberately identical to the shape ``services.alert_engine`` already
    returns for a capability denial — ``ok``/``code``/``capability``/``message``
    — so one client handler serves both and neither has to be special-cased.

    ``error`` duplicates ``code`` because the native client reads
    ``data.error_code`` or ``data.error`` when it builds ``PulseApiError.code``;
    a refusal carrying only ``code`` would arrive at the app as an untyped
    failure and the upgrade sheet would never open. ``_crypto_api_result``
    duplicates it for exactly the same reason.
    """
    from services import premium_crypto_access
    return {"ok": False, "message": message, "code": code, "error": code,
            "capability": premium_crypto_access.PORTFOLIO}


def add_portfolio_item(user_id, symbol, coin_name="", amount=0, average_buy_price=0, notes=""):
    ok, message, code = _limit_check(user_id, "holdings")
    if not ok:
        return _limit_refusal(message, code)
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
    portfolio_events.enqueue(
        cur, user_id=user_id, event_type=portfolio_events.EVENT_HOLDING_ADDED,
        symbol=symbol)
    conn.commit()
    conn.close()
    log_activity(user_id, "portfolio_item_added", symbol, {"amount": amount})
    _kick_projection(user_id)
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
    changed = cur.rowcount
    if changed:
        portfolio_events.enqueue(
            cur, user_id=user_id,
            event_type=portfolio_events.EVENT_HOLDING_UPDATED,
            item_id=int(item_id or 0))
    conn.commit()
    conn.close()
    if changed:
        log_activity(user_id, "portfolio_item_updated", str(item_id), {})
        _kick_projection(user_id)
    return {"ok": bool(changed), "message": "Holding updated." if changed else "Holding not found."}


def delete_portfolio_item(user_id, item_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM portfolio_items WHERE id=? AND user_id=?", (item_id, user_id))
    changed = cur.rowcount
    if changed:
        portfolio_events.enqueue(
            cur, user_id=user_id,
            event_type=portfolio_events.EVENT_HOLDING_REMOVED,
            item_id=int(item_id or 0))
    conn.commit()
    conn.close()
    if changed:
        log_activity(user_id, "portfolio_item_deleted", str(item_id), {})
        _kick_projection(user_id)
    return {"ok": bool(changed), "message": "Holding deleted." if changed else "Holding not found."}


def add_watchlist_item(user_id, symbol, coin_name=""):
    ok, message, code = _limit_check(user_id, "watchlist")
    if not ok:
        return _limit_refusal(message, code)
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
    ok, message, code = _limit_check(user_id, "alerts")
    if not ok:
        return _limit_refusal(message, code)
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


def _value_holding(item, live):
    """Value one holding, keeping "unknown" distinct from "zero" at every step.

    Two facts can go missing independently and they fail in different ways:

    * **No live price.** The holding's value is unknown. Calling it zero makes a
      total that silently omits the asset, and — because profit used to be
      computed as ``value - cost`` regardless — turns an unquoted asset into a
      fabricated total loss that then flows into the portfolio percentage, the
      "top loser" slot, and the saved snapshot.
    * **No cost basis.** Holdings carried over from the original CoinPilotX
      portfolio have an amount but no buy price. Their value is knowable and
      their profit is not; reporting break-even would invent a basis they never
      had.

    So ``price``/``value``/``cost``/``pnl_value``/``pnl_percent`` are each
    ``None`` when the input for them is absent, and ``priced`` states which case
    the caller is looking at without having to test for ``None``.
    """
    amount = float(item.get("amount") or 0)
    basis = float(item.get("average_buy_price") or 0)
    priced = bool(live) and live.get("price") is not None
    price = float(live.get("price") or 0) if priced else None
    value = amount * price if price is not None else None
    # A legacy row's basis is absent rather than zero, so it earns no cost line.
    cost = amount * basis if (basis > 0 and not item.get("legacy")) else None
    if value is None or cost is None:
        pnl = None
        pnl_percent = None
    else:
        pnl = value - cost
        pnl_percent = (pnl / cost * 100) if cost else None
    return {
        "price": price,
        "value": value,
        "cost": cost,
        "pnl_value": pnl,
        "pnl_percent": pnl_percent,
        "change_24h": live.get("change_24h") if priced else None,
        "priced": priced,
    }


def _valuation_warning(unpriced):
    """Name the assets the totals leave out, rather than hinting at an outage.

    The old wording — "Live price feed temporarily unavailable." — told a member
    something was wrong but not that the total under it was short, which is the
    part that actually matters when reading a number.
    """
    if not unpriced:
        return ""
    shown = ", ".join(unpriced[:4])
    if len(unpriced) > 4:
        shown += f" and {len(unpriced) - 4} more"
    return (
        f"Live prices are unavailable for {shown}. "
        "The totals below cover your other holdings only."
    )


def calculate_user_portfolio(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM portfolio_items WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    holdings = _rows(cur)
    known_symbols = {str(item.get("symbol") or "").upper() for item in holdings}
    try:
        cur.execute("SELECT asset, amount FROM manual_portfolio WHERE user_id=? AND amount > 0 ORDER BY asset", (user_id,))
        for row in _rows(cur):
            symbol = str(row.get("asset") or "").upper()
            if symbol and symbol not in known_symbols:
                holdings.append(
                    {
                        "id": 0,
                        "legacy": True,
                        "symbol": symbol,
                        "coin_name": symbol,
                        "amount": float(row.get("amount") or 0),
                        "average_buy_price": 0,
                        "notes": "Imported from your original CoinPilotX portfolio.",
                    }
                )
                known_symbols.add(symbol)
    except Exception:
        pass
    conn.close()
    enriched = []
    total_value = 0.0
    total_cost = 0.0
    total_pnl = 0.0
    unpriced = []
    for item in holdings:
        valued = _value_holding(item, get_live_price(item.get("symbol")))
        if valued["value"] is None:
            unpriced.append(str(item.get("symbol") or "").upper() or "?")
        else:
            total_value += valued["value"]
        # A cost only joins the basis once its value side is known. Adding it
        # otherwise would divide a partial gain by a whole basis and report the
        # missing prices as a loss.
        if valued["pnl_value"] is not None:
            total_cost += valued["cost"]
            total_pnl += valued["pnl_value"]
        enriched.append({**item, **valued})
    total_pnl_percent = (total_pnl / total_cost * 100) if total_cost else 0
    # Only holdings with a real P/L can be ranked. Including the others would
    # let an asset that simply could not be priced win "top loser".
    ranked = [h for h in enriched if h.get("pnl_percent") is not None]
    top_gainer = max(ranked, key=lambda h: h["pnl_percent"], default=None)
    top_loser = min(ranked, key=lambda h: h["pnl_percent"], default=None)
    return {
        "holdings": enriched,
        "total_value": total_value,
        "total_cost": total_cost,
        "pnl_value": total_pnl,
        "pnl_percent": total_pnl_percent,
        "top_gainer": top_gainer,
        "top_loser": top_loser,
        # What the totals above actually cover. A caller that renders a total
        # without reading this is showing a sum over an unstated subset.
        "valuation": {
            "complete": not unpriced,
            "holdings": len(enriched),
            "priced": len(enriched) - len(unpriced),
            "unpriced": len(unpriced),
            "unpriced_symbols": unpriced,
            "basis_known": len(ranked),
        },
        "warning": _valuation_warning(unpriced),
    }


def get_watchlist(user_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM watchlist_items WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = _rows(cur)
    known_symbols = {str(item.get("symbol") or "").upper() for item in rows}
    try:
        cur.execute("SELECT asset FROM watchlists WHERE user_id=? ORDER BY asset", (user_id,))
        for row in _rows(cur):
            symbol = str(row.get("asset") or "").upper()
            if symbol and symbol not in known_symbols:
                rows.append({"id": 0, "legacy": True, "symbol": symbol, "coin_name": symbol})
                known_symbols.add(symbol)
    except Exception:
        pass
    conn.close()
    enriched = []
    for item in rows:
        live = get_live_price(item.get("symbol"))
        enriched.append({**item, **(live or {}), "trend": "up" if live and (live.get("change_24h") or 0) > 0 else "down" if live and (live.get("change_24h") or 0) < 0 else "mixed"})
    return enriched


def watchlist_symbols(user_id):
    """The plain set of symbols on this account's watchlist.

    Deliberately not :func:`get_watchlist`. That one enriches every row with a live
    price, which means a read-back built on it would call a third-party market API
    once per holding and report ``verification_pending`` — an unverified write — the
    moment CoinGecko is slow. A verifier must depend on nothing but the store it is
    checking, so this reads the two canonical tables and stops.
    """
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT symbol FROM watchlist_items WHERE user_id=?", (int(user_id),))
        symbols = {str(row["symbol"] or "").upper() for row in _rows(cur)}
        try:
            cur.execute("SELECT asset FROM watchlists WHERE user_id=?", (int(user_id),))
            symbols |= {str(row["asset"] or "").upper() for row in _rows(cur)}
        except Exception:
            # The legacy table is optional. Its absence is not a failed read-back.
            pass
    finally:
        conn.close()
    return {symbol for symbol in symbols if symbol}


def watchlist_item_id(user_id, symbol):
    """This account's watchlist row id for one symbol, or ``0``.

    Lets a caller address the watchlist by symbol — which is how a person refers to
    it — while the delete still runs against a row id scoped to ``user_id``. The
    resolution happens server-side precisely so a symbol supplied by a model can
    never become an id belonging to somebody else.
    """
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM watchlist_items WHERE user_id=? AND symbol=? ORDER BY id LIMIT 1",
            (int(user_id), str(symbol or "").upper().strip()[:16]),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return int(row["id"]) if row else 0


def list_portfolio_items(user_id):
    """Stored holdings for one account, without market enrichment."""
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, symbol, coin_name, amount, average_buy_price, notes, updated_at
               FROM portfolio_items WHERE user_id=? ORDER BY id DESC""",
            (int(user_id),),
        )
        return _rows(cur)
    finally:
        conn.close()


def get_portfolio_item(user_id, item_id):
    """One holding owned by this account, or ``None``.

    Scoped by ``user_id`` in the WHERE clause rather than filtered afterwards, so a
    holding belonging to another account is indistinguishable from one that does not
    exist and the read cannot be used to probe for other people's rows.
    """
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, symbol, coin_name, amount, average_buy_price, notes, updated_at
               FROM portfolio_items WHERE id=? AND user_id=? LIMIT 1""",
            (int(item_id or 0), int(user_id)),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


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
    detail = "PulseSoc Premium view adds richer creator-grade presentation and prestige context." if pro else "Free core view includes concentration, volatility, alert, and watchlist context."
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
    portfolio_premium = has_premium_portfolio(user_id)
    portfolio = calculate_user_portfolio(user_id)
    watchlist = get_watchlist(user_id)
    alerts = get_alerts(user_id)
    market = market_data.live_market_board(limit=12)
    data = {
        "ok": True,
        "user": {
            "name": user.get("full_name") or user.get("display_name") or "PulseSoc user",
            "email": user_context.mask_email(user.get("email")),
            "plan": "PulseSoc Premium" if paid_pro else "Legacy Trial" if trialing else "Premium" if pro else "Free Core",
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
        # The advertised ceiling and the enforced one have to come from one
        # reader. `pro` above is the legacy subscription flag and stays in the
        # payload for the surfaces that already read it, but what the limits say
        # is now decided by the same gate `_limit_check` applies, so the
        # dashboard cannot promise unlimited holdings while the add path refuses.
        "limits": {
            "pro": pro,
            "portfolio_premium": portfolio_premium,
            **({} if portfolio_premium else FREE_LIMITS),
        },
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
    """Record today's totals — but only when they cover the whole portfolio.

    A snapshot is history: it is read back long after the minute that produced
    it, when nothing remains to say a price feed was down. Writing one while a
    holding could not be priced would put a permanently, invisibly short total
    into the series, and a run of them would read as a real drawdown. No row is
    better than a wrong one, because the next complete pass writes a right one.

    The default is ``True`` so a caller still passing the older portfolio shape
    keeps its existing behaviour rather than silently never snapshotting.
    """
    if not (portfolio.get("valuation") or {}).get("complete", True):
        return
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
