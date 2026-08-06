"""Business OS — Marketplace SELLER REPORTS: ledger-backed finance + sales.

Phase 7 of the Store OS plan. Read-only projections for the Reports screens.
The spec's rules, enforced here:

  * every money figure comes from the ORDER ROWS and the SHARED LEDGER — the
    client never computes balances (Section 16);
  * every report carries a ``generated_at`` freshness timestamp so the UI can
    say WHEN the numbers were true instead of implying "live";
  * zero-vs-unavailable (Section 15): an empty period reports honest zeros
    over the seller's own required substrate (the orders table), but a
    subsystem missing from the deployment answers ``None``;
  * payout money is reported by STATE — ``in_escrow`` (captured, not yet
    settled), ``payable`` (accrued to the seller off the ledger), and
    lifetime ``paid_out`` is explicitly ``None``/out-of-scope because
    disbursement is provider-side — never a fabricated number.

Flag-gated by ``BUSINESS_OS_MARKETPLACE``. Additive, read-only: no tables of
its own, no mutation anywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.marketplace import refunds as _ref
from services.business_os.ledger import ledger as _ledger


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _valid_day(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


# --- finance report ----------------------------------------------------------
def finance_report(seller_user_id: Any, *, currency: str = "usd",
                   conn=None) -> dict:
    """Money by state, straight off the ledger + order rows.

    * ``in_escrow_cents``   — sum of live escrow balances across the seller's
      unsettled orders (paid/fulfilled);
    * ``payable_cents``     — the seller's accrued ledger balance;
    * ``refunded_cents`` / ``gross_captured_cents`` / ``platform_fees_cents``
      — sums over the seller's order rows;
    * ``paid_out_cents``    — ``None``: disbursement is provider-side and this
      environment does not perform it, so it does not invent a figure.
    """
    _svc._require_enabled()
    sid = _svc._sid(seller_user_id)
    cur = str(currency or "usd").lower()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        money = _row(conn.execute(
            "SELECT COALESCE(SUM(total_cents), 0) AS gross, "
            "COALESCE(SUM(refunded_cents), 0) AS refunded, "
            "COALESCE(SUM(platform_fee_cents), 0) AS fees "
            "FROM business_os_mkt_orders WHERE seller_user_id = ? "
            "AND currency = ? AND status IN "
            "('paid','fulfilled','completed','refunded')", (sid, cur)).fetchone())
        escrow = 0
        for r in conn.execute(
                "SELECT order_id FROM business_os_mkt_orders "
                "WHERE seller_user_id = ? AND currency = ? "
                "AND status IN ('paid','fulfilled')", (sid, cur)).fetchall():
            rr = _row(r)
            escrow += int(_ledger.get_balance(
                _ord.escrow_account(rr["order_id"]), cur))
        payable = _ref.seller_payout_balance(seller_user_id, cur)
        return {"seller_user_id": sid, "currency": cur,
                "generated_at": _now_iso(),
                "gross_captured_cents": int(money["gross"]),
                "refunded_cents": int(money["refunded"]),
                "platform_fees_cents": int(money["fees"]),
                "in_escrow_cents": escrow,
                "payable_cents": int(payable["payable_cents"]),
                "paid_out_cents": None,
                "paid_out_note": "disbursement is provider-side; not tracked here"}
    finally:
        if owned:
            conn.close()


# --- sales over time ---------------------------------------------------------
def sales_by_day(seller_user_id: Any, *, currency: str = "usd",
                 start_day: Optional[str] = None,
                 end_day: Optional[str] = None,
                 limit_days: int = 366, conn=None) -> dict:
    """Orders and captured money grouped by calendar day (UTC, from the order
    rows' ``created_at``). Days with no orders are simply absent — the client
    fills the axis; absent-here never means "unavailable", because this comes
    from the required substrate. Optional inclusive YYYY-MM-DD bounds."""
    _svc._require_enabled()
    from services.business_os.marketplace.service import MarketplaceError
    for label, day in (("start_day", start_day), ("end_day", end_day)):
        if day is not None and not _valid_day(day):
            raise MarketplaceError(f"{label} must be YYYY-MM-DD.", 400,
                                   "invalid_day")
    sid = _svc._sid(seller_user_id)
    cur = str(currency or "usd").lower()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        sql = ("SELECT substr(created_at, 1, 10) AS day, "
               "COUNT(*) AS orders, "
               "COALESCE(SUM(total_cents), 0) AS gross_cents, "
               "COALESCE(SUM(refunded_cents), 0) AS refunded_cents "
               "FROM business_os_mkt_orders "
               "WHERE seller_user_id = ? AND currency = ? "
               "AND status IN ('paid','fulfilled','completed','refunded')")
        params: list = [sid, cur]
        if start_day:
            sql += " AND substr(created_at, 1, 10) >= ?"
            params.append(start_day)
        if end_day:
            sql += " AND substr(created_at, 1, 10) <= ?"
            params.append(end_day)
        sql += " GROUP BY day ORDER BY day DESC LIMIT ?"
        params.append(int(limit_days))
        days = [{"day": r["day"], "orders": int(r["orders"]),
                 "gross_cents": int(r["gross_cents"]),
                 "refunded_cents": int(r["refunded_cents"])}
                for r in (_row(x) for x in conn.execute(sql, params).fetchall())]
        return {"seller_user_id": sid, "currency": cur,
                "generated_at": _now_iso(),
                "start_day": start_day, "end_day": end_day, "days": days}
    finally:
        if owned:
            conn.close()
