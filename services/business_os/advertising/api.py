"""Advertising slice 2 — framework-agnostic HTTP controller.

bot.py owns authentication, CSRF, and RBAC; it then calls these pure functions
with an *already-authenticated* identity and parsed input, and turns the returned
``(status_code, body)`` tuple into a Flask JSON response. Keeping the decision
logic here (not inline in bot.py) makes every branch unit-testable without
importing Flask/stripe/telegram (bot.py is not importable in the hermetic
sandbox).

Contract for every handler:

  * returns ``(int status_code, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole canonical surface is DARK when ``BUSINESS_OS_ADVERTISING`` is off —
    every handler returns 404 so no partial canonical path is exposed;
  * ownership is enforced in the service (non-owner ⇒ 404, existence not leaked);
  * only the AdvertisingError message (curated, safe) is surfaced — never an
    internal/unexpected exception string;
  * clients may never set lifecycle status directly; archive/restore are the only
    lifecycle verbs and they map to fixed target states server-side.

Identity is passed in by bot.py (``owner_user_id`` derived from the session/token,
never from the request body). ``context`` carries fresh ``{account_status,
access_enabled}`` so account-hold precedence is evaluated on live state.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.advertising import service as ad
from services.business_os.advertising import funding as adf
from services.business_os.advertising import operations as ado
from services.business_os.advertising import ad_sets as ads
from services.business_os.advertising import creatives as adc
from services.business_os.advertising import readiness as adr
from services.business_os.advertising import delivery as adl
from services.business_os.advertising import events as ave
from services.business_os.advertising import reporting as adrep
from services.business_os.advertising import spend as adspend
from services.business_os.advertising import assistant as adasst
from services.business_os.advertising import admin as adadmin

# Fields a client may supply on create/update. Anything else is rejected.
CREATE_FIELDS = {"name", "objective", "destination_url"}
UPDATE_FIELDS = {"name", "objective", "destination_url"}
REGISTER_FIELDS = {"display_name", "notes"}
# Fields a client may supply when configuring a campaign budget (slice 4).
BUDGET_FIELDS = {"budget_cents", "currency"}
# Fields a client may supply when reserving campaign funds (slice 4). The
# idempotency key may alternatively be supplied via an Idempotency-Key header,
# which bot.py folds into this payload before calling.
RESERVE_FIELDS = {"amount_cents", "currency", "idempotency_key"}
RELEASE_FIELDS = {"idempotency_key"}
# Fields a client may supply when scheduling/activating a campaign (slice 5). Only
# an optional UTC start/end window; clients never send a raw operational_status.
SCHEDULE_FIELDS = {"start_at", "end_at"}
# Fields a client may supply when pausing/cancelling (slice 5): an optional reason.
OP_REASON_FIELDS = {"reason"}
# Advertiser-facing lifecycle verbs -> fixed server-side target states. Clients
# never send a raw status; they send an action.
LIFECYCLE_ACTIONS = {"archive": "archived", "restore": "draft"}

# Slice 6 — ad-set + creative fields a client may supply. Anything else is
# rejected server-side; clients never send a raw status/version/owner/id.
AD_SET_FIELDS = {
    "name", "placements", "audience", "schedule_start_at", "schedule_end_at",
    "budget_allocation",
}
CREATIVE_FIELDS = {
    "creative_type", "media_asset_id", "thumbnail_asset_id", "headline", "body",
    "call_to_action", "destination_type", "destination_ref", "accessibility_text",
}
# Slice-6 lifecycle verbs are actions, never raw statuses. Each maps to a gated
# owner-service function; review moves (approve/reject) are admin-only elsewhere.
AD_SET_ACTIONS = {"submit", "withdraw", "pause", "resume", "archive", "restore"}
CREATIVE_ACTIONS = {"submit", "withdraw", "archive", "restore"}

# Stage-2 report/spend window fields a client may supply (all optional).
REPORT_FIELDS = {"currency", "start", "end", "placement"}
SPEND_FIELDS = {"currency"}
# Assistant plan/execute fields. ``tool`` + ``params`` describe the intent; the
# confirmation token is echoed back on execute for a consequential tool.
ASSISTANT_FIELDS = {"tool", "params", "confirmation_token"}
# Governed admin action fields — a reason is mandatory server-side, validated in
# the admin module (a route may also require the RBAC role/reason up front).
ADMIN_REASON_FIELDS = {"reason"}
APPEAL_FIELDS = {"reason", "campaign_id"}
APPEAL_RESOLVE_FIELDS = {"decision", "reason"}

# Slice 7 — delivery request/impression/click fields a viewer client may supply.
# These are strictly the NON-PII request signals; advertiser/campaign/audience/
# price/destination/billable amount are ALWAYS server-side and never trusted from
# the client. Anything else is rejected.
DELIVERY_REQUEST_FIELDS = {
    "country", "region", "language", "locale", "device_class", "viewer_age",
    "request_id",
}
IMPRESSION_FIELDS = {"token", "placement", "idempotency_key", "request_meta"}
CLICK_FIELDS = {"idempotency_key", "request_meta"}


def _dark():
    return (404, {"ok": False, "error": "Not found."})


def _enabled() -> bool:
    return ad.is_enabled()


def _err(exc: ad.AdvertisingError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


def _allowlist(payload: Any, allowed: set) -> dict:
    """Return only allowlisted keys from a dict payload; reject unknown keys.

    Raises AdvertisingError(400, unknown_field) if the caller sent anything not on
    the allowlist, so silent field-dropping never hides a client bug or injection.
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ad.AdvertisingError("Invalid request body.", 400, "bad_body")
    unknown = set(payload) - allowed
    if unknown:
        raise ad.AdvertisingError(
            f"Unknown field(s): {sorted(unknown)}.", 400, "unknown_field")
    return {k: payload[k] for k in payload}


# --- advertiser handlers ----------------------------------------------------
def get_eligibility(owner_user_id: Any, *, context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    elig = ad.advertiser_eligibility(owner_user_id, context=context)
    return (200, {"ok": True, "eligibility": ad.eligibility_public_view(elig)})


def register_advertiser(owner_user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, REGISTER_FIELDS)
        advertiser = ad.upsert_advertiser(
            owner_user_id,
            display_name=fields.get("display_name"),
            notes=fields.get("notes"),
        )
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "advertiser": advertiser})


def create_draft(owner_user_id: Any, payload: Any = None, *,
                 context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, CREATE_FIELDS)
        campaign = ad.create_campaign_draft(
            owner_user_id,
            name=fields.get("name"),
            objective=fields.get("objective"),
            destination_url=fields.get("destination_url"),
            context=context,
        )
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (201, {"ok": True, "campaign": campaign})


def list_own_campaigns(owner_user_id: Any, *, status: Optional[str] = None):
    if not _enabled():
        return _dark()
    if status is not None and status not in ad.CAMPAIGN_STATUSES:
        return _err(ad.AdvertisingError(
            f"Unknown status filter: {status!r}.", 400, "bad_status"))
    campaigns = ad.list_campaigns_for_owner(owner_user_id, status=status)
    return (200, {"ok": True, "campaigns": campaigns})


def get_own_campaign(owner_user_id: Any, campaign_id: str):
    if not _enabled():
        return _dark()
    try:
        campaign = ad.get_campaign(campaign_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "campaign": campaign})


def update_draft(owner_user_id: Any, campaign_id: str, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, UPDATE_FIELDS)
        if not fields:
            raise ad.AdvertisingError("No fields to update.", 400, "no_fields")
        campaign = ad.update_campaign_draft(
            campaign_id, requester_user_id=owner_user_id, fields=fields)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "campaign": campaign})


def lifecycle(owner_user_id: Any, campaign_id: str, action: str):
    """Advertiser lifecycle verb: 'archive' or 'restore' only. No raw status in."""
    if not _enabled():
        return _dark()
    target = LIFECYCLE_ACTIONS.get((action or "").strip().lower())
    if target is None:
        return _err(ad.AdvertisingError(
            f"Unknown lifecycle action: {action!r}.", 400, "bad_action"))
    try:
        campaign = ad.transition_campaign(
            campaign_id, target, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "campaign": campaign})


def submit(owner_user_id: Any, campaign_id: str, *,
           context: Optional[dict] = None):
    """Advertiser submits an owned draft for review (draft -> submitted)."""
    if not _enabled():
        return _dark()
    try:
        campaign = ad.submit_campaign(
            campaign_id, requester_user_id=owner_user_id, context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "campaign": campaign})


def withdraw(owner_user_id: Any, campaign_id: str):
    """Advertiser withdraws an owned submitted campaign back to draft."""
    if not _enabled():
        return _dark()
    try:
        campaign = ad.withdraw_campaign(
            campaign_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "campaign": campaign})


def reopen(owner_user_id: Any, campaign_id: str):
    """Advertiser reopens an owned rejected campaign for revision (-> draft)."""
    if not _enabled():
        return _dark()
    try:
        campaign = ad.reopen_campaign(
            campaign_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "campaign": campaign})


# --- funding handlers (slice 4) ---------------------------------------------
# Funding readiness is a SEPARATE concern from review approval and from delivery.
# None of these deliver, auction, bid, or pace. "funded" means budget reserved in
# escrow via the canonical ledger; activation_ready is derived, never stored.
def get_funding(owner_user_id: Any, campaign_id: str):
    """Read an owned campaign's funding readiness (funding_status + derived
    activation_ready)."""
    if not _enabled():
        return _dark()
    try:
        view = adf.get_funding_view(campaign_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "funding": view})


def set_budget(owner_user_id: Any, campaign_id: str, payload: Any = None, *,
               context: Optional[dict] = None):
    """Configure/update an owned campaign's total budget before funding."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, BUDGET_FIELDS)
        if "budget_cents" not in fields:
            raise ad.AdvertisingError(
                "budget_cents is required.", 400, "budget_required")
        view = adf.set_campaign_budget(
            campaign_id, requester_user_id=owner_user_id,
            budget_cents=fields.get("budget_cents"),
            currency=fields.get("currency", "usd"), context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "funding": view})


def reserve(owner_user_id: Any, campaign_id: str, payload: Any = None, *,
            context: Optional[dict] = None):
    """Reserve the configured budget for an owned, review-approved campaign.

    Requires an idempotency key (payload ``idempotency_key`` or the folded-in
    Idempotency-Key header). A genuine retry is a no-op; the same key reused for a
    different operation is rejected 409; insufficient balance is rejected 402."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, RESERVE_FIELDS)
        if "amount_cents" not in fields:
            raise ad.AdvertisingError(
                "amount_cents is required.", 400, "amount_required")
        view = adf.reserve_funds(
            campaign_id, requester_user_id=owner_user_id,
            idempotency_key=fields.get("idempotency_key"),
            amount_cents=fields.get("amount_cents"),
            currency=fields.get("currency", "usd"), context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "funding": view})


def release(owner_user_id: Any, campaign_id: str, payload: Any = None, *,
            context: Optional[dict] = None):
    """Release a funded owned campaign's reserved budget back to the wallet.

    Requires an idempotency key. Duplicate release is an idempotent no-op."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, RELEASE_FIELDS)
        view = adf.release_funds(
            campaign_id, requester_user_id=owner_user_id,
            idempotency_key=fields.get("idempotency_key"), context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "funding": view})


# --- admin handlers ---------------------------------------------------------
# bot.py has already enforced the owner guard before these run. Each state change
# returns before/after so the bot.py adapter can write the admin audit trail
# (acting admin, target, previous state, new state, reason, timestamp, ref).
def admin_list_advertisers(*, status: Optional[str] = None, limit: int = 200):
    if not _enabled():
        return _dark()
    if status is not None and status not in ad.ADVERTISER_STATUSES:
        return _err(ad.AdvertisingError(
            f"Unknown status filter: {status!r}.", 400, "bad_status"))
    rows = ad.admin_list_advertisers(status=status, limit=limit)
    return (200, {"ok": True, "advertisers": rows})


def admin_get_advertiser(user_id: Any):
    if not _enabled():
        return _dark()
    advertiser = ad.get_advertiser(user_id)
    if advertiser is None:
        return _err(ad.AdvertisingError("Advertiser not found.", 404, "not_found"))
    return (200, {"ok": True, "advertiser": advertiser})


def admin_set_advertiser_status(actor: Any, user_id: Any, status: str, *,
                                reason: Optional[str] = None):
    """Approve/reject/suspend/restore an advertiser. Returns before+after so the
    caller can record the administrative audit trail."""
    if not _enabled():
        return _dark()
    try:
        before = ad.get_advertiser(user_id)
        before_status = None if before is None else before.get("status")
        advertiser = ad.set_advertiser_status(
            user_id, status, actor=actor, reason=reason)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {
        "ok": True,
        "advertiser": advertiser,
        "before_status": before_status,
        "after_status": advertiser.get("status"),
    })


def admin_review(actor: Any, campaign_id: str, decision: str, *,
                 reason: Optional[str] = None):
    """Admin review decision on a submitted campaign: approve or reject.

    Returns before/after status + the cleaned reason so the bot.py adapter can
    write the administrative audit trail. ``approved`` is review-approved only —
    it funds nothing and activates no delivery."""
    if not _enabled():
        return _dark()
    try:
        result = ad.admin_review_campaign(
            campaign_id, decision, actor=actor, reason=reason)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {
        "ok": True,
        "campaign": result["campaign"],
        "before_status": result["before_status"],
        "after_status": result["after_status"],
        "reason": result["reason"],
    })


def admin_list_campaigns(*, status: Optional[str] = None,
                         advertiser_user_id: Optional[Any] = None,
                         limit: int = 200):
    if not _enabled():
        return _dark()
    if status is not None and status not in ad.CAMPAIGN_STATUSES:
        return _err(ad.AdvertisingError(
            f"Unknown status filter: {status!r}.", 400, "bad_status"))
    rows = ad.admin_list_campaigns(
        status=status, advertiser_user_id=advertiser_user_id, limit=limit)
    return (200, {"ok": True, "campaigns": rows})


def admin_get_campaign(campaign_id: str):
    if not _enabled():
        return _dark()
    try:
        campaign = ad.admin_get_campaign(campaign_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    if campaign is None:
        return _err(ad.AdvertisingError("Campaign not found.", 404, "not_found"))
    return (200, {"ok": True, "campaign": campaign})


def admin_get_funding(campaign_id: str):
    """Admin funding view: state + ledger references + escrow balance + the
    append-only funding operation log. Trusted caller (route enforces RBAC)."""
    if not _enabled():
        return _dark()
    try:
        view = adf.admin_get_funding(campaign_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "funding": view})


def admin_list_funding(*, funding_status: Optional[str] = None, limit: int = 200):
    """Admin cross-owner funding listing; filter e.g. ``funding_status=funding_failed``
    to inspect failed/inconsistent reservations."""
    if not _enabled():
        return _dark()
    if funding_status is not None and funding_status not in adf.FUNDING_STATUSES:
        return _err(ad.AdvertisingError(
            f"Unknown funding status filter: {funding_status!r}.",
            400, "bad_status"))
    rows = adf.admin_list_funding(funding_status=funding_status, limit=limit)
    return (200, {"ok": True, "funding": rows})


# --- operational handlers (slice 5) -----------------------------------------
# Operational status is a SEPARATE concern from review approval, funding, and
# delivery. None of these deliver, auction, bid, pace, or move money. "active"
# means operationally AUTHORIZED for a future delivery worker, NOT delivering.
def get_operational(owner_user_id: Any, campaign_id: str):
    """Read an owned campaign's operational readiness (review + funding +
    operational states side by side, plus derived activation_ready)."""
    if not _enabled():
        return _dark()
    try:
        view = ado.get_operational_view(
            campaign_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def schedule(owner_user_id: Any, campaign_id: str, payload: Any = None, *,
             context: Optional[dict] = None):
    """Schedule an owned, eligible campaign (-> scheduled) with an optional UTC
    start/end window. Requires the full activation gate."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, SCHEDULE_FIELDS)
        view = ado.schedule_campaign(
            campaign_id, requester_user_id=owner_user_id,
            start_at=fields.get("start_at"), end_at=fields.get("end_at"),
            context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def activate(owner_user_id: Any, campaign_id: str, payload: Any = None, *,
             context: Optional[dict] = None):
    """Activate an owned, eligible campaign (-> active) with an optional UTC
    window. Requires the full activation gate. Activation delivers nothing."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, SCHEDULE_FIELDS)
        view = ado.activate_campaign(
            campaign_id, requester_user_id=owner_user_id,
            start_at=fields.get("start_at"), end_at=fields.get("end_at"),
            context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def pause(owner_user_id: Any, campaign_id: str, payload: Any = None):
    """Pause an owned scheduled/active campaign (-> paused). No activation gate."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, OP_REASON_FIELDS)
        view = ado.pause_campaign(
            campaign_id, requester_user_id=owner_user_id,
            reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def resume(owner_user_id: Any, campaign_id: str, *,
           context: Optional[dict] = None):
    """Resume an owned paused campaign (-> active). Re-runs the full activation
    gate, so a suspended advertiser or released funding blocks resume."""
    if not _enabled():
        return _dark()
    try:
        view = ado.resume_campaign(
            campaign_id, requester_user_id=owner_user_id, context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def cancel(owner_user_id: Any, campaign_id: str, payload: Any = None):
    """Cancel an owned campaign (-> cancelled). Terminal; does NOT release funds."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, OP_REASON_FIELDS)
        view = ado.cancel_campaign(
            campaign_id, requester_user_id=owner_user_id,
            reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def admin_get_operational(campaign_id: str):
    """Admin operational view: review + funding + operational states together plus
    the full funding projection. Trusted caller (route enforces RBAC)."""
    if not _enabled():
        return _dark()
    try:
        view = ado.admin_get_operational(campaign_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def admin_list_operations(*, operational_status: Optional[str] = None,
                          limit: int = 200):
    """Admin cross-owner operational listing with an optional status filter."""
    if not _enabled():
        return _dark()
    if operational_status is not None \
            and operational_status not in ado.OPERATIONAL_STATUSES:
        return _err(ad.AdvertisingError(
            f"Unknown operational status filter: {operational_status!r}.",
            400, "bad_status"))
    rows = ado.admin_list_operations(
        operational_status=operational_status, limit=limit)
    return (200, {"ok": True, "operations": rows})


def admin_pause(actor: Any, campaign_id: str, payload: Any = None):
    """Admin pause intervention (-> paused). Returns before/after via the
    operational view so the caller can write the admin audit trail."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, OP_REASON_FIELDS)
        view = ado.admin_pause_campaign(
            campaign_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def admin_cancel(actor: Any, campaign_id: str, payload: Any = None):
    """Admin cancel intervention (-> cancelled). Does not release funds."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, OP_REASON_FIELDS)
        view = ado.admin_cancel_campaign(
            campaign_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


def admin_complete(actor: Any, campaign_id: str, payload: Any = None):
    """Authorized admin path to mark a run finished (active -> completed)."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, OP_REASON_FIELDS)
        view = ado.admin_complete_campaign(
            campaign_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "operational": view})


# --- ad-set handlers (slice 6) ----------------------------------------------
# An ad set carries its OWN review lifecycle, separate from the campaign review,
# funding, and operational states. Nothing here delivers, targets a real user, or
# moves money. Ownership is enforced in the service (non-owner ⇒ 404).
def create_ad_set(owner_user_id: Any, campaign_id: str, payload: Any = None, *,
                  context: Optional[dict] = None):
    """Create a DRAFT ad set under an owned, non-archived campaign."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, AD_SET_FIELDS)
        ad_set = ads.create_ad_set(
            owner_user_id, campaign_id, fields, context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (201, {"ok": True, "ad_set": ad_set})


def list_ad_sets(owner_user_id: Any, *, campaign_id: Optional[str] = None):
    if not _enabled():
        return _dark()
    rows = ads.list_ad_sets(owner_user_id, campaign_id=campaign_id)
    return (200, {"ok": True, "ad_sets": rows})


def get_ad_set(owner_user_id: Any, ad_set_id: str):
    if not _enabled():
        return _dark()
    try:
        ad_set = ads.get_ad_set(ad_set_id, requester_user_id=owner_user_id)
        if ad_set is None:
            raise ad.AdvertisingError("Ad set not found.", 404, "not_found")
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "ad_set": ad_set})


def update_ad_set(owner_user_id: Any, ad_set_id: str, payload: Any = None):
    """In-place edit of an owned DRAFT/REJECTED ad set (strict allowlist)."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, AD_SET_FIELDS)
        if not fields:
            raise ad.AdvertisingError("No fields to update.", 400, "no_fields")
        ad_set = ads.update_ad_set(
            ad_set_id, requester_user_id=owner_user_id, fields=fields)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "ad_set": ad_set})


def ad_set_lifecycle(owner_user_id: Any, ad_set_id: str, action: str):
    """Owner ad-set lifecycle verb: submit/withdraw/pause/resume/archive/restore.
    Clients send an action, never a raw status; review approve/reject is admin-only."""
    if not _enabled():
        return _dark()
    verb = (action or "").strip().lower()
    if verb not in AD_SET_ACTIONS:
        return _err(ad.AdvertisingError(
            f"Unknown ad-set action: {action!r}.", 400, "bad_action"))
    fn = {
        "submit": ads.submit_ad_set, "withdraw": ads.withdraw_ad_set,
        "pause": ads.pause_ad_set, "resume": ads.resume_ad_set,
        "archive": ads.archive_ad_set, "restore": ads.restore_ad_set,
    }[verb]
    try:
        ad_set = fn(ad_set_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "ad_set": ad_set})


# --- creative handlers (slice 6) --------------------------------------------
# A creative binds to an ad set + campaign under the same owner and carries its
# own review lifecycle. Media is validated against the authoritative media
# ownership system; destinations are verified/normalized. Nothing here delivers.
def create_creative(owner_user_id: Any, ad_set_id: str, payload: Any = None, *,
                    context: Optional[dict] = None):
    """Create a DRAFT creative under an owned, non-archived ad set."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, CREATIVE_FIELDS)
        creative = adc.create_creative(
            owner_user_id, ad_set_id, fields, context=context)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (201, {"ok": True, "creative": creative})


def list_creatives(owner_user_id: Any, *, ad_set_id: Optional[str] = None):
    if not _enabled():
        return _dark()
    rows = adc.list_creatives(owner_user_id, ad_set_id=ad_set_id)
    return (200, {"ok": True, "creatives": rows})


def get_creative(owner_user_id: Any, creative_id: str):
    if not _enabled():
        return _dark()
    try:
        creative = adc.get_creative(creative_id, requester_user_id=owner_user_id)
        if creative is None:
            raise ad.AdvertisingError("Creative not found.", 404, "not_found")
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "creative": creative})


def update_creative(owner_user_id: Any, creative_id: str, payload: Any = None):
    """In-place edit of an owned DRAFT/REJECTED creative. A submitted/approved
    creative is immutable here (409) — use ``revise_creative`` for a new version."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, CREATIVE_FIELDS)
        if not fields:
            raise ad.AdvertisingError("No fields to update.", 400, "no_fields")
        creative = adc.update_creative(
            creative_id, requester_user_id=owner_user_id, fields=fields)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "creative": creative})


def revise_creative(owner_user_id: Any, creative_id: str, payload: Any = None):
    """Materially revise an owned SUBMITTED/APPROVED creative into a NEW version.
    The reviewed original is left intact; the response is the new draft version."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, CREATIVE_FIELDS)
        if not fields:
            raise ad.AdvertisingError("No fields to revise.", 400, "no_fields")
        creative = adc.revise_creative(
            creative_id, requester_user_id=owner_user_id, fields=fields)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (201, {"ok": True, "creative": creative})


def creative_lifecycle(owner_user_id: Any, creative_id: str, action: str):
    """Owner creative lifecycle verb: submit/withdraw/archive/restore. Submission
    enforces the media + destination completeness contract in the service."""
    if not _enabled():
        return _dark()
    verb = (action or "").strip().lower()
    if verb not in CREATIVE_ACTIONS:
        return _err(ad.AdvertisingError(
            f"Unknown creative action: {action!r}.", 400, "bad_action"))
    fn = {
        "submit": adc.submit_creative, "withdraw": adc.withdraw_creative,
        "archive": adc.archive_creative, "restore": adc.restore_creative,
    }[verb]
    try:
        creative = fn(creative_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "creative": creative})


def get_creative_readiness(owner_user_id: Any, creative_id: str):
    """Owner-scoped DERIVED hierarchy-readiness for a creative. Every input is kept
    SEPARATE and nothing is ever stored; ``hierarchy_ready`` is computed live."""
    if not _enabled():
        return _dark()
    try:
        view = adr.hierarchy_readiness(
            creative_id, requester_user_id=owner_user_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "readiness": view})


# --- admin ad-set / creative review handlers (slice 6) ----------------------
# bot.py enforces the owner guard before these run. Each review returns
# before/after status + reason so the adapter can write the admin audit trail.
# Approval is review-approval only — it publishes and delivers nothing.
def admin_list_ad_sets(*, status: Optional[str] = None):
    if not _enabled():
        return _dark()
    try:
        rows = ads.admin_list_ad_sets(status=status)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "ad_sets": rows})


def admin_get_ad_set(ad_set_id: str):
    if not _enabled():
        return _dark()
    try:
        view = ads.admin_get_ad_set(ad_set_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "ad_set": view})


def admin_review_ad_set(actor: Any, ad_set_id: str, decision: str, *,
                        reason: Optional[str] = None):
    if not _enabled():
        return _dark()
    try:
        before = ads.get_ad_set(ad_set_id)
        before_status = None if before is None else before.get("status")
        ad_set = ads.admin_review_ad_set(
            ad_set_id, decision, actor=actor, reason=reason)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {
        "ok": True,
        "ad_set": ad_set,
        "before_status": before_status,
        "after_status": ad_set.get("status"),
    })


def admin_list_creatives(*, status: Optional[str] = "submitted"):
    if not _enabled():
        return _dark()
    try:
        rows = adc.admin_list_creatives(status=status)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "creatives": rows})


def admin_get_creative(creative_id: str):
    if not _enabled():
        return _dark()
    try:
        view = adc.admin_get_creative(creative_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "creative": view})


def admin_review_creative(actor: Any, creative_id: str, decision: str, *,
                          reason: Optional[str] = None):
    if not _enabled():
        return _dark()
    try:
        before = adc.get_creative(creative_id)
        before_status = None if before is None else before.get("status")
        creative = adc.admin_review_creative(
            creative_id, decision, actor=actor, reason=reason)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {
        "ok": True,
        "creative": creative,
        "before_status": before_status,
        "after_status": creative.get("status"),
    })


def admin_get_creative_readiness(creative_id: str):
    """Admin (trusted) DERIVED hierarchy-readiness for a creative — inputs kept
    separate, never stored."""
    if not _enabled():
        return _dark()
    try:
        view = adr.hierarchy_readiness(creative_id, requester_user_id=None)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "readiness": view})


# --- slice 7: delivery / impression / click viewer handlers -----------------
# bot.py resolves the viewer identity from the session (never the body) and passes
# it in. The whole surface is DARK (404) when the flag is off. The viewer only ever
# supplies non-PII request signals + the opaque impression token it was handed in
# the sponsored payload; every hierarchy/price/destination fact is server-side.
def request_delivery(viewer_user_id: Any, placement: str, payload: Any = None):
    """Return ONE eligible sponsored Feed/Reels placement, or a no-placement body.

    Never raises for 'no ad' — that is a normal 200 with ``sponsored: null``. Real
    errors (bad placement, rate limit) surface as curated AdvertisingError codes.
    """
    if not _enabled():
        return _dark()
    try:
        req = _allowlist(payload, DELIVERY_REQUEST_FIELDS)
        result = adl.request_placement(viewer_user_id, placement, request=req)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, **result})


def record_impression(viewer_user_id: Any, delivery_id: str, payload: Any = None):
    """Record ONE impression for a delivery, idempotently. The token from the
    sponsored payload authenticates the display; the client supplies no hierarchy."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, IMPRESSION_FIELDS)
        token = fields.get("token")
        if not token:
            raise ad.AdvertisingError("Missing delivery token.", 400, "missing_token")
        event = ave.record_impression(
            delivery_id, token,
            placement=fields.get("placement"),
            idempotency_key=fields.get("idempotency_key"),
            viewer_user_id=viewer_user_id,
            request_meta=fields.get("request_meta"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "impression": event})


def record_click(viewer_user_id: Any, delivery_id: str, payload: Any = None):
    """Record ONE click for a delivery, idempotently. The destination is SERVER-
    resolved from the bound creative version; the client cannot supply one."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, CLICK_FIELDS)
        event = ave.record_click(
            delivery_id,
            idempotency_key=fields.get("idempotency_key"),
            viewer_user_id=viewer_user_id,
            request_meta=fields.get("request_meta"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "click": event})


# --- slice 7: admin read-only delivery/event visibility (spec §13) ----------
# Strictly READ. Admins can never fabricate an event or mutate a delivery here.
def admin_list_deliveries(*, advertiser_user_id: Optional[Any] = None,
                          campaign_id: Optional[str] = None,
                          placement: Optional[str] = None,
                          since: Optional[str] = None,
                          until: Optional[str] = None, limit: int = 100):
    if not _enabled():
        return _dark()
    try:
        rows = adl.admin_search_deliveries(
            advertiser_user_id=advertiser_user_id, campaign_id=campaign_id,
            placement=placement, since=since, until=until, limit=limit)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "deliveries": rows})


def admin_get_delivery(delivery_id: str):
    if not _enabled():
        return _dark()
    try:
        view = adl.admin_get_delivery(delivery_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "delivery": view})


def admin_list_impressions(*, delivery_id: Optional[str] = None,
                           campaign_id: Optional[str] = None,
                           advertiser_user_id: Optional[Any] = None,
                           fraud_status: Optional[str] = None,
                           since: Optional[str] = None,
                           until: Optional[str] = None, limit: int = 100):
    if not _enabled():
        return _dark()
    try:
        rows = ave.admin_search_impressions(
            delivery_id=delivery_id, campaign_id=campaign_id,
            advertiser_user_id=advertiser_user_id, fraud_status=fraud_status,
            since=since, until=until, limit=limit)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "impressions": rows})


def admin_list_clicks(*, delivery_id: Optional[str] = None,
                      campaign_id: Optional[str] = None,
                      advertiser_user_id: Optional[Any] = None,
                      fraud_status: Optional[str] = None,
                      since: Optional[str] = None,
                      until: Optional[str] = None, limit: int = 100):
    if not _enabled():
        return _dark()
    try:
        rows = ave.admin_search_clicks(
            delivery_id=delivery_id, campaign_id=campaign_id,
            advertiser_user_id=advertiser_user_id, fraud_status=fraud_status,
            since=since, until=until, limit=limit)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "clicks": rows})


# --- Part 2/1: advertiser-facing reporting + spend (owner-scoped) ------------
def _assert_owned_campaign(owner_user_id: Any, campaign_id: Any):
    """Ownership guard for owner-facing reads whose service fn is not owner-scoped:
    resolve the campaign as the requester; a non-owner (or missing) ⇒ 404."""
    if ad.get_campaign(ad._sid(campaign_id), requester_user_id=owner_user_id) is None:
        raise ad.AdvertisingError("Campaign not found.", 404, "not_found")


def get_campaign_report(owner_user_id: Any, campaign_id: str, payload: Any = None):
    """Authoritative advertiser performance report for an OWNED campaign, over an
    optional currency/window/placement. Ownership enforced (non-owner ⇒ 404)."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, REPORT_FIELDS)
        _assert_owned_campaign(owner_user_id, campaign_id)
        report = adrep.campaign_report(
            campaign_id, currency=fields.get("currency") or "usd",
            start=fields.get("start"), end=fields.get("end"),
            placement=fields.get("placement"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "report": report})


def get_campaign_spend(owner_user_id: Any, campaign_id: str, payload: Any = None):
    """Authoritative spend view for an OWNED campaign. Ownership enforced."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, SPEND_FIELDS)
        _assert_owned_campaign(owner_user_id, campaign_id)
        view = adspend.get_campaign_spend(campaign_id, fields.get("currency") or "usd")
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "spend": view})


# --- Part 4: governed UNDX advertising assistant ----------------------------
# Two-phase. plan() may run a read immediately or mint a confirmation token; execute()
# requires the matching token for a consequential tool and verifies the write against
# canonical state. The identity is the authenticated owner — never from the body.
def assistant_list_tools(owner_user_id: Any):
    if not _enabled():
        return _dark()
    return (200, {"ok": True, "tools": adasst.list_tools()})


def assistant_plan(owner_user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, ASSISTANT_FIELDS)
        tool = fields.get("tool")
        if not tool:
            raise ad.AdvertisingError("A tool is required.", 400, "tool_required")
        result = adasst.plan(owner_user_id, tool, fields.get("params") or {})
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "plan": result})


def assistant_execute(owner_user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, ASSISTANT_FIELDS)
        tool = fields.get("tool")
        if not tool:
            raise ad.AdvertisingError("A tool is required.", 400, "tool_required")
        result = adasst.execute(
            owner_user_id, tool, fields.get("params") or {},
            confirmation_token=fields.get("confirmation_token"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "result": result})


# --- Part 6: admin billing inspection / fraud / spend controls / restrictions
# / appeals. bot.py enforces the admin RBAC role BEFORE these run; the governed
# actions additionally require a non-empty actor + reason inside the admin module,
# and return before/after so bot.py can persist the administrative audit trail.
def admin_billing_summary(campaign_id: str, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        currency = (payload or {}).get("currency") or "usd" \
            if isinstance(payload, dict) else "usd"
        view = adadmin.admin_billing_summary(campaign_id, currency)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "billing": view})


def admin_list_billing_events(*, campaign_id: Optional[Any] = None,
                              advertiser_user_id: Optional[Any] = None,
                              billing_status: Optional[str] = None, limit: int = 200):
    if not _enabled():
        return _dark()
    try:
        rows = adadmin.admin_list_billing_events(
            campaign_id=campaign_id, advertiser_user_id=advertiser_user_id,
            billing_status=billing_status, limit=limit)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "billing_events": rows})


def admin_fraud_summary(campaign_id: str):
    if not _enabled():
        return _dark()
    try:
        view = adadmin.admin_fraud_summary(campaign_id)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "fraud": view})


def admin_list_flagged_events(campaign_id: str, *, kind: str = "click",
                              limit: int = 200):
    if not _enabled():
        return _dark()
    try:
        rows = adadmin.admin_list_flagged_events(campaign_id, kind=kind, limit=limit)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "flagged_events": rows})


def admin_halt_spend(actor: Any, campaign_id: str, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, ADMIN_REASON_FIELDS)
        out = adadmin.admin_halt_spend(
            campaign_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, **out})


def admin_lift_spend_halt(actor: Any, campaign_id: str, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, ADMIN_REASON_FIELDS)
        out = adadmin.admin_lift_spend_halt(
            campaign_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, **out})


def admin_restrict_advertiser(actor: Any, user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, ADMIN_REASON_FIELDS)
        out = adadmin.admin_restrict_advertiser(
            user_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, **out})


def admin_lift_restriction(actor: Any, user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, ADMIN_REASON_FIELDS)
        out = adadmin.admin_lift_restriction(
            user_id, actor=actor, reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, **out})


def submit_appeal(owner_user_id: Any, payload: Any = None):
    """Advertiser-initiated appeal of a restriction/rejection. The subject is the
    authenticated owner — never taken from the body."""
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, APPEAL_FIELDS)
        out = adadmin.submit_appeal(
            owner_user_id, reason=fields.get("reason"),
            campaign_id=fields.get("campaign_id"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "appeal": out})


def admin_list_appeals(*, user_id: Optional[Any] = None,
                       state: Optional[str] = None, limit: int = 200):
    if not _enabled():
        return _dark()
    try:
        rows = adadmin.admin_list_appeals(user_id=user_id, state=state, limit=limit)
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, "appeals": rows})


def admin_resolve_appeal(actor: Any, appeal_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, APPEAL_RESOLVE_FIELDS)
        out = adadmin.admin_resolve_appeal(
            appeal_id, fields.get("decision"), actor=actor,
            reason=fields.get("reason"))
    except ad.AdvertisingError as exc:
        return _err(exc)
    return (200, {"ok": True, **out})
