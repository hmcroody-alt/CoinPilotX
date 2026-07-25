"""Business OS — Marketplace: consolidated administrative governance surface (Stage 3 Part 6).

The seller/order/refund modules already own the buyer- and seller-facing verbs. This
module is the admin lens over the same canonical tables — cross-owner reads plus the
governed state-changing actions an operator needs, mirroring the advertising admin
surface exactly:

  * **Order inspection** — cross-owner read of any order with its items, event history,
    ledger-derived money summary, refunds, and disputes in one payload.
  * **Refund / dispute resolution** — governed wrappers over the canonical
    ``refunds.refund_order`` / ``refunds.resolve_dispute`` money primitives (they already
    enforce escrow state + post to the shared ledger); this layer just carries the admin
    RBAC actor and reason through and adds an admin-scoped audit row.
  * **Seller restrictions** — governed restrict / lift on a seller account, riding the
    canonical ``suspended`` seller status, requiring a role + explicit reason.
  * **Appeals** — seller-initiated appeal of a restriction, and the admin resolution
    (grant/deny) — grant lifts the restriction as part of the same governed action.
  * **Payout controls** — read the accrued (owed) seller-payable balance off the ledger,
    and record a governed, audit-only settlement NOTE. This module NEVER disburses money:
    the actual bank/Stripe payout is a provider-side action out of scope here, and the
    note explicitly does not move a cent on the ledger.

Governance invariants for every SENSITIVE (state-changing) action:
  1. a non-empty ``actor`` is required (``actor_required``);
  2. a non-empty ``reason`` is required (``reason_required``);
  3. the change is written to the append-only ``business_os_mkt_audit`` log with the
     acting admin, the reason, and an explicit BEFORE and AFTER snapshot;
  4. the return value carries before/after so the caller sees exactly what moved.

This module owns NO new tables and invents NO new money paths — it rides the existing
``business_os_mkt_audit`` log (keyed by distinct ``action`` names), so it needs no
migration of its own.
"""

from __future__ import annotations

import json as _json
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.marketplace import refunds as _rf
from services.business_os.marketplace.service import MarketplaceError

try:  # ledger read is best-effort context, never a precondition of an admin read
    from services.business_os.ledger import ledger as _ledger
except Exception:  # pragma: no cover - defensive
    _ledger = None


# Audit action names for the Part-6 governed actions (distinct, greppable).
_ACTION_RESTRICT = "admin_seller_restrict"
_ACTION_LIFT_RESTRICT = "admin_seller_lift_restriction"
_ACTION_APPEAL = "seller_appeal"
_ACTION_APPEAL_RESOLVE = "admin_appeal_resolution"
_ACTION_PAYOUT_NOTE = "admin_payout_settlement_note"

_APPEAL_DECISIONS = {"grant", "deny"}


# --- governance guards ------------------------------------------------------
def _need_actor(actor: Any) -> str:
    a = "" if actor is None else str(actor).strip()
    if not a:
        raise MarketplaceError("An acting administrator is required.", 400, "actor_required")
    return a


def _need_reason(reason: Any) -> str:
    r = "" if reason is None else str(reason).strip()
    if not r:
        raise MarketplaceError(
            "An explicit reason is required for this action.", 400, "reason_required")
    return r[:500]


# --- order inspection -------------------------------------------------------
def admin_get_order(order_id: Any, *, conn=None) -> dict:
    """Cross-owner full inspection of one order: the order row plus its line items,
    event history, ledger-derived money summary, refunds, and disputes. Read-only;
    no ownership scoping (the route enforces admin RBAC)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = _ord.get_order(order_id, conn=conn)  # no requester -> cross-owner
        if order is None:
            raise MarketplaceError("Order not found.", 404, "not_found")
        return {
            "order": order,
            "items": _ord.get_order_items(order_id, conn=conn),
            "events": _ord.get_order_events(order_id, conn=conn),
            "money": _ord.order_money_summary(order_id, conn=conn),
            "refunds": _rf.list_refunds(order_id, conn=conn),
            "disputes": _rf.list_disputes(order_id=order_id, conn=conn),
        }
    finally:
        if owned:
            conn.close()


def admin_list_orders(*, buyer_user_id: Optional[Any] = None,
                      seller_user_id: Optional[Any] = None,
                      status: Optional[str] = None, limit: int = 200,
                      conn=None) -> list:
    """Cross-owner order search, newest first. Any filter is optional."""
    _svc._require_enabled()
    return _ord.list_orders(buyer_user_id=buyer_user_id, seller_user_id=seller_user_id,
                            status=status, limit=limit, conn=conn)


# --- refund / dispute resolution (governed) ---------------------------------
def admin_refund_order(order_id: Any, *, actor: Any, reason: Any,
                       amount_cents: Optional[int] = None) -> dict:
    """Governed admin refund. Delegates to the canonical ``refund_order`` money
    primitive (escrow-state checks + ledger post live there); this layer requires a
    role + explicit reason and adds an admin-scoped audit row with before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    before = _ord.get_order(order_id)
    if before is None:
        raise MarketplaceError("Order not found.", 404, "not_found")
    out = _rf.refund_order(order_id, amount_cents=amount_cents, reason=reason,
                           actor=actor, kind="admin_refund")
    conn = db.connect()
    try:
        _svc._audit(conn, subject_type="order", subject_ref=order_id,
                    action="admin_refund", actor=actor, reason=reason,
                    before={"status": before.get("status"),
                            "refunded_cents": before.get("refunded_cents")},
                    after={"status": out.get("order_status"),
                           "refunded_amount_cents": out.get("amount_cents")})
        conn.commit()
    finally:
        conn.close()
    return {"order_id": _svc._sid(order_id), "action": "admin_refund",
            "before": {"status": before.get("status"),
                       "refunded_cents": before.get("refunded_cents")},
            "after": {"status": out.get("order_status")},
            "refund": out, "reason": reason}


def admin_list_disputes(*, status: Optional[str] = None, order_id: Optional[Any] = None,
                        limit: int = 200, conn=None) -> list:
    """Cross-owner dispute search (all sellers/buyers). ``status`` filters open/resolved."""
    _svc._require_enabled()
    return _rf.list_disputes(status=status, order_id=order_id, limit=limit, conn=conn)


def admin_resolve_dispute(dispute_id: Any, decision: str, *, actor: Any, reason: Any,
                          refund_amount_cents: Optional[int] = None) -> dict:
    """Governed dispute resolution. ``decision`` in {'refund','deny'} (mapped to the
    canonical resolution). A 'refund' resolution issues the refund through the governed
    money primitive. Requires a role + explicit reason."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    decision = (decision or "").strip().lower()
    if decision not in {"refund", "deny"}:
        raise MarketplaceError(
            f"Unknown dispute decision: {decision!r}.", 400, "bad_decision")
    return _rf.resolve_dispute(dispute_id, resolution=decision, actor=actor,
                               reason=reason, refund_amount_cents=refund_amount_cents)


# --- seller restrictions (governed) -----------------------------------------
def admin_restrict_seller(user_id: Any, *, actor: Any, reason: Any) -> dict:
    """Governed restriction: move a seller account to the canonical ``suspended``
    state. Requires a role + explicit reason; the underlying status change writes its
    own before/after audit row and this returns before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    before = _svc.get_seller(user_id)
    if before is None:
        raise MarketplaceError("Seller not found.", 404, "not_found")
    if before.get("status") == "suspended":
        raise MarketplaceError("Seller is already restricted.", 409, "already_restricted")
    after = _svc.set_seller_status(user_id, "suspended", actor=actor, reason=reason)
    conn = db.connect()
    try:
        _svc._audit(conn, subject_type="seller", subject_ref=_svc._sid(user_id),
                    action=_ACTION_RESTRICT, actor=actor, reason=reason,
                    before={"status": before.get("status")},
                    after={"status": after.get("status")})
        conn.commit()
    finally:
        conn.close()
    _rf._emit(user_id, "seller_restricted", None)
    return {"user_id": _svc._sid(user_id), "action": _ACTION_RESTRICT,
            "before_status": before.get("status"),
            "after_status": after.get("status"), "reason": reason}


def admin_lift_seller_restriction(user_id: Any, *, actor: Any, reason: Any) -> dict:
    """Governed lift of a restriction: restore a suspended seller to ``approved``.
    Requires a role + explicit reason; records before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    before = _svc.get_seller(user_id)
    if before is None:
        raise MarketplaceError("Seller not found.", 404, "not_found")
    if before.get("status") != "suspended":
        raise MarketplaceError("Seller is not currently restricted.", 409, "not_restricted")
    after = _svc.set_seller_status(user_id, "approved", actor=actor, reason=reason)
    conn = db.connect()
    try:
        _svc._audit(conn, subject_type="seller", subject_ref=_svc._sid(user_id),
                    action=_ACTION_LIFT_RESTRICT, actor=actor, reason=reason,
                    before={"status": before.get("status")},
                    after={"status": after.get("status")})
        conn.commit()
    finally:
        conn.close()
    _rf._emit(user_id, "seller_reinstated", None)
    return {"user_id": _svc._sid(user_id), "action": _ACTION_LIFT_RESTRICT,
            "before_status": before.get("status"),
            "after_status": after.get("status"), "reason": reason}


# --- appeals ----------------------------------------------------------------
def submit_appeal(user_id: Any, *, reason: Any, order_id: Optional[Any] = None,
                  conn=None) -> dict:
    """Seller-initiated appeal, recorded on the append-only audit log. Requires a
    non-empty reason and an existing seller record. The appeal id is the audit row id,
    which the admin later resolves."""
    _svc._require_enabled()
    reason = _need_reason(reason)
    uid = _svc._sid(user_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        seller = _svc.get_seller(uid, conn=conn)
        if seller is None:
            raise MarketplaceError("Seller not found.", 404, "not_found")
        _svc._audit(conn, subject_type="seller", subject_ref=uid,
                    action=_ACTION_APPEAL, actor=uid, reason=reason,
                    before={"seller_status": seller.get("status")},
                    after={"appeal_state": "open",
                           "order_id": None if order_id is None else str(order_id)})
        conn.commit()
        row = conn.execute(
            "SELECT id, created_at FROM business_os_mkt_audit "
            "WHERE subject_ref = ? AND action = ? ORDER BY id DESC LIMIT 1",
            (uid, _ACTION_APPEAL)).fetchone()
        d = _svc._row(row) or {}
        return {"appeal_id": d.get("id"), "user_id": uid,
                "order_id": None if order_id is None else str(order_id),
                "state": "open", "reason": reason, "created_at": d.get("created_at")}
    finally:
        if owned:
            conn.close()


def admin_list_appeals(*, user_id: Optional[Any] = None, state: Optional[str] = None,
                       limit: int = 200, conn=None) -> list:
    """List seller appeals with their resolution state. An appeal is ``open`` until a
    resolution row (``admin_appeal_resolution``) references its id; then it carries the
    resolution decision. ``state`` filters to 'open' or 'resolved'."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        args: list = [_ACTION_APPEAL]
        sql = "SELECT * FROM business_os_mkt_audit WHERE action = ?"
        if user_id is not None:
            sql += " AND subject_ref = ?"; args.append(_svc._sid(user_id))
        sql += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        appeals = [_svc._row(r) for r in conn.execute(sql, tuple(args)).fetchall()]
        res_rows = [_svc._row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_audit WHERE action = ? ORDER BY id DESC",
            (_ACTION_APPEAL_RESOLVE,)).fetchall()]
        resolved_by_appeal: dict = {}
        for rr in res_rows:
            try:
                meta = _json.loads(rr.get("before_json") or "{}")
            except Exception:
                meta = {}
            aid = meta.get("appeal_id")
            if aid is not None and aid not in resolved_by_appeal:
                try:
                    after = _json.loads(rr.get("after_json") or "{}")
                except Exception:
                    after = {}
                resolved_by_appeal[aid] = {
                    "decision": after.get("decision"),
                    "resolved_by": rr.get("actor"),
                    "resolved_at": rr.get("created_at"),
                    "resolution_reason": rr.get("reason")}
        out = []
        for a in appeals:
            resolution = resolved_by_appeal.get(a.get("id"))
            item = {
                "appeal_id": a.get("id"),
                "user_id": a.get("subject_ref"),
                "reason": a.get("reason"),
                "created_at": a.get("created_at"),
                "state": "resolved" if resolution else "open",
                "resolution": resolution,
            }
            if state is None or item["state"] == state:
                out.append(item)
        return out
    finally:
        if owned:
            conn.close()


def admin_resolve_appeal(appeal_id: Any, decision: str, *, actor: Any,
                         reason: Any) -> dict:
    """Admin resolution of a seller appeal: ``grant`` or ``deny``. Requires a role +
    explicit reason. On ``grant`` a restricted seller is lifted back to ``approved`` as
    part of the same governed action. An already-resolved appeal is refused (409)."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    decision = (decision or "").strip().lower()
    if decision not in _APPEAL_DECISIONS:
        raise MarketplaceError(f"Unknown appeal decision: {decision!r}.", 400, "bad_decision")
    try:
        aid = int(appeal_id)
    except (TypeError, ValueError):
        raise MarketplaceError("Invalid appeal id.", 400, "bad_appeal_id")
    conn = db.connect()
    try:
        appeal = _svc._row(conn.execute(
            "SELECT * FROM business_os_mkt_audit WHERE id = ? AND action = ?",
            (aid, _ACTION_APPEAL)).fetchone())
        if appeal is None:
            raise MarketplaceError("Appeal not found.", 404, "not_found")
        if _appeal_is_resolved(conn, aid):
            raise MarketplaceError("Appeal is already resolved.", 409, "already_resolved")
        uid = appeal.get("subject_ref")
        _svc._audit(conn, subject_type="seller", subject_ref=uid,
                    action=_ACTION_APPEAL_RESOLVE, actor=actor, reason=reason,
                    before={"appeal_id": aid}, after={"decision": decision})
        conn.commit()
    finally:
        conn.close()
    lifted = None
    if decision == "grant":
        seller = _svc.get_seller(uid)
        if seller is not None and seller.get("status") == "suspended":
            lifted = admin_lift_seller_restriction(
                uid, actor=actor, reason=f"Appeal {aid} granted: {reason}")
    return {"appeal_id": aid, "decision": decision, "resolved_by": actor,
            "user_id": uid, "restriction_lifted": bool(lifted), "reason": reason}


def _appeal_is_resolved(conn, appeal_id: int) -> bool:
    rows = conn.execute(
        "SELECT before_json FROM business_os_mkt_audit WHERE action = ?",
        (_ACTION_APPEAL_RESOLVE,)).fetchall()
    for r in rows:
        d = _svc._row(r)
        try:
            meta = _json.loads(d.get("before_json") or "{}")
        except Exception:
            continue
        if meta.get("appeal_id") == appeal_id:
            return True
    return False


# --- payout controls (accrual read + audit-only settlement note) ------------
def admin_seller_payout_balance(seller_user_id: Any, currency: str = "usd") -> dict:
    """Read the seller's accrued (owed) payable balance off the canonical ledger. This
    is what the platform OWES; the bank/Stripe disbursement is a provider-side action
    this environment does not perform."""
    _svc._require_enabled()
    return _rf.seller_payout_balance(seller_user_id, currency)


def admin_record_payout_note(seller_user_id: Any, *, actor: Any, reason: Any,
                             amount_cents: Optional[int] = None,
                             provider_reference: Optional[str] = None,
                             currency: str = "usd") -> dict:
    """Record a GOVERNED, AUDIT-ONLY note that an off-platform disbursement was made to
    a seller. This DOES NOT move any money on the ledger — actual payout execution is a
    provider-side action out of scope here (see the marketplace payout policy). It only
    documents, with actor + reason + optional provider reference, that a payout occurred,
    so operators have an append-only record. Requires a role + explicit reason."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    uid = _svc._sid(seller_user_id)
    cur = str(currency or "usd").strip().lower()
    payable = _rf.seller_payout_balance(uid, cur)
    conn = db.connect()
    try:
        _svc._audit(
            conn, subject_type="seller", subject_ref=uid,
            action=_ACTION_PAYOUT_NOTE, actor=actor, reason=reason,
            before={"payable_cents": payable.get("payable_cents"), "currency": cur},
            after={"note_amount_cents": amount_cents,
                   "provider_reference": provider_reference,
                   "moved_money": False, "disbursement": "provider_side_out_of_scope"})
        conn.commit()
    finally:
        conn.close()
    return {"seller_user_id": uid, "action": _ACTION_PAYOUT_NOTE,
            "payable_cents": payable.get("payable_cents"), "currency": cur,
            "note_amount_cents": amount_cents, "provider_reference": provider_reference,
            "moved_money": False, "disbursement": "provider_side_out_of_scope",
            "reason": reason}
