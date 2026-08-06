"""Business OS — Marketplace RETURNS: buyer-initiated return workflow.

Sits ON TOP of the existing modules and moves no money itself:

  * the request/approve/receive lifecycle lives here (new tables, additive only);
  * the refund step is a thin, idempotent call into ``refunds.refund_order`` —
    the ONE governed refund primitive — keyed ``return:{return_id}`` so a return
    refunds at most once no matter how many times the verb is retried (the same
    derived-key shape ``resolve_dispute`` uses);
  * escrow physics are inherited honestly: a refund succeeds only while the
    order's funds are still in escrow (``paid``/``fulfilled``). A return on a
    COMPLETED order (funds already accrued to the seller) surfaces the refund
    engine's 409 — it is closed without money via ``close_return``, and the
    provider-side make-good is out of scope here, exactly as refunds.py states
    for payouts.

Distinct from DISPUTES (refunds.py): a dispute is buyer-opened and
admin-resolved; a return is the seller-operated merchandise flow
(request -> approve -> receive -> refund/close). One open return per order.

State machine (verb-mapped; a client can never write a status):

    requested -> approved | declined | cancelled
    approved  -> received | cancelled
    received  -> refunded | closed
    declined / cancelled / refunded / closed are terminal

Flag-gated by ``BUSINESS_OS_MARKETPLACE`` like the rest of the package.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.marketplace import refunds as _ref
from services.business_os.marketplace.service import MarketplaceError

try:
    from services.business_os.marketplace import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


ALLOWED_RETURN_TRANSITIONS = {
    "requested": {"approved", "declined", "cancelled"},
    "approved": {"received", "cancelled"},
    "received": {"refunded", "closed"},
    "declined": set(),
    "cancelled": set(),
    "refunded": set(),
    "closed": set(),
}
OPEN_RETURN_STATUSES = {"requested", "approved", "received"}
# Order states from which a buyer may open a return. ``completed`` is included
# on purpose: the request/approve flow is still meaningful, only the escrow
# refund path is gone (see module docstring).
RETURNABLE_ORDER_STATUSES = {"paid", "fulfilled", "completed"}

MAX_REASON_LEN = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema(conn=None) -> None:
    """Idempotent DDL (no migration framework — same contract as every pack)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_returns ("
            "return_id TEXT PRIMARY KEY, "
            "order_id TEXT NOT NULL, "
            "buyer_user_id TEXT NOT NULL, "
            "seller_user_id TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "reason TEXT, "
            "product_id TEXT, "
            "quantity INTEGER, "
            "decline_reason TEXT, "
            "close_reason TEXT, "
            "refund_id TEXT, "
            "refund_amount_cents INTEGER, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_returns_order "
            "ON business_os_mkt_returns (order_id, status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_returns_seller "
            "ON business_os_mkt_returns (seller_user_id, status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_returns_buyer "
            "ON business_os_mkt_returns (buyer_user_id, status)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_return_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "return_id TEXT NOT NULL, "
            "from_status TEXT, "
            "to_status TEXT NOT NULL, "
            "actor TEXT, "
            "reason TEXT, "
            "metadata_json TEXT, "
            "created_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_return_events_ret "
            "ON business_os_mkt_return_events (return_id, id)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# --- internal helpers --------------------------------------------------------
def _record_event(conn, return_id, from_status, to_status, actor,
                  reason=None, meta=None) -> None:
    conn.execute(
        "INSERT INTO business_os_mkt_return_events "
        "(return_id, from_status, to_status, actor, reason, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(return_id), from_status, to_status,
         None if actor is None else str(actor), reason,
         None if meta is None else json.dumps(meta, sort_keys=True), _now_iso()))


def _audit(conn, *, subject_ref, action, actor, reason=None,
           before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO business_os_mkt_audit "
        "(subject_type, subject_ref, action, actor, reason, before_json, after_json, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("return", None if subject_ref is None else str(subject_ref), action,
         None if actor is None else str(actor), reason,
         None if before is None else json.dumps(before, sort_keys=True),
         None if after is None else json.dumps(after, sort_keys=True), _now_iso()))


def _assert_transition(cur_status: str, target: str) -> None:
    if target not in ALLOWED_RETURN_TRANSITIONS.get(cur_status, set()):
        raise MarketplaceError(
            f"Illegal return transition {cur_status} -> {target}.",
            409, "illegal_transition")


def _get_scoped(conn, return_id: Any, requester_user_id: Any,
                *, side: Optional[str] = None) -> dict:
    """Fetch a return the requester is a party to; anyone else gets a 404
    (existence not leaked). ``side`` narrows to 'buyer' or 'seller' for verbs
    only that party may perform — the wrong party ALSO gets 404 rather than a
    role hint."""
    row = _row(conn.execute(
        "SELECT * FROM business_os_mkt_returns WHERE return_id = ?",
        (str(return_id),)).fetchone())
    if row is not None and requester_user_id is not None:
        rid = _svc._sid(requester_user_id)
        allowed = {"buyer": (row.get("buyer_user_id"),),
                   "seller": (row.get("seller_user_id"),)}.get(
            side, (row.get("buyer_user_id"), row.get("seller_user_id")))
        if rid not in allowed:
            row = None
    if row is None:
        raise MarketplaceError("Return not found.", 404, "not_found")
    return row


def _clean_reason(reason: Any, *, required: bool) -> Optional[str]:
    if reason is None or not str(reason).strip():
        if required:
            raise MarketplaceError("reason is required.", 400, "reason_required")
        return None
    text = str(reason).strip()
    if len(text) > MAX_REASON_LEN:
        raise MarketplaceError("reason is too long.", 400, "reason_too_long")
    return text


def _emit(user_id, kind, order_id) -> None:
    if _notify is None or user_id is None:
        return
    try:
        _notify.emit_order_event(user_id, kind, order_id)
    except Exception:
        pass


# --- reads -------------------------------------------------------------------
def get_return(return_id: Any, *, requester_user_id: Any = None,
               conn=None) -> Optional[dict]:
    """Buyer or seller may read; anyone else gets None (existence not leaked)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        try:
            return _get_scoped(conn, return_id, requester_user_id)
        except MarketplaceError:
            return None
    finally:
        if owned:
            conn.close()


def get_return_events(return_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_return_events WHERE return_id = ? "
            "ORDER BY id", (str(return_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_returns(*, buyer_user_id: Any = None, seller_user_id: Any = None,
                 order_id: Any = None, status: Optional[str] = None,
                 limit: int = 200, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_returns WHERE 1=1"
        params: list = []
        if buyer_user_id is not None:
            q += " AND buyer_user_id = ?"; params.append(_svc._sid(buyer_user_id))
        if seller_user_id is not None:
            q += " AND seller_user_id = ?"; params.append(_svc._sid(seller_user_id))
        if order_id is not None:
            q += " AND order_id = ?"; params.append(str(order_id))
        if status:
            q += " AND status = ?"; params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        return [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


# --- buyer verbs -------------------------------------------------------------
def request_return(buyer_user_id: Any, order_id: Any, *, reason: str,
                   product_id: Any = None, quantity: Any = None,
                   context: Optional[dict] = None, conn=None) -> dict:
    """Buyer opens a return on their own order. One open return per order.

    ``product_id``/``quantity`` optionally narrow the return to a line item
    (validated against the order's items); omitted means the whole order."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    reason_text = _clean_reason(reason, required=True)
    if quantity is not None and (isinstance(quantity, bool)
                                 or not isinstance(quantity, int) or quantity <= 0):
        raise MarketplaceError("quantity must be a positive integer.",
                               400, "invalid_quantity")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = _ord.get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None or order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        if order.get("status") not in RETURNABLE_ORDER_STATUSES:
            raise MarketplaceError(
                "This order is not in a returnable state.", 409, "not_returnable")
        existing = conn.execute(
            "SELECT return_id FROM business_os_mkt_returns "
            "WHERE order_id = ? AND status IN ('requested','approved','received')",
            (str(order_id),)).fetchone()
        if existing is not None:
            raise MarketplaceError("A return is already open on this order.",
                                   409, "return_exists")
        if product_id is not None:
            items = _ord.get_order_items(order_id, conn=conn)
            match = next((it for it in items
                          if str(it.get("product_id")) == str(product_id)), None)
            if match is None:
                raise MarketplaceError(
                    "That product is not in this order.", 400, "product_not_in_order")
            if quantity is not None and quantity > int(match.get("quantity") or 0):
                raise MarketplaceError(
                    "quantity exceeds the ordered quantity.", 400, "invalid_quantity")
        elif quantity is not None:
            raise MarketplaceError(
                "quantity requires product_id.", 400, "invalid_quantity")

        rid = "mktret_" + uuid.uuid4().hex
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_returns "
            "(return_id, order_id, buyer_user_id, seller_user_id, status, reason, "
            "product_id, quantity, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'requested', ?, ?, ?, ?, ?)",
            (rid, str(order_id), order["buyer_user_id"], order["seller_user_id"],
             reason_text, None if product_id is None else str(product_id),
             quantity, now, now))
        _record_event(conn, rid, None, "requested", buyer_user_id, reason=reason_text)
        _audit(conn, subject_ref=rid, action="return_request", actor=buyer_user_id,
               reason=reason_text,
               after={"status": "requested", "order_id": str(order_id)})
        if owned:
            conn.commit()
        _emit(order.get("seller_user_id"), "return_requested", order_id)
        return get_return(rid, conn=conn)
    finally:
        if owned:
            conn.close()


def cancel_return(return_id: Any, buyer_user_id: Any, *,
                  context: Optional[dict] = None, conn=None) -> dict:
    """Buyer withdraws their own return (allowed until the seller has received
    the merchandise)."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    return _transition(return_id, buyer_user_id, side="buyer", target="cancelled",
                       action="return_cancel", notify_other=True, conn=conn)


# --- seller verbs ------------------------------------------------------------
def approve_return(return_id: Any, seller_user_id: Any, *,
                   context: Optional[dict] = None, conn=None) -> dict:
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    return _transition(return_id, seller_user_id, side="seller", target="approved",
                       action="return_approve", notify_other=True, conn=conn)


def decline_return(return_id: Any, seller_user_id: Any, *, reason: str,
                   context: Optional[dict] = None, conn=None) -> dict:
    """Declining a request requires a stated reason (it lands in the buyer-visible
    record, the event trail, and the audit row)."""
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    reason_text = _clean_reason(reason, required=True)
    return _transition(return_id, seller_user_id, side="seller", target="declined",
                       action="return_decline", reason=reason_text,
                       extra_set={"decline_reason": reason_text},
                       notify_other=True, conn=conn)


def mark_received(return_id: Any, seller_user_id: Any, *,
                  context: Optional[dict] = None, conn=None) -> dict:
    """Seller confirms the merchandise arrived back. Unlocks refund/close."""
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    return _transition(return_id, seller_user_id, side="seller", target="received",
                       action="return_receive", notify_other=False, conn=conn)


def refund_return(return_id: Any, seller_user_id: Any, *,
                  amount_cents: Optional[int] = None,
                  context: Optional[dict] = None, conn=None) -> dict:
    """received -> refunded, with the money moved by the ONE governed refund
    primitive, keyed ``return:{return_id}`` (at-most-once per return no matter
    the retries). ``amount_cents=None`` refunds the full remaining escrow.

    Escrow physics surface honestly: if the order's funds have already left
    escrow (order ``completed``), the refund engine answers 409 and this return
    stays ``received`` — close it without money via ``close_return``."""
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ret = _get_scoped(conn, return_id, seller_user_id, side="seller")
        _assert_transition(ret["status"], "refunded")
        refund = _ref.refund_order(
            ret["order_id"], amount_cents=amount_cents,
            reason=f"Return {ret['return_id']} refund.", actor=seller_user_id,
            kind="return_refund", conn=conn,
            idempotency_key=f"return:{ret['return_id']}")
        now = _now_iso()
        conn.execute(
            "UPDATE business_os_mkt_returns SET status = 'refunded', refund_id = ?, "
            "refund_amount_cents = ?, updated_at = ? WHERE return_id = ?",
            (refund.get("refund_id"), int(refund.get("amount_cents") or 0),
             now, str(return_id)))
        _record_event(conn, return_id, ret["status"], "refunded", seller_user_id,
                      meta={"refund_id": refund.get("refund_id"),
                            "amount_cents": refund.get("amount_cents")})
        _audit(conn, subject_ref=return_id, action="return_refund",
               actor=seller_user_id,
               before={"status": ret["status"]},
               after={"status": "refunded",
                      "refund_id": refund.get("refund_id"),
                      "amount_cents": refund.get("amount_cents")})
        if owned:
            conn.commit()
        _emit(ret.get("buyer_user_id"), "return_refunded", ret.get("order_id"))
        out = get_return(return_id, conn=conn)
        out["refund"] = refund
        return out
    finally:
        if owned:
            conn.close()


def close_return(return_id: Any, seller_user_id: Any, *, reason: str,
                 context: Optional[dict] = None, conn=None) -> dict:
    """received -> closed WITHOUT money (e.g. escrow already released, or the
    parties settled outside). Requires a stated reason for the audit trail."""
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    reason_text = _clean_reason(reason, required=True)
    return _transition(return_id, seller_user_id, side="seller", target="closed",
                       action="return_close", reason=reason_text,
                       extra_set={"close_reason": reason_text},
                       notify_other=True, conn=conn)


# --- shared transition worker ------------------------------------------------
def _transition(return_id: Any, actor_user_id: Any, *, side: str, target: str,
                action: str, reason: Optional[str] = None,
                extra_set: Optional[dict] = None, notify_other: bool = False,
                conn=None) -> dict:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ret = _get_scoped(conn, return_id, actor_user_id, side=side)
        _assert_transition(ret["status"], target)
        now = _now_iso()
        sets = {"status": target, "updated_at": now}
        sets.update(extra_set or {})
        assign = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE business_os_mkt_returns SET {assign} WHERE return_id = ?",
            (*sets.values(), str(return_id)))
        _record_event(conn, return_id, ret["status"], target, actor_user_id,
                      reason=reason)
        _audit(conn, subject_ref=return_id, action=action, actor=actor_user_id,
               reason=reason, before={"status": ret["status"]},
               after={"status": target})
        if owned:
            conn.commit()
        if notify_other:
            other = (ret.get("seller_user_id") if side == "buyer"
                     else ret.get("buyer_user_id"))
            _emit(other, f"return_{target}", ret.get("order_id"))
        return get_return(return_id, conn=conn)
    finally:
        if owned:
            conn.close()
