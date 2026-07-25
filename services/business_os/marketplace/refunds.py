"""Business OS — Marketplace: refunds / returns / disputes / payouts / reviews.

All money movement rides the SHARED canonical ledger. A refund is the exact ledger
reversal of a capture while the funds are still in escrow:

    debit  mkt_order_escrow:<order_id>     (the held funds)
    credit platform:marketplace_intake     (money returning to the buyer via provider)

The ledger's overdraft guard (escrow is NOT allow-negative) makes an over-refund
impossible: a refund larger than the remaining escrow is refused by the ledger, so
this module can never pay out more than was captured.

Disputes are buyer-opened and admin/seller-resolved; a ``refund`` resolution issues
the refund through the same governed primitive. Reviews are verified-purchase only
(the buyer must have a COMPLETED order containing the product).

PAYOUT EXECUTION NOTE: ``complete_order`` (orders.py) already accrues the seller's net
into ``seller_payable:<seller_id>`` on the ledger. ``seller_payout_balance`` reads that
accrual. Moving the money OUT to the seller's bank is a provider-side transfer that is
prohibited in this environment and deliberately not attempted here.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.marketplace.service import MarketplaceError
from services.business_os.ledger import ledger as _ledger

try:
    from services.business_os.marketplace import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


# --- refunds ----------------------------------------------------------------
def refund_order(order_id: Any, *, amount_cents: Optional[int] = None, reason: str,
                 actor: Any, kind: str = "refund", conn=None) -> dict:
    """Governed refund primitive. ``actor`` and ``reason`` are REQUIRED (this is a
    money-moving admin/seller action). Refunds only while funds are still in escrow
    (order ``paid`` or ``fulfilled``). ``amount_cents=None`` refunds the full remaining
    escrow. A full refund transitions the order to ``refunded``."""
    _svc._require_enabled()
    if actor is None or not str(actor).strip():
        raise MarketplaceError("actor is required.", 400, "actor_required")
    if not reason or not str(reason).strip():
        raise MarketplaceError("reason is required.", 400, "reason_required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = _ord.get_order(order_id, conn=conn)
        if order is None:
            raise MarketplaceError("Order not found.", 404, "not_found")
        if order.get("status") not in _ord.IN_ESCROW_STATUSES:
            raise MarketplaceError(
                "Order funds are not in a refundable state.", 409, "not_refundable")
        cur = order.get("currency", "usd")
        remaining = _ledger.get_balance(_ord.escrow_account(order_id), cur)
        if remaining <= 0:
            raise MarketplaceError("No escrow funds remain to refund.", 409, "nothing_to_refund")
        amt = remaining if amount_cents is None else int(amount_cents)
        if isinstance(amount_cents, bool):
            raise MarketplaceError("amount_cents must be an integer.", 400, "invalid_amount")
        if amt <= 0:
            raise MarketplaceError("amount_cents must be positive.", 400, "invalid_amount")
        if amt > remaining:
            raise MarketplaceError(
                "Refund exceeds remaining escrow.", 409, "refund_exceeds_escrow")

        rid = "mktr_" + uuid.uuid4().hex
        try:
            txn = _ledger.post_entry(
                idempotency_key=f"mkt_refund:{rid}",
                actor=_svc._sid(actor), amount_cents=amt, currency=cur,
                entry_type="marketplace_refund",
                source=_ord.escrow_account(order_id),
                destination=_ord.INTAKE_ACCOUNT,
                reason="Marketplace refund.", related_object=str(order_id))
        except _ledger.LedgerError as exc:
            raise MarketplaceError(str(exc), 409, "refund_rejected")

        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_refunds "
            "(refund_id, order_id, amount_cents, currency, reason, kind, actor, "
            "ledger_txn_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, str(order_id), amt, cur, reason, kind, _svc._sid(actor),
             txn.get("transaction_id"), now))
        new_refunded = int(order.get("refunded_cents") or 0) + amt
        new_status = order.get("status")
        if new_refunded >= int(order["total_cents"]):
            new_status = "refunded"
        conn.execute(
            "UPDATE business_os_mkt_orders SET refunded_cents = ?, status = ?, updated_at = ? "
            "WHERE order_id = ?", (new_refunded, new_status, now, str(order_id)))
        _ord._record_event(conn, order_id, order.get("status"), new_status, actor,
                           reason=reason, meta={"refund_id": rid, "amount_cents": amt})
        _audit(conn, subject_type="order", subject_ref=order_id, action="refund",
               actor=actor, reason=reason,
               before={"refunded_cents": order.get("refunded_cents")},
               after={"refunded_cents": new_refunded, "status": new_status})
        if owned:
            conn.commit()
        _emit(order.get("buyer_user_id"), "order_refunded", order_id)
        return {"refund_id": rid, "order_id": str(order_id), "amount_cents": amt,
                "currency": cur, "order_status": new_status,
                "ledger_txn_ref": txn.get("transaction_id")}
    finally:
        if owned:
            conn.close()


def list_refunds(order_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_refunds WHERE order_id = ? ORDER BY created_at",
            (str(order_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


# --- disputes ---------------------------------------------------------------
def open_dispute(order_id: Any, buyer_user_id: Any, *, reason: str,
                 context: Optional[dict] = None, conn=None) -> dict:
    """Buyer opens a dispute on their own order. One open dispute per order at a time."""
    _svc._require_enabled()
    if not reason or not str(reason).strip():
        raise MarketplaceError("reason is required.", 400, "reason_required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = _ord.get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None or order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        existing = conn.execute(
            "SELECT dispute_id FROM business_os_mkt_disputes "
            "WHERE order_id = ? AND status = 'open'", (str(order_id),)).fetchone()
        if existing is not None:
            raise MarketplaceError("A dispute is already open on this order.",
                                   409, "dispute_exists")
        did = "mktd_" + uuid.uuid4().hex
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_disputes "
            "(dispute_id, order_id, buyer_user_id, status, reason, created_at, updated_at) "
            "VALUES (?, ?, ?, 'open', ?, ?, ?)",
            (did, str(order_id), _svc._sid(buyer_user_id), reason, now, now))
        _audit(conn, subject_type="dispute", subject_ref=did, action="dispute_open",
               actor=buyer_user_id, reason=reason, after={"status": "open"})
        if owned:
            conn.commit()
        _emit(order.get("seller_user_id"), "dispute_opened", order_id)
        return get_dispute(did, conn=conn)
    finally:
        if owned:
            conn.close()


def get_dispute(dispute_id: Any, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return _row(conn.execute(
            "SELECT * FROM business_os_mkt_disputes WHERE dispute_id = ?",
            (str(dispute_id),)).fetchone())
    finally:
        if owned:
            conn.close()


def resolve_dispute(dispute_id: Any, *, resolution: str, actor: Any, reason: str,
                    refund_amount_cents: Optional[int] = None, conn=None) -> dict:
    """Governed admin resolution. ``resolution`` in {'refund','deny'}. A 'refund'
    resolution issues the refund through the governed primitive. actor + reason
    required; a resolved dispute cannot be resolved twice."""
    _svc._require_enabled()
    if actor is None or not str(actor).strip():
        raise MarketplaceError("actor is required.", 400, "actor_required")
    if not reason or not str(reason).strip():
        raise MarketplaceError("reason is required.", 400, "reason_required")
    if resolution not in {"refund", "deny"}:
        raise MarketplaceError("resolution must be 'refund' or 'deny'.", 400, "invalid_resolution")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        dispute = get_dispute(dispute_id, conn=conn)
        if dispute is None:
            raise MarketplaceError("Dispute not found.", 404, "not_found")
        if dispute.get("status") != "open":
            raise MarketplaceError("Dispute is already resolved.", 409, "already_resolved")

        refund_out = None
        if resolution == "refund":
            refund_out = refund_order(
                dispute["order_id"], amount_cents=refund_amount_cents,
                reason=f"Dispute resolution: {reason}", actor=actor,
                kind="dispute_refund", conn=conn)
        conn.execute(
            "UPDATE business_os_mkt_disputes SET status = 'resolved', resolution = ?, "
            "resolver = ?, updated_at = ? WHERE dispute_id = ?",
            (resolution, _svc._sid(actor), _now_iso(), str(dispute_id)))
        _audit(conn, subject_type="dispute", subject_ref=dispute_id,
               action="dispute_resolve", actor=actor, reason=reason,
               before={"status": "open"},
               after={"status": "resolved", "resolution": resolution})
        if owned:
            conn.commit()
        _emit(dispute.get("buyer_user_id"), "dispute_resolved", dispute.get("order_id"))
        out = get_dispute(dispute_id, conn=conn)
        if refund_out:
            out["refund"] = refund_out
        return out
    finally:
        if owned:
            conn.close()


def list_disputes(*, status: Optional[str] = None, order_id: Any = None,
                  limit: int = 200, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_disputes WHERE 1=1"
        params: list = []
        if status:
            q += " AND status = ?"; params.append(status)
        if order_id is not None:
            q += " AND order_id = ?"; params.append(str(order_id))
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        return [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


# --- payouts (accrual read; disbursement is out of scope) -------------------
def seller_payout_balance(seller_user_id: Any, currency: str = "usd") -> dict:
    """Read the seller's accrued payable balance straight off the canonical ledger.
    This is what is OWED; the bank/Stripe disbursement is a separate provider action
    that this environment does not perform."""
    _svc._require_enabled()
    bal = _ledger.get_balance(
        _ord.seller_payable_account(seller_user_id), str(currency or "usd").lower())
    return {"seller_user_id": _svc._sid(seller_user_id), "currency": str(currency or "usd").lower(),
            "payable_cents": bal, "disbursement": "provider_side_out_of_scope"}


# --- reviews (verified purchase) --------------------------------------------
def create_review(buyer_user_id: Any, *, product_id: Any, order_id: Any, rating: int,
                  body: Optional[str] = None, context: Optional[dict] = None,
                  conn=None) -> dict:
    """Verified-purchase review: the buyer must own a COMPLETED order (``order_id``)
    that contains ``product_id``. Rating is an integer 1..5. One review per
    (buyer, order, product)."""
    _svc._require_enabled()
    if isinstance(rating, bool) or not isinstance(rating, int) or rating < 1 or rating > 5:
        raise MarketplaceError("rating must be an integer 1..5.", 400, "invalid_rating")
    if body is not None and len(str(body)) > 4000:
        raise MarketplaceError("review body is too long.", 400, "body_too_long")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = _ord.get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None or order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        if order.get("status") != "completed":
            raise MarketplaceError(
                "Only a completed order can be reviewed.", 409, "order_not_completed")
        items = _ord.get_order_items(order_id, conn=conn)
        if not any(str(it.get("product_id")) == str(product_id) for it in items):
            raise MarketplaceError(
                "That product is not in this order.", 400, "product_not_in_order")
        rvid = "mktv_" + uuid.uuid4().hex
        now = _now_iso()
        try:
            conn.execute(
                "INSERT INTO business_os_mkt_reviews "
                "(review_id, product_id, order_id, buyer_user_id, rating, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rvid, str(product_id), str(order_id), _svc._sid(buyer_user_id),
                 rating, body, now))
        except Exception as exc:
            name = exc.__class__.__name__.lower()
            if "integrity" in name or "unique" in str(exc).lower():
                raise MarketplaceError(
                    "You already reviewed this product for this order.",
                    409, "already_reviewed")
            raise
        if owned:
            conn.commit()
        return {"review_id": rvid, "product_id": str(product_id), "order_id": str(order_id),
                "rating": rating, "body": body, "created_at": now}
    finally:
        if owned:
            conn.close()


def product_rating_summary(product_id: Any, conn=None) -> dict:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(AVG(rating), 0) AS avg_rating "
            "FROM business_os_mkt_reviews WHERE product_id = ?",
            (str(product_id),)).fetchone()
        n = int(row["n"] if hasattr(row, "keys") else row[0])
        avg = float(row["avg_rating"] if hasattr(row, "keys") else row[1])
        return {"product_id": str(product_id), "review_count": n,
                "average_rating": round(avg, 3)}
    finally:
        if owned:
            conn.close()


def list_reviews(product_id: Any, *, limit: int = 100, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT review_id, product_id, order_id, buyer_user_id, rating, body, created_at "
            "FROM business_os_mkt_reviews WHERE product_id = ? ORDER BY created_at DESC LIMIT ?",
            (str(product_id), int(limit))).fetchall()]
    finally:
        if owned:
            conn.close()


# --- shared audit + notify --------------------------------------------------
def _audit(conn, *, subject_type, subject_ref, action, actor, reason=None,
           before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO business_os_mkt_audit "
        "(subject_type, subject_ref, action, actor, reason, before_json, after_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (subject_type, None if subject_ref is None else str(subject_ref), action,
         None if actor is None else str(actor), reason,
         None if before is None else json.dumps(before, sort_keys=True),
         None if after is None else json.dumps(after, sort_keys=True), _now_iso()))


def _emit(user_id, kind, order_id):
    if _notify is None or user_id is None:
        return
    try:
        _notify.emit_order_event(user_id, kind, order_id)
    except Exception:
        pass
