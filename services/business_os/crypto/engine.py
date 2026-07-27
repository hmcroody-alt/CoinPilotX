"""Crypto cost-basis / P&L engine (Stage 5 Part 2).

Informational accounting over the append-only transaction log. NO custody, NO
trade execution — this only records what the user tells us happened and reports
the resulting position and profit/loss.

Model
=====
Every user action is an immutable row in ``business_os_crypto_transactions``:

* a **buy** opens a lot (``business_os_crypto_lots``) with a remaining quantity
  and a per-unit cost in integer cents (unit price plus an allocated share of the
  fee);
* a **sell** consumes open lots — FIFO (oldest lot first) or AVERAGE (single
  blended cost) depending on the holding's method — realizing profit/loss as
  (proceeds − consumed cost) in integer cents.

``business_os_crypto_holdings`` is a *projection* recomputed after each write: net
quantity (Decimal string), remaining cost basis (cents), and cumulative realized
P&L (cents). It is always rebuildable from the log, so it is never the authority.

Numeric discipline
===================
* **Quantities** are :class:`decimal.Decimal` throughout, serialized to a canonical
  string for storage — never float. Crypto needs 8+ decimal places.
* **Money** is integer cents everywhere. Per-unit cost is stored in cents; when a
  sell consumes a fractional slice of a lot we compute consumed cost as
  ``round(unit_cost_cents * consumed_qty)`` using Decimal rounding, so cents never
  drift into floats.

Idempotency
===========
An ingest carrying a ``(source, external_ref)`` that already exists is a no-op
(the DB unique index enforces it; we detect and skip). Manual entries pass
``external_ref=None`` and are always accepted.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional

from services import db
from services.business_os.crypto import schema as _schema


class CryptoEngineError(ValueError):
    """Curated engine error (bad quantity, oversell, unknown side)."""


getcontext_prec = 40  # generous precision for satoshi-scale math

_ZERO = Decimal("0")


# --------------------------------------------------------------------------- #
# small numeric helpers
# --------------------------------------------------------------------------- #
def _dec(value) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CryptoEngineError(f"invalid decimal: {value!r}") from exc
    if d != d:  # NaN
        raise CryptoEngineError(f"invalid decimal (NaN): {value!r}")
    return d


def _qty_str(d: Decimal) -> str:
    """Canonical decimal string (no scientific notation, trimmed)."""
    d = d.normalize()
    # normalize() can yield exponent form for integers (e.g. 1E+1); expand it.
    if d == d.to_integral_value():
        d = d.quantize(Decimal(1))
    s = format(d, "f")
    return s


def _cents(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# transaction recording
# --------------------------------------------------------------------------- #
def record_transaction(
    user_id: str,
    symbol: str,
    side: str,
    quantity,
    unit_price_cents: int,
    *,
    fee_cents: int = 0,
    executed_at: Optional[str] = None,
    source: str = "manual",
    external_ref: Optional[str] = None,
    notes: Optional[str] = None,
    method: str = "fifo",
    conn=None,
) -> dict:
    """Append a buy/sell, update lots + holdings projection, return a result dict.

    ``method`` (``'fifo'`` | ``'average'``) is fixed per (user, symbol) on first
    touch and reused thereafter. Raises :class:`CryptoEngineError` on a bad quantity,
    an unknown side, or a sell that exceeds the held quantity (oversell guard)."""
    side = (side or "").strip().lower()
    if side not in ("buy", "sell"):
        raise CryptoEngineError(f"unknown side: {side!r}")
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise CryptoEngineError("symbol required")
    qty = _dec(quantity)
    if qty <= 0:
        raise CryptoEngineError("quantity must be > 0")
    unit_price_cents = int(unit_price_cents)
    fee_cents = int(fee_cents or 0)
    if unit_price_cents < 0 or fee_cents < 0:
        raise CryptoEngineError("money fields must be >= 0")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        now = _schema.utc_now_iso()
        executed_at = executed_at or now

        # Idempotent ingest: skip a replayed external event.
        if external_ref is not None:
            existing = conn.execute(
                "SELECT txn_id FROM business_os_crypto_transactions "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref),
            ).fetchone()
            if existing:
                holding = _load_holding(conn, user_id, symbol)
                return {"recorded": False, "duplicate": True,
                        "txn_id": existing[0], "holding": holding}

        txn_id = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_crypto_transactions "
            "(txn_id,user_id,symbol,side,quantity,unit_price_cents,fee_cents,"
            " currency,executed_at,source,external_ref,notes,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (txn_id, user_id, symbol, side, _qty_str(qty), unit_price_cents,
             fee_cents, "usd", executed_at, source, external_ref, notes, now),
        )

        method = _holding_method(conn, user_id, symbol, method)
        if side == "buy":
            realized = 0
            _open_lot(conn, user_id, symbol, txn_id, qty, unit_price_cents,
                      fee_cents, executed_at, now)
        else:
            realized = _consume_lots(conn, user_id, symbol, qty,
                                     unit_price_cents, fee_cents, method)

        holding = _reproject_holding(conn, user_id, symbol, method,
                                     txn_id, realized, now)
        if owned:
            conn.commit()
        return {"recorded": True, "duplicate": False, "txn_id": txn_id,
                "side": side, "realized_pnl_cents": realized, "holding": holding}
    finally:
        if owned:
            conn.close()


def _holding_method(conn, user_id, symbol, requested) -> str:
    row = conn.execute(
        "SELECT method FROM business_os_crypto_holdings "
        "WHERE user_id = ? AND symbol = ?", (user_id, symbol)).fetchone()
    if row and row[0]:
        return row[0]
    m = (requested or "fifo").strip().lower()
    return m if m in ("fifo", "average") else "fifo"


def _open_lot(conn, user_id, symbol, txn_id, qty, unit_price_cents, fee_cents,
              acquired_at, now):
    # Allocate the fee into the per-unit cost so cost basis is all-in.
    fee_per_unit = (_dec(fee_cents) / qty) if qty > 0 else _ZERO
    unit_cost = _cents(_dec(unit_price_cents) + fee_per_unit)
    conn.execute(
        "INSERT INTO business_os_crypto_lots "
        "(lot_id,user_id,symbol,txn_id,original_quantity,remaining_quantity,"
        " unit_cost_cents,acquired_at,closed,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_schema.new_id(), user_id, symbol, txn_id, _qty_str(qty),
         _qty_str(qty), unit_cost, acquired_at, 0, now),
    )


def _open_lots(conn, user_id, symbol, method):
    order = "acquired_at ASC, created_at ASC"
    return conn.execute(
        "SELECT lot_id, remaining_quantity, unit_cost_cents "
        "FROM business_os_crypto_lots "
        "WHERE user_id = ? AND symbol = ? AND closed = 0 "
        f"ORDER BY {order}", (user_id, symbol)).fetchall()


def _consume_lots(conn, user_id, symbol, sell_qty, sell_unit_price_cents,
                  fee_cents, method) -> int:
    """Consume open lots for a sell; return realized P&L in integer cents.

    FIFO consumes oldest-first. AVERAGE blends all open lots into one cost/unit
    and consumes proportionally. Raises on oversell."""
    lots = _open_lots(conn, user_id, symbol, method)
    held = sum((_dec(l[1]) for l in lots), _ZERO)
    if sell_qty > held:
        raise CryptoEngineError(
            f"oversell: selling {_qty_str(sell_qty)} but only "
            f"{_qty_str(held)} held")

    # Proceeds are net of the sell fee, spread across the sold quantity.
    fee_per_unit = (_dec(fee_cents) / sell_qty) if sell_qty > 0 else _ZERO
    net_unit_proceeds = _dec(sell_unit_price_cents) - fee_per_unit

    if method == "average":
        total_cost = sum((_dec(l[1]) * _dec(l[2]) for l in lots), _ZERO)
        avg_unit_cost = (total_cost / held) if held > 0 else _ZERO
        consumed_cost = _cents(avg_unit_cost * sell_qty)
        _decrement_lots_proportional(conn, lots, sell_qty, held)
    else:  # fifo
        remaining = sell_qty
        consumed_cost_dec = _ZERO
        for lot_id, rem_q, unit_cost in lots:
            if remaining <= 0:
                break
            rem_q = _dec(rem_q)
            take = rem_q if rem_q <= remaining else remaining
            consumed_cost_dec += take * _dec(unit_cost)
            new_rem = rem_q - take
            conn.execute(
                "UPDATE business_os_crypto_lots "
                "SET remaining_quantity = ?, closed = ? WHERE lot_id = ?",
                (_qty_str(new_rem), 1 if new_rem <= 0 else 0, lot_id))
            remaining -= take
        consumed_cost = _cents(consumed_cost_dec)

    proceeds = _cents(net_unit_proceeds * sell_qty)
    return proceeds - consumed_cost


def _decrement_lots_proportional(conn, lots, sell_qty, held):
    """AVERAGE method: reduce every open lot by the same fraction sold."""
    if held <= 0:
        return
    frac = sell_qty / held
    for lot_id, rem_q, _unit_cost in lots:
        rem_q = _dec(rem_q)
        new_rem = rem_q - (rem_q * frac)
        if new_rem < 0:
            new_rem = _ZERO
        conn.execute(
            "UPDATE business_os_crypto_lots "
            "SET remaining_quantity = ?, closed = ? WHERE lot_id = ?",
            (_qty_str(new_rem), 1 if new_rem <= 0 else 0, lot_id))


def _reproject_holding(conn, user_id, symbol, method, last_txn_id,
                       realized_delta, now) -> dict:
    """Recompute the holdings projection from open lots + accumulate realized."""
    lots = _open_lots(conn, user_id, symbol, method)
    net_qty = sum((_dec(l[1]) for l in lots), _ZERO)
    cost_basis = _cents(sum((_dec(l[1]) * _dec(l[2]) for l in lots), _ZERO))

    prev = conn.execute(
        "SELECT holding_id, realized_pnl_cents FROM business_os_crypto_holdings "
        "WHERE user_id = ? AND symbol = ?", (user_id, symbol)).fetchone()
    if prev:
        holding_id = prev[0]
        realized_total = int(prev[1]) + int(realized_delta)
        conn.execute(
            "UPDATE business_os_crypto_holdings SET quantity = ?, "
            "cost_basis_cents = ?, realized_pnl_cents = ?, method = ?, "
            "last_txn_id = ?, updated_at = ? WHERE holding_id = ?",
            (_qty_str(net_qty), cost_basis, realized_total, method,
             last_txn_id, now, holding_id))
    else:
        holding_id = _schema.new_id()
        realized_total = int(realized_delta)
        conn.execute(
            "INSERT INTO business_os_crypto_holdings "
            "(holding_id,user_id,symbol,quantity,cost_basis_cents,"
            " realized_pnl_cents,method,last_txn_id,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (holding_id, user_id, symbol, _qty_str(net_qty), cost_basis,
             realized_total, method, last_txn_id, now))
    return {
        "user_id": user_id, "symbol": symbol,
        "quantity": _qty_str(net_qty), "cost_basis_cents": cost_basis,
        "realized_pnl_cents": realized_total, "method": method,
    }


def _load_holding(conn, user_id, symbol) -> Optional[dict]:
    row = conn.execute(
        "SELECT quantity, cost_basis_cents, realized_pnl_cents, method "
        "FROM business_os_crypto_holdings WHERE user_id = ? AND symbol = ?",
        (user_id, symbol)).fetchone()
    if not row:
        return None
    return {"user_id": user_id, "symbol": symbol, "quantity": row[0],
            "cost_basis_cents": int(row[1]), "realized_pnl_cents": int(row[2]),
            "method": row[3]}


# --------------------------------------------------------------------------- #
# read / reporting
# --------------------------------------------------------------------------- #
def get_holding(user_id: str, symbol: str, *, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return _load_holding(conn, user_id, (symbol or "").strip().upper())
    finally:
        if owned:
            conn.close()


def unrealized_for_holding(holding: dict, price_cents: int) -> dict:
    """Given a holding dict and a current per-unit price in cents, return market
    value, unrealized P&L (cents), and total (realized + unrealized)."""
    qty = _dec(holding.get("quantity", "0"))
    cost_basis = int(holding.get("cost_basis_cents", 0))
    realized = int(holding.get("realized_pnl_cents", 0))
    market_value = _cents(qty * _dec(price_cents))
    unrealized = market_value - cost_basis
    return {
        "market_value_cents": market_value,
        "unrealized_pnl_cents": unrealized,
        "realized_pnl_cents": realized,
        "total_pnl_cents": realized + unrealized,
    }


def portfolio_summary(user_id: str, price_lookup=None, *, conn=None) -> dict:
    """Summarize every open holding for ``user_id``.

    ``price_lookup`` is an optional ``symbol -> price_cents`` callable (the unified
    market service in production, a stub in tests). When it returns ``None`` for a
    symbol, that holding contributes cost basis + realized but no unrealized/value.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT symbol, quantity, cost_basis_cents, realized_pnl_cents, method "
            "FROM business_os_crypto_holdings WHERE user_id = ? "
            "ORDER BY symbol ASC", (user_id,)).fetchall()
        holdings = []
        tot_cost = tot_value = tot_realized = tot_unrealized = 0
        priced = 0
        for symbol, qty, cost_basis, realized, method in rows:
            h = {"user_id": user_id, "symbol": symbol, "quantity": qty,
                 "cost_basis_cents": int(cost_basis),
                 "realized_pnl_cents": int(realized), "method": method}
            price_cents = None
            if price_lookup is not None:
                try:
                    price_cents = price_lookup(symbol)
                except Exception:
                    price_cents = None
            if price_cents is not None:
                u = unrealized_for_holding(h, int(price_cents))
                h.update(u)
                h["price_cents"] = int(price_cents)
                tot_value += u["market_value_cents"]
                tot_unrealized += u["unrealized_pnl_cents"]
                priced += 1
            tot_cost += int(cost_basis)
            tot_realized += int(realized)
            holdings.append(h)
        return {
            "user_id": user_id,
            "holdings": holdings,
            "totals": {
                "cost_basis_cents": tot_cost,
                "market_value_cents": tot_value,
                "unrealized_pnl_cents": tot_unrealized,
                "realized_pnl_cents": tot_realized,
                "total_pnl_cents": tot_realized + tot_unrealized,
                "priced_symbols": priced,
                "symbols": len(holdings),
            },
        }
    finally:
        if owned:
            conn.close()
