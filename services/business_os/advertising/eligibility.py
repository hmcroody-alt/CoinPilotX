"""Business OS — Advertising slice 7 delivery-eligibility service.

Answers the request-time question the slice-6 readiness service does NOT: *for
THIS placement request and THIS viewer, right now, is a given approved creative an
eligible delivery candidate?* (spec §2). It composes on top of, and never
duplicates, the authoritative hierarchy view:

    readiness.hierarchy_readiness  -> the whole approve/fund/operational/ad-set/
                                      creative/placement/audience chain (slice 6)

then layers the delivery-only gates that only exist at request time:

    advertiser eligibility     (flag + account hold + advertiser approval)
    placement<->creative type  (feed:{image,video}, reels:{reels_video})
    audience matches request    (country/language/device against the ad-set spec)
    campaign schedule active     (operations start_at/end_at window)
    ad-set schedule override      (schedule_start_at/end_at window)
    frequency cap not reached      (derived from immutable impression log)
    creative media usable           (re-validated against pulse_media_assets)
    destination structurally valid   (re-validated against creatives service)

Every gate is kept SEPARATE and contributes a named reason to ``reasons`` on
failure — nothing is silently collapsed, and missing authority is NEVER treated as
eligible (spec §2). This module is pure read/compose: it selects, it does not
deliver, spend, auction, or write.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.advertising import service as _svc
from services.business_os.advertising import readiness as _readiness
from services.business_os.advertising import ad_sets as _adset
from services.business_os.advertising import creatives as _creative
from services.business_os.advertising import frequency as _freq
from services.business_os.advertising.service import AdvertisingError
from . import delivery_common as _c


# --- request context --------------------------------------------------------
def normalize_request_context(payload: Optional[dict]) -> dict:
    """Extract only the NON-PII request signals we match on. The client never sends
    advertiser/campaign/audience/price; those are server-side. Age is optional and
    only ever used to DISQUALIFY, never stored."""
    p = payload or {}

    def _s(v):
        return v.strip().lower() if isinstance(v, str) and v.strip() else None

    age = p.get("viewer_age")
    try:
        age = int(age) if age not in (None, "") else None
    except Exception:
        age = None
    return {
        "country": _s(p.get("country") or p.get("region")),
        "language": _s(p.get("language") or p.get("locale")),
        "device_class": _s(p.get("device_class")),
        "viewer_age": age,
    }


# --- individual gates -------------------------------------------------------
def _placement_type_compatible(placement: str, creative_type: str) -> bool:
    allowed = _c.PLACEMENT_CREATIVE_COMPAT.get(placement, set())
    return creative_type in allowed


def _audience_matches(audience: dict, ctx: dict) -> bool:
    """The ad-set audience is an allowlist per field. A request value that is
    PRESENT and NOT in the allowlist disqualifies; an ABSENT request value cannot
    disqualify (broad match). Exclusions disqualify on presence. This mirrors the
    'audience rules match request' contract without inventing new targeting.
    """
    audience = audience or {}

    def _lc_list(v):
        return {str(x).strip().lower() for x in v if str(x).strip()} if isinstance(v, (list, tuple, set)) else set()

    # positive allowlists
    for field, ctx_key in (("countries", "country"),
                           ("languages", "language"),
                           ("device_classes", "device_class")):
        allow = _lc_list(audience.get(field))
        val = ctx.get(ctx_key)
        if allow and val is not None and val not in allow:
            return False

    # exclusions (currently countries only, per targeting allowlist)
    exclusions = audience.get("exclusions") or {}
    if isinstance(exclusions, dict):
        excl_countries = _lc_list(exclusions.get("countries"))
        if excl_countries and ctx.get("country") in excl_countries:
            return False

    # age band — only disqualifies when the request actually carries an age
    age = ctx.get("viewer_age")
    if age is not None:
        mn = audience.get("min_age")
        mx = audience.get("max_age")
        try:
            if mn is not None and age < int(mn):
                return False
            if mx is not None and age > int(mx):
                return False
        except Exception:
            pass
    return True


def _window_active(start_at: Any, end_at: Any, now_dt) -> bool:
    """True when now is within [start, end]. Either bound may be absent (open)."""
    start = _c.parse_iso(start_at)
    end = _c.parse_iso(end_at)
    if start is not None and now_dt < start:
        return False
    if end is not None and now_dt > end:
        return False
    return True


def _media_usable(conn, creative_row: dict) -> bool:
    media_id = creative_row.get("media_asset_id")
    if not media_id:
        return False
    try:
        _creative._validate_media_asset(
            conn, media_id, creative_row.get("advertiser_user_id"),
            creative_row.get("creative_type"), field="media_asset_id")
        return True
    except AdvertisingError:
        return False


def _destination_valid(conn, creative_row: dict) -> bool:
    dtype = creative_row.get("destination_type")
    dref = creative_row.get("destination_ref")
    if not dtype or not dref:
        return False
    try:
        _creative._validate_destination(conn, dtype, dref)
        return True
    except AdvertisingError:
        return False


# --- evaluation -------------------------------------------------------------
def evaluate(conn, creative_row: dict, *, placement: str, request_ctx: dict,
             subject_ref: str) -> dict:
    """Evaluate one approved creative as a delivery candidate for this request.

    Returns a structured decision: ``eligible`` plus SEPARATE per-gate booleans and
    a ``reasons`` list naming every failing gate. Never raises for an ineligible
    candidate — ineligibility is data, not an error.
    """
    reasons: list = []
    creative_id = creative_row.get("creative_id")
    advertiser_uid = creative_row.get("advertiser_user_id")
    now_dt = _c.now_utc()

    # 1) whole hierarchy readiness (slice-6 authoritative composition)
    readiness = _readiness.hierarchy_readiness(
        creative_id, requester_user_id=None, conn=conn)
    hierarchy_ready = bool(readiness.get("hierarchy_ready"))
    if not hierarchy_ready:
        for r in readiness.get("denial_reasons") or []:
            reasons.append(f"hierarchy:{r}")

    # 2) advertiser still eligible (flag + account hold + approval)
    elig = _svc.advertiser_eligibility(advertiser_uid, context=None, conn=conn)
    advertiser_eligible = bool(elig.get("eligible"))
    if not advertiser_eligible:
        reasons.append(f"advertiser_ineligible:{elig.get('reason')}")

    # 3) placement <-> creative type compatibility
    creative_type = creative_row.get("creative_type")
    placement_compatible = _placement_type_compatible(placement, creative_type)
    if not placement_compatible:
        reasons.append("placement_incompatible")

    # ad-set context for audience + schedule override
    ad_set_row = _adset._get_row(conn, creative_row.get("ad_set_id"),
                                 requester_user_id=None)
    ad_set_view = _adset._ad_set_public(ad_set_row) if ad_set_row else None

    # placement must also be one the ad set actually selected
    selected_placements = set((ad_set_view or {}).get("placements") or [])
    if selected_placements and placement not in selected_placements:
        placement_compatible = False
        if "placement_incompatible" not in reasons:
            reasons.append("placement_not_selected")

    # 4) audience matches request
    audience_ok = _audience_matches((ad_set_view or {}).get("audience") or {}, request_ctx)
    if not audience_ok:
        reasons.append("audience_mismatch")

    # 5) campaign schedule window (operations start/end)
    campaign_id = creative_row.get("campaign_id")
    from services.business_os.advertising import operations as _ops
    ops_row = _ops._get_ops_row(conn, campaign_id)
    campaign_schedule_ok = _window_active(
        (ops_row or {}).get("start_at"), (ops_row or {}).get("end_at"), now_dt)
    if not campaign_schedule_ok:
        reasons.append("campaign_schedule_inactive")

    # 6) ad-set schedule override window
    ad_set_schedule_ok = _window_active(
        (ad_set_view or {}).get("schedule_start_at"),
        (ad_set_view or {}).get("schedule_end_at"), now_dt)
    if not ad_set_schedule_ok:
        reasons.append("ad_set_schedule_inactive")

    # 7) frequency cap (derived from immutable impression log)
    freq = _freq.frequency_state(conn, campaign_id, subject_ref)
    frequency_ok = not freq.get("cap_reached")
    if not frequency_ok:
        reasons.append("frequency_cap_reached")

    # 8) creative media usable (re-validated authoritatively)
    media_ok = _media_usable(conn, creative_row)
    if not media_ok:
        reasons.append("media_unavailable")

    # 9) destination structurally valid (re-validated authoritatively)
    destination_ok = _destination_valid(conn, creative_row)
    if not destination_ok:
        reasons.append("destination_invalid")

    eligible = not reasons
    return {
        "creative_id": creative_id,
        "ad_set_id": creative_row.get("ad_set_id"),
        "campaign_id": campaign_id,
        "advertiser_user_id": advertiser_uid,
        "creative_version": creative_row.get("version"),
        "placement": placement,
        "eligible": eligible,
        # SEPARATE per-gate signals
        "hierarchy_ready": hierarchy_ready,
        "advertiser_eligible": advertiser_eligible,
        "placement_compatible": placement_compatible,
        "audience_ok": audience_ok,
        "campaign_schedule_ok": campaign_schedule_ok,
        "ad_set_schedule_ok": ad_set_schedule_ok,
        "frequency_ok": frequency_ok,
        "frequency_state": freq,
        "media_ok": media_ok,
        "destination_ok": destination_ok,
        "reasons": reasons,
        # snapshot of the readiness inputs for the delivery record (spec §1)
        "readiness_snapshot": {
            k: readiness.get(k) for k in (
                "campaign_review_approved", "campaign_funded",
                "campaign_operational_active", "ad_set_approved",
                "creative_approved", "placement_valid", "audience_valid",
                "campaign_review_status", "funding_status",
                "operational_status", "ad_set_status", "creative_status",
            )
        },
    }


def list_candidate_creatives(conn, placement: str) -> list:
    """Approved creatives whose type is compatible with the requested placement.

    This is a cheap pre-filter (approved + type match) — the full per-request gates
    run in ``evaluate``. Ordered deterministically so selection is reproducible.
    """
    compat_types = _c.PLACEMENT_CREATIVE_COMPAT.get(placement, set())
    if not compat_types:
        return []
    placeholders = ", ".join("?" for _ in compat_types)
    cur = conn.execute(
        "SELECT * FROM business_os_ad_creatives "
        f"WHERE status = 'approved' AND creative_type IN ({placeholders}) "
        "ORDER BY created_at ASC, creative_id ASC",
        tuple(sorted(compat_types)),
    )
    return [_svc._row_to_dict(r) for r in cur.fetchall()]
