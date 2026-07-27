"""Business OS — Marketplace: the canonical order state machine + ledger settlement.

This is the real ``orders`` table the legacy 0-row ``marketplace_orders_placeholder``
was always meant to become. It owns the order lifecycle

    created ──pay──▶ paid ──fulfill──▶ fulfilled ──complete──▶ completed
        │                 │                  │
        └──cancel──▶ cancelled  └──refund──▶ refunded  └──refund──▶ refunded

and every money movement rides the SHARED canonical double-entry ledger
(``services.business_os.ledger.ledger``) — this module mutates NO bare balance and
creates NO second financial foundation. Integer cents everywhere; no floats.

Money accounts (mirrors advertising's escrow discipline):

  * ``platform:marketplace_intake``  — external buyer money already captured by the
    payment provider (allow-negative liability, ``platform:`` prefix);
  * ``mkt_order_escrow:<order_id>``  — per-order escrow that HOLDS the captured funds;
    NOT allow-negative, so it is overdraft-guarded by the ledger (a refund/settle that
    would take it negative is refused);
  * ``seller_payable:<seller_id>``   — accrued amount owed to the seller (NOT the same
    as a bank disbursement — see the module note);
  * ``platform:marketplace_revenue`` — platform fee accrual (allow-negative).

Flows:
  * **capture** (pay):   intake ─▶ escrow            (order total)
  * **settle** (complete): escrow ─▶ revenue (fee) + escrow ─▶ seller_payable (net)
  * **refund** (refunds.py): escrow ─▶ intake        (while funds are still in escrow)

NOTE ON PAYOUT EXECUTION: crediting ``seller_payable`` is the canonical *accrual* of
what the seller is owed. The actual bank/Stripe transfer that moves money OUT to the
seller is a provider-side disbursement that this sandbox cannot perform and this module
deliberately does not attempt; it is surfaced honestly in the completion report.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace.service import MarketplaceError
from services.business_os.ledger import ledger as _ledger

try:
    from services.business_os.marketplace import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- default platform fee (server-authoritative, versionable later) ---------
DEFAULT_FEE_BPS = 1000  # 10.00% marketplace take rate


ORDER_STATUSES = {"created", "paid", "fulfilled", "completed", "cancelled", "refunded"}
ALLOWED_ORDER_TRANSITIONS = {
    "created": {"paid", "cancelled"},
    "paid": {"fulfilled", "refunded"},
    "fulfilled": {"completed", "refunded"},
    "completed": set(),
    "cancelled": set(),
    "refunded": set(),
}
# States in which captured funds are still sitting in escrow (refundable).
IN_ESCROW_STATUSES = {"paid", "fulfilled"}


# --- account helpers (shared with refunds.py) -------------------------------
INTAKE_ACCOUNT = "platform:marketplace_intake"
PLATFORM_REVENUE_ACCOUNT = "platform:marketplace_revenue"


def escrow_account(order_id: Any) -> str:
    return f"mkt_order_escrow:{order_id}"


def seller_payable_account(seller_user_id: Any) -> str:
    return f"seller_payable:{_svc._sid(seller_user_id)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _fee_split(total_cents: int, fee_bps: int) -> tuple:
    fee = (int(total_cents) * int(fee_bps)) // 10000
    net = int(total_cents) - fee
    return fee, net


# --- reads ------------------------------------------------------------------
def get_order(order_id: Any, *, requester_user_id: Any = None, conn=None) -> Optional[dict]:
    """Fetch an order. When ``requester_user_id`` is given, only the buyer or the
    seller may read it; anyone else gets None (existence not leaked)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM business_os_mkt_orders WHERE order_id = ?", (str(order_id),))
        row = _row(cur.fetchone())
        if row is None:
            return None
        if requester_user_id is not None:
            rid = _svc._sid(requester_user_id)
            if rid not in (row.get("buyer_user_id"), row.get("seller_user_id")):
                return None
        return row
    finally:
        if owned:
            conn.close()


def get_order_items(order_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_order_items WHERE order_id = ? ORDER BY id",
            (str(order_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


def get_order_events(order_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_order_events WHERE order_id = ? ORDER BY id",
            (str(order_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_orders(*, buyer_user_id: Any = None, seller_user_id: Any = None,
                status: Optional[str] = None, limit: int = 200, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_orders WHERE 1=1"
        params: list = []
        if buyer_user_id is not None:
            q += " AND buyer_user_id = ?"; params.append(_svc._sid(buyer_user_id))
        if seller_user_id is not None:
            q += " AND seller_user_id = ?"; params.append(_svc._sid(seller_user_id))
        if status:
            q += " AND status = ?"; params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        return [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


# --- internal state-machine helper ------------------------------------------
def _record_event(conn, order_id, from_status, to_status, actor, reason=None, meta=None):
    conn.execute(
        "INSERT INTO business_os_mkt_order_events "
        "(order_id, from_status, to_status, actor, reason, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(order_id), from_status, to_status,
         None if actor is None else str(actor), reason,
         None if meta is None else json.dumps(meta, sort_keys=True), _now_iso()))


def _assert_transition(cur_status, target):
    if target not in ALLOWED_ORDER_TRANSITIONS.get(cur_status, set()):
        raise MarketplaceError(
            f"Illegal order transition {cur_status} -> {target}.", 409, "illegal_transition")


# --- create -----------------------------------------------------------------
def create_order(buyer_user_id: Any, product_id: Any, *, quantity: int = 1,
                 context: Optional[dict] = None, conn=None) -> dict:
    """Place an order for an ``active`` product. Snapshots price + fee. Moves NO money
    (that happens at ``pay_order``). A seller cannot buy their own product."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise MarketplaceError("quantity must be a positive integer.", 400, "invalid_quantity")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        product = _svc.get_product(product_id, for_public=True, conn=conn)
        if product is None:
            raise MarketplaceError("Product not available.", 404, "not_found")
        if product.get("seller_user_id") == _svc._sid(buyer_user_id):
            raise MarketplaceError("You cannot buy your own product.", 400, "self_purchase")
        inv = product.get("inventory_qty")
        if inv is not None and inv < quantity:
            raise MarketplaceError("Not enough inventory.", 409, "insufficient_inventory")
        unit = int(product["price_cents"])
        subtotal = unit * quantity
        fee_bps = DEFAULT_FEE_BPS
        fee, net = _fee_split(subtotal, fee_bps)
        oid = "mkto_" + uuid.uuid4().hex
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_orders "
            "(order_id, buyer_user_id, seller_user_id, status, currency, subtotal_cents, "
            "total_cents, platform_fee_bps, platform_fee_cents, seller_net_cents, "
            "refunded_cents, fulfillment_type, created_at, updated_at) "
            "VALUES (?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (oid, _svc._sid(buyer_user_id), product["seller_user_id"],
             product.get("currency", "usd"), subtotal, subtotal, fee_bps, fee, net,
             product.get("fulfillment_type", "physical"), now, now))
        conn.execute(
            "INSERT INTO business_os_mkt_order_items "
            "(order_id, product_id, title, unit_price_cents, quantity, line_total_cents, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (oid, product["product_id"], product.get("title"), unit, quantity, subtotal, now))
        _record_event(conn, oid, None, "created", buyer_user_id)
        if owned:
            conn.commit()
        return get_order(oid, conn=conn)
    finally:
        if owned:
            conn.close()


# --- pay (capture into escrow, atomic inventory decrement) ------------------
def pay_order(order_id: Any, buyer_user_id: Any, *, context: Optional[dict] = None,
              conn=None) -> dict:
    """created ─▶ paid. Atomically decrements inventory, then captures the order total
    into the per-order escrow via the canonical ledger. If capture fails, the inventory
    decrement and the state flip are COMPENSATED so the order returns to ``created``.

    NOTE: this models the *post-capture* bookkeeping. Collecting the card payment
    itself (Stripe PaymentIntent) is the provider's job and is done before this call;
    we record the captured funds moving into escrow.
    """
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None:
            raise MarketplaceError("Order not found.", 404, "not_found")
        if order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "paid")

        items = get_order_items(order_id, conn=conn)
        # 1) atomic inventory decrement for each line (guarded), then flip state.
        for it in items:
            cur = conn.execute(
                "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty - ?, "
                "updated_at = ? WHERE product_id = ? AND inventory_qty IS NOT NULL "
                "AND inventory_qty >= ?",
                (it["quantity"], _now_iso(), it["product_id"], it["quantity"]))
            if getattr(cur, "rowcount", 0) == 0:
                # Either unlimited (NULL inventory) or genuinely out of stock.
                prod = conn.execute(
                    "SELECT inventory_qty FROM business_os_mkt_products WHERE product_id = ?",
                    (it["product_id"],)).fetchone()
                inv = None if prod is None else (prod["inventory_qty"] if hasattr(prod, "keys") else prod[0])
                if inv is not None:
                    raise MarketplaceError("Item sold out.", 409, "insufficient_inventory")
                # NULL inventory ⇒ unlimited (digital); nothing to decrement.
        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'paid', updated_at = ? "
            "WHERE order_id = ?", (_now_iso(), str(order_id)))
        _record_event(conn, order_id, "created", "paid", buyer_user_id)
        if owned:
            conn.commit()

        # 2) capture into escrow (idempotent).
        try:
            txn = _ledger.post_entry(
                idempotency_key=f"mkt_capture:{order_id}",
                actor=_svc._sid(buyer_user_id),
                amount_cents=int(order["total_cents"]),
                currency=order.get("currency", "usd"),
                entry_type="marketplace_capture",
                source=INTAKE_ACCOUNT,
                destination=escrow_account(order_id),
                reason="Marketplace order captured into escrow.",
                related_object=str(order_id))
        except Exception:
            # Compensate: restore inventory + revert state so nothing is stranded.
            c2 = db.connect()
            try:
                for it in items:
                    c2.execute(
                        "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty + ? "
                        "WHERE product_id = ? AND inventory_qty IS NOT NULL",
                        (it["quantity"], it["product_id"]))
                c2.execute(
                    "UPDATE business_os_mkt_orders SET status = 'created', updated_at = ? "
                    "WHERE order_id = ?", (_now_iso(), str(order_id)))
                _record_event(c2, order_id, "paid", "created", buyer_user_id,
                              reason="capture_failed")
                c2.commit()
            finally:
                c2.close()
            raise MarketplaceError("Payment capture failed.", 502, "capture_failed")

        c3 = db.connect()
        try:
            c3.execute(
                "UPDATE business_os_mkt_orders SET capture_txn_ref = ?, updated_at = ? "
                "WHERE order_id = ?",
                (txn.get("transaction_id"), _now_iso(), str(order_id)))
            c3.commit()
        finally:
            c3.close()

        _emit(order.get("seller_user_id"), "order_paid", order_id)
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- fulfill ----------------------------------------------------------------
def fulfill_order(order_id: Any, seller_user_id: Any, *, tracking_ref: Optional[str] = None,
                  context: Optional[dict] = None, conn=None) -> dict:
    """paid ─▶ fulfilled. Seller-only. Physical orders may carry a tracking ref;
    digital orders are considered delivered immediately."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, conn=conn)
        if order is None or order.get("seller_user_id") != _svc._sid(seller_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "fulfilled")
        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'fulfilled', tracking_ref = ?, "
            "updated_at = ? WHERE order_id = ?",
            (tracking_ref, _now_iso(), str(order_id)))
        _record_event(conn, order_id, "paid", "fulfilled", seller_user_id,
                      meta={"tracking_ref": tracking_ref} if tracking_ref else None)
        if owned:
            conn.commit()
        _emit(order.get("buyer_user_id"), "order_fulfilled", order_id)
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- complete (settle escrow → fee + seller net) ----------------------------
def complete_order(order_id: Any, buyer_user_id: Any, *, context: Optional[dict] = None,
                   actor: Any = None, conn=None) -> dict:
    """fulfilled ─▶ completed. Buyer confirms receipt (or an admin completes it),
    which SETTLES escrow: the platform fee accrues to marketplace revenue and the
    remainder accrues to the seller's payable account. Settlement is computed from
    the CURRENT escrow balance so any prior partial refund is already netted out and
    escrow always zeroes exactly."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, conn=conn)
        if order is None:
            raise MarketplaceError("Order not found.", 404, "not_found")
        # Buyer confirms; an explicit admin actor may also complete.
        actor = actor if actor is not None else buyer_user_id
        if actor is None or (_svc._sid(actor) != order.get("buyer_user_id")
                             and _svc._sid(buyer_user_id) != order.get("buyer_user_id")):
            # Only the buyer path is exposed here; admin completion goes via admin.py.
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "completed")

        remaining = _ledger.get_balance(escrow_account(order_id), order.get("currency", "usd"))
        fee, net = _fee_split(remaining, order.get("platform_fee_bps", DEFAULT_FEE_BPS))

        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'completed', updated_at = ? "
            "WHERE order_id = ?", (_now_iso(), str(order_id)))
        _record_event(conn, order_id, "fulfilled", "completed", actor,
                      meta={"settled_cents": remaining, "fee_cents": fee, "net_cents": net})
        if owned:
            conn.commit()

        settle_ref = None
        if fee > 0:
            _ledger.post_entry(
                idempotency_key=f"mkt_settle_fee:{order_id}",
                actor=_svc._sid(actor), amount_cents=fee,
                currency=order.get("currency", "usd"), entry_type="marketplace_fee",
                source=escrow_account(order_id), destination=PLATFORM_REVENUE_ACCOUNT,
                reason="Marketplace platform fee.", related_object=str(order_id))
        if net > 0:
            txn = _ledger.post_entry(
                idempotency_key=f"mkt_settle_net:{order_id}",
                actor=_svc._sid(actor), amount_cents=net,
                currency=order.get("currency", "usd"), entry_type="marketplace_payout_accrual",
                source=escrow_account(order_id),
                destination=seller_payable_account(order.get("seller_user_id")),
                reason="Marketplace seller net accrual.", related_object=str(order_id))
            settle_ref = txn.get("transaction_id")
        if settle_ref:
            c3 = db.connect()
            try:
                c3.execute(
                    "UPDATE business_os_mkt_orders SET settle_txn_ref = ?, updated_at = ? "
                    "WHERE order_id = ?", (settle_ref, _now_iso(), str(order_id)))
                c3.commit()
            finally:
                c3.close()

        _emit(order.get("seller_user_id"), "order_completed", order_id)
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- cancel (only before payment; no money moved) ---------------------------
def cancel_order(order_id: Any, buyer_user_id: Any, *, reason: Optional[str] = None,
                 context: Optional[dict] = None, conn=None) -> dict:
    """created ─▶ cancelled. Buyer-only, and only before payment (no funds moved). A
    paid order is unwound through a refund, not a cancel."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None or order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "cancelled")
        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'cancelled', updated_at = ? "
            "WHERE order_id = ?", (_now_iso(), str(order_id)))
        _record_event(conn, order_id, "created", "cancelled", buyer_user_id, reason=reason)
        if owned:
            conn.commit()
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- money summary (audit tool) ---------------------------------------------
def order_money_summary(order_id: Any, conn=None) -> dict:
    """Derived money view for an order straight off the canonical ledger balances —
    never a stored authority."""
    order = get_order(order_id, conn=conn)
    if order is None:
        raise MarketplaceError("Order not found.", 404, "not_found")
    cur = order.get("currency", "usd")
    return {
        "order_id": str(order_id),
        "status": order.get("status"),
        "total_cents": order.get("total_cents"),
        "refunded_cents": order.get("refunded_cents"),
        "escrow_balance_cents": _ledger.get_balance(escrow_account(order_id), cur),
        "seller_payable_cents": _ledger.get_balance(
            seller_payable_account(order.get("seller_user_id")), cur),
        "platform_fee_cents": order.get("platform_fee_cents"),
        "currency": cur,
    }


def _emit(user_id, kind, order_id):
    if _notify is None or user_id is None:
        return
    try:
        _notify.emit_order_event(user_id, kind, order_id)
    except Exception:
        pass
