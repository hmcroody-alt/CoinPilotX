"""PulseSoc Advertising OS — creative library (backend slice 2).

One place for an advertiser to see every asset and creative they own:
grouped overview with filters, per-creative detail with moderation history
and preview URLs, metadata editing (delegating to the existing editability
rules in pulse_ads_os.update_creative), and copy-to-campaign.

Performance numbers come from the real event tables
(pulse_ad_impressions / pulse_ad_clicks) or they are zero.
"""

from __future__ import annotations

from services import pulse_ads_os, pulse_ads_service, pulse_advertiser_portal
from services.pulse_ads_service import (
    PulseAdsError,
    audit_log,
    clean_json,
    clean_text,
    now_iso,
    row_to_dict,
    safe_int,
)

LIBRARY_FILTERS = ("all", "images", "videos", "posts")
_FILTER_CREATIVE_TYPES = {
    "images": {"image"},
    "videos": {"video", "reel", "live_replay"},
    # everything that is not an uploaded image/video render: text ads and
    # content-backed creatives promoting posts/listings/events.
    "posts": {"text", "post", "listing", "event", "hologram", "audio"},
}
_FILTER_ASSET_TYPES = {"images": {"image"}, "videos": {"video"}}

# Campaign states that still accept new creatives via copy.
_UNCOPYABLE_CAMPAIGN_STATUSES = {"archived", "deleted", "suspended", "completed"}


def _bucket_for_type(creative_type: str) -> str:
    creative_type = clean_text(creative_type, 40).lower()
    for bucket, types in _FILTER_CREATIVE_TYPES.items():
        if creative_type in types:
            return bucket
    return "posts"


def _owned_account_ids(conn, user_id) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT id FROM pulse_ad_accounts WHERE owner_user_id=? ORDER BY id", (user_id,))
    return [safe_int(row_to_dict(row).get("id"), 0) for row in cur.fetchall()]


def _metrics_by_creative(conn, creative_ids: list[int]) -> dict:
    """Impressions/clicks/CTR per creative from the real event tables."""
    metrics = {cid: {"impressions": 0, "clicks": 0, "ctr": 0.0} for cid in creative_ids}
    if not creative_ids:
        return metrics
    marks = ",".join("?" for _ in creative_ids)
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT creative_id, COUNT(*) AS n FROM pulse_ad_impressions WHERE creative_id IN ({marks}) GROUP BY creative_id",
            tuple(creative_ids),
        )
        for row in cur.fetchall():
            item = row_to_dict(row)
            metrics[safe_int(item.get("creative_id"), 0)]["impressions"] = safe_int(item.get("n"), 0)
    except Exception:
        pass
    try:
        cur.execute(
            f"SELECT creative_id, COUNT(*) AS n FROM pulse_ad_clicks WHERE creative_id IN ({marks}) GROUP BY creative_id",
            tuple(creative_ids),
        )
        for row in cur.fetchall():
            item = row_to_dict(row)
            metrics[safe_int(item.get("creative_id"), 0)]["clicks"] = safe_int(item.get("n"), 0)
    except Exception:
        pass
    for entry in metrics.values():
        if entry["impressions"]:
            entry["ctr"] = round(entry["clicks"] / entry["impressions"], 4)
    return metrics


def _policy_flags_by_creative(conn, creative_ids: list[int]) -> dict:
    flags: dict = {cid: [] for cid in creative_ids}
    if not creative_ids:
        return flags
    marks = ",".join("?" for _ in creative_ids)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT creative_id, flag_type, severity, details, created_at
            FROM pulse_ad_policy_flags WHERE creative_id IN ({marks})
            ORDER BY id DESC LIMIT 500
            """,
            tuple(creative_ids),
        )
        for row in cur.fetchall():
            item = row_to_dict(row)
            flags.setdefault(safe_int(item.get("creative_id"), 0), []).append({
                "flag_type": item.get("flag_type") or "",
                "severity": item.get("severity") or "",
                "details": item.get("details") or "",
                "created_at": item.get("created_at") or "",
            })
    except Exception:
        pass
    return flags


def _library_item(conn, creative: dict, metrics: dict, flags: list) -> dict:
    attached = pulse_ads_service.attach_creative_media(conn, creative)
    return {
        "id": attached.get("id"),
        "ad_account_id": attached.get("ad_account_id"),
        "creative_type": attached.get("creative_type") or "",
        "bucket": _bucket_for_type(attached.get("creative_type")),
        "title": attached.get("title") or "",
        "status": attached.get("status") or "",
        "moderation_status": attached.get("moderation_status") or "",
        "rejection_reason": attached.get("rejection_reason") or "",
        "policy_flags": flags,
        "campaign": {
            "campaign_id": attached.get("campaign_id"),
            "campaign_name": attached.get("campaign_name") or "",
            "campaign_status": attached.get("campaign_status") or "",
            "adset_id": safe_int(attached.get("adset_id"), 0) or None,
        },
        "media_url": attached.get("media_url") or "",
        "thumbnail_url": attached.get("thumbnail_url") or "",
        "playback_url": attached.get("playback_url") or "",
        "media_ready": bool(attached.get("media_ready")),
        "performance": metrics,
        "created_at": attached.get("created_at") or "",
        "updated_at": attached.get("updated_at") or "",
    }


def library_overview(conn, user_id, filter_kind: str = "all") -> dict:
    """All assets + creatives the user's ad accounts own, grouped and
    filterable (all / images / videos / posts), each with policy status,
    campaign usage and real performance."""
    filter_kind = clean_text(filter_kind, 20).lower() or "all"
    if filter_kind not in LIBRARY_FILTERS:
        raise PulseAdsError("filter must be one of all, images, videos, posts.")
    account_ids = _owned_account_ids(conn, user_id)
    if not account_ids:
        return {"filter": filter_kind, "creatives": [], "assets": [],
                "counts": {"all": 0, "images": 0, "videos": 0, "posts": 0}}
    marks = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT cr.*, c.campaign_name, c.status AS campaign_status
        FROM pulse_ad_creatives cr
        LEFT JOIN pulse_ad_campaigns c ON c.id=cr.campaign_id
        WHERE cr.ad_account_id IN ({marks})
        ORDER BY cr.id DESC LIMIT 200
        """,
        tuple(account_ids),
    )
    creatives = [row_to_dict(row) for row in cur.fetchall()]
    creative_ids = [safe_int(item.get("id"), 0) for item in creatives]
    metrics = _metrics_by_creative(conn, creative_ids)
    flags = _policy_flags_by_creative(conn, creative_ids)
    counts = {"all": len(creatives), "images": 0, "videos": 0, "posts": 0}
    items = []
    for creative in creatives:
        bucket = _bucket_for_type(creative.get("creative_type"))
        counts[bucket] += 1
        if filter_kind != "all" and bucket != filter_kind:
            continue
        cid = safe_int(creative.get("id"), 0)
        items.append(_library_item(conn, creative, metrics.get(cid, {"impressions": 0, "clicks": 0, "ctr": 0.0}), flags.get(cid, [])))
    assets = []
    if filter_kind in ("all", "images", "videos"):
        cur.execute(
            f"""
            SELECT * FROM pulse_ad_media_assets
            WHERE ad_account_id IN ({marks}) AND COALESCE(deleted_at,'')=''
            ORDER BY id DESC LIMIT 200
            """,
            tuple(account_ids),
        )
        allowed_types = _FILTER_ASSET_TYPES.get(filter_kind)
        for row in cur.fetchall():
            asset = row_to_dict(row)
            if allowed_types and clean_text(asset.get("media_type"), 40).lower() not in allowed_types:
                continue
            assets.append(pulse_ads_service._ad_asset_public(asset))
    return {"filter": filter_kind, "creatives": items, "assets": assets, "counts": counts}


def asset_detail(conn, user_id, creative_id) -> dict:
    """Everything the library knows about one creative: media previews,
    policy status + flags, campaign usage, real metrics, moderation history
    and appeal history."""
    creative = pulse_ads_service.get_creative(conn, user_id, creative_id)
    cur = conn.cursor()
    campaign = {}
    if safe_int(creative.get("campaign_id"), 0):
        cur.execute("SELECT id, campaign_name, status FROM pulse_ad_campaigns WHERE id=?", (creative.get("campaign_id"),))
        campaign = row_to_dict(cur.fetchone())
    metrics = _metrics_by_creative(conn, [creative_id]).get(creative_id, {"impressions": 0, "clicks": 0, "ctr": 0.0})
    flags = _policy_flags_by_creative(conn, [creative_id]).get(creative_id, [])
    history = []
    try:
        cur.execute(
            """
            SELECT status, reviewer_id, notes, risk_score, created_at, reviewed_at
            FROM pulse_ad_moderation_queue WHERE creative_id=? ORDER BY id DESC LIMIT 20
            """,
            (creative_id,),
        )
        history = [dict(row_to_dict(row), source="moderation_queue") for row in cur.fetchall()]
    except Exception:
        history = []
    try:
        cur.execute(
            """
            SELECT review_status, automated_review_status, human_review_status, review_reason,
                   reviewer_id, reviewed_at, created_at
            FROM pulse_ad_review_board WHERE creative_id=? ORDER BY id DESC LIMIT 20
            """,
            (creative_id,),
        )
        history.extend(dict(row_to_dict(row), source="review_board") for row in cur.fetchall())
    except Exception:
        pass
    appeals = []
    try:
        cur.execute("SELECT * FROM pulse_ad_appeals WHERE creative_id=? ORDER BY id DESC LIMIT 20", (creative_id,))
        appeals = [pulse_ads_os._appeal_public(row_to_dict(row)) for row in cur.fetchall()]
    except Exception:
        appeals = []
    item = _library_item(conn, creative, metrics, flags)
    item["body"] = creative.get("body") or ""
    item["headline"] = creative.get("headline") or ""
    item["primary_text"] = creative.get("primary_text") or ""
    item["call_to_action"] = creative.get("call_to_action") or ""
    item["destination_url"] = creative.get("destination_url") or ""
    item["campaign"] = {
        "campaign_id": campaign.get("id") or creative.get("campaign_id"),
        "campaign_name": campaign.get("campaign_name") or "",
        "campaign_status": campaign.get("status") or "",
        "adset_id": safe_int(creative.get("adset_id"), 0) or None,
    }
    item["previews"] = {
        "media_url": item.get("media_url"),
        "thumbnail_url": item.get("thumbnail_url"),
        "playback_url": item.get("playback_url"),
    }
    item["moderation_history"] = history
    item["appeals"] = appeals
    item["editable"] = clean_text(creative.get("status"), 40) in pulse_ads_os.EDITABLE_CREATIVE_STATUSES
    return item


def update_creative_metadata(conn, user_id, creative_id, payload: dict) -> dict:
    """Edit title/body/cta/headline/etc. Delegates to the single source of
    editability truth (pulse_ads_os.update_creative): only draft or rejected
    creatives are editable, and every edit resets moderation to draft so the
    creative must be resubmitted for review."""
    return pulse_ads_os.update_creative(conn, user_id, creative_id, payload)


def duplicate_creative_to_campaign(conn, user_id, creative_id, campaign_id, adset_id=None) -> dict:
    """Copy a creative into another campaign as a fresh draft.

    Ownership is enforced on both sides; the copy starts at status/moderation
    'draft' and must pass review on its own. Cross-account copies are refused
    because media assets are account-scoped.
    """
    source = pulse_ads_service.get_creative(conn, user_id, creative_id)
    target = pulse_ads_service._owned_campaign(conn, user_id, campaign_id)
    if safe_int(source.get("ad_account_id"), 0) != safe_int(target.get("ad_account_id"), 0):
        raise PulseAdsError("Creatives can only be copied between campaigns on the same ad account.", 400)
    target_status = clean_text(target.get("status"), 40).lower()
    if target_status in _UNCOPYABLE_CAMPAIGN_STATUSES:
        raise PulseAdsError(f"Campaign is {target_status} and no longer accepts creatives.", 409)
    adset_id = safe_int(adset_id, 0)
    has_adset_column = pulse_advertiser_portal._has_column(conn, "pulse_ad_creatives", "adset_id")
    cur = conn.cursor()
    if adset_id:
        if not has_adset_column:
            raise PulseAdsError("Ad sets are not available on this deployment.", 400)
        cur.execute("SELECT id FROM pulse_ad_adsets WHERE id=? AND campaign_id=?", (adset_id, campaign_id))
        if not cur.fetchone():
            raise PulseAdsError("Ad set not found on the target campaign.", 404)
    now = now_iso()
    columns = [
        "ad_account_id", "campaign_id", "creative_type", "title", "body", "media_url",
        "thumbnail_url", "destination_url", "media_asset_id", "thumbnail_asset_id",
        "media_ready", "media_metadata_json", "call_to_action", "content_ref_type",
        "content_ref_id", "headline", "primary_text", "status", "moderation_status",
        "rejection_reason", "metadata_json", "compatibility_json",
        "moderation_history_json", "created_at", "updated_at",
    ]
    values = [
        safe_int(target.get("ad_account_id"), 0),
        campaign_id,
        source.get("creative_type"),
        clean_text(f"{source.get('title')} copy", 100),
        source.get("body"),
        source.get("media_url"),
        source.get("thumbnail_url"),
        source.get("destination_url"),
        source.get("media_asset_id"),
        source.get("thumbnail_asset_id"),
        source.get("media_ready") or 0,
        source.get("media_metadata_json") or "{}",
        source.get("call_to_action"),
        source.get("content_ref_type") or "",
        safe_int(source.get("content_ref_id"), 0),
        source.get("headline") or "",
        source.get("primary_text") or "",
        "draft",
        "draft",
        "",
        source.get("metadata_json") or "{}",
        source.get("compatibility_json") or "{}",
        clean_json({"source_creative_id": creative_id, "copied_to_campaign_id": campaign_id, "copied_at": now}),
        now,
        now,
    ]
    if has_adset_column:
        columns.insert(2, "adset_id")
        values.insert(2, adset_id or None)
    marks = ",".join("?" for _ in columns)
    cur.execute(
        f"INSERT INTO pulse_ad_creatives ({', '.join(columns)}) VALUES ({marks})",
        tuple(values),
    )
    new_id = cur.lastrowid
    audit_log(
        conn, user_id, "ad_creative_copied_to_campaign", "pulse_ad_creatives", new_id,
        before={"source_creative_id": creative_id},
        after={"campaign_id": campaign_id, "adset_id": adset_id or None},
    )
    conn.commit()
    return pulse_ads_service.get_creative(conn, user_id, new_id)
