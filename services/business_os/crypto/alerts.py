"""Durable, restart-safe crypto price alerts (Stage 5 Part 4).

Informational only — an alert is a notification trigger, never an order. This
module owns the standing alert definitions and a sweeper that evaluates them
against the unified market quotes and records fired events.

Durability / idempotency design
===============================
Two failure modes a naive "if price > threshold, notify" loop gets wrong:

1. **Chatter.** A simple ``price > threshold`` fires on every tick while the price
   stays above. We store ``last_state`` (``'above'`` / ``'below'``) and only act on
   an actual *edge* — the tick where the side changes.
2. **Double-paging across a restart.** If the process dies after notifying but
   before persisting, a restart re-fires. We derive a deterministic
   ``crossing_key`` for each edge and INSERT it into
   ``business_os_crypto_alert_events`` under a UNIQUE (alert_id, crossing_key)
   index *before* declaring the event new. A replay collides and is a no-op, so a
   crossing notifies exactly once regardless of restarts.

Comparators
===========
* ``above`` / ``below``      — level alerts; fire on the edge INTO the region.
* ``crosses_above`` / ``crosses_below`` — explicit directional crossings.

``repeat_mode``: ``'once'`` deactivates the alert after it first fires; ``'always'``
keeps re-arming on each new crossing (subject to ``cooldown_seconds``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from services import db
from services.business_os.crypto import schema as _schema


class CryptoAlertError(ValueError):
    """Curated alert error (bad comparator / threshold)."""


_COMPARATORS = ("above", "below", "crosses_above", "crosses_below")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# definition CRUD
# --------------------------------------------------------------------------- #
def create_alert(user_id: str, symbol: str, comparator: str, threshold,
                 *, metric: str = "price_usd", repeat_mode: str = "once",
                 cooldown_seconds: int = 0, note: Optional[str] = None,
                 conn=None) -> dict:
    comparator = (comparator or "").strip().lower()
    if comparator not in _COMPARATORS:
        raise CryptoAlertError(f"bad comparator: {comparator!r}")
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise CryptoAlertError("symbol required")
    try:
        Decimal(str(threshold))
    except Exception as exc:
        raise CryptoAlertError(f"bad threshold: {threshold!r}") from exc
    repeat_mode = (repeat_mode or "once").strip().lower()
    if repeat_mode not in ("once", "always"):
        repeat_mode = "once"

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        now = _iso()
        alert_id = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_crypto_alerts "
            "(alert_id,user_id,symbol,metric,comparator,threshold,active,"
            " repeat_mode,last_state,last_value,last_fired_at,cooldown_seconds,"
            " note,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (alert_id, user_id, symbol, metric, comparator, str(threshold), 1,
             repeat_mode, None, None, None, int(cooldown_seconds or 0), note,
             now, now),
        )
        if owned:
            conn.commit()
        return {"alert_id": alert_id, "user_id": user_id, "symbol": symbol,
                "comparator": comparator, "threshold": str(threshold),
                "active": True, "repeat_mode": repeat_mode}
    finally:
        if owned:
            conn.close()


def list_alerts(user_id: str, *, active_only: bool = False, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = ("SELECT alert_id,symbol,metric,comparator,threshold,active,"
             "repeat_mode,last_state,last_fired_at FROM business_os_crypto_alerts "
             "WHERE user_id = ?")
        params = [user_id]
        if active_only:
            q += " AND active = 1"
        q += " ORDER BY created_at ASC"
        rows = conn.execute(q, tuple(params)).fetchall()
        return [{"alert_id": r[0], "symbol": r[1], "metric": r[2],
                 "comparator": r[3], "threshold": r[4], "active": bool(r[5]),
                 "repeat_mode": r[6], "last_state": r[7], "last_fired_at": r[8]}
                for r in rows]
    finally:
        if owned:
            conn.close()


def deactivate_alert(alert_id: str, *, conn=None) -> bool:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE business_os_crypto_alerts SET active = 0, updated_at = ? "
            "WHERE alert_id = ?", (_iso(), alert_id))
        if owned:
            conn.commit()
        return getattr(cur, "rowcount", 0) != 0
    finally:
        if owned:
            conn.close()


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #
def _side(value: Decimal, threshold: Decimal) -> str:
    return "above" if value >= threshold else "below"


def _is_crossing(comparator: str, prev_state: Optional[str], side: str) -> bool:
    """Edge detection. Fires only on the transition into the target region, and
    only when we have a prior state to compare against (first observation arms the
    state without firing, so a brand-new alert created while already above doesn't
    immediately page)."""
    if prev_state is None:
        return False
    if prev_state == side:
        return False  # no edge
    if comparator in ("above", "crosses_above"):
        return side == "above"
    if comparator in ("below", "crosses_below"):
        return side == "below"
    return False


def _crossing_key(alert_id: str, side: str, fired_at: str) -> str:
    # Deterministic per edge occurrence: alert + direction + the wall-clock second
    # of the sweep. A restart replaying the same sweep second collides and no-ops.
    stamp = (fired_at or _iso())[:19]  # to the second
    return f"{side}@{stamp}"


def evaluate_alert(alert_row: dict, value_cents: Optional[int], *,
                   now: Optional[datetime] = None, conn=None) -> dict:
    """Evaluate ONE alert against a current value (integer cents; None if the quote
    is unavailable — in which case we skip without changing state). Persists the new
    edge-detect state and, on a genuine crossing, inserts a deduped event row.

    Returns a dict describing the outcome (``fired`` bool + reason)."""
    if value_cents is None:
        return {"alert_id": alert_row["alert_id"], "fired": False,
                "reason": "no_quote"}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        now = now or _now()
        value = Decimal(int(value_cents))
        threshold_cents = (Decimal(alert_row["threshold"]) * 100).to_integral_value()
        side = _side(value, threshold_cents)
        prev_state = alert_row.get("last_state")
        comparator = alert_row["comparator"]

        crossing = _is_crossing(comparator, prev_state, side)

        # Cooldown gate (only relevant when it would otherwise fire).
        if crossing and int(alert_row.get("cooldown_seconds") or 0) > 0:
            last_fired = _parse_iso(alert_row.get("last_fired_at"))
            if last_fired is not None:
                elapsed = (now - last_fired).total_seconds()
                if elapsed < int(alert_row["cooldown_seconds"]):
                    crossing = False  # suppressed; still update state below

        fired = False
        reason = "no_edge" if not crossing else "crossed"
        if crossing:
            fired_at = _iso(now)
            key = _crossing_key(alert_row["alert_id"], side, fired_at)
            try:
                conn.execute(
                    "INSERT INTO business_os_crypto_alert_events "
                    "(event_id,alert_id,user_id,symbol,crossing_key,"
                    " observed_value,threshold,comparator,delivered,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_schema.new_id(), alert_row["alert_id"], alert_row["user_id"],
                     alert_row["symbol"], key, str(value_cents),
                     alert_row["threshold"], comparator, 0, fired_at),
                )
                fired = True
            except Exception:
                # UNIQUE(alert_id, crossing_key) collision -> already fired this
                # crossing (restart replay). Idempotent no-op.
                conn.rollback()
                fired = False
                reason = "duplicate_suppressed"

            if fired:
                new_active = 0 if alert_row.get("repeat_mode") == "once" else 1
                conn.execute(
                    "UPDATE business_os_crypto_alerts SET last_state = ?, "
                    "last_value = ?, last_fired_at = ?, active = ?, updated_at = ? "
                    "WHERE alert_id = ?",
                    (side, str(value_cents), fired_at, new_active, fired_at,
                     alert_row["alert_id"]))
                if owned:
                    conn.commit()
                return {"alert_id": alert_row["alert_id"], "fired": True,
                        "reason": reason, "side": side,
                        "crossing_key": key, "value_cents": int(value_cents)}

        # No fire (or suppressed): still advance the edge-detect state so the next
        # sweep can detect a future crossing.
        conn.execute(
            "UPDATE business_os_crypto_alerts SET last_state = ?, last_value = ?, "
            "updated_at = ? WHERE alert_id = ?",
            (side, str(value_cents), _iso(now), alert_row["alert_id"]))
        if owned:
            conn.commit()
        return {"alert_id": alert_row["alert_id"], "fired": False,
                "reason": reason, "side": side}
    finally:
        if owned:
            conn.close()


def sweep(price_lookup: Callable[[str], Optional[int]], *,
          now: Optional[datetime] = None, conn=None) -> dict:
    """Evaluate every active alert against ``price_lookup(symbol) -> price_cents``.

    Restart-safe: a crash mid-sweep and re-run re-evaluates cleanly because each
    crossing is deduped by the events table. Returns a summary with the fired list.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT alert_id,user_id,symbol,metric,comparator,threshold,active,"
            "repeat_mode,last_state,last_value,last_fired_at,cooldown_seconds "
            "FROM business_os_crypto_alerts WHERE active = 1 "
            "ORDER BY created_at ASC").fetchall()
        fired = []
        evaluated = 0
        for r in rows:
            alert = {"alert_id": r[0], "user_id": r[1], "symbol": r[2],
                     "metric": r[3], "comparator": r[4], "threshold": r[5],
                     "active": bool(r[6]), "repeat_mode": r[7],
                     "last_state": r[8], "last_value": r[9],
                     "last_fired_at": r[10], "cooldown_seconds": r[11]}
            try:
                price_cents = price_lookup(alert["symbol"])
            except Exception:
                price_cents = None
            res = evaluate_alert(alert, price_cents, now=now, conn=conn)
            evaluated += 1
            if res.get("fired"):
                fired.append(res)
        if owned:
            conn.commit()
        return {"evaluated": evaluated, "fired_count": len(fired), "fired": fired}
    finally:
        if owned:
            conn.close()
