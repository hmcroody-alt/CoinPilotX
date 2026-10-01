"""Business OS — Marketplace SELLER DASHBOARD: action center + sales summary.

Read-only projections feeding the Store Management Hub's "action center" and the
seller home tiles (mission spec Sections 2/15; plan Phases 3/6). Nothing here
mutates state and nothing is fabricated:

  * every count is a real query over the canonical tables the sibling engines
    own (orders, returns, disputes, offers + reservations);
  * money figures come from the order rows and the SHARED ledger only — the
    client never computes balances (spec Section 16);
  * subsystems that are not initialised in a deployment (e.g. the offers pack)
    contribute ``None`` — "unavailable" — never a fake zero, so the display
    layer can honour the zero-vs-unavailable rule from Section 15.

Flag-gated by ``BUSINESS_OS_MARKETPLACE`` like the rest of the package.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import refunds as _ref


MAX_PREVIEW_ITEMS = 10


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _count(conn, sql: str, params: tuple) -> Optional[int]:
    """A count, or None when the underlying table does not exist in this
    deployment (subsystem never initialised) — unavailable, not zero."""
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    return int(row[0] if not hasattr(row, "keys") else list(row)[0])


def _preview(conn, sql: str, params: tuple, limit: int = MAX_PREVIEW_ITEMS) -> list:
    try:
        return [_row(r) for r in conn.execute(sql + " LIMIT ?",
                                              (*params, limit)).fetchall()]
    except Exception:
        return []


# --- action center -----------------------------------------------------------
def action_center(seller_user_id: Any, conn=None) -> dict:
    """Everything currently waiting on THIS seller, with small previews.

    Queues:
      * ``to_fulfill``       — paid orders awaiting shipment;
      * ``returns_to_answer``— return requests awaiting approve/decline;
      * ``returns_received`` — merchandise back, awaiting refund/close;
      * ``offers_to_answer`` — open offers where the buyer proposed last
                               (turn-taking says the seller must respond);
      * ``open_disputes``    — buyer-opened disputes on the seller's orders.

    A queue whose subsystem is not initialised reports ``count: None``
    (unavailable) with an empty preview.
    """
    _svc._require_enabled()
    sid = _svc._sid(seller_user_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        queues = {}

        queues["to_fulfill"] = {
            "count": _count(conn,
                            "SELECT COUNT(*) FROM business_os_mkt_orders "
                            "WHERE seller_user_id = ? AND status = 'paid'", (sid,)),
            "preview": _preview(conn,
                                "SELECT order_id, total_cents, currency, created_at "
                                "FROM business_os_mkt_orders WHERE seller_user_id = ? "
                                "AND status = 'paid' ORDER BY created_at", (sid,))}

        queues["returns_to_answer"] = {
            "count": _count(conn,
                            "SELECT COUNT(*) FROM business_os_mkt_returns "
                            "WHERE seller_user_id = ? AND status = 'requested'",
                            (sid,)),
            "preview": _preview(conn,
                                "SELECT return_id, order_id, reason, created_at "
                                "FROM business_os_mkt_returns WHERE seller_user_id = ? "
                                "AND status = 'requested' ORDER BY created_at",
                                (sid,))}

        queues["returns_received"] = {
            "count": _count(conn,
                            "SELECT COUNT(*) FROM business_os_mkt_returns "
                            "WHERE seller_user_id = ? AND status = 'received'",
                            (sid,)),
            "preview": _preview(conn,
                                "SELECT return_id, order_id, reason, created_at "
                                "FROM business_os_mkt_returns WHERE seller_user_id = ? "
                                "AND status = 'received' ORDER BY created_at",
                                (sid,))}

        # Turn-taking: the seller owes an answer when the BUYER proposed last
        # and the offer is still open. Expiry is enforced on touch by the offers
        # engine; a lapsed row here is at worst one sweep behind, and acting on
        # it surfaces the honest 409.
        queues["offers_to_answer"] = {
            "count": _count(conn,
                            "SELECT COUNT(*) FROM business_os_mkt_offers "
                            "WHERE seller_user_id = ? AND current_proposer = 'buyer' "
                            "AND status IN ('needs_response','countered')", (sid,)),
            "preview": _preview(conn,
                                "SELECT offer_id, product_id, current_amount_cents, "
                                "quantity, expires_at FROM business_os_mkt_offers "
                                "WHERE seller_user_id = ? AND current_proposer = 'buyer' "
                                "AND status IN ('needs_response','countered') "
                                "ORDER BY created_at", (sid,))}

        queues["open_disputes"] = {
            "count": _count(conn,
                            "SELECT COUNT(*) FROM business_os_mkt_disputes d "
                            "JOIN business_os_mkt_orders o ON o.order_id = d.order_id "
                            "WHERE o.seller_user_id = ? AND d.status = 'open'", (sid,)),
            "preview": _preview(conn,
                                "SELECT d.dispute_id, d.order_id, d.reason, d.created_at "
                                "FROM business_os_mkt_disputes d "
                                "JOIN business_os_mkt_orders o ON o.order_id = d.order_id "
                                "WHERE o.seller_user_id = ? AND d.status = 'open' "
                                "ORDER BY d.created_at", (sid,))}

        available = [q["count"] for q in queues.values() if q["count"] is not None]
        return {"seller_user_id": sid, "queues": queues,
                "total_actionable": sum(available) if available else None}
    finally:
        if owned:
            conn.close()


# --- sales summary -----------------------------------------------------------
def sales_summary(seller_user_id: Any, *, currency: str = "usd",
                  conn=None) -> dict:
    """Order-state counts plus money truth for the seller home tiles.

    * ``orders_by_status`` — real counts (a state with no rows is honestly 0
      here because the orders table is this module's own required substrate;
      if even that table is missing the whole call fails loudly rather than
      rendering a fake dashboard);
    * ``gross_captured_cents`` / ``refunded_cents`` — sums over the seller's
      order rows in the requested currency;
    * ``payable_cents`` — read straight off the shared ledger via the existing
      ``refunds.seller_payout_balance`` (accrual; disbursement out of scope).
    """
    _svc._require_enabled()
    sid = _svc._sid(seller_user_id)
    cur = str(currency or "usd").lower()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        by_status: dict = {}
        for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM business_os_mkt_orders "
                "WHERE seller_user_id = ? AND currency = ? GROUP BY status",
                (sid, cur)).fetchall():
            rr = _row(r)
            by_status[rr["status"]] = int(rr["n"])
        money = _row(conn.execute(
            "SELECT COALESCE(SUM(total_cents), 0) AS gross, "
            "COALESCE(SUM(refunded_cents), 0) AS refunded "
            "FROM business_os_mkt_orders WHERE seller_user_id = ? AND currency = ? "
            "AND status IN ('paid','fulfilled','completed','refunded')",
            (sid, cur)).fetchone())
        payable = _ref.seller_payout_balance(seller_user_id, cur)
        return {"seller_user_id": sid, "currency": cur,
                "orders_by_status": by_status,
                "gross_captured_cents": int(money["gross"]),
                "refunded_cents": int(money["refunded"]),
                "payable_cents": int(payable["payable_cents"])}
    finally:
        if owned:
            conn.close()
