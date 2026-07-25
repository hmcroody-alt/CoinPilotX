"""Business OS — Advertising vertical, slice-5 operational lifecycle (flag-gated).

Lets a REVIEW-APPROVED, FUNDED campaign enter a controlled *operational* lifecycle
— WITHOUT serving a single ad. It keeps FOUR concerns strictly separate:

    1. Review status      -> campaign.status ('approved')        (slice 3)
    2. Funding status     -> funding_status ('funded')           (slice 4)
    3. Operational status -> operational_status                  (THIS slice)
    4. Delivery execution -> NOT STARTED anywhere

Operational states (their own vocabulary, never mixed with review/funding):
``inactive | scheduled | active | paused | completed | cancelled``.

Only these transitions are permitted (all others rejected 409):

    inactive  -> scheduled | active
    scheduled -> active | paused | cancelled
    active    -> paused | cancelled | completed
    paused    -> active | cancelled

A campaign may become ``scheduled`` or ``active`` only when it is review-approved,
funded, derived ``activation_ready`` is true, the advertiser remains approved, the
account is active/access-enabled, the campaign is not archived, the budget/currency
remain valid, and any supplied UTC start/end window is valid. Approval alone does
not activate; funding alone does not activate.

``active`` means the campaign is operationally AUTHORIZED for a FUTURE delivery
worker — it is NOT currently delivering. NOTHING here selects an audience, returns
a placement, records an impression/click, deducts spend, moves escrow, paces, or
auctions. Cancellation deliberately does NOT release funds — releasing reserved
budget stays an explicit call into the slice-4 funding service.

Every transition is written to the existing append-only ``business_os_ad_audit``
trail (actor, campaign, previous state, new state, reason, timestamp) — no
competing audit framework is introduced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.advertising import funding as _fnd
from services.business_os.advertising import service as _svc
from services.business_os.advertising.service import AdvertisingError

try:  # canonical notification adapters; import defensively (never a precondition).
    from services.business_os.advertising import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- vocabulary -------------------------------------------------------------
OPERATIONAL_STATUSES = {
    "inactive", "scheduled", "active", "paused", "completed", "cancelled",
}
# Single source of truth for legal operational moves. Any pair not listed here is
# rejected server-side (409 illegal_operational_transition).
OPERATIONAL_TRANSITIONS = {
    "inactive": {"scheduled", "active"},
    "scheduled": {"active", "paused", "cancelled"},
    "active": {"paused", "cancelled", "completed"},
    "paused": {"active", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
# The two states whose ENTRY requires the full activation-eligibility gate.
ACTIVATION_TARGETS = {"scheduled", "active"}

REASON_MAX = 500


# --- small helpers ----------------------------------------------------------
def _clean_reason(reason: Any) -> Optional[str]:
    if reason is None:
        return None
    s = str(reason).strip()
    return s[:REASON_MAX] if s else None


def _parse_utc_dt(value: Any, field: str) -> datetime:
    """Parse an ISO-8601 timestamp (or epoch seconds) into an aware UTC datetime.

    A naive timestamp is assumed to already be UTC. Anything unparseable is
    rejected 400 so an invalid schedule can never be stored.
    """
    if isinstance(value, bool):
        raise AdvertisingError(f"Invalid {field} timestamp.", 400, "bad_timestamp")
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            raise AdvertisingError(f"Invalid {field} timestamp.", 400, "bad_timestamp")
    s = str(value or "").strip()
    if not s:
        raise AdvertisingError(f"Invalid {field} timestamp.", 400, "bad_timestamp")
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        raise AdvertisingError(
            f"Invalid {field} timestamp: {value!r}.", 400, "bad_timestamp")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_window(start_at: Any, end_at: Any) -> Optional[dict]:
    """Normalize an optional UTC start/end window.

    Returns ``None`` when neither is supplied (no window change). Otherwise returns
    a dict with only the supplied, UTC-normalized fields. When BOTH are supplied,
    the end must be strictly after the start (else 400 ``bad_window``).
    """
    if start_at is None and end_at is None:
        return None
    sdt = _parse_utc_dt(start_at, "start_at") if start_at is not None else None
    edt = _parse_utc_dt(end_at, "end_at") if end_at is not None else None
    if sdt is not None and edt is not None and edt <= sdt:
        raise AdvertisingError(
            "End time must be after start time.", 400, "bad_window")
    out: dict = {}
    if sdt is not None:
        out["start_at"] = _fmt(sdt)
    if edt is not None:
        out["end_at"] = _fmt(edt)
    return out or None


def _get_ops_row(conn, campaign_id: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_campaign_operations WHERE campaign_id = ?",
        (campaign_id,)).fetchone())


def _ensure_ops_row(conn, campaign_id: str, advertiser_uid: Any) -> dict:
    """Return the operational row, creating an ``inactive`` one if absent.

    Must be called inside an open transaction.
    """
    row = _get_ops_row(conn, campaign_id)
    if row is None:
        now = _svc._now_iso()
        conn.execute(
            "INSERT INTO business_os_ad_campaign_operations "
            "(campaign_id, advertiser_user_id, operational_status, "
            "created_at, updated_at) VALUES (?, ?, 'inactive', ?, ?)",
            (campaign_id, _svc._sid(advertiser_uid), now, now))
        row = _get_ops_row(conn, campaign_id)
    return row


def _ops_public(campaign: dict, funding_view: dict,
                ops_row: Optional[dict]) -> dict:
    """Client-safe operational projection. Shows the THREE separate states side by
    side without merging them, and states explicitly that being ``active`` does not
    mean delivering."""
    op_status = (ops_row or {}).get("operational_status") or "inactive"
    return {
        "campaign_id": campaign.get("campaign_id"),
        "advertiser_user_id": campaign.get("advertiser_user_id"),
        "review_status": campaign.get("status"),
        "funding_status": funding_view.get("funding_status"),
        "activation_ready": bool(funding_view.get("activation_ready")),
        "operational_status": op_status,
        "start_at": (ops_row or {}).get("start_at"),
        "end_at": (ops_row or {}).get("end_at"),
        "activated_at": (ops_row or {}).get("activated_at"),
        "paused_at": (ops_row or {}).get("paused_at"),
        "completed_at": (ops_row or {}).get("completed_at"),
        "cancelled_at": (ops_row or {}).get("cancelled_at"),
        "last_reason": (ops_row or {}).get("last_reason"),
        # This slice authorizes FUTURE delivery only; it never delivers.
        "delivering": False,
        "updated_at": (ops_row or {}).get("updated_at"),
    }


def _apply_transition(conn, campaign_id: str, advertiser_uid: Any,
                      cur_status: str, new_status: str, *, actor,
                      reason: Optional[str], action: str,
                      extra_sets: Optional[dict] = None) -> None:
    """Validate against ``OPERATIONAL_TRANSITIONS`` and persist the move + audit.

    ``extra_sets`` keys come only from fixed literals in the callers (timestamp
    columns, start_at/end_at) — never from client input — so the built SQL carries
    no injection surface. Must be called inside an open transaction.
    """
    allowed = OPERATIONAL_TRANSITIONS.get(cur_status, set())
    if new_status not in allowed:
        raise AdvertisingError(
            f"Illegal operational transition {cur_status} -> {new_status}.",
            409, "illegal_operational_transition")
    sets = {"operational_status": new_status}
    if extra_sets:
        sets.update(extra_sets)
    if reason is not None:
        sets["last_reason"] = reason
    sets["updated_at"] = _svc._now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(
        f"UPDATE business_os_ad_campaign_operations SET {set_clause} "
        "WHERE campaign_id = ?",
        tuple(list(sets.values()) + [campaign_id]))
    _svc._audit(conn, campaign_id=campaign_id, advertiser_user_id=advertiser_uid,
                action=action, actor=actor, reason=reason,
                before={"operational_status": cur_status},
                after={"operational_status": new_status})


def _load_activation_context(conn, campaign_id: str, requester_user_id: Any,
                             context: Optional[dict]) -> tuple:
    """Enforce ALL activation requirements and return (campaign, funding_view).

    Order: advertiser eligible (flag + account hold + approval) ⇒ 403; campaign
    owned ⇒ 404; not archived ⇒ 409; review-approved ⇒ 409; funded ⇒ 409;
    derived activation_ready ⇒ 409; budget/currency valid ⇒ 409. Approval alone or
    funding alone can never satisfy this — all inputs must hold.
    """
    elig = _svc.advertiser_eligibility(requester_user_id, context=context, conn=conn)
    if not elig.get("eligible"):
        raise AdvertisingError(
            f"Not eligible to activate campaigns ({elig.get('reason')}).",
            403, "ineligible")
    campaign = _svc.get_campaign(
        campaign_id, requester_user_id=requester_user_id, conn=conn)  # 404
    if campaign.get("status") == "archived":
        raise AdvertisingError(
            "Archived campaigns cannot be activated.", 409, "archived")
    if campaign.get("status") != "approved":
        raise AdvertisingError(
            f"Only review-approved campaigns can be activated (is "
            f"{campaign.get('status')}).", 409, "not_approved")
    funding = _fnd._get_funding_row(conn, campaign_id)
    fview = _fnd._funding_public(campaign, funding)
    if fview.get("funding_status") != "funded":
        raise AdvertisingError(
            f"Campaign must be funded before activation (is "
            f"{fview.get('funding_status')}).", 409, "not_funded")
    if not fview.get("activation_ready"):
        raise AdvertisingError(
            "Campaign is not activation-ready.", 409, "not_activation_ready")
    if funding is None or funding.get("budget_cents") is None \
            or int(funding.get("budget_cents") or 0) <= 0:
        raise AdvertisingError(
            "A valid budget is required before activation.", 409, "bad_budget")
    try:
        _fnd._norm_currency(funding.get("currency"))
    except AdvertisingError:
        raise AdvertisingError(
            "Campaign budget currency is invalid.", 409, "bad_currency")
    return campaign, fview


# --- reads ------------------------------------------------------------------
def get_operational_view(campaign_id: str, *,
                         requester_user_id: Optional[Any] = None,
                         conn=None) -> dict:
    """Operational readiness for one campaign. Ownership enforced when a requester
    is supplied (non-owner ⇒ 404); pass ``requester_user_id=None`` from admin."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        fview = _fnd._funding_public(campaign, _fnd._get_funding_row(conn, campaign_id))
        return _ops_public(campaign, fview, _get_ops_row(conn, campaign_id))
    finally:
        if owned:
            conn.close()


# --- advertiser verbs -------------------------------------------------------
def schedule_campaign(campaign_id: str, *, requester_user_id: Any,
                      start_at: Any = None, end_at: Any = None,
                      context: Optional[dict] = None,
                      actor: Optional[Any] = None) -> dict:
    """Advertiser schedules an owned, eligible campaign: -> ``scheduled``.

    Requires the full activation gate (approved + funded + activation_ready +
    eligible + not archived + valid budget). Optional UTC start/end window is
    normalized and range-validated. Scheduling reserves NO delivery — it only marks
    the campaign operationally ready to go active later.
    """
    _svc._require_enabled()
    window = _normalize_window(start_at, end_at)
    conn = db.connect()
    try:
        campaign, _fview = _load_activation_context(
            conn, campaign_id, requester_user_id, context)
        advertiser_uid = campaign.get("advertiser_user_id")
        _svc._begin(conn)
        row = _ensure_ops_row(conn, campaign_id, advertiser_uid)
        extra = dict(window) if window else {}
        _apply_transition(
            conn, campaign_id, advertiser_uid, row.get("operational_status"),
            "scheduled", actor=actor if actor is not None else requester_user_id,
            reason=None, action="campaign_op_schedule", extra_sets=extra or None)
        _svc._commit(conn)
        return get_operational_view(campaign_id, requester_user_id=requester_user_id)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def activate_campaign(campaign_id: str, *, requester_user_id: Any,
                      start_at: Any = None, end_at: Any = None,
                      context: Optional[dict] = None,
                      actor: Optional[Any] = None) -> dict:
    """Advertiser activates an owned, eligible campaign: -> ``active`` (immediate
    activation from ``inactive`` or a prior ``scheduled``).

    Requires the full activation gate. Optional UTC window may be supplied.
    ``active`` authorizes a FUTURE delivery worker; it does not deliver, spend, or
    move money here. ``activated_at`` is stamped on first activation only.
    """
    _svc._require_enabled()
    window = _normalize_window(start_at, end_at)
    conn = db.connect()
    try:
        campaign, _fview = _load_activation_context(
            conn, campaign_id, requester_user_id, context)
        advertiser_uid = campaign.get("advertiser_user_id")
        _svc._begin(conn)
        row = _ensure_ops_row(conn, campaign_id, advertiser_uid)
        extra = dict(window) if window else {}
        if not row.get("activated_at"):
            extra["activated_at"] = _svc._now_iso()
        _apply_transition(
            conn, campaign_id, advertiser_uid, row.get("operational_status"),
            "active", actor=actor if actor is not None else requester_user_id,
            reason=None, action="campaign_op_activate", extra_sets=extra or None)
        _svc._commit(conn)
        if _notify is not None:
            _notify.notify_campaign_activated(advertiser_uid, campaign_id)
        return get_operational_view(campaign_id, requester_user_id=requester_user_id)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def pause_campaign(campaign_id: str, *, requester_user_id: Any,
                   reason: Any = None, context: Optional[dict] = None,
                   actor: Optional[Any] = None) -> dict:
    """Advertiser pauses an owned campaign: ``scheduled``/``active`` -> ``paused``.

    No activation gate (pausing never grants capability). Ownership enforced (404).
    Pausing an ``inactive`` campaign is an illegal transition (409).
    """
    _svc._require_enabled()
    conn = db.connect()
    try:
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)  # 404
        advertiser_uid = campaign.get("advertiser_user_id")
        _svc._begin(conn)
        row = _ensure_ops_row(conn, campaign_id, advertiser_uid)
        _apply_transition(
            conn, campaign_id, advertiser_uid, row.get("operational_status"),
            "paused", actor=actor if actor is not None else requester_user_id,
            reason=_clean_reason(reason), action="campaign_op_pause",
            extra_sets={"paused_at": _svc._now_iso()})
        _svc._commit(conn)
        if _notify is not None:
            _notify.notify_campaign_paused(
                advertiser_uid, campaign_id, reason=_clean_reason(reason))
        return get_operational_view(campaign_id, requester_user_id=requester_user_id)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def resume_campaign(campaign_id: str, *, requester_user_id: Any,
                    context: Optional[dict] = None,
                    actor: Optional[Any] = None) -> dict:
    """Advertiser resumes an owned, still-eligible campaign: ``paused`` -> ``active``.

    Because resuming re-enters ``active``, it re-runs the FULL activation gate — a
    suspended advertiser or a campaign whose funding was released cannot resume.
    """
    _svc._require_enabled()
    conn = db.connect()
    try:
        campaign, _fview = _load_activation_context(
            conn, campaign_id, requester_user_id, context)
        advertiser_uid = campaign.get("advertiser_user_id")
        _svc._begin(conn)
        row = _ensure_ops_row(conn, campaign_id, advertiser_uid)
        cur = row.get("operational_status")
        if cur != "paused":
            raise AdvertisingError(
                f"Only paused campaigns can be resumed (is {cur}).",
                409, "not_paused")
        extra = {} if row.get("activated_at") else {"activated_at": _svc._now_iso()}
        _apply_transition(
            conn, campaign_id, advertiser_uid, cur, "active",
            actor=actor if actor is not None else requester_user_id,
            reason=None, action="campaign_op_resume", extra_sets=extra or None)
        _svc._commit(conn)
        if _notify is not None:
            _notify.notify_campaign_activated(advertiser_uid, campaign_id)
        return get_operational_view(campaign_id, requester_user_id=requester_user_id)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def cancel_campaign(campaign_id: str, *, requester_user_id: Any,
                    reason: Any = None, context: Optional[dict] = None,
                    actor: Optional[Any] = None) -> dict:
    """Advertiser cancels an owned campaign: ``scheduled``/``paused``/``active`` ->
    ``cancelled``.

    Ownership enforced (404). Cancellation is terminal for the operational
    lifecycle. It deliberately does NOT release reserved funds — releasing budget
    stays an explicit, separate call into the funding service so the two lifecycles
    never entangle.
    """
    _svc._require_enabled()
    conn = db.connect()
    try:
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)  # 404
        advertiser_uid = campaign.get("advertiser_user_id")
        _svc._begin(conn)
        row = _ensure_ops_row(conn, campaign_id, advertiser_uid)
        _apply_transition(
            conn, campaign_id, advertiser_uid, row.get("operational_status"),
            "cancelled", actor=actor if actor is not None else requester_user_id,
            reason=_clean_reason(reason), action="campaign_op_cancel",
            extra_sets={"cancelled_at": _svc._now_iso()})
        _svc._commit(conn)
        return get_operational_view(campaign_id, requester_user_id=requester_user_id)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


# --- admin verbs (trusted callers; RBAC enforced at the route) --------------
def _admin_transition(campaign_id: str, new_status: str, *, actor: Any,
                      reason: Any, action: str,
                      stamp_col: Optional[str]) -> dict:
    _svc._require_enabled()
    conn = db.connect()
    try:
        campaign = _svc.get_campaign(campaign_id, requester_user_id=None, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        advertiser_uid = campaign.get("advertiser_user_id")
        _svc._begin(conn)
        row = _ensure_ops_row(conn, campaign_id, advertiser_uid)
        extra = {stamp_col: _svc._now_iso()} if stamp_col else {}
        _apply_transition(
            conn, campaign_id, advertiser_uid, row.get("operational_status"),
            new_status, actor=actor, reason=_clean_reason(reason),
            action=action, extra_sets=extra or None)
        _svc._commit(conn)
        if _notify is not None and new_status == "paused":
            _notify.notify_campaign_paused(
                advertiser_uid, campaign_id, reason=_clean_reason(reason))
        return admin_get_operational(campaign_id)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def admin_pause_campaign(campaign_id: str, *, actor: Any, reason: Any = None) -> dict:
    """Admin pause (intervention): ``scheduled``/``active`` -> ``paused``."""
    return _admin_transition(
        campaign_id, "paused", actor=actor, reason=reason,
        action="campaign_op_admin_pause", stamp_col="paused_at")


def admin_cancel_campaign(campaign_id: str, *, actor: Any, reason: Any = None) -> dict:
    """Admin cancel (intervention): -> ``cancelled``. Does not release funds."""
    return _admin_transition(
        campaign_id, "cancelled", actor=actor, reason=reason,
        action="campaign_op_admin_cancel", stamp_col="cancelled_at")


def admin_complete_campaign(campaign_id: str, *, actor: Any, reason: Any = None) -> dict:
    """Authorized server/admin path to mark a run finished: ``active`` ->
    ``completed``. Completion is not an advertiser verb."""
    return _admin_transition(
        campaign_id, "completed", actor=actor, reason=reason,
        action="campaign_op_admin_complete", stamp_col="completed_at")


# --- admin reads ------------------------------------------------------------
def admin_get_operational(campaign_id: str, *, conn=None) -> dict:
    """Admin view: the review, funding, and operational states together, plus the
    full funding projection. No ownership scoping (route enforces owner RBAC)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        campaign = _svc.get_campaign(campaign_id, requester_user_id=None, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        fview = _fnd._funding_public(campaign, _fnd._get_funding_row(conn, campaign_id))
        view = _ops_public(campaign, fview, _get_ops_row(conn, campaign_id))
        view["funding"] = fview
        return view
    finally:
        if owned:
            conn.close()


def admin_list_operations(*, operational_status: Optional[str] = None,
                          limit: int = 200, conn=None) -> list:
    """Admin cross-owner operational listing with an optional status filter."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if operational_status is not None:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_campaign_operations "
                "WHERE operational_status = ? ORDER BY updated_at DESC LIMIT ?",
                (operational_status, int(limit)))
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_campaign_operations "
                "ORDER BY updated_at DESC LIMIT ?", (int(limit),))
        return [_svc._row_to_dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()
