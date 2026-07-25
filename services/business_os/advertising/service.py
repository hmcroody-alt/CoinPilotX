"""Business OS — Advertising vertical, slice-1 service (flag-gated, canonical).

The smallest end-to-end advertiser capability: an eligible advertiser can create
and own a *draft* campaign, move it between draft/archived, and admins can see
everything. Nothing here spends money, bills, delivers, targets, or reports — the
legacy ``pulse_ads_service`` remains the only delivery/auction path and is never
touched.

Server-authoritative and additive: reads/writes only the ``business_os_ad_*``
tables. Every state-changing and eligibility entrypoint is gated behind the
``BUSINESS_OS_ADVERTISING`` flag; with the flag off the module raises
``AdvertisingError`` from those entrypoints and touches nothing, so the slice is
fully reversible.

Eligibility composes THREE separate inputs (never merged — see §8 of the
shared-foundation checkpoint):

    1. Account hold / suspension   -> ``facade.account_hold`` (overrides all)
    2. Advertiser approval state   -> ``business_os_ad_advertisers.status``
    3. Feature rollout             -> ``BUSINESS_OS_ADVERTISING`` flag

Commercial entitlement quota (``advertising.campaign.create`` in the entitlement
catalog) and usage allowance are deliberately NOT consumed here: creating a draft
is not a billable/metered action in slice 1. That composition is a later slice.

Precedence for eligibility: account hold beats approval; approval is required;
rollout flag gates the whole surface.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.advertising import schema as _schema

try:  # facade is the shared suspension authority; import defensively.
    from services.business_os.entitlements import facade as _facade
except Exception:  # pragma: no cover - facade always present in-tree
    _facade = None

try:  # canonical notification adapters; import defensively (never a precondition).
    from services.business_os.advertising import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- flag -------------------------------------------------------------------
FLAG_ENV = "BUSINESS_OS_ADVERTISING"


def is_enabled() -> bool:
    """True only when the rollout flag is explicitly on.

    Accepts ``1/true/on/yes/enabled`` (case-insensitive). Anything else — including
    unset — is off, so the default posture is inert.
    """
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


# --- vocabularies -----------------------------------------------------------
ADVERTISER_STATUSES = {"pending", "approved", "rejected", "suspended"}
CAMPAIGN_OBJECTIVES = {"awareness", "traffic", "engagement", "leads", "conversions"}
# Slice-3 lifecycle: draft -> submitted -> {approved | rejected}, plus archive.
#   * "approved" is REVIEW-approved only — NOT funded, live, or delivering. Funding
#     readiness and delivery activation are separate concepts handled in later
#     slices and are deliberately absent here.
CAMPAIGN_STATUSES = {"draft", "submitted", "approved", "rejected", "archived"}
# Any state that is not "archived" may be archived by the owner.
NON_ARCHIVED_STATUSES = {"draft", "submitted", "approved", "rejected"}
# The only lifecycle targets reachable through the generic archive/restore
# primitive (``transition_campaign``). Review transitions are NOT reachable this
# way — they must go through the gated submit/withdraw/reopen/review functions so
# eligibility, validation, and admin authority are always enforced.
ARCHIVE_RESTORE_STATES = {"draft", "archived"}
ALLOWED_TRANSITIONS = {
    "draft": {"submitted", "archived"},
    "submitted": {"approved", "rejected", "draft", "archived"},
    "approved": {"archived"},
    "rejected": {"draft", "archived"},
    "archived": {"draft"},
}

ENTITLEMENT_KEY = "advertising.campaign.create"  # catalog key, reserved for a later (metered) slice

NAME_MAX = 120
URL_MAX = 2048


class AdvertisingError(ValueError):
    """Raised when an advertising operation is rejected before any state change.

    Carries an ``http_status`` so a future bot.py route can map it directly
    (403 for ineligibility, 404 for missing/again-not-owned, 409 for illegal
    transition, 400 for validation, 503 when the flag is off).
    """

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


# --- time / db helpers ------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uid() -> str:
    return uuid.uuid4().hex


def _sid(user_id: Any) -> str:
    return str(user_id)


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _begin(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        try:
            conn.isolation_level = None
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")


def _commit(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        conn.execute("COMMIT")
    else:
        conn.commit()


def _rollback(conn) -> None:
    try:
        if db.ENGINE_NAME == "sqlite":
            conn.execute("ROLLBACK")
        else:
            conn.rollback()
    except Exception:
        pass


def ensure_schema(conn=None) -> None:
    """Create advertising tables. Idempotent; delegates to schema module."""
    _schema.ensure_schema(conn)


def _require_enabled() -> None:
    if not is_enabled():
        raise AdvertisingError(
            "Advertising is not enabled in this environment.",
            http_status=503,
            code="disabled",
        )


def _audit(conn, *, campaign_id, advertiser_user_id, action, actor,
           reason=None, before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO business_os_ad_audit "
        "(campaign_id, advertiser_user_id, action, actor, reason, "
        "before_json, after_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            advertiser_user_id,
            action,
            None if actor is None else str(actor),
            reason,
            None if before is None else json.dumps(before, sort_keys=True),
            None if after is None else json.dumps(after, sort_keys=True),
            _now_iso(),
        ),
    )


def _apply_transition(conn, campaign: dict, new_status: str, *, actor,
                      reason=None, action: str, extra_sets: Optional[dict] = None) -> None:
    """Validate + persist a lifecycle status change and write its audit row.

    Enforces ``ALLOWED_TRANSITIONS`` centrally (illegal ⇒ 409). ``extra_sets``
    carries additional columns to update alongside ``status`` (e.g. clearing or
    setting ``review_reason``); its keys come only from fixed literals in the
    callers, never from client input, so the built SQL has no injection surface.
    Must be called inside an open transaction.
    """
    cur_status = campaign.get("status")
    allowed = ALLOWED_TRANSITIONS.get(cur_status, set())
    if new_status not in allowed:
        raise AdvertisingError(
            f"Illegal transition {cur_status} -> {new_status}.",
            409, "illegal_transition")
    sets = {"status": new_status}
    if extra_sets:
        sets.update(extra_sets)
    sets["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(
        f"UPDATE business_os_ad_campaigns SET {set_clause} WHERE campaign_id = ?",
        tuple(list(sets.values()) + [campaign.get("campaign_id")]),
    )
    _audit(conn, campaign_id=campaign.get("campaign_id"),
           advertiser_user_id=campaign.get("advertiser_user_id"),
           action=action, actor=actor, reason=reason,
           before={"status": cur_status}, after={"status": new_status})


# --- advertiser approval (input #2) -----------------------------------------
def upsert_advertiser(user_id: Any, *, display_name: Optional[str] = None,
                      notes: Optional[str] = None, conn=None) -> dict:
    """Create the advertiser record for ``user_id`` if absent (status 'pending').

    Idempotent: an existing record is returned unchanged except that a supplied
    ``display_name``/``notes`` refreshes those descriptive fields. Approval is a
    separate admin action (``set_advertiser_status``) — self-registration never
    grants approval.
    """
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    uid = _sid(user_id)
    try:
        _begin(conn)
        existing = _row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_advertisers WHERE user_id = ?", (uid,)
        ).fetchone())
        now = _now_iso()
        if existing is None:
            conn.execute(
                "INSERT INTO business_os_ad_advertisers "
                "(user_id, status, display_name, notes, approved_by, approved_at, "
                "metadata_json, created_at, updated_at) "
                "VALUES (?, 'pending', ?, ?, NULL, NULL, NULL, ?, ?)",
                (uid, display_name, notes, now, now),
            )
            _audit(conn, campaign_id=None, advertiser_user_id=uid,
                   action="advertiser_register", actor=user_id,
                   after={"status": "pending"})
        elif display_name is not None or notes is not None:
            conn.execute(
                "UPDATE business_os_ad_advertisers "
                "SET display_name = COALESCE(?, display_name), "
                "notes = COALESCE(?, notes), updated_at = ? WHERE user_id = ?",
                (display_name, notes, now, uid),
            )
        _commit(conn)
        return get_advertiser(uid, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def get_advertiser(user_id: Any, *, conn=None) -> Optional[dict]:
    """Return the advertiser record, or None. Read-only; no flag requirement."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return _row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_advertisers WHERE user_id = ?",
            (_sid(user_id),)
        ).fetchone())
    finally:
        if owned:
            conn.close()


def set_advertiser_status(user_id: Any, status: str, *, actor: Any,
                          reason: Optional[str] = None, conn=None) -> dict:
    """Admin-only approval transition. Sets the advertiser approval state.

    This is the *role/administrative* authority acting on the *advertiser
    approval* input — distinct from any commercial grant. Records approver +
    timestamp on approval.
    """
    _require_enabled()
    status = (status or "").strip().lower()
    if status not in ADVERTISER_STATUSES:
        raise AdvertisingError(
            f"Unknown advertiser status: {status!r}", http_status=400, code="bad_status")
    owned = conn is None
    if owned:
        conn = db.connect()
    uid = _sid(user_id)
    try:
        _begin(conn)
        before = _row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_advertisers WHERE user_id = ?", (uid,)
        ).fetchone())
        if before is None:
            raise AdvertisingError(
                "No advertiser record to update.", http_status=404, code="not_found")
        now = _now_iso()
        approved_by = str(actor) if status == "approved" else before.get("approved_by")
        approved_at = now if status == "approved" else before.get("approved_at")
        conn.execute(
            "UPDATE business_os_ad_advertisers "
            "SET status = ?, approved_by = ?, approved_at = ?, updated_at = ? "
            "WHERE user_id = ?",
            (status, approved_by, approved_at, now, uid),
        )
        _audit(conn, campaign_id=None, advertiser_user_id=uid,
               action="advertiser_status", actor=actor, reason=reason,
               before={"status": before.get("status")}, after={"status": status})
        _commit(conn)
        return get_advertiser(uid, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- eligibility (composes inputs 1+2+3) ------------------------------------
def advertiser_eligibility(user_id: Any, *, context: Optional[dict] = None,
                           conn=None) -> dict:
    """Explainable eligibility to act as an advertiser.

    Composes, WITHOUT merging, the three separate inputs and returns why:

        {eligible, reason, flag_enabled, account_hold, advertiser_status}

    Precedence: rollout flag → account hold (overrides) → advertiser approved.
    Fails safe (not eligible) on any missing input.
    """
    flag_enabled = is_enabled()
    result = {
        "eligible": False,
        "reason": None,
        "flag_enabled": flag_enabled,
        "account_hold": None,
        "advertiser_status": None,
    }
    if not flag_enabled:
        result["reason"] = "advertising_disabled"
        return result

    # Input #1: account hold / suspension — shared authority, overrides all.
    hold = None
    if _facade is not None:
        try:
            hold = _facade.account_hold(user_id, context)
        except Exception:
            hold = None
    result["account_hold"] = hold
    if hold is not None and hold.get("on_hold"):
        result["reason"] = hold.get("reason") or "account_hold"
        return result

    # Input #2: advertiser approval state.
    advertiser = get_advertiser(user_id, conn=conn)
    result["advertiser_status"] = None if advertiser is None else advertiser.get("status")
    if advertiser is None:
        result["reason"] = "advertiser_not_registered"
        return result
    if advertiser.get("status") != "approved":
        result["reason"] = f"advertiser_{advertiser.get('status')}"
        return result

    result["eligible"] = True
    result["reason"] = "ok"
    return result


# --- validation -------------------------------------------------------------
def _validate_draft(name, objective, destination_url) -> tuple:
    name = (name or "").strip()
    if not name:
        raise AdvertisingError("Campaign name is required.", 400, "name_required")
    if len(name) > NAME_MAX:
        raise AdvertisingError(
            f"Campaign name exceeds {NAME_MAX} characters.", 400, "name_too_long")
    objective = (objective or "").strip().lower()
    if objective not in CAMPAIGN_OBJECTIVES:
        raise AdvertisingError(
            f"Unknown objective: {objective!r}.", 400, "bad_objective")
    url = None
    if destination_url is not None and str(destination_url).strip():
        url = str(destination_url).strip()
        if len(url) > URL_MAX:
            raise AdvertisingError(
                f"Destination URL exceeds {URL_MAX} characters.", 400, "url_too_long")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise AdvertisingError(
                "Destination URL must start with http:// or https://.",
                400, "bad_url")
    return name, objective, url


# --- campaign draft creation + lifecycle ------------------------------------
def create_campaign_draft(owner_user_id: Any, *, name: str, objective: str,
                          destination_url: Optional[str] = None,
                          created_by: Optional[Any] = None,
                          metadata: Optional[dict] = None,
                          context: Optional[dict] = None, conn=None) -> dict:
    """Create and persist a draft campaign owned by ``owner_user_id``.

    Enforces eligibility (flag + account hold + advertiser approval) and full
    server-side validation. Status is always forced to 'draft' regardless of any
    caller input — slice 1 cannot create anything else.
    """
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    uid = _sid(owner_user_id)
    try:
        elig = advertiser_eligibility(owner_user_id, context=context, conn=conn)
        if not elig.get("eligible"):
            raise AdvertisingError(
                f"Not eligible to create campaigns ({elig.get('reason')}).",
                http_status=403, code="ineligible")
        name, objective, url = _validate_draft(name, objective, destination_url)

        _begin(conn)
        cid = _uid()
        now = _now_iso()
        actor = uid if created_by is None else str(created_by)
        conn.execute(
            "INSERT INTO business_os_ad_campaigns "
            "(campaign_id, advertiser_user_id, name, objective, status, "
            "destination_url, created_by, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)",
            (
                cid, uid, name, objective, url, actor,
                None if metadata is None else json.dumps(metadata, sort_keys=True),
                now, now,
            ),
        )
        _audit(conn, campaign_id=cid, advertiser_user_id=uid,
               action="campaign_create", actor=actor,
               after={"name": name, "objective": objective, "status": "draft"})
        _commit(conn)
        return get_campaign(cid, requester_user_id=owner_user_id, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


EDITABLE_FIELDS = {"name", "objective", "destination_url"}


def update_campaign_draft(campaign_id: str, *, requester_user_id: Any,
                          fields: dict, actor: Optional[Any] = None,
                          reason: Optional[str] = None, conn=None) -> dict:
    """Owner-scoped edit of a *draft* campaign's allowlisted content fields.

    Only ``name``/``objective``/``destination_url`` may change; any other key is
    rejected (``unknown_field``). Status is never editable here — a caller cannot
    move lifecycle state through this path. Editing is allowed only while the
    campaign is in ``draft`` (archived campaigns raise 409 ``not_editable``).
    Ownership is enforced via ``get_campaign`` (non-owner ⇒ 404).
    """
    _require_enabled()
    if not isinstance(fields, dict):
        raise AdvertisingError("No fields to update.", 400, "no_fields")
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise AdvertisingError(
            f"Unknown field(s): {sorted(unknown)}.", 400, "unknown_field")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        current = get_campaign(campaign_id, requester_user_id=requester_user_id,
                               conn=conn)  # enforces ownership / 404
        if current.get("status") != "draft":
            raise AdvertisingError(
                "Only draft campaigns can be edited.", 409, "not_editable")
        updates: dict = {}
        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise AdvertisingError("Campaign name is required.", 400, "name_required")
            if len(name) > NAME_MAX:
                raise AdvertisingError(
                    f"Campaign name exceeds {NAME_MAX} characters.", 400, "name_too_long")
            updates["name"] = name
        if "objective" in fields:
            objective = (fields["objective"] or "").strip().lower()
            if objective not in CAMPAIGN_OBJECTIVES:
                raise AdvertisingError(
                    f"Unknown objective: {objective!r}.", 400, "bad_objective")
            updates["objective"] = objective
        if "destination_url" in fields:
            raw = fields["destination_url"]
            if raw is None or not str(raw).strip():
                updates["destination_url"] = None
            else:
                url = str(raw).strip()
                if len(url) > URL_MAX:
                    raise AdvertisingError(
                        f"Destination URL exceeds {URL_MAX} characters.", 400, "url_too_long")
                if not (url.startswith("http://") or url.startswith("https://")):
                    raise AdvertisingError(
                        "Destination URL must start with http:// or https://.",
                        400, "bad_url")
                updates["destination_url"] = url
        if not updates:
            return current
        _begin(conn)
        # Column names come only from the fixed EDITABLE_FIELDS allowlist.
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [_now_iso(), campaign_id]
        conn.execute(
            f"UPDATE business_os_ad_campaigns SET {set_clause}, updated_at = ? "
            "WHERE campaign_id = ?",
            tuple(params),
        )
        _audit(conn, campaign_id=campaign_id,
               advertiser_user_id=current.get("advertiser_user_id"),
               action="campaign_update",
               actor=actor if actor is not None else requester_user_id,
               reason=reason,
               before={k: current.get(k) for k in updates}, after=updates)
        _commit(conn)
        return get_campaign(campaign_id, requester_user_id=requester_user_id, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def eligibility_public_view(elig: dict) -> dict:
    """Explainable, client-safe projection of an eligibility result.

    Keeps the separate policy inputs *un-merged* — each remains its own field:
    ``eligible`` (overall), ``rollout_enabled`` (feature flag), ``account_hold``
    (suspension authority), ``advertiser_status`` (approval state), and
    ``denial_reason`` (why, when not eligible). No internal objects leak.
    """
    hold = elig.get("account_hold")
    return {
        "eligible": bool(elig.get("eligible")),
        "rollout_enabled": bool(elig.get("flag_enabled")),
        "account_hold": bool(hold.get("on_hold")) if isinstance(hold, dict) else False,
        "advertiser_status": elig.get("advertiser_status"),
        "denial_reason": None if elig.get("eligible") else elig.get("reason"),
    }


def get_campaign(campaign_id: str, *, requester_user_id: Optional[Any] = None,
                 conn=None) -> Optional[dict]:
    """Fetch a campaign. If ``requester_user_id`` is given, ownership is enforced:
    a non-owner gets ``AdvertisingError`` 404 (existence not leaked).

    Pass ``requester_user_id=None`` only from trusted admin paths.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_campaigns WHERE campaign_id = ?",
            (campaign_id,)
        ).fetchone())
        if row is None:
            if requester_user_id is not None:
                raise AdvertisingError("Campaign not found.", 404, "not_found")
            return None
        if requester_user_id is not None and \
                row.get("advertiser_user_id") != _sid(requester_user_id):
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        return row
    finally:
        if owned:
            conn.close()


def list_campaigns_for_owner(owner_user_id: Any, *, status: Optional[str] = None,
                             conn=None) -> list:
    """All campaigns owned by ``owner_user_id``, newest first. Owner-scoped read."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if status is not None:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_campaigns "
                "WHERE advertiser_user_id = ? AND status = ? "
                "ORDER BY created_at DESC, campaign_id DESC",
                (_sid(owner_user_id), status),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_campaigns "
                "WHERE advertiser_user_id = ? "
                "ORDER BY created_at DESC, campaign_id DESC",
                (_sid(owner_user_id),),
            )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def transition_campaign(campaign_id: str, new_status: str, *,
                        requester_user_id: Any, actor: Optional[Any] = None,
                        reason: Optional[str] = None, conn=None) -> dict:
    """Owner-scoped archive/restore primitive: ``archived`` or ``draft`` only.

    This is the generic lifecycle move used by the advertiser archive/restore
    verbs. It deliberately refuses review states (submitted/approved/rejected) so
    a caller can never reach them through this path and bypass the eligibility,
    validation, and admin-authority gates enforced by submit/withdraw/reopen/
    review. Ownership enforced (non-owner ⇒ 404); illegal transitions raise 409;
    fully reversible. Restoring to ``draft`` also clears any prior review reason.
    """
    _require_enabled()
    new_status = (new_status or "").strip().lower()
    if new_status not in ARCHIVE_RESTORE_STATES:
        raise AdvertisingError(
            f"Unknown campaign status: {new_status!r}.", 400, "bad_status")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        current = get_campaign(campaign_id, requester_user_id=requester_user_id,
                               conn=conn)  # enforces ownership / 404
        cur_status = current.get("status")
        if cur_status == new_status:
            return current  # idempotent no-op
        # Landing back in draft clears the stale rejection reason.
        extra = {"review_reason": None} if new_status == "draft" else None
        _begin(conn)
        _apply_transition(
            conn, current, new_status,
            actor=actor if actor is not None else requester_user_id,
            reason=reason, action="campaign_transition", extra_sets=extra)
        _commit(conn)
        return get_campaign(campaign_id, requester_user_id=requester_user_id, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- review lifecycle (slice 3) ---------------------------------------------
# Advertiser-driven verbs (submit/withdraw/reopen) and the admin review decision.
# Each is a thin, authority-aware wrapper over ``_apply_transition`` so the single
# source of truth for legal moves stays ``ALLOWED_TRANSITIONS``. None of these move
# money, reserve funds, or activate delivery — "approved" is review-approved only.
def submit_campaign(campaign_id: str, *, requester_user_id: Any,
                    context: Optional[dict] = None, actor: Optional[Any] = None,
                    conn=None) -> dict:
    """Advertiser submits an owned *draft* for review: draft -> submitted.

    Requires the advertiser to be eligible (rollout flag on, no account hold,
    advertiser approved) — account hold overrides advertiser approval. The stored
    draft content is re-validated so an invalid campaign cannot enter review.
    """
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        elig = advertiser_eligibility(requester_user_id, context=context, conn=conn)
        if not elig.get("eligible"):
            raise AdvertisingError(
                f"Not eligible to submit campaigns ({elig.get('reason')}).",
                http_status=403, code="ineligible")
        current = get_campaign(campaign_id, requester_user_id=requester_user_id,
                               conn=conn)  # ownership / 404
        if current.get("status") != "draft":
            raise AdvertisingError(
                f"Only draft campaigns can be submitted (is "
                f"{current.get('status')}).", 409, "not_submittable")
        # Re-validate the persisted draft; structured error on any invalid field.
        _validate_draft(current.get("name"), current.get("objective"),
                        current.get("destination_url"))
        _begin(conn)
        _apply_transition(
            conn, current, "submitted",
            actor=actor if actor is not None else requester_user_id,
            action="campaign_submit", extra_sets={"review_reason": None})
        _commit(conn)
        return get_campaign(campaign_id, requester_user_id=requester_user_id, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def withdraw_campaign(campaign_id: str, *, requester_user_id: Any,
                      actor: Optional[Any] = None, conn=None) -> dict:
    """Advertiser withdraws an owned *submitted* campaign back to draft."""
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        current = get_campaign(campaign_id, requester_user_id=requester_user_id,
                               conn=conn)  # ownership / 404
        if current.get("status") != "submitted":
            raise AdvertisingError(
                f"Only submitted campaigns can be withdrawn (is "
                f"{current.get('status')}).", 409, "not_withdrawable")
        _begin(conn)
        _apply_transition(
            conn, current, "draft",
            actor=actor if actor is not None else requester_user_id,
            action="campaign_withdraw", extra_sets={"review_reason": None})
        _commit(conn)
        return get_campaign(campaign_id, requester_user_id=requester_user_id, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def reopen_campaign(campaign_id: str, *, requester_user_id: Any,
                    actor: Optional[Any] = None, conn=None) -> dict:
    """Advertiser reopens an owned *rejected* campaign for revision: rejected ->
    draft. Clears the prior rejection reason so the fresh draft starts clean."""
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        current = get_campaign(campaign_id, requester_user_id=requester_user_id,
                               conn=conn)  # ownership / 404
        if current.get("status") != "rejected":
            raise AdvertisingError(
                f"Only rejected campaigns can be reopened (is "
                f"{current.get('status')}).", 409, "not_reopenable")
        _begin(conn)
        _apply_transition(
            conn, current, "draft",
            actor=actor if actor is not None else requester_user_id,
            action="campaign_reopen", extra_sets={"review_reason": None})
        _commit(conn)
        return get_campaign(campaign_id, requester_user_id=requester_user_id, conn=conn)
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


CAMPAIGN_REVIEW_DECISIONS = {"approve": "approved", "reject": "rejected"}


def admin_review_campaign(campaign_id: str, decision: str, *, actor: Any,
                          reason: Optional[str] = None, conn=None) -> dict:
    """Admin review decision on a *submitted* campaign: approve or reject.

    Trusted caller — bot.py has already enforced the owner/admin RBAC guard.
    ``reject`` requires a reason (surfaced to the owner via ``review_reason``);
    ``approve`` clears any prior reason. ``approved`` is REVIEW-approved only: it
    does not fund, activate, or deliver anything. Returns before/after + reason so
    the route can write the administrative audit trail. Only submitted campaigns
    are reviewable (else 409).
    """
    _require_enabled()
    decision = (decision or "").strip().lower()
    target = CAMPAIGN_REVIEW_DECISIONS.get(decision)
    if target is None:
        raise AdvertisingError(
            "Review decision must be 'approve' or 'reject'.", 400, "bad_decision")
    clean_reason = None if reason is None else str(reason).strip() or None
    if decision == "reject" and not clean_reason:
        raise AdvertisingError(
            "A reason is required to reject a campaign.", 400, "reason_required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        current = get_campaign(campaign_id, requester_user_id=None, conn=conn)
        if current is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        before_status = current.get("status")
        if before_status != "submitted":
            raise AdvertisingError(
                f"Only submitted campaigns can be reviewed (is "
                f"{before_status}).", 409, "not_reviewable")
        # approve clears the reason; reject stores it for owner visibility.
        extra = {"review_reason": clean_reason if decision == "reject" else None}
        _begin(conn)
        _apply_transition(
            conn, current, target, actor=actor, reason=clean_reason,
            action="campaign_review", extra_sets=extra)
        _commit(conn)
        campaign = get_campaign(campaign_id, requester_user_id=None, conn=conn)
        # Notify the owner AFTER the decision is committed. A notification is a side
        # effect of the review, never a precondition — emit() never raises.
        if _notify is not None:
            advertiser_uid = (campaign or {}).get("advertiser_user_id")
            if target == "approved":
                _notify.notify_campaign_approved(advertiser_uid, campaign_id)
            else:
                _notify.notify_campaign_rejected(
                    advertiser_uid, campaign_id, reason=clean_reason)
        return {
            "campaign": campaign,
            "before_status": before_status,
            "after_status": target,
            "reason": clean_reason,
        }
    except AdvertisingError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- admin visibility (input: role/administrative permission) ---------------
# These are trusted-caller reads; the bot.py admin route is responsible for RBAC
# before calling. They deliberately do NOT enforce per-owner scoping.
def admin_list_campaigns(*, status: Optional[str] = None,
                         advertiser_user_id: Optional[Any] = None,
                         limit: int = 200, conn=None) -> list:
    """Admin cross-owner campaign listing with optional filters."""
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if advertiser_user_id is not None:
            clauses.append("advertiser_user_id = ?")
            params.append(_sid(advertiser_user_id))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        cur = conn.execute(
            "SELECT * FROM business_os_ad_campaigns" + where +
            " ORDER BY created_at DESC, campaign_id DESC LIMIT ?",
            tuple(params),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def admin_list_advertisers(*, status: Optional[str] = None, limit: int = 200,
                           conn=None) -> list:
    """Admin advertiser listing with optional status filter."""
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if status is not None:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_advertisers WHERE status = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (status, int(limit)),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_advertisers "
                "ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def admin_get_campaign(campaign_id: str, *, conn=None) -> Optional[dict]:
    """Admin single-campaign fetch (no ownership scoping)."""
    _require_enabled()
    return get_campaign(campaign_id, requester_user_id=None, conn=conn)
