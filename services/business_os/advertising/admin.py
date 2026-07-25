"""Business OS — Advertising: consolidated administrative governance surface (Part 6).

The existing modules already expose the *review* and *monitoring* half of the admin
surface (advertiser listing/status, campaign/creative/ad-set review, funding inspection,
delivery/impression/click search). This module fills the remaining Part-6 gaps as one
consolidated surface:

  * **Billing inspection** — cross-owner read of canonical billing events + a per-campaign
    money summary (processed / failed / pending totals, escrow balance, budget, latch).
  * **Fraud signals** — per-campaign aggregation of impression/click ``fraud_status`` +
    ``billing_eligible`` counts, and a flat list of the flagged (non-clean) events.
  * **Spend controls** — a governed HALT that stops a campaign from spending further
    (pauses delivery→billing) and its lift, both requiring a role + explicit reason.
  * **Restrictions** — governed restrict / lift on an advertiser account (rides the
    canonical ``suspended`` advertiser status), requiring a role + explicit reason.
  * **Appeals** — advertiser-initiated appeal of a restriction/rejection, and the admin
    resolution (grant/deny), recorded on the append-only audit log.

Governance invariants for every SENSITIVE (state-changing) action here:
  1. a non-empty ``actor`` is required (``actor_required``) — the route enforces the
     actual RBAC role; the service refuses to act anonymously;
  2. a non-empty ``reason`` is required (``reason_required``);
  3. the change is written to the append-only ``business_os_ad_audit`` log with the
     acting admin, the reason, and an explicit BEFORE and AFTER snapshot;
  4. the return value carries ``before`` / ``after`` so the caller sees exactly what moved.

This module owns NO new tables and invents NO new money paths — restrictions reuse the
canonical advertiser status, spend halts reuse the operational pause, and appeals/
resolutions are rows on the existing audit log (keyed by a distinct ``action``).
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising import operations as _ops
from services.business_os.advertising import funding as _fnd
from services.business_os.advertising.service import AdvertisingError

try:  # ledger read is best-effort context, never a precondition of an admin read
    from services.business_os.ledger import ledger as _ledger
except Exception:  # pragma: no cover - defensive
    _ledger = None


# Audit action names for the Part-6 governed actions (distinct, greppable).
_ACTION_SPEND_HALT = "admin_spend_halt"
_ACTION_SPEND_LIFT = "admin_spend_lift"
_ACTION_RESTRICT = "admin_advertiser_restrict"
_ACTION_LIFT_RESTRICT = "admin_advertiser_lift_restriction"
_ACTION_APPEAL = "advertiser_appeal"
_ACTION_APPEAL_RESOLVE = "admin_appeal_resolution"

_APPEAL_DECISIONS = {"grant", "deny"}
_ESCROW_PREFIX = "ad_campaign_escrow:"


# --- governance guards ------------------------------------------------------
def _need_actor(actor: Any) -> str:
    a = "" if actor is None else str(actor).strip()
    if not a:
        raise AdvertisingError(
            "An acting administrator is required.", 400, "actor_required")
    return a


def _need_reason(reason: Any) -> str:
    r = "" if reason is None else str(reason).strip()
    if not r:
        raise AdvertisingError(
            "An explicit reason is required for this action.", 400, "reason_required")
    return r[:500]


def _row_to_dict(row) -> Optional[dict]:
    return _svc._row_to_dict(row)


# --- billing inspection -----------------------------------------------------
def admin_list_billing_events(*, campaign_id: Optional[Any] = None,
                              advertiser_user_id: Optional[Any] = None,
                              billing_status: Optional[str] = None,
                              limit: int = 200, conn=None) -> list:
    """Cross-owner read of canonical billing events, newest first. Any filter is
    optional; the route enforces admin RBAC (no ownership scoping here)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        where, args = [], []
        if campaign_id is not None:
            where.append("campaign_id = ?"); args.append(_svc._sid(campaign_id))
        if advertiser_user_id is not None:
            where.append("advertiser_user_id = ?")
            args.append(_svc._sid(advertiser_user_id))
        if billing_status is not None:
            where.append("billing_status = ?")
            args.append(str(billing_status).strip().lower())
        sql = "SELECT * FROM business_os_ad_billing_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, billing_event_id DESC LIMIT ?"
        args.append(int(limit))
        rows = conn.execute(sql, tuple(args)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def admin_billing_summary(campaign_id: Any, currency: str = "usd", *,
                          conn=None) -> dict:
    """Authoritative money summary for one campaign: counts + totals by billing
    status, the live escrow balance, the configured budget, and the exhaustion
    latch. Read-only; reconciles what was billed against what is held."""
    _svc._require_enabled()
    cid = _svc._sid(campaign_id)
    cur = str(currency or "usd").strip().lower()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        campaign = _svc.get_campaign(cid, requester_user_id=None, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        rows = conn.execute(
            "SELECT billing_status, COUNT(*) AS n, "
            "COALESCE(SUM(total_amount_cents), 0) AS cents "
            "FROM business_os_ad_billing_events "
            "WHERE campaign_id = ? AND currency = ? GROUP BY billing_status",
            (cid, cur)).fetchall()
        by_status: dict = {}
        for r in rows:
            d = _row_to_dict(r)
            by_status[d["billing_status"]] = {
                "count": int(d["n"] or 0), "amount_cents": int(d["cents"] or 0)}
        processed = by_status.get("processed", {"count": 0, "amount_cents": 0})
        escrow_balance = None
        if _ledger is not None:
            try:
                escrow_balance = _ledger.get_balance(_ESCROW_PREFIX + cid, currency=cur)
            except Exception:
                escrow_balance = None
        acc = conn.execute(
            "SELECT budget_exhausted FROM business_os_ad_spend_accumulator "
            "WHERE campaign_id = ? AND currency = ?", (cid, cur)).fetchone()
        latch = 0 if acc is None else int(
            (acc[0] if not hasattr(acc, "keys") else acc["budget_exhausted"]) or 0)
        fview = _fnd.get_funding_view(cid, requester_user_id=None, conn=conn)
        return {
            "campaign_id": cid,
            "currency": cur,
            "spent_cents": int(processed.get("amount_cents", 0)),
            "billed_events": {k: v for k, v in by_status.items()},
            "escrow_balance_cents": escrow_balance,
            "budget_cents": (fview or {}).get("budget_cents"),
            "funding_status": (fview or {}).get("funding_status"),
            "budget_exhausted": bool(latch),
        }
    finally:
        if owned:
            conn.close()


# --- fraud signals ----------------------------------------------------------
def _fraud_breakdown(conn, table: str, cid: str) -> dict:
    rows = conn.execute(
        f"SELECT fraud_status, billing_eligible, COUNT(*) AS n FROM {table} "
        "WHERE campaign_id = ? GROUP BY fraud_status, billing_eligible",
        (cid,)).fetchall()
    total = 0
    clean = 0
    flagged = 0
    eligible = 0
    by_status: dict = {}
    for r in rows:
        d = _row_to_dict(r)
        n = int(d["n"] or 0)
        status = d.get("fraud_status") or "unknown"
        total += n
        by_status[status] = by_status.get(status, 0) + n
        if status == "clean":
            clean += n
        else:
            flagged += n
        if int(d.get("billing_eligible") or 0) == 1:
            eligible += n
    return {"total": total, "clean": clean, "flagged": flagged,
            "billing_eligible": eligible, "by_status": by_status}


def admin_fraud_summary(campaign_id: Any, *, conn=None) -> dict:
    """Per-campaign fraud-signal aggregation across impression + click events:
    clean vs flagged counts, billing-eligibility, and a status histogram. This is
    the MVP fraud lens — the underlying ``fraud_status`` is set at event time."""
    _svc._require_enabled()
    cid = _svc._sid(campaign_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        campaign = _svc.get_campaign(cid, requester_user_id=None, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        return {
            "campaign_id": cid,
            "impressions": _fraud_breakdown(
                conn, "business_os_ad_impression_events", cid),
            "clicks": _fraud_breakdown(
                conn, "business_os_ad_click_events", cid),
        }
    finally:
        if owned:
            conn.close()


def admin_list_flagged_events(campaign_id: Any, *, kind: str = "click",
                              limit: int = 200, conn=None) -> list:
    """The flat list of flagged (non-``clean``) events for one campaign, for hands-on
    fraud review. ``kind`` is 'click' or 'impression'."""
    _svc._require_enabled()
    cid = _svc._sid(campaign_id)
    table = {"click": "business_os_ad_click_events",
             "impression": "business_os_ad_impression_events"}.get(kind)
    if table is None:
        raise AdvertisingError(
            f"Unknown event kind: {kind!r}.", 400, "bad_kind")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE campaign_id = ? "
            "AND fraud_status IS NOT NULL AND fraud_status != 'clean' "
            "ORDER BY event_at DESC LIMIT ?",
            (cid, int(limit))).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


# --- spend controls (governed) ----------------------------------------------
def admin_halt_spend(campaign_id: Any, *, actor: Any, reason: Any) -> dict:
    """Governed spend HALT: stop a campaign from spending further by pausing its
    delivery (delivery is the only thing that produces billable events). Requires a
    role + explicit reason; records a spend-halt audit row with before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    cid = _svc._sid(campaign_id)
    before = _ops.admin_get_operational(cid)  # 404 if missing
    before_op = (before or {}).get("operational_status")
    after = _ops.admin_pause_campaign(cid, actor=actor, reason=reason)
    after_op = (after or {}).get("operational_status")
    conn = db.connect()
    try:
        _svc._audit(
            conn, campaign_id=cid,
            advertiser_user_id=(after or {}).get("advertiser_user_id"),
            action=_ACTION_SPEND_HALT, actor=actor, reason=reason,
            before={"operational_status": before_op},
            after={"operational_status": after_op})
        conn.commit()
    finally:
        conn.close()
    return {"campaign_id": cid, "action": _ACTION_SPEND_HALT,
            "before": {"operational_status": before_op},
            "after": {"operational_status": after_op}, "reason": reason}


def admin_lift_spend_halt(campaign_id: Any, *, actor: Any, reason: Any) -> dict:
    """Governed lift of a spend halt: resume a paused campaign back to active.
    Requires a role + explicit reason; records a lift audit row with before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    cid = _svc._sid(campaign_id)
    before = _ops.admin_get_operational(cid)  # 404 if missing
    before_op = (before or {}).get("operational_status")
    # Admin lift transitions paused->active via the canonical operational transition
    # (a legal move in OPERATIONAL_TRANSITIONS); resume is otherwise an owner verb.
    after = _ops._admin_transition(
        cid, "active", actor=actor, reason=reason,
        action="campaign_op_admin_resume", stamp_col=None)
    after_op = (after or {}).get("operational_status")
    conn = db.connect()
    try:
        _svc._audit(
            conn, campaign_id=cid,
            advertiser_user_id=(after or {}).get("advertiser_user_id"),
            action=_ACTION_SPEND_LIFT, actor=actor, reason=reason,
            before={"operational_status": before_op},
            after={"operational_status": after_op})
        conn.commit()
    finally:
        conn.close()
    return {"campaign_id": cid, "action": _ACTION_SPEND_LIFT,
            "before": {"operational_status": before_op},
            "after": {"operational_status": after_op}, "reason": reason}


# --- advertiser restrictions (governed) -------------------------------------
def admin_restrict_advertiser(user_id: Any, *, actor: Any, reason: Any) -> dict:
    """Governed restriction: move an advertiser account to the canonical
    ``suspended`` state. Requires a role + explicit reason; the underlying status
    change writes its own before/after audit row and this returns before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    before = _svc.get_advertiser(user_id)
    if before is None:
        raise AdvertisingError("Advertiser not found.", 404, "not_found")
    if before.get("status") == "suspended":
        raise AdvertisingError(
            "Advertiser is already restricted.", 409, "already_restricted")
    after = _svc.set_advertiser_status(
        user_id, "suspended", actor=actor, reason=reason)
    conn = db.connect()
    try:
        _svc._audit(
            conn, campaign_id=None, advertiser_user_id=_svc._sid(user_id),
            action=_ACTION_RESTRICT, actor=actor, reason=reason,
            before={"status": before.get("status")},
            after={"status": after.get("status")})
        conn.commit()
    finally:
        conn.close()
    return {"user_id": _svc._sid(user_id), "action": _ACTION_RESTRICT,
            "before_status": before.get("status"),
            "after_status": after.get("status"), "reason": reason}


def admin_lift_restriction(user_id: Any, *, actor: Any, reason: Any) -> dict:
    """Governed lift of a restriction: restore a suspended advertiser to
    ``approved``. Requires a role + explicit reason; records before/after."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    before = _svc.get_advertiser(user_id)
    if before is None:
        raise AdvertisingError("Advertiser not found.", 404, "not_found")
    if before.get("status") != "suspended":
        raise AdvertisingError(
            "Advertiser is not currently restricted.", 409, "not_restricted")
    after = _svc.set_advertiser_status(
        user_id, "approved", actor=actor, reason=reason)
    conn = db.connect()
    try:
        _svc._audit(
            conn, campaign_id=None, advertiser_user_id=_svc._sid(user_id),
            action=_ACTION_LIFT_RESTRICT, actor=actor, reason=reason,
            before={"status": before.get("status")},
            after={"status": after.get("status")})
        conn.commit()
    finally:
        conn.close()
    return {"user_id": _svc._sid(user_id), "action": _ACTION_LIFT_RESTRICT,
            "before_status": before.get("status"),
            "after_status": after.get("status"), "reason": reason}


# --- appeals ----------------------------------------------------------------
def submit_appeal(user_id: Any, *, reason: Any, campaign_id: Optional[Any] = None,
                  conn=None) -> dict:
    """Advertiser-initiated appeal, recorded on the append-only audit log. Requires
    a non-empty reason and an existing advertiser record. The appeal id is the audit
    row id, which the admin later resolves."""
    _svc._require_enabled()
    reason = _need_reason(reason)
    uid = _svc._sid(user_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        advertiser = _svc.get_advertiser(uid, conn=conn)
        if advertiser is None:
            raise AdvertisingError("Advertiser not found.", 404, "not_found")
        cid = _svc._sid(campaign_id) if campaign_id is not None else None
        _svc._audit(
            conn, campaign_id=cid, advertiser_user_id=uid,
            action=_ACTION_APPEAL, actor=uid, reason=reason,
            before={"advertiser_status": advertiser.get("status")},
            after={"appeal_state": "open"})
        conn.commit()
        row = conn.execute(
            "SELECT id, created_at FROM business_os_ad_audit "
            "WHERE advertiser_user_id = ? AND action = ? "
            "ORDER BY id DESC LIMIT 1", (uid, _ACTION_APPEAL)).fetchone()
        d = _row_to_dict(row) or {}
        return {"appeal_id": d.get("id"), "user_id": uid, "campaign_id": cid,
                "state": "open", "reason": reason, "created_at": d.get("created_at")}
    finally:
        if owned:
            conn.close()


def admin_list_appeals(*, user_id: Optional[Any] = None, state: Optional[str] = None,
                       limit: int = 200, conn=None) -> list:
    """List appeals with their resolution state. An appeal is ``open`` until a
    resolution row (``admin_appeal_resolution``) references its id; then it carries
    the resolution decision. ``state`` filters to 'open' or 'resolved'."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        args = [_ACTION_APPEAL]
        sql = ("SELECT * FROM business_os_ad_audit WHERE action = ?")
        if user_id is not None:
            sql += " AND advertiser_user_id = ?"; args.append(_svc._sid(user_id))
        sql += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        appeals = [_row_to_dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
        # resolutions carry the appeal id in the reason-independent 'before' snapshot
        res_rows = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM business_os_ad_audit WHERE action = ? ORDER BY id DESC",
            (_ACTION_APPEAL_RESOLVE,)).fetchall()]
        resolved_by_appeal: dict = {}
        for rr in res_rows:
            import json as _json
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
                "user_id": a.get("advertiser_user_id"),
                "campaign_id": a.get("campaign_id"),
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
    """Admin resolution of an appeal: ``grant`` or ``deny``. Requires a role +
    explicit reason. On ``grant`` a restricted advertiser is lifted back to
    ``approved`` as part of the same governed action. Idempotency: an appeal that is
    already resolved is refused (409)."""
    _svc._require_enabled()
    actor = _need_actor(actor)
    reason = _need_reason(reason)
    decision = (decision or "").strip().lower()
    if decision not in _APPEAL_DECISIONS:
        raise AdvertisingError(
            f"Unknown appeal decision: {decision!r}.", 400, "bad_decision")
    try:
        aid = int(appeal_id)
    except (TypeError, ValueError):
        raise AdvertisingError("Invalid appeal id.", 400, "bad_appeal_id")
    conn = db.connect()
    try:
        appeal = _row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_audit WHERE id = ? AND action = ?",
            (aid, _ACTION_APPEAL)).fetchone())
        if appeal is None:
            raise AdvertisingError("Appeal not found.", 404, "not_found")
        if _appeal_is_resolved(conn, aid):
            raise AdvertisingError(
                "Appeal is already resolved.", 409, "already_resolved")
        uid = appeal.get("advertiser_user_id")
        _svc._audit(
            conn, campaign_id=appeal.get("campaign_id"), advertiser_user_id=uid,
            action=_ACTION_APPEAL_RESOLVE, actor=actor, reason=reason,
            before={"appeal_id": aid},
            after={"decision": decision})
        conn.commit()
    finally:
        conn.close()
    lifted = None
    if decision == "grant":
        adv = _svc.get_advertiser(uid)
        if adv is not None and adv.get("status") == "suspended":
            lifted = admin_lift_restriction(
                uid, actor=actor, reason=f"Appeal {aid} granted: {reason}")
    return {"appeal_id": aid, "decision": decision, "resolved_by": actor,
            "user_id": uid, "restriction_lifted": bool(lifted), "reason": reason}


def _appeal_is_resolved(conn, appeal_id: int) -> bool:
    import json as _json
    rows = conn.execute(
        "SELECT before_json FROM business_os_ad_audit WHERE action = ?",
        (_ACTION_APPEAL_RESOLVE,)).fetchall()
    for r in rows:
        d = _row_to_dict(r)
        try:
            meta = _json.loads(d.get("before_json") or "{}")
        except Exception:
            continue
        if meta.get("appeal_id") == appeal_id:
            return True
    return False
