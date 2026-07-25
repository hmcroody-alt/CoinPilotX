"""Business OS — Advertising vertical, slice-6 hierarchy-readiness derivation.

This module answers one question — *is a creative's whole hierarchy delivery-ready?*
— WITHOUT ever storing the answer. Readiness is DERIVED live, on every call, from
the separate authoritative states that already live in their own tables:

    campaign review status   (business_os_ad_campaigns.status)
    campaign funding status  (business_os_ad_campaign_funding.funding_status)
    campaign operational     (business_os_ad_campaign_operations.operational_status)
    ad-set review status     (business_os_ad_sets.status)
    creative review status   (business_os_ad_creatives.status)
    placement validity       (targeting.placements_are_valid on the ad set)
    audience validity        (targeting.audience_is_valid on the ad set)

Spec §8 is explicit: the readiness response keeps every input SEPARATE and never
merges them into a single stored boolean. ``hierarchy_ready`` is the live AND of
the inputs and ``denial_reasons`` names precisely what is blocking. Nothing here
delivers, auctions, spends, or writes — it is a pure read/compose over existing
services, so a caller can trust it never mutates state.

Parent-child integrity (spec §8) falls out of this composition: a creative whose
parent ad set is rejected/unapproved, or whose campaign is archived/cancelled,
simply reports ``hierarchy_ready = false`` with the specific reason — the review
history of every object is left completely intact.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising import funding as _funding
from services.business_os.advertising import operations as _ops
from services.business_os.advertising import ad_sets as _adset
from services.business_os.advertising import creatives as _creative
from services.business_os.advertising.service import AdvertisingError


def _compose(conn, creative_row: dict) -> dict:
    """Given an authoritative creative row, compose the live readiness view from
    all the SEPARATE inputs. Assumes ownership was already enforced by the caller.
    """
    campaign_id = creative_row.get("campaign_id")
    ad_set_id = creative_row.get("ad_set_id")

    # --- campaign: review / funding / operational (three separate states) ---
    campaign = _svc.get_campaign(campaign_id, conn=conn)  # trusted read
    campaign_review_status = (campaign or {}).get("status")
    campaign_review_approved = campaign_review_status == "approved"
    campaign_archived = campaign_review_status == "archived"

    funding_row = _funding._get_funding_row(conn, campaign_id) if campaign else None
    funding_status = (funding_row or {}).get("funding_status") or "unfunded"
    campaign_funded = funding_status == "funded"

    ops_row = _ops._get_ops_row(conn, campaign_id) if campaign else None
    operational_status = (ops_row or {}).get("operational_status") or "inactive"
    campaign_operational_active = operational_status == "active"

    # --- ad set: review + placement/audience validity ------------------------
    ad_set_row = _adset._get_row(conn, ad_set_id, requester_user_id=None)
    ad_set_view = _adset._ad_set_public(ad_set_row) if ad_set_row else None
    ad_set_status = (ad_set_row or {}).get("status")
    ad_set_approved = ad_set_status == "approved"
    ad_set_archived = ad_set_status == "archived"
    placement_valid = bool(ad_set_view and ad_set_view.get("placement_valid"))
    audience_valid = bool(ad_set_view and ad_set_view.get("audience_valid"))

    # --- creative review -----------------------------------------------------
    creative_status = creative_row.get("status")
    creative_approved = creative_status == "approved"

    # --- derive denial reasons, each keyed to its OWN separate input ---------
    denial_reasons: list = []
    if not campaign_review_approved:
        denial_reasons.append("campaign_not_approved")
    if campaign_archived:
        denial_reasons.append("campaign_archived")
    if not campaign_funded:
        denial_reasons.append("campaign_not_funded")
    if not campaign_operational_active:
        denial_reasons.append("campaign_not_operational_active")
    if not ad_set_approved:
        denial_reasons.append("ad_set_not_approved")
    if ad_set_archived:
        denial_reasons.append("ad_set_archived")
    if not creative_approved:
        denial_reasons.append("creative_not_approved")
    if not placement_valid:
        denial_reasons.append("placement_invalid")
    if not audience_valid:
        denial_reasons.append("audience_invalid")

    hierarchy_ready = not denial_reasons

    return {
        "creative_id": creative_row.get("creative_id"),
        "ad_set_id": ad_set_id,
        "campaign_id": campaign_id,
        "advertiser_user_id": creative_row.get("advertiser_user_id"),
        # SEPARATE inputs — never collapsed into one another.
        "campaign_review_approved": bool(campaign_review_approved),
        "campaign_funded": bool(campaign_funded),
        "campaign_operational_active": bool(campaign_operational_active),
        "ad_set_approved": bool(ad_set_approved),
        "creative_approved": bool(creative_approved),
        "placement_valid": bool(placement_valid),
        "audience_valid": bool(audience_valid),
        # Raw status strings for observability (not authorities on their own).
        "campaign_review_status": campaign_review_status,
        "funding_status": funding_status,
        "operational_status": operational_status,
        "ad_set_status": ad_set_status,
        "creative_status": creative_status,
        # Derived-live composite + the precise blockers.
        "hierarchy_ready": bool(hierarchy_ready),
        "denial_reasons": denial_reasons,
        # This slice authorizes FUTURE delivery only; it never delivers.
        "delivering": False,
    }


def hierarchy_readiness(creative_id: str, *,
                        requester_user_id: Optional[Any] = None,
                        conn=None) -> dict:
    """Derive, live, whether ``creative_id``'s full hierarchy is delivery-ready.

    Ownership is enforced when ``requester_user_id`` is supplied (a non-owner gets
    404, existence not leaked); pass ``requester_user_id=None`` only from trusted
    admin paths. The returned dict keeps every input separate (spec §8) and is
    never persisted.
    """
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _creative._get_row(conn, creative_id, requester_user_id=requester_user_id)
        if row is None:
            raise AdvertisingError("Creative not found.", 404, "not_found")
        return _compose(conn, row)
    finally:
        if owned:
            conn.close()
