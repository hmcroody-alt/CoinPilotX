"""Business OS — Advertising vertical, slice-6 ad-set service (flag-gated).

An ad set is the campaign's child in the canonical hierarchy
``advertiser -> campaign -> ad set -> creative``. It owns a placement selection
and a governed audience spec, and carries its OWN lifecycle status
(``draft|submitted|approved|rejected|paused|archived``) that is deliberately
SEPARATE from the campaign review status, funding status, and operational status.

Hard invariants enforced here:

  * An ad set NEVER becomes deliverable just because its parent campaign is
    active — this service only manages the object's own review lifecycle. Whether
    the whole hierarchy is delivery-ready is DERIVED live elsewhere
    (``readiness.hierarchy_readiness``) and is never a stored boolean.
  * Ownership is derived from the authenticated user, never from the request
    body; a non-owner gets 404 (existence is not leaked).
  * The parent campaign must exist, be owned by the same advertiser, and not be
    archived when creating or submitting a child.
  * Advertisers may only edit DRAFT or REJECTED ad sets, through a strict field
    allowlist, and can never assign a review state directly — submit/approve/
    reject go through the gated verbs.

Nothing here delivers, auctions, paces, targets a real user, or moves money.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising import targeting as _tgt
from services.business_os.advertising.service import AdvertisingError


# --- lifecycle vocabulary ---------------------------------------------------
AD_SET_STATUSES = {"draft", "submitted", "approved", "rejected", "paused", "archived"}
NON_ARCHIVED_AD_SET_STATUSES = {"draft", "submitted", "approved", "rejected", "paused"}
# Owner + admin reachable transitions. Review moves (submitted->approved|rejected)
# are admin-only and go through admin_review_ad_set; the rest are owner verbs.
AD_SET_TRANSITIONS = {
    "draft": {"submitted", "archived"},
    "submitted": {"approved", "rejected", "draft", "archived"},
    "approved": {"paused", "archived"},
    "rejected": {"draft", "archived"},
    "paused": {"approved", "archived"},
    "archived": {"draft"},
}

NAME_MAX = 120
AUDIENCE_JSON_MAX = 8000
EDITABLE_FIELDS = {
    "name", "placements", "audience", "schedule_start_at", "schedule_end_at",
    "budget_allocation",
}


# --- projection -------------------------------------------------------------
def _loads(value):
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _ad_set_public(row: dict) -> dict:
    """Client-safe ad-set projection. JSON columns are parsed back to objects and
    the placement/audience validity is surfaced as SEPARATE derived signals (never
    merged into a single readiness boolean)."""
    if row is None:
        return None
    placements = _loads(row.get("placements_json")) or []
    audience = _loads(row.get("audience_json")) or {}
    return {
        "ad_set_id": row.get("ad_set_id"),
        "campaign_id": row.get("campaign_id"),
        "advertiser_user_id": row.get("advertiser_user_id"),
        "name": row.get("name"),
        "status": row.get("status"),
        "placements": placements,
        "audience": audience,
        "schedule_start_at": row.get("schedule_start_at"),
        "schedule_end_at": row.get("schedule_end_at"),
        "budget_allocation": _loads(row.get("budget_allocation_json")),
        "review_reason": row.get("review_reason"),
        "version": row.get("version"),
        "archived": row.get("status") == "archived",
        "placement_valid": _tgt.placements_are_valid(placements),
        "audience_valid": _tgt.audience_is_valid(audience),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# --- internal helpers -------------------------------------------------------
def _get_row(conn, ad_set_id: str, *, requester_user_id: Optional[Any]) -> Optional[dict]:
    row = _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_sets WHERE ad_set_id = ?", (ad_set_id,)
    ).fetchone())
    if row is None:
        if requester_user_id is not None:
            raise AdvertisingError("Ad set not found.", 404, "not_found")
        return None
    if requester_user_id is not None and \
            row.get("advertiser_user_id") != _svc._sid(requester_user_id):
        raise AdvertisingError("Ad set not found.", 404, "not_found")
    return row


def _require_owned_campaign(conn, campaign_id: str, requester_user_id: Any) -> dict:
    """Campaign must exist, be owned by the requester, and not be archived."""
    campaign = _svc.get_campaign(campaign_id, requester_user_id=requester_user_id, conn=conn)
    if campaign is None:
        raise AdvertisingError("Campaign not found.", 404, "not_found")
    return campaign


def _validate_schedule(fields: dict) -> dict:
    """Validate an optional schedule override. Reuses operations' UTC parser so the
    format is consistent across slices. Returns {schedule_start_at, schedule_end_at}
    normalized (ISO-Z) or empty."""
    start_raw = fields.get("schedule_start_at")
    end_raw = fields.get("schedule_end_at")
    if start_raw in (None, "") and end_raw in (None, ""):
        return {"schedule_start_at": None, "schedule_end_at": None}
    from services.business_os.advertising import operations as _ops
    start_dt = _ops._parse_utc_dt(start_raw, "schedule_start_at") if start_raw not in (None, "") else None
    end_dt = _ops._parse_utc_dt(end_raw, "schedule_end_at") if end_raw not in (None, "") else None
    if start_dt is not None and end_dt is not None and end_dt <= start_dt:
        raise AdvertisingError(
            "schedule_end_at must be after schedule_start_at.", 400, "bad_window")
    return {
        "schedule_start_at": _ops._fmt(start_dt) if start_dt else None,
        "schedule_end_at": _ops._fmt(end_dt) if end_dt else None,
    }


def _validate_budget_allocation(value: Any) -> Optional[str]:
    """Optional allocation METADATA only. This is not money and moves nothing; it
    is a small bounded JSON blob the future pacing layer may read. We only check
    it is a JSON-serializable object of bounded size."""
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise AdvertisingError(
            "budget_allocation must be an object.", 400, "bad_allocation")
    try:
        blob = json.dumps(value, sort_keys=True)
    except Exception:
        raise AdvertisingError(
            "budget_allocation is not serializable.", 400, "bad_allocation")
    if len(blob) > 2000:
        raise AdvertisingError("budget_allocation too large.", 400, "bad_allocation")
    return blob


def _validate_name(name: Any) -> str:
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise AdvertisingError("Ad set name is required.", 400, "name_required")
    if len(name) > NAME_MAX:
        raise AdvertisingError(
            f"Ad set name exceeds {NAME_MAX} characters.", 400, "name_too_long")
    return name


# --- create / read / list ---------------------------------------------------
def create_ad_set(owner_user_id: Any, campaign_id: str, payload: dict, *,
                  context: Optional[dict] = None, conn=None) -> dict:
    """Create a DRAFT ad set under an owned, non-archived campaign.

    Enforces eligibility + campaign ownership, validates the placement selection
    and governed audience spec, and persists a versioned canonical structure.
    Status is always forced to 'draft'.
    """
    _svc._require_enabled()
    if not isinstance(payload, dict):
        raise AdvertisingError("Body must be an object.", 400, "bad_body")
    owned = conn is None
    if owned:
        conn = db.connect()
    uid = _svc._sid(owner_user_id)
    try:
        elig = _svc.advertiser_eligibility(owner_user_id, context=context, conn=conn)
        if not elig.get("eligible"):
            raise AdvertisingError(
                f"Not eligible to create ad sets ({elig.get('reason')}).",
                403, "ineligible")
        campaign = _require_owned_campaign(conn, campaign_id, owner_user_id)
        if campaign.get("status") == "archived":
            raise AdvertisingError(
                "Cannot add an ad set to an archived campaign.", 409, "parent_archived")

        name = _validate_name(payload.get("name"))
        placements = _tgt.validate_placements(payload.get("placements"))
        audience = _tgt.validate_audience(payload.get("audience"))
        sched = _validate_schedule(payload)
        allocation_blob = _validate_budget_allocation(payload.get("budget_allocation"))

        _svc._begin(conn)
        asid = _svc._uid()
        now = _svc._now_iso()
        conn.execute(
            "INSERT INTO business_os_ad_sets "
            "(ad_set_id, campaign_id, advertiser_user_id, name, status, "
            "placements_json, audience_json, schedule_start_at, schedule_end_at, "
            "budget_allocation_json, review_reason, version, archived_at, "
            "created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, NULL, 1, NULL, ?, ?, ?)",
            (
                asid, campaign_id, uid, name,
                json.dumps(placements), json.dumps(audience, sort_keys=True),
                sched["schedule_start_at"], sched["schedule_end_at"],
                allocation_blob, uid, now, now,
            ),
        )
        _svc._audit(conn, campaign_id=campaign_id, advertiser_user_id=uid,
                    action="ad_set_create", actor=uid,
                    after={"ad_set_id": asid, "status": "draft",
                           "placements": placements})
        _svc._commit(conn)
        return _ad_set_public(_get_row(conn, asid, requester_user_id=owner_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def get_ad_set(ad_set_id: str, *, requester_user_id: Optional[Any] = None,
               conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, ad_set_id, requester_user_id=requester_user_id)
        return _ad_set_public(row) if row else None
    finally:
        if owned:
            conn.close()


def list_ad_sets(owner_user_id: Any, *, campaign_id: Optional[str] = None,
                 conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if campaign_id is not None:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_sets "
                "WHERE advertiser_user_id = ? AND campaign_id = ? "
                "ORDER BY created_at DESC, ad_set_id DESC",
                (_svc._sid(owner_user_id), campaign_id))
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_sets WHERE advertiser_user_id = ? "
                "ORDER BY created_at DESC, ad_set_id DESC",
                (_svc._sid(owner_user_id),))
        return [_ad_set_public(_svc._row_to_dict(r)) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


# --- draft edit -------------------------------------------------------------
def update_ad_set(ad_set_id: str, *, requester_user_id: Any, fields: dict,
                  conn=None) -> dict:
    """Owner-scoped edit of a DRAFT or REJECTED ad set through a strict allowlist.

    Any unknown key is rejected (``unknown_field``). Review state is never
    editable here. Editing an approved/submitted/paused/archived ad set is 409
    ``not_editable``. Each successful edit bumps ``version``.
    """
    _svc._require_enabled()
    if not isinstance(fields, dict):
        raise AdvertisingError("Body must be an object.", 400, "bad_body")
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise AdvertisingError(
            f"Unknown field(s): {', '.join(sorted(unknown))}.", 400, "unknown_field")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, ad_set_id, requester_user_id=requester_user_id)
        if row.get("status") not in {"draft", "rejected"}:
            raise AdvertisingError(
                "Only draft or rejected ad sets can be edited.", 409, "not_editable")

        updates: dict = {}
        if "name" in fields:
            updates["name"] = _validate_name(fields["name"])
        if "placements" in fields:
            updates["placements_json"] = json.dumps(
                _tgt.validate_placements(fields["placements"]))
        if "audience" in fields:
            updates["audience_json"] = json.dumps(
                _tgt.validate_audience(fields["audience"]), sort_keys=True)
        if "schedule_start_at" in fields or "schedule_end_at" in fields:
            merged = {
                "schedule_start_at": fields.get(
                    "schedule_start_at", row.get("schedule_start_at")),
                "schedule_end_at": fields.get(
                    "schedule_end_at", row.get("schedule_end_at")),
            }
            sched = _validate_schedule(merged)
            updates["schedule_start_at"] = sched["schedule_start_at"]
            updates["schedule_end_at"] = sched["schedule_end_at"]
        if "budget_allocation" in fields:
            updates["budget_allocation_json"] = _validate_budget_allocation(
                fields["budget_allocation"])
        if not updates:
            return _ad_set_public(row)

        _svc._begin(conn)
        set_clause = ", ".join(f"{k} = ?" for k in updates)  # keys are fixed literals
        params = list(updates.values()) + [_svc._now_iso(), ad_set_id]
        conn.execute(
            f"UPDATE business_os_ad_sets SET {set_clause}, version = version + 1, "
            "updated_at = ? WHERE ad_set_id = ?",
            tuple(params))
        _svc._audit(conn, campaign_id=row.get("campaign_id"),
                    advertiser_user_id=row.get("advertiser_user_id"),
                    action="ad_set_update", actor=requester_user_id,
                    before={"fields": sorted(updates)},
                    after={"ad_set_id": ad_set_id})
        _svc._commit(conn)
        return _ad_set_public(_get_row(conn, ad_set_id, requester_user_id=requester_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- lifecycle transitions --------------------------------------------------
def _transition(conn, row: dict, new_status: str, *, actor, action: str,
                reason=None, extra_sets: Optional[dict] = None) -> None:
    cur_status = row.get("status")
    if new_status not in AD_SET_TRANSITIONS.get(cur_status, set()):
        raise AdvertisingError(
            f"Illegal ad-set transition {cur_status} -> {new_status}.",
            409, "illegal_transition")
    sets = {"status": new_status}
    if extra_sets:
        sets.update(extra_sets)
    sets["updated_at"] = _svc._now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in sets)  # fixed-literal keys only
    conn.execute(
        f"UPDATE business_os_ad_sets SET {set_clause}, version = version + 1 "
        "WHERE ad_set_id = ?",
        tuple(list(sets.values()) + [row.get("ad_set_id")]))
    _svc._audit(conn, campaign_id=row.get("campaign_id"),
                advertiser_user_id=row.get("advertiser_user_id"),
                action=action, actor=actor, reason=reason,
                before={"status": cur_status},
                after={"ad_set_id": row.get("ad_set_id"), "status": new_status})


def _owner_transition(ad_set_id, requester_user_id, new_status, action, *,
                      require_parent_active_ok=True, conn=None) -> dict:
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, ad_set_id, requester_user_id=requester_user_id)
        # Submitting a child requires a live (non-archived) parent campaign.
        if new_status == "submitted":
            campaign = _svc.get_campaign(
                row.get("campaign_id"), requester_user_id=requester_user_id, conn=conn)
            if campaign is None or campaign.get("status") == "archived":
                raise AdvertisingError(
                    "Parent campaign is archived; cannot submit.",
                    409, "parent_archived")
        _svc._begin(conn)
        extra = {}
        if new_status in {"draft", "submitted"}:
            extra["review_reason"] = None
        if new_status == "archived":
            extra["archived_at"] = _svc._now_iso()
        _transition(conn, row, new_status, actor=requester_user_id,
                    action=action, extra_sets=extra)
        _svc._commit(conn)
        return _ad_set_public(_get_row(conn, ad_set_id, requester_user_id=requester_user_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def submit_ad_set(ad_set_id, *, requester_user_id, conn=None) -> dict:
    return _owner_transition(ad_set_id, requester_user_id, "submitted",
                             "ad_set_submit", conn=conn)


def withdraw_ad_set(ad_set_id, *, requester_user_id, conn=None) -> dict:
    return _owner_transition(ad_set_id, requester_user_id, "draft",
                             "ad_set_withdraw", conn=conn)


def pause_ad_set(ad_set_id, *, requester_user_id, conn=None) -> dict:
    return _owner_transition(ad_set_id, requester_user_id, "paused",
                             "ad_set_pause", conn=conn)


def resume_ad_set(ad_set_id, *, requester_user_id, conn=None) -> dict:
    return _owner_transition(ad_set_id, requester_user_id, "approved",
                             "ad_set_resume", conn=conn)


def archive_ad_set(ad_set_id, *, requester_user_id, conn=None) -> dict:
    return _owner_transition(ad_set_id, requester_user_id, "archived",
                             "ad_set_archive", conn=conn)


def restore_ad_set(ad_set_id, *, requester_user_id, conn=None) -> dict:
    return _owner_transition(ad_set_id, requester_user_id, "draft",
                             "ad_set_restore", conn=conn)


# --- admin review -----------------------------------------------------------
def admin_list_ad_sets(*, status: Optional[str] = None, conn=None) -> list:
    """Trusted admin read across all owners. Optional status filter."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if status is not None:
            if status not in AD_SET_STATUSES:
                raise AdvertisingError(f"Unknown status: {status!r}.", 400, "bad_status")
            cur = conn.execute(
                "SELECT * FROM business_os_ad_sets WHERE status = ? "
                "ORDER BY created_at DESC, ad_set_id DESC", (status,))
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_sets "
                "ORDER BY created_at DESC, ad_set_id DESC")
        return [_ad_set_public(_svc._row_to_dict(r)) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def admin_get_ad_set(ad_set_id: str, *, conn=None) -> dict:
    """Admin read of one ad set plus its parent campaign context."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, ad_set_id, requester_user_id=None)
        if row is None:
            raise AdvertisingError("Ad set not found.", 404, "not_found")
        view = _ad_set_public(row)
        campaign = _svc.get_campaign(row.get("campaign_id"), conn=conn)
        view["campaign"] = None if campaign is None else {
            "campaign_id": campaign.get("campaign_id"),
            "name": campaign.get("name"),
            "status": campaign.get("status"),
            "advertiser_user_id": campaign.get("advertiser_user_id"),
        }
        return view
    finally:
        if owned:
            conn.close()


def admin_review_ad_set(ad_set_id: str, decision: str, *, actor: Any,
                        reason: Optional[str] = None, conn=None) -> dict:
    """Admin approves or rejects a SUBMITTED ad set. Records the acting admin,
    previous/new state, reason, and version in the audit trail. Approval does NOT
    publish or deliver anything."""
    _svc._require_enabled()
    decision = (decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise AdvertisingError("Decision must be approve or reject.", 400, "bad_decision")
    if decision == "reject":
        reason = (reason or "").strip()
        if not reason:
            raise AdvertisingError("A rejection reason is required.", 400, "reason_required")
        reason = reason[:500]
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_row(conn, ad_set_id, requester_user_id=None)
        if row is None:
            raise AdvertisingError("Ad set not found.", 404, "not_found")
        if row.get("status") != "submitted":
            raise AdvertisingError(
                "Only a submitted ad set can be reviewed.", 409, "not_submitted")
        new_status = "approved" if decision == "approve" else "rejected"
        _svc._begin(conn)
        _transition(conn, row, new_status, actor=actor,
                    action=f"ad_set_{new_status}", reason=reason,
                    extra_sets={"review_reason": reason if decision == "reject" else None})
        _svc._commit(conn)
        return _ad_set_public(_get_row(conn, ad_set_id, requester_user_id=None))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()
