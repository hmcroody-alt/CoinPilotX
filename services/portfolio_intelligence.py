"""Premium portfolio intelligence over the real portfolio backend.

Valuation is computed from ``portfolio_items`` (amount, average_buy_price)
priced by the existing CoinGecko-backed market board (``services.market_data``).
History is read from real ``portfolio_snapshots`` rows and, when the optional
``services.market_observations`` module is importable, densified with real
observed prices.

Honesty rules (do not weaken):
- ``change_24h_pct`` is only reported when a real ~24h-old data point exists
  (a portfolio snapshot, or observed prices for every priced holding). It is
  never estimated or extrapolated.
- Unrealized P/L is only computed for holdings that carry an
  ``average_buy_price``. There is no transaction ledger in this product, so
  realized P/L does not exist and must not be fabricated.
- History points are real snapshots/observations only — no interpolation.
"""

import json
import logging
from datetime import datetime, timedelta

VALID_PERIODS = {
    "24h": 1.0,
    "7d": 7.0,
    "30d": 30.0,
    "90d": 90.0,
    "1y": 365.0,
    "all": None,
}

# Append-on-read snapshot cadence: at most one snapshot per user per hour.
SNAPSHOT_MIN_INTERVAL_SECONDS = 3600

# A prior data point counts as "24 hours ago" when it falls inside 24h +/- 6h.
CHANGE_24H_TARGET_SECONDS = 24 * 3600
CHANGE_24H_TOLERANCE_SECONDS = 6 * 3600

# Coverage is "full" when real points span at least this share of the window.
FULL_COVERAGE_RATIO = 0.9


def _now():
    return datetime.now()


def _now_iso():
    return _now().isoformat()


def _connect():
    from services import db

    return db.connect()


def _market_board():
    """The cached CoinGecko-backed market board, or None when unreachable."""
    try:
        from services import market_data

        return market_data.live_market_board(limit=250)
    except Exception as exc:  # pragma: no cover - network path
        logging.info("Portfolio intelligence: market board unavailable: %s", exc)
        return None


def _parse_ts(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _valid_user_id(user_id):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    return uid if uid > 0 else None


def _user_exists(cur, user_id):
    """True when the user row exists; permissive if the users table is absent."""
    try:
        cur.execute("SELECT 1 FROM users WHERE user_id=? LIMIT 1", (user_id,))
        return cur.fetchone() is not None
    except Exception:
        return True


def _load_holdings(cur, user_id):
    cur.execute(
        """
        SELECT symbol, coin_name, amount, average_buy_price
        FROM portfolio_items
        WHERE user_id=? AND COALESCE(amount, 0) > 0
        ORDER BY id ASC
        """,
        (user_id,),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        abp = item.get("average_buy_price")
        try:
            abp = float(abp) if abp is not None else None
        except (TypeError, ValueError):
            abp = None
        # A stored 0 means "not provided" (the write path coerces blank -> 0).
        if abp is not None and abp <= 0:
            abp = None
        rows.append(
            {
                "symbol": symbol,
                "coin_name": str(item.get("coin_name") or "") or symbol,
                "amount": float(item.get("amount") or 0),
                "average_buy_price": abp,
            }
        )
    return rows


def _snapshot_value_near_24h_ago(cur, user_id, now):
    """Real snapshot total_value closest to 24h ago (within tolerance), or None."""
    try:
        cur.execute(
            "SELECT total_value, created_at FROM portfolio_snapshots WHERE user_id=? ORDER BY id DESC LIMIT 500",
            (user_id,),
        )
        rows = cur.fetchall()
    except Exception:
        return None
    best = None
    best_offset = None
    for row in rows:
        item = dict(row)
        ts = _parse_ts(item.get("created_at"))
        if ts is None:
            continue
        age = (now - ts).total_seconds()
        offset = abs(age - CHANGE_24H_TARGET_SECONDS)
        if offset > CHANGE_24H_TOLERANCE_SECONDS:
            continue
        if best_offset is None or offset < best_offset:
            best_offset = offset
            try:
                best = float(item.get("total_value"))
            except (TypeError, ValueError):
                best = None
    return best


def _observed_value_near_24h_ago(holdings, now):
    """Portfolio value ~24h ago from real market observations, or None.

    Requires an observation near 24h ago for EVERY holding — a partial
    reconstruction would misstate the change, so it is refused.
    """
    if not holdings:
        return None
    try:
        from services import market_observations
    except ImportError:
        return None
    getter = getattr(market_observations, "get_observations", None)
    if not callable(getter):
        return None
    target = now - timedelta(seconds=CHANGE_24H_TARGET_SECONDS)
    start = (target - timedelta(seconds=CHANGE_24H_TOLERANCE_SECONDS)).isoformat()
    end = (target + timedelta(seconds=CHANGE_24H_TOLERANCE_SECONDS)).isoformat()
    total = 0.0
    try:
        for item in holdings:
            observations = getter(item["symbol"], start, end) or []
            best_price = None
            best_offset = None
            for obs in observations:
                ts = _parse_ts(obs.get("observed_at") or obs.get("created_at") or obs.get("t"))
                price = obs.get("price") if obs.get("price") is not None else obs.get("value")
                if ts is None or price is None:
                    continue
                offset = abs((ts - target).total_seconds())
                if best_offset is None or offset < best_offset:
                    best_offset = offset
                    best_price = float(price)
            if best_price is None:
                return None
            total += item["amount"] * best_price
    except Exception as exc:
        logging.info("Portfolio intelligence: observation lookup failed: %s", exc)
        return None
    return total


def _maybe_append_snapshot(cur, conn, user_id, total_value, total_cost, unrealized_pl, holdings, now):
    """Append a portfolio snapshot at most once per hour per user.

    Snapshots are otherwise only written ad hoc (dashboard reads), so this
    keeps history accruing for users who only hit the mobile valuation route.
    Idempotent within the hour and never allowed to break the read path.
    """
    try:
        cur.execute(
            "SELECT created_at FROM portfolio_snapshots WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            last = _parse_ts(dict(row).get("created_at"))
            if last is not None and (now - last).total_seconds() < SNAPSHOT_MIN_INTERVAL_SECONDS:
                return False
        pnl_value = unrealized_pl if unrealized_pl is not None else 0.0
        pnl_percent = (pnl_value / total_cost * 100.0) if total_cost else 0.0
        cur.execute(
            """
            INSERT INTO portfolio_snapshots (user_id, total_value, total_cost, pnl_value, pnl_percent, holdings_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                float(total_value),
                float(total_cost),
                float(pnl_value),
                float(pnl_percent),
                json.dumps(holdings)[:8000],
                now.isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        logging.info("Portfolio intelligence: snapshot append skipped: %s", exc)
        return False


def compute_portfolio_valuation(user_id):
    """Current portfolio valuation for one user. See module docstring for rules."""
    uid = _valid_user_id(user_id)
    if uid is None:
        return {"ok": False, "code": "invalid_user"}
    now = _now()
    try:
        conn = _connect()
    except Exception as exc:
        logging.info("Portfolio intelligence: db unavailable: %s", exc)
        return {"ok": False, "code": "storage_unavailable"}
    try:
        cur = conn.cursor()
        if not _user_exists(cur, uid):
            return {"ok": False, "code": "user_not_found"}
        items = _load_holdings(cur, uid)

        calculated_at = now.isoformat()
        if not items:
            return {
                "ok": True,
                "total_value": 0.0,
                "calculated_at": calculated_at,
                "market_data_observed_at": calculated_at,
                "change_24h_pct": None,
                "unrealized_pl": None,
                "holdings": [],
                "concentration": {"top_symbol": None, "top_pct": 0.0},
            }

        board = _market_board()
        markets = {}
        for market_row in (board or {}).get("markets") or []:
            symbol = str(market_row.get("symbol") or "").upper()
            if symbol and symbol not in markets:
                markets[symbol] = market_row

        holdings = []
        total_value = 0.0
        total_cost = 0.0
        unrealized_total = None
        priced_any = False
        for item in items:
            market_row = markets.get(item["symbol"]) or {}
            price = market_row.get("price")
            price = float(price) if price is not None else None
            value = item["amount"] * price if price is not None else 0.0
            if price is not None:
                priced_any = True
            unrealized = None
            if item["average_buy_price"] is not None:
                total_cost += item["amount"] * item["average_buy_price"]
                if price is not None:
                    unrealized = (price - item["average_buy_price"]) * item["amount"]
                    unrealized_total = (unrealized_total or 0.0) + unrealized
            total_value += value
            holdings.append(
                {
                    "asset_id": market_row.get("id") or item["symbol"].lower(),
                    "symbol": item["symbol"],
                    "name": market_row.get("name") or item["coin_name"],
                    "amount": item["amount"],
                    "current_price": price,
                    "current_value": value,
                    "allocation_pct": 0.0,
                    "average_buy_price": item["average_buy_price"],
                    "unrealized_pl": unrealized,
                }
            )

        if not priced_any:
            return {"ok": False, "code": "market_data_unavailable"}

        for holding in holdings:
            holding["allocation_pct"] = (
                holding["current_value"] / total_value * 100.0 if total_value else 0.0
            )
        top = max(holdings, key=lambda h: h["current_value"])
        concentration = {"top_symbol": top["symbol"], "top_pct": top["allocation_pct"]}

        change_24h_pct = None
        prior_value = _snapshot_value_near_24h_ago(cur, uid, now)
        if prior_value is None:
            prior_value = _observed_value_near_24h_ago(items, now)
        if prior_value is not None and prior_value > 0 and total_value > 0:
            change_24h_pct = (total_value - prior_value) / prior_value * 100.0

        _maybe_append_snapshot(cur, conn, uid, total_value, total_cost, unrealized_total, holdings, now)

        observed_at = _parse_ts((board or {}).get("updated_at"))
        return {
            "ok": True,
            "total_value": total_value,
            "calculated_at": calculated_at,
            "market_data_observed_at": observed_at.isoformat() if observed_at else calculated_at,
            "change_24h_pct": change_24h_pct,
            "unrealized_pl": unrealized_total,
            "holdings": holdings,
            "concentration": concentration,
        }
    except Exception as exc:
        logging.exception("Portfolio intelligence valuation failed for user %s: %s", uid, exc)
        return {"ok": False, "code": "valuation_failed"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _observation_history_points(cur, user_id, cutoff, now):
    """Real observed-price portfolio points for densification, or [].

    Uses hourly buckets and only emits a point when EVERY held symbol has an
    observation in that bucket — never a partially-reconstructed value.
    Degrades to [] whenever the optional observation service is absent or its
    shape differs.
    """
    try:
        from services import market_observations
    except ImportError:
        return []
    getter = getattr(market_observations, "get_observations", None)
    if not callable(getter):
        return []
    try:
        items = _load_holdings(cur, user_id)
        if not items:
            return []
        start = (cutoff or (now - timedelta(days=365 * 10))).isoformat()
        end = now.isoformat()
        per_symbol = {}
        for item in items:
            buckets = {}
            for obs in getter(item["symbol"], start, end) or []:
                ts = _parse_ts(obs.get("observed_at") or obs.get("created_at") or obs.get("t"))
                price = obs.get("price") if obs.get("price") is not None else obs.get("value")
                if ts is None or price is None:
                    continue
                bucket = ts.replace(minute=0, second=0, microsecond=0)
                previous = buckets.get(bucket)
                if previous is None or ts > previous[0]:
                    buckets[bucket] = (ts, float(price))
            per_symbol[item["symbol"]] = buckets
        shared = None
        for buckets in per_symbol.values():
            keys = set(buckets)
            shared = keys if shared is None else shared & keys
        points = []
        for bucket in sorted(shared or ()):
            value = sum(
                item["amount"] * per_symbol[item["symbol"]][bucket][1] for item in items
            )
            points.append({"t": bucket.isoformat(), "value": value})
        return points
    except Exception as exc:
        logging.info("Portfolio intelligence: history densification skipped: %s", exc)
        return []


def get_portfolio_history(user_id, period):
    """Real portfolio value history for one user over a named period."""
    period_key = str(period or "").strip().lower()
    if period_key not in VALID_PERIODS:
        return {"ok": False, "code": "invalid_period"}
    uid = _valid_user_id(user_id)
    if uid is None:
        return {"ok": False, "code": "invalid_user"}
    now = _now()
    days = VALID_PERIODS[period_key]
    cutoff = now - timedelta(days=days) if days is not None else None
    try:
        conn = _connect()
    except Exception as exc:
        logging.info("Portfolio intelligence: db unavailable: %s", exc)
        return {"ok": False, "code": "storage_unavailable"}
    try:
        cur = conn.cursor()
        if not _user_exists(cur, uid):
            return {"ok": False, "code": "user_not_found"}
        cur.execute(
            "SELECT total_value, created_at FROM portfolio_snapshots WHERE user_id=? ORDER BY id ASC",
            (uid,),
        )
        by_time = {}
        for row in cur.fetchall():
            item = dict(row)
            ts = _parse_ts(item.get("created_at"))
            if ts is None or (cutoff is not None and ts < cutoff):
                continue
            try:
                by_time[ts] = float(item.get("total_value") or 0)
            except (TypeError, ValueError):
                continue
        for point in _observation_history_points(cur, uid, cutoff, now):
            ts = _parse_ts(point.get("t"))
            if ts is None or (cutoff is not None and ts < cutoff) or ts in by_time:
                continue
            by_time[ts] = float(point.get("value") or 0)
        points = [
            {"t": ts.isoformat(), "value": by_time[ts]} for ts in sorted(by_time)
        ]
        if not points:
            coverage = "none"
        elif cutoff is None:
            coverage = "full"
        else:
            window = timedelta(days=days).total_seconds()
            covered = (now - _parse_ts(points[0]["t"])).total_seconds()
            coverage = "full" if covered >= FULL_COVERAGE_RATIO * window else "partial"
        return {"ok": True, "period": period_key, "points": points, "coverage": coverage}
    except Exception as exc:
        logging.exception("Portfolio intelligence history failed for user %s: %s", uid, exc)
        return {"ok": False, "code": "history_failed"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
