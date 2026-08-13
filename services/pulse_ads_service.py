"""PulseSoc Ads foundation service.

This module owns campaign eligibility, moderation state, privacy-safe tracking,
and payload sanitization for PulseSoc sponsored placements. It deliberately does
not expose private targeting data to clients.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from services import ad_policy_engine


PLACEMENTS = [
    ("feed_inline", "Feed inline signal", "all", "feed", 6),
    ("feed_side_ufo_desktop", "Desktop side signal", "desktop", "side", 4),
    ("feed_inline_ufo_mobile", "Mobile inline signal", "mobile", "feed", 4),
    ("pulse_network_hologram", "Pulse Network hologram", "all", "network", 4),
    ("creator_sidebar_signal", "Creator sidebar signal", "desktop", "sidebar", 3),
    ("marketplace_sponsor", "Marketplace sponsor", "all", "marketplace", 5),
    ("pulse_radio_sponsor", "Pulse Radio sponsor", "all", "radio", 5),
    ("video_pre_roll", "Video pre-roll", "all", "video", 3),
    ("status_interstitial", "Status interstitial", "mobile", "status", 3),
    ("search_sponsored_result", "Search sponsored result", "all", "search", 4),
    ("dashboard_sponsor", "Dashboard sponsor", "all", "dashboard", 3),
    ("profile_sponsor", "Profile sponsor", "all", "profile", 3),
]

CONTEXT_PLACEMENTS = {
    "home": ["feed_inline", "feed_side_ufo_desktop", "feed_inline_ufo_mobile", "pulse_network_hologram"],
    "feed": ["feed_inline", "feed_side_ufo_desktop", "feed_inline_ufo_mobile"],
    "marketplace": ["marketplace_sponsor"],
    "radio": ["pulse_radio_sponsor"],
    "video": ["video_pre_roll"],
    "status": ["status_interstitial"],
    "search": ["search_sponsored_result"],
    "dashboard": ["dashboard_sponsor"],
    "profile": ["profile_sponsor"],
    "creator": ["creator_sidebar_signal"],
}

ACTIVE_CAMPAIGN_STATUS = {"active"}
APPROVED_CREATIVE_STATUS = {"approved"}
VALID_CREATIVE_TYPES = {
    "image", "video", "text", "hologram", "audio",
    # Content-backed creative types: the ad IS an existing piece of PulseSoc
    # content (identified by content_ref_type/content_ref_id on the creative).
    "listing", "post", "reel", "event", "live_replay",
}

# Creative types whose media comes from an existing piece of owned content
# rather than an uploaded ad media asset.
CONTENT_CREATIVE_TYPES = {"listing", "post", "reel", "event", "live_replay"}

# Which surfaces each content-backed creative type may serve on. Conservative
# on purpose: listings belong on commerce + feed + search surfaces, posts and
# events on feed surfaces, reels and live replays on feed + video surfaces.
CONTENT_CREATIVE_PLACEMENTS = {
    "listing": {"feed_inline", "feed_inline_ufo_mobile", "marketplace_sponsor", "search_sponsored_result"},
    "post": {"feed_inline", "feed_side_ufo_desktop", "feed_inline_ufo_mobile"},
    "event": {"feed_inline", "feed_side_ufo_desktop", "feed_inline_ufo_mobile"},
    "reel": {"feed_inline", "feed_inline_ufo_mobile", "video_pre_roll"},
    "live_replay": {"feed_inline", "feed_inline_ufo_mobile", "video_pre_roll"},
}
VALID_OBJECTIVES = {
    "awareness",
    "brand_awareness",
    "traffic",
    "website_traffic",
    "engagement",
    "creator_growth",
    "creator_promotion",
    "marketplace",
    "marketplace_sales",
    "radio",
    "pulse_radio",
    "music_promotion",
    "video_promotion",
    "app_promotion",
    "event_promotion",
    "hologram_campaign",
    "video_views",
    "messages",
    "app_activity",
    "lead_generation",
    "profile_growth",
    "live_promotion",
}

# The eleven canonical objectives the Advertising OS clients render. Every value
# in VALID_OBJECTIVES maps onto exactly one of these; legacy synonyms keep
# working on write and are normalized on read via `canonical_objective`.
CANONICAL_OBJECTIVES = {
    "awareness",
    "engagement",
    "video_views",
    "website_traffic",
    "messages",
    "marketplace_sales",
    "app_activity",
    "lead_generation",
    "event_promotion",
    "profile_growth",
    "live_promotion",
}

OBJECTIVE_SYNONYMS = {
    "brand_awareness": "awareness",
    "traffic": "website_traffic",
    "marketplace": "marketplace_sales",
    "creator_growth": "profile_growth",
    "creator_promotion": "profile_growth",
    "app_promotion": "app_activity",
    "video_promotion": "video_views",
    "radio": "awareness",
    "pulse_radio": "awareness",
    "music_promotion": "awareness",
    "hologram_campaign": "awareness",
}


def canonical_objective(value) -> str:
    """Map any stored/submitted objective to one of the 11 canonical objectives.

    Unknown values fall back to 'awareness' rather than leaking a raw string the
    clients have no rendering for.
    """
    objective = str(value or "").strip().lower()
    if objective in CANONICAL_OBJECTIVES:
        return objective
    return OBJECTIVE_SYNONYMS.get(objective, "awareness")
VALID_EVENTS = {
    "viewability",
    "conversion",
    "hide",
    "report",
    "save",
    "dismiss",
    "video_start",
    "video_25",
    "video_50",
    "video_75",
    "video_complete",
    "audio_start",
    "audio_complete",
    "mute",
    "unmute",
    "error",
}
VALID_BUDGET_TYPES = {"daily", "lifetime"}
VALID_ACCOUNT_STATUS = {"draft", "pending_verification", "active", "suspended"}
VALID_DEVICE_TYPES = {"desktop", "mobile", "tablet", "all"}
DELIVERY_TOKEN_TTL_SECONDS = 60 * 60 * 6

PLACEMENT_METADATA = {
    key: {
        "placement_key": key,
        "display_name": name,
        "device_type": device_type,
        "placement_type": placement_type,
        "max_frequency": max_frequency,
        "priority": 6 if placement_type in {"feed", "marketplace", "radio", "search"} else 4,
        "card_style": {
            "feed": "signal-card",
            "side": "ufo-side",
            "network": "hologram",
            "sidebar": "creator-signal",
            "marketplace": "marketplace-sponsored",
            "radio": "radio-sponsor",
            "video": "video-pre-roll",
            "status": "status-interstitial",
            "search": "search-result",
            "dashboard": "dashboard-sponsor",
            "profile": "profile-sponsor",
        }.get(placement_type, "signal-card"),
        "supported_creative_types": ["image", "video", "text", "hologram", "audio"] + sorted(
            content_type
            for content_type, allowed_keys in CONTENT_CREATIVE_PLACEMENTS.items()
            if key in allowed_keys
        ),
    }
    for key, name, device_type, placement_type, max_frequency in PLACEMENTS
}

TEXT_LIMITS = {
    "business_name": 120,
    "business_email": 160,
    "business_phone": 40,
    "business_website": 240,
    "business_type": 80,
    "campaign_name": 120,
    "objective": 40,
    "title": 100,
    "body": 240,
    "headline": 100,
    "primary_text": 500,
    "call_to_action": 40,
    "rejection_reason": 400,
    "notes": 600,
}


class PulseAdsError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def clean_text(value, max_len: int = 240) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def clean_json(value, max_len: int = 6000) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = {"value": clean_text(value, 1000)}
    else:
        parsed = value
    encoded = json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))
    return encoded[:max_len]


def safe_int(value, default=0, minimum=None, maximum=None) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def hash_value(value: str) -> str:
    if not value:
        return ""
    salt = os.getenv("ANALYTICS_SALT", "coinpilotxai-inc")
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


def _ads_secret() -> str:
    return os.getenv("PULSE_ADS_DELIVERY_SECRET") or os.getenv("SESSION_SECRET") or os.getenv("FLASK_SECRET_KEY") or "pulse-ads-local-secret"


def _compact_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sign_payload(payload: dict) -> str:
    return hmac.new(_ads_secret().encode("utf-8"), _compact_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def _delivery_subject(viewer_user_id=None, session_id="") -> str:
    if viewer_user_id:
        return f"user:{hash_value(str(viewer_user_id))[:24]}"
    return f"session:{hash_value(str(session_id or 'anon'))[:24]}"


def make_delivery_token(creative_id, campaign_id, placement_key, viewer_user_id=None, session_id="") -> tuple[str, str]:
    issued_at = int(time.time())
    nonce = hashlib.sha256(f"{issued_at}:{creative_id}:{campaign_id}:{placement_key}:{session_id}:{os.urandom(8).hex()}".encode("utf-8")).hexdigest()[:24]
    payload = {
        "cid": safe_int(creative_id, minimum=1),
        "cmp": safe_int(campaign_id, minimum=1),
        "pl": clean_text(placement_key, 80),
        "sub": _delivery_subject(viewer_user_id, session_id),
        "iat": issued_at,
        "exp": issued_at + DELIVERY_TOKEN_TTL_SECONDS,
        "nonce": nonce,
    }
    token = f"{_compact_json(payload)}.{_sign_payload(payload)}"
    return token, nonce


def verify_delivery_token(token: str, creative_id, campaign_id, placement_key, viewer_user_id=None, session_id="") -> dict:
    raw = str(token or "")
    if "." not in raw or len(raw) > 1200:
        raise PulseAdsError("Ad delivery token is required.", 403)
    payload_raw, signature = raw.rsplit(".", 1)
    try:
        payload = json.loads(payload_raw)
    except Exception as exc:
        raise PulseAdsError("Invalid ad delivery token.", 403) from exc
    expected = _sign_payload(payload)
    if not hmac.compare_digest(expected, signature):
        raise PulseAdsError("Invalid ad delivery token.", 403)
    if safe_int(payload.get("exp"), 0) < int(time.time()):
        raise PulseAdsError("Ad delivery token expired.", 403)
    if safe_int(payload.get("cid"), 0) != safe_int(creative_id, minimum=1):
        raise PulseAdsError("Ad delivery token does not match creative.", 403)
    if safe_int(payload.get("cmp"), 0) != safe_int(campaign_id, minimum=1):
        raise PulseAdsError("Ad delivery token does not match campaign.", 403)
    if clean_text(payload.get("pl"), 80) != clean_text(placement_key, 80):
        raise PulseAdsError("Ad delivery token does not match placement.", 403)
    if payload.get("sub") != _delivery_subject(viewer_user_id, session_id):
        raise PulseAdsError("Ad delivery token does not match viewer.", 403)
    return payload


def validate_destination_url(url: str, required: bool = True) -> str:
    cleaned = clean_text(url, 500)
    if not cleaned:
        if required:
            raise PulseAdsError("Destination URL is required.")
        return ""
    lowered = cleaned.lower()
    if any(lowered.startswith(prefix) for prefix in ("javascript:", "data:", "file:", "vbscript:")):
        raise PulseAdsError("Unsafe destination URL.")
    if cleaned.startswith("/") and not cleaned.startswith("//"):
        parsed_path = urlparse(cleaned)
        if parsed_path.scheme or parsed_path.netloc:
            raise PulseAdsError("Unsafe destination URL.")
        # Any site-internal path is a valid ad destination — content-backed ads
        # land on posts, reels, listings, events, and profiles, which live
        # outside the historical /pulse/ prefix. Administrative and API paths
        # stay off-limits.
        path_lower = parsed_path.path.lower()
        blocked_prefixes = ("/admin", "/api", "/pulse/admin", "/pulse/api", "/internal")
        if path_lower.startswith("/_") or any(
            path_lower == prefix or path_lower.startswith(prefix + "/") for prefix in blocked_prefixes
        ):
            raise PulseAdsError("Internal ad destination is not allowed.")
        return cleaned
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise PulseAdsError("Destination URL must be http, https, or a safe PulseSoc path.")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        raise PulseAdsError("Local destination URLs are not allowed.")
    return cleaned


def validate_media_url(url: str) -> str:
    cleaned = clean_text(url, 500)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if any(lowered.startswith(prefix) for prefix in ("javascript:", "data:", "file:", "vbscript:")):
        raise PulseAdsError("Unsafe media URL.")
    if cleaned.startswith("/") and not cleaned.startswith("//"):
        parsed_path = urlparse(cleaned)
        if parsed_path.scheme or parsed_path.netloc:
            raise PulseAdsError("Unsafe media URL.")
        if not parsed_path.path.startswith("/static/uploads/pulse_ads/"):
            raise PulseAdsError("Internal ad media must use approved ads storage.")
        return cleaned
    return validate_destination_url(cleaned, required=False)


AD_MEDIA_REQUIRED_TYPES = {
    "image": {"image", "gif"},
    "video": {"video"},
    "audio": {"audio"},
}


def _asset_type_allowed(creative_type: str, media_type: str) -> bool:
    required = AD_MEDIA_REQUIRED_TYPES.get(clean_text(creative_type, 30).lower())
    if not required:
        return True
    return clean_text(media_type, 40).lower() in required


# Content references a creative may point at, mapped onto the content types the
# promotions module already knows how to resolve and authorize.
CONTENT_REF_TYPES = {"post", "reel", "video", "event", "listing", "live_replay"}
_CONTENT_REF_PROMOTION_TYPES = {
    "post": "post",
    "reel": "reel",
    "video": "video",
    "event": "event",
    "listing": "marketplace_listing",
    # Finalized replays live in pulse_videos (source_type live/replay); the
    # promotions module resolves + authorizes them as "live_replay" rows.
    "live_replay": "live_replay",
}


def resolve_content_ref(conn, owner_user_id, content_ref_type, content_ref_id) -> dict:
    """Resolve a creative's content reference, enforcing ownership.

    Returns the content row (owner, title, body, status, moderation) plus the
    derived destination_url, media_url and thumbnail_url for that content.
    Raises PulseAdsError when the reference is unknown, missing, or owned by
    someone else — an advertiser can only run ads for their own content.
    """
    from services import pulsesoc_promotions

    ref_type = clean_text(content_ref_type, 30).lower()
    if ref_type not in CONTENT_REF_TYPES:
        raise PulseAdsError("Unsupported content reference type.")
    ref_id = safe_int(content_ref_id, 0)
    if ref_id <= 0:
        raise PulseAdsError("Content reference id is required.")
    promo_type = _CONTENT_REF_PROMOTION_TYPES[ref_type]
    content = pulsesoc_promotions._query_content(conn, promo_type, ref_id)
    if not content:
        raise PulseAdsError("Referenced content was not found.", 404)
    if safe_int(content.get("owner_user_id")) != safe_int(owner_user_id):
        raise PulseAdsError("You can only advertise content you own.", 403)
    content = dict(content)
    content["content_ref_type"] = ref_type
    content["content_ref_id"] = ref_id
    content["destination_url"] = pulsesoc_promotions._destination(promo_type, ref_id)
    media_url, thumbnail_url = _content_ref_media(conn, ref_type, ref_id)
    content["media_url"] = media_url
    content["thumbnail_url"] = thumbnail_url
    return content


def _content_ref_media(conn, ref_type: str, ref_id: int) -> tuple[str, str]:
    """Best-effort media/thumbnail for a content reference. Never raises."""
    cur = conn.cursor()
    try:
        if ref_type == "reel":
            cur.execute("SELECT video_url, poster_url FROM pulse_reels WHERE id=?", (ref_id,))
            row = row_to_dict(cur.fetchone())
            return clean_text(row.get("video_url"), 1000), clean_text(row.get("poster_url"), 1000)
        if ref_type == "video":
            cur.execute("SELECT COALESCE(playback_url, media_url) AS media_url, thumbnail_url FROM pulse_videos WHERE id=?", (ref_id,))
            row = row_to_dict(cur.fetchone())
            return clean_text(row.get("media_url"), 1000), clean_text(row.get("thumbnail_url"), 1000)
        if ref_type == "listing":
            cur.execute("SELECT COALESCE(cover_image_url, media_url) AS media_url FROM marketplace_listings WHERE id=?", (ref_id,))
            row = row_to_dict(cur.fetchone())
            url = clean_text(row.get("media_url"), 1000)
            return url, url
        if ref_type == "live_replay":
            cur.execute(
                "SELECT COALESCE(playback_url, media_url) AS media_url, thumbnail_url FROM pulse_videos WHERE id=? AND source_type IN ('live','replay')",
                (ref_id,),
            )
            row = row_to_dict(cur.fetchone())
            return clean_text(row.get("media_url"), 1000), clean_text(row.get("thumbnail_url"), 1000)
    except Exception:
        return "", ""
    return "", ""


def _ad_asset_public(asset: dict) -> dict:
    item = dict(asset or {})
    return {
        "id": item.get("id"),
        "asset_id": item.get("asset_id") or item.get("id"),
        "asset_kind": item.get("asset_kind") or "",
        "media_type": item.get("media_type") or "",
        "mime_type": item.get("mime_type") or "",
        "width": safe_int(item.get("width"), 0),
        "height": safe_int(item.get("height"), 0),
        "duration_seconds": float(item.get("duration_seconds") or 0),
        "file_size": safe_int(item.get("file_size"), 0),
        "public_url": item.get("playback_url") or item.get("public_url") or "",
        "thumbnail_url": item.get("thumbnail_url") or item.get("poster_url") or item.get("public_url") or "",
        "poster_url": item.get("poster_url") or item.get("thumbnail_url") or "",
        "playback_url": item.get("playback_url") or "",
        "moderation_status": item.get("moderation_status") or "pending",
        "security_status": item.get("security_status") or "passed",
    }


def _owned_ad_media_asset(conn, owner_user_id, ad_account_id, asset_id, *, allowed_kinds=None) -> dict:
    cur = conn.cursor()
    identifier = safe_int(asset_id, 0)
    if identifier:
        cur.execute(
            """
            SELECT * FROM pulse_ad_media_assets
            WHERE id=? AND owner_user_id=? AND ad_account_id=? AND COALESCE(deleted_at, '')=''
            """,
            (identifier, owner_user_id, ad_account_id),
        )
    else:
        cur.execute(
            """
            SELECT * FROM pulse_ad_media_assets
            WHERE asset_id=? AND owner_user_id=? AND ad_account_id=? AND COALESCE(deleted_at, '')=''
            """,
            (clean_text(asset_id, 80), owner_user_id, ad_account_id),
        )
    asset = row_to_dict(cur.fetchone())
    if not asset:
        raise PulseAdsError("Uploaded ad media was not found.", 404)
    if allowed_kinds and clean_text(asset.get("asset_kind"), 40) not in allowed_kinds:
        raise PulseAdsError("Uploaded media is not compatible with this creative field.")
    return asset


def create_ad_media_asset(conn, owner_user_id, ad_account_id, media: dict, asset_kind="creative_media") -> dict:
    _owned_account(conn, owner_user_id, ad_account_id)
    asset_kind = clean_text(asset_kind or "creative_media", 40)
    if asset_kind not in {"creative_media", "thumbnail", "companion_image"}:
        raise PulseAdsError("Unsupported ad media asset type.")
    media_id = safe_int((media or {}).get("id"), 0)
    if media_id <= 0:
        raise PulseAdsError("Uploaded media record is required.")
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM chat_media_uploads
        WHERE id=? AND uploader_user_id=? AND COALESCE(deleted_at, '')=''
        LIMIT 1
        """,
        (media_id, owner_user_id),
    )
    upload = row_to_dict(cur.fetchone())
    if not upload:
        raise PulseAdsError("Uploaded media does not belong to this advertiser.", 403)
    media_type = clean_text(media.get("media_type") or upload.get("media_type"), 40).lower()
    if asset_kind in {"thumbnail", "companion_image"} and media_type not in {"image", "gif"}:
        raise PulseAdsError("Custom thumbnails must be uploaded as images.")
    public_url = clean_text(media.get("valid_url") or media.get("media_url") or upload.get("media_url"), 1000)
    if not public_url:
        raise PulseAdsError("Uploaded media is not ready yet.")
    storage_key = clean_text(media.get("storage_key") or upload.get("storage_key"), 600)
    checksum = hashlib.sha256(f"{owner_user_id}:{media_id}:{storage_key}:{public_url}".encode("utf-8")).hexdigest()
    now = now_iso()
    metadata = {
        "source_media_id": media_id,
        "context_type": clean_text(upload.get("context_type"), 120),
        "processing_status": clean_text(media.get("processing_status") or upload.get("processing_status") or "ready", 80),
    }
    cur.execute(
        """
        INSERT INTO pulse_ad_media_assets
        (asset_id, owner_user_id, ad_account_id, media_upload_id, asset_kind, media_type, storage_provider, storage_key,
         public_url, thumbnail_url, poster_url, playback_url, mime_type, width, height, duration_seconds, file_size,
         checksum, moderation_status, security_status, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'passed', ?, ?, ?)
        """,
        (
            f"adma_{checksum[:24]}",
            owner_user_id,
            ad_account_id,
            media_id,
            asset_kind,
            media_type,
            clean_text(media.get("storage_provider") or upload.get("storage_provider") or "local", 40),
            storage_key,
            public_url,
            clean_text(media.get("thumbnail_url") or upload.get("thumbnail_url"), 1000),
            clean_text(media.get("poster_url") or upload.get("poster_url") or media.get("thumbnail_url") or upload.get("thumbnail_url"), 1000),
            clean_text(media.get("playback_url") or upload.get("playback_url"), 1000),
            clean_text(media.get("mime_type") or upload.get("mime_type"), 120),
            safe_int(media.get("width") or upload.get("width"), 0),
            safe_int(media.get("height") or upload.get("height"), 0),
            float(media.get("duration") or media.get("duration_seconds") or upload.get("duration_seconds") or 0),
            safe_int(media.get("file_size_bytes") or media.get("file_size") or upload.get("file_size_bytes"), 0),
            checksum,
            clean_json(metadata),
            now,
            now,
        ),
    )
    asset_id = cur.lastrowid
    audit_log(conn, owner_user_id, "ad_media_asset_uploaded", "pulse_ad_media_assets", asset_id, after={"asset_kind": asset_kind, "media_type": media_type})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_media_assets WHERE id=?", (asset_id,))
    return _ad_asset_public(row_to_dict(cur.fetchone()))


def delete_ad_media_asset(conn, owner_user_id, ad_account_id, asset_id) -> dict:
    asset = _owned_ad_media_asset(conn, owner_user_id, ad_account_id, asset_id)
    cur = conn.cursor()
    cur.execute("SELECT id FROM pulse_ad_creatives WHERE media_asset_id=? OR thumbnail_asset_id=? LIMIT 1", (asset.get("id"), asset.get("id")))
    if row_to_dict(cur.fetchone()):
        raise PulseAdsError("Media already attached to a creative cannot be deleted. Replace or delete the draft creative first.", 409)
    now = now_iso()
    cur.execute("UPDATE pulse_ad_media_assets SET deleted_at=?, updated_at=? WHERE id=?", (now, now, asset.get("id")))
    audit_log(conn, owner_user_id, "ad_media_asset_deleted", "pulse_ad_media_assets", asset.get("id"), before=_ad_asset_public(asset), after={"deleted_at": now})
    conn.commit()
    return {"ok": True, "asset_id": asset.get("id"), "deleted": True}


def seed_placements(cur) -> None:
    now = now_iso()
    for key, name, device_type, placement_type, max_frequency in PLACEMENTS:
        cur.execute("SELECT id FROM pulse_ad_placements WHERE placement_key=?", (key,))
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO pulse_ad_placements
            (placement_key, display_name, device_type, placement_type, is_active, max_frequency, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (key, name, device_type, placement_type, max_frequency, now, now),
        )
    for key, meta in PLACEMENT_METADATA.items():
        try:
            cur.execute(
                """
                UPDATE pulse_ad_placements
                SET priority=?,
                    supported_creative_types=?,
                    card_style=?
                WHERE placement_key=?
                """,
                (
                    meta["priority"],
                    ",".join(meta["supported_creative_types"]),
                    meta["card_style"],
                    key,
                ),
            )
        except Exception:
            pass


def platform_ads_enabled(conn) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT setting_value FROM pulse_ad_platform_settings WHERE setting_key='ads_enabled'")
    row = cur.fetchone()
    value = row_to_dict(row).get("setting_value") if row else None
    return str(value or "true").lower() not in {"0", "false", "off", "disabled"}


def set_kill_switch(conn, enabled: bool, actor_user_id=None) -> dict:
    now = now_iso()
    cur = conn.cursor()
    value = "true" if enabled else "false"
    cur.execute("SELECT setting_value FROM pulse_ad_platform_settings WHERE setting_key='ads_enabled'")
    before = row_to_dict(cur.fetchone())
    if before:
        cur.execute(
            "UPDATE pulse_ad_platform_settings SET setting_value=?, updated_by=?, updated_at=? WHERE setting_key='ads_enabled'",
            (value, actor_user_id, now),
        )
    else:
        cur.execute(
            "INSERT INTO pulse_ad_platform_settings (setting_key, setting_value, updated_by, updated_at) VALUES ('ads_enabled', ?, ?, ?)",
            (value, actor_user_id, now),
        )
    audit_log(conn, actor_user_id, "ads_kill_switch_update", "pulse_ad_platform_settings", "ads_enabled", before, {"setting_value": value})
    conn.commit()
    return {"ads_enabled": enabled}


def audit_log(conn, actor_user_id, action, entity_type, entity_id, before=None, after=None, ip_hash="", user_agent_hash="") -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_audit_logs
        (actor_user_id, action, entity_type, entity_id, before_json, after_json, ip_hash, user_agent_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_user_id,
            clean_text(action, 80),
            clean_text(entity_type, 80),
            str(entity_id or ""),
            clean_json(before or {}),
            clean_json(after or {}),
            clean_text(ip_hash, 128),
            clean_text(user_agent_hash, 128),
            now_iso(),
        ),
    )


def _owned_account(conn, owner_user_id, account_id) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=? AND owner_user_id=?", (account_id, owner_user_id))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise PulseAdsError("Ad account not found.", 404)
    return account


def _owned_campaign(conn, owner_user_id, campaign_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.* FROM pulse_ad_campaigns c
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        WHERE c.id=? AND a.owner_user_id=?
        """,
        (campaign_id, owner_user_id),
    )
    campaign = row_to_dict(cur.fetchone())
    if not campaign:
        raise PulseAdsError("Campaign not found.", 404)
    campaign["objective_canonical"] = canonical_objective(campaign.get("objective"))
    return campaign


def create_ad_account(conn, owner_user_id, payload: dict) -> dict:
    business_name = clean_text(payload.get("business_name"), TEXT_LIMITS["business_name"])
    if not business_name:
        raise PulseAdsError("Business name is required.")
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_accounts
        (owner_user_id, business_name, business_email, business_phone, business_website, business_type, status, verification_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending_verification', 'unverified', ?, ?)
        """,
        (
            owner_user_id,
            business_name,
            clean_text(payload.get("business_email"), TEXT_LIMITS["business_email"]),
            clean_text(payload.get("business_phone"), TEXT_LIMITS["business_phone"]),
            validate_destination_url(payload.get("business_website"), required=False),
            clean_text(payload.get("business_type"), TEXT_LIMITS["business_type"]),
            now,
            now,
        ),
    )
    account_id = cur.lastrowid
    audit_log(conn, owner_user_id, "ad_account_created", "pulse_ad_accounts", account_id, after={"business_name": business_name})
    conn.commit()
    return get_ad_account(conn, owner_user_id, account_id)


def get_ad_account(conn, owner_user_id, account_id) -> dict:
    return _owned_account(conn, owner_user_id, account_id)


def list_ad_accounts(conn, owner_user_id) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, business_name, business_type, status, verification_status, created_at, updated_at
        FROM pulse_ad_accounts WHERE owner_user_id=? ORDER BY id DESC LIMIT 100
        """,
        (owner_user_id,),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


# Advertiser verification lifecycle.
#
# `verification_status` existed as a column from the beginning and was read in
# three places — the account health score, the promotions billing check, and the
# ad selector by way of `status` — but nothing ever wrote it. An account was
# created 'unverified'/'pending_verification' and stayed there, which meant the
# selector's `a.status='active'` condition could never be satisfied by an account
# the product itself had created. Every advertiser was permanently ineligible and
# the only message about it pointed at a door with nothing behind it.
#
# The states below are the whole vocabulary. They are deliberately few:
#
#   unverified      never submitted — the advertiser's move
#   pending         submitted, waiting on review — nobody's move, it resolves
#   verified        approved; paired with status='active', which is what the
#                   selector actually reads
#   rejected        declined with a reason, and re-submittable — a rejection the
#                   advertiser cannot answer is the dead end §37 rules out
#
# Approval writes `status` as well as `verification_status`. They are two columns
# describing one decision and letting them disagree is how you get an account
# that is "verified" and still cannot serve.
VERIFICATION_STATES = {"unverified", "pending", "verified", "rejected", "changes_requested"}
# Both a rejection and a changes-requested decision hand the account back to the
# advertiser to act on, so both are re-submittable; only 'verified' and 'pending'
# are not (one is done, the other is already in the queue).
VERIFICATION_RESUBMIT_FROM = {"unverified", "rejected", "changes_requested", ""}


def account_verification_state(account: dict) -> str:
    """The verification state of an account row, normalised.

    Old rows predate this vocabulary and carry 'approved' from a hand-run script;
    they mean 'verified' and are read as such rather than being left in a state
    no branch below handles.

    'changes_requested' is distinct from 'rejected': a rejection is a decision the
    reviewer stands behind, while a changes request is an open ask the advertiser
    is expected to answer and resubmit. 'needs_more_info' predates the split and
    means the latter, so it is read as changes_requested rather than as a
    rejection the advertiser can only re-guess at.
    """
    raw = clean_text(account.get("verification_status"), 40).lower()
    if raw in {"approved", "verified"}:
        return "verified"
    if raw in {"pending", "submitted", "in_review"}:
        return "pending"
    if raw in {"changes_requested", "needs_changes", "action_required", "needs_more_info"}:
        return "changes_requested"
    if raw in {"rejected", "declined"}:
        return "rejected"
    return "unverified"


def _notify_account_owner(owner_user_id, account_id, event_type, title, body, dedupe_key=None) -> None:
    """Push a verification update into the canonical PulseSoc notification system.

    Best-effort by design: a verification decision is a database fact that must
    commit whether or not a notification can be enqueued, so every failure here is
    swallowed. The dedupe_key carries idempotency down into the notification
    layer — enqueuing the same decision twice collapses to one notification.
    """
    try:
        owner_id = safe_int(owner_user_id, minimum=1)
        if owner_id <= 0:
            return
        from services import pulsesoc_notification_system as notifications
        notifications.intake_event(
            event_type,
            recipient_user_id=owner_id,
            source_type="pulse_ad_account",
            source_id=str(safe_int(account_id) or ""),
            title=title,
            body=body,
            deep_link="/pulse/ads",
            category="verification",
            channels=["in_app", "push"],
            dedupe_key=dedupe_key,
        )
    except Exception:
        # Notification transport is downstream of the decision and must never be
        # able to fail it. The audit log already carries the durable record.
        pass


def submit_account_verification(conn, owner_user_id, account_id, payload: dict | None = None) -> dict:
    """Advertiser asks for their account to be reviewed.

    Only the owner can submit, because verification is a statement about the
    business behind the account rather than about a campaign, and it is the owner
    whose details are being asserted.
    """
    account = _owned_account(conn, owner_user_id, account_id)
    if clean_text(account.get("status"), 40).lower() == "suspended":
        raise PulseAdsError("This ad account is suspended. Contact support before requesting verification.", 409)
    state = account_verification_state(account)
    if state == "verified":
        raise PulseAdsError("This ad account is already verified.")
    if state == "pending":
        raise PulseAdsError("Verification is already in review. We'll let you know when it's decided.")
    if not clean_text(account.get("business_name"), TEXT_LIMITS["business_name"]):
        raise PulseAdsError("Add your business name before requesting verification.")
    now = now_iso()
    note = clean_text((payload or {}).get("note"), 500)
    resubmission = state in {"rejected", "changes_requested"}
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pulse_ad_accounts
        SET verification_status='pending', verification_submitted_at=?,
            verification_reviewed_at=NULL, verification_reviewer_id=NULL,
            verification_reason=?, updated_at=?
        WHERE id=?
        """,
        (now, note, now, safe_int(account_id)),
    )
    audit_log(
        conn, owner_user_id,
        "ad_account_verification_resubmitted" if resubmission else "ad_account_verification_submitted",
        "pulse_ad_accounts", account_id,
        before={"verification_status": account.get("verification_status")},
        after={"verification_status": "pending", "resubmission": resubmission},
    )
    conn.commit()
    _notify_account_owner(
        owner_user_id, account_id,
        "ad_verification_submitted",
        "Verification submitted",
        "We received your advertiser verification and it's now in review. We'll let you know when it's decided.",
        dedupe_key=f"ad_verify_submitted:{safe_int(account_id)}:{now}",
    )
    return {
        "ok": True,
        "account_id": safe_int(account_id),
        "verification_status": "pending",
        "status": clean_text(account.get("status"), 40) or "pending_verification",
        "submitted_at": now,
    }


def approve_account_verification(conn, admin_user_id, account_id, notes: str = "") -> dict:
    """Admin verifies an account, which is also what makes it able to serve.

    `status='active'` is written here and only here in the product. It is the
    condition `select_ads` tests, so approving verification without writing it
    would produce a verified account whose ads never appear — the exact split
    between what the record says and what the system does that this phase exists
    to close.
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=?", (safe_int(account_id),))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise PulseAdsError("Ad account not found.", 404)
    if clean_text(account.get("status"), 40) == "suspended":
        raise PulseAdsError("This account is suspended. Lift the suspension before verifying it.", 409)
    # Idempotent approve: a second click, a retried request, or a race between two
    # reviewers must not re-audit or re-notify. An account already verified and
    # active is the terminal state this function writes, so re-writing it is a
    # no-op that returns the same shape rather than a duplicate event.
    if account_verification_state(account) == "verified" and clean_text(account.get("status"), 40) == "active":
        return {"ok": True, "account_id": safe_int(account_id), "status": "active", "verification_status": "verified", "deduped": True}
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_ad_accounts
        SET verification_status='verified', status='active', verification_reviewed_at=?,
            verification_reviewer_id=?, verification_reason=?, updated_at=?
        WHERE id=?
        """,
        (now, safe_int(admin_user_id), clean_text(notes, 500), now, safe_int(account_id)),
    )
    audit_log(
        conn, admin_user_id, "ad_account_verified", "pulse_ad_accounts", account_id,
        before={"status": account.get("status"), "verification_status": account.get("verification_status")},
        after={"status": "active", "verification_status": "verified"},
    )
    conn.commit()
    _notify_account_owner(
        account.get("owner_user_id"), account_id,
        "verification_approved",
        "Advertiser account verified",
        "Your advertiser account is verified. Approved campaigns can now deliver.",
        dedupe_key=f"ad_verify_approved:{safe_int(account_id)}:{now}",
    )
    return {"ok": True, "account_id": safe_int(account_id), "status": "active", "verification_status": "verified"}


def reject_account_verification(conn, admin_user_id, account_id, reason: str = "") -> dict:
    """Admin declines verification, with a reason the advertiser can act on.

    The reason is required. A rejection with no reason is a locked door with no
    sign on it, and the advertiser's only remaining move is to guess.
    """
    reason = clean_text(reason, 500)
    if not reason:
        raise PulseAdsError("A rejection reason is required so the advertiser knows what to fix.")
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=?", (safe_int(account_id),))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise PulseAdsError("Ad account not found.", 404)
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_ad_accounts
        SET verification_status='rejected', status='pending_verification',
            verification_reviewed_at=?, verification_reviewer_id=?, verification_reason=?, updated_at=?
        WHERE id=?
        """,
        (now, safe_int(admin_user_id), reason, now, safe_int(account_id)),
    )
    audit_log(
        conn, admin_user_id, "ad_account_verification_rejected", "pulse_ad_accounts", account_id,
        before={"status": account.get("status"), "verification_status": account.get("verification_status")},
        after={"status": "pending_verification", "verification_status": "rejected", "reason": reason},
    )
    conn.commit()
    _notify_account_owner(
        account.get("owner_user_id"), account_id,
        "verification_rejected",
        "Advertiser verification declined",
        f"Your advertiser verification was declined: {reason} Update your business details and request verification again.",
        dedupe_key=f"ad_verify_rejected:{safe_int(account_id)}:{now}",
    )
    return {
        "ok": True,
        "account_id": safe_int(account_id),
        "status": "pending_verification",
        "verification_status": "rejected",
        "reason": reason,
    }


def request_account_changes(conn, admin_user_id, account_id, reason: str = "", user_note: str = "") -> dict:
    """Admin hands verification back for the advertiser to fix, without rejecting.

    Distinct from rejection: the account is not turned away, it is asked a specific
    question. `verification_status='changes_requested'` is a re-submittable state
    (see VERIFICATION_RESUBMIT_FROM), and the advertiser-safe note is what the
    owner sees and can act on. The internal reason is separated from the user note
    so a reviewer can record context the advertiser should not read while still
    telling the advertiser precisely what to change.
    """
    reason = clean_text(reason, 500)
    user_note = clean_text(user_note, 500) or reason
    if not user_note:
        raise PulseAdsError("Describe the change the advertiser needs to make so they can act on it.")
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=?", (safe_int(account_id),))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise PulseAdsError("Ad account not found.", 404)
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_ad_accounts
        SET verification_status='changes_requested', status='pending_verification',
            verification_reviewed_at=?, verification_reviewer_id=?, verification_reason=?, updated_at=?
        WHERE id=?
        """,
        (now, safe_int(admin_user_id), user_note, now, safe_int(account_id)),
    )
    audit_log(
        conn, admin_user_id, "ad_account_verification_changes_requested", "pulse_ad_accounts", account_id,
        before={"status": account.get("status"), "verification_status": account.get("verification_status")},
        after={"status": "pending_verification", "verification_status": "changes_requested",
               "user_note": user_note, "internal_reason": reason},
    )
    conn.commit()
    _notify_account_owner(
        account.get("owner_user_id"), account_id,
        "verification_needs_info",
        "Advertiser verification needs changes",
        f"We need a change before we can verify your advertiser account: {user_note} Update your details and resubmit.",
        dedupe_key=f"ad_verify_changes:{safe_int(account_id)}:{now}",
    )
    return {
        "ok": True,
        "account_id": safe_int(account_id),
        "status": "pending_verification",
        "verification_status": "changes_requested",
        "reason": user_note,
    }


def suspend_account(conn, admin_user_id, account_id, reason: str = "") -> dict:
    """Admin suspends an advertiser account — a distinct state from unverified.

    Suspension freezes the account regardless of verification: `status='suspended'`
    is checked first by every gate, so a suspended account cannot serve even if it
    was previously verified. `verification_status` is left intact so lifting the
    suspension can restore the prior standing rather than forcing a re-review.
    """
    reason = clean_text(reason, 500)
    if not reason:
        raise PulseAdsError("A suspension reason is required.")
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=?", (safe_int(account_id),))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise PulseAdsError("Ad account not found.", 404)
    if clean_text(account.get("status"), 40).lower() == "suspended":
        return {"ok": True, "account_id": safe_int(account_id), "status": "suspended", "deduped": True}
    now = now_iso()
    cur.execute(
        "UPDATE pulse_ad_accounts SET status='suspended', verification_reason=?, updated_at=? WHERE id=?",
        (reason, now, safe_int(account_id)),
    )
    # A suspended account must stop delivering immediately, not at the next review.
    cur.execute(
        "UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE ad_account_id=? AND status='active'",
        (now, safe_int(account_id)),
    )
    audit_log(
        conn, admin_user_id, "ad_account_suspended", "pulse_ad_accounts", account_id,
        before={"status": account.get("status")},
        after={"status": "suspended", "reason": reason},
    )
    conn.commit()
    _notify_account_owner(
        account.get("owner_user_id"), account_id,
        "account_restriction",
        "Advertiser account suspended",
        f"Your advertiser account has been suspended: {reason} Contact support for next steps.",
        dedupe_key=f"ad_account_suspended:{safe_int(account_id)}:{now}",
    )
    return {"ok": True, "account_id": safe_int(account_id), "status": "suspended", "reason": reason}


def restore_account(conn, admin_user_id, account_id, notes: str = "") -> dict:
    """Admin lifts a suspension, returning the account to its verification standing.

    A previously verified account comes back active; anything else returns to
    pending_verification so it re-enters the normal path rather than silently
    serving. Restoring is the deliberate inverse of suspend, so it is an explicit
    action with its own audit event rather than a generic status edit.
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=?", (safe_int(account_id),))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise PulseAdsError("Ad account not found.", 404)
    if clean_text(account.get("status"), 40).lower() != "suspended":
        return {"ok": True, "account_id": safe_int(account_id), "status": clean_text(account.get("status"), 40), "deduped": True}
    verified = account_verification_state(account) == "verified"
    new_status = "active" if verified else "pending_verification"
    now = now_iso()
    cur.execute(
        "UPDATE pulse_ad_accounts SET status=?, verification_reason=?, updated_at=? WHERE id=?",
        (new_status, clean_text(notes, 500), now, safe_int(account_id)),
    )
    audit_log(
        conn, admin_user_id, "ad_account_restored", "pulse_ad_accounts", account_id,
        before={"status": account.get("status")},
        after={"status": new_status, "notes": clean_text(notes, 300)},
    )
    conn.commit()
    _notify_account_owner(
        account.get("owner_user_id"), account_id,
        "verification_approved" if verified else "verification_needs_info",
        "Advertiser account restored",
        "Your advertiser account suspension has been lifted."
        + (" Approved campaigns can deliver again." if verified else " Complete verification to start delivering."),
        dedupe_key=f"ad_account_restored:{safe_int(account_id)}:{now}",
    )
    return {"ok": True, "account_id": safe_int(account_id), "status": new_status}


# The filter keys the admin verification queue exposes, each mapped to the raw
# verification_status / status values that belong under it. 'suspended' is an
# account-status fact rather than a verification value, so it is matched on status.
VERIFICATION_FILTERS = {
    "pending": ("verification", ("pending", "submitted", "in_review")),
    "changes_requested": ("verification", ("changes_requested", "needs_changes", "action_required", "needs_more_info")),
    "approved": ("verification", ("approved", "verified")),
    "rejected": ("verification", ("rejected", "declined")),
    "suspended": ("status", ("suspended",)),
}


def _verification_row(row) -> dict:
    account = row_to_dict(row)
    account["verification_state"] = account_verification_state(account)
    return account


def account_review_board(conn, limit=100, status_filter="pending", search="") -> list[dict]:
    """Advertiser accounts for the verification queue, filtered and searchable.

    Defaults to the pending queue, oldest request first, because that is the work
    someone is sitting in waiting on. Other filters and search let a reviewer find
    a specific account without leaving the queue. 'all' returns every account.
    """
    status_filter = clean_text(status_filter, 40).lower() or "pending"
    search = clean_text(search, 120)
    clauses = []
    params: list = []
    if status_filter in VERIFICATION_FILTERS:
        column, values = VERIFICATION_FILTERS[status_filter]
        placeholders = ",".join(["?"] * len(values))
        if column == "status":
            clauses.append(f"lower(COALESCE(status,'')) IN ({placeholders})")
        else:
            clauses.append(f"lower(COALESCE(verification_status,'')) IN ({placeholders})")
        params.extend(values)
    if search:
        like = f"%{search.lower()}%"
        clauses.append(
            "(lower(COALESCE(business_name,'')) LIKE ? OR lower(COALESCE(business_email,'')) LIKE ?"
            " OR CAST(id AS TEXT)=? OR CAST(owner_user_id AS TEXT)=?)"
        )
        params.extend([like, like, search, search])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(safe_int(limit, 100, minimum=1, maximum=500))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, owner_user_id, business_name, business_type, business_email, business_website,
               status, verification_status, verification_submitted_at, verification_reviewed_at,
               verification_reason, created_at, updated_at
        FROM pulse_ad_accounts
        {where}
        ORDER BY COALESCE(verification_submitted_at, created_at) ASC, id ASC
        LIMIT ?
        """,
        tuple(params),
    )
    return [_verification_row(row) for row in cur.fetchall()]


def verification_summary_counts(conn) -> dict:
    """How many accounts sit under each verification filter, plus the oldest wait.

    Feeds the admin dashboard card and the queue's filter badges: 'N pending,
    oldest pending' is the whole point of the card, so it is computed here once
    rather than by counting rows in the page.
    """
    cur = conn.cursor()
    counts = {key: 0 for key in list(VERIFICATION_FILTERS.keys()) + ["unverified", "all"]}
    cur.execute("SELECT status, verification_status FROM pulse_ad_accounts")
    rows = cur.fetchall()
    for row in rows:
        account = row_to_dict(row)
        counts["all"] += 1
        if clean_text(account.get("status"), 40).lower() == "suspended":
            counts["suspended"] += 1
        state = account_verification_state(account)
        if state == "pending":
            counts["pending"] += 1
        elif state == "changes_requested":
            counts["changes_requested"] += 1
        elif state == "verified":
            counts["approved"] += 1
        elif state == "rejected":
            counts["rejected"] += 1
        else:
            counts["unverified"] += 1
    cur.execute(
        """
        SELECT MIN(COALESCE(verification_submitted_at, created_at)) AS oldest
        FROM pulse_ad_accounts
        WHERE lower(COALESCE(verification_status,'')) IN ('pending','submitted','in_review')
        """
    )
    oldest = row_to_dict(cur.fetchone()).get("oldest")
    counts["oldest_pending"] = oldest or ""
    return counts


def create_campaign(conn, owner_user_id, payload: dict) -> dict:
    account_id = safe_int(payload.get("ad_account_id"), minimum=1)
    _owned_account(conn, owner_user_id, account_id)
    objective = clean_text(payload.get("objective") or "awareness", TEXT_LIMITS["objective"]).lower()
    if objective not in VALID_OBJECTIVES:
        raise PulseAdsError("Unsupported campaign objective.")
    budget_type = clean_text(payload.get("budget_type") or "daily", 20).lower()
    if budget_type not in VALID_BUDGET_TYPES:
        raise PulseAdsError("Unsupported budget type.")
    campaign_name = clean_text(payload.get("campaign_name"), TEXT_LIMITS["campaign_name"])
    if not campaign_name:
        raise PulseAdsError("Campaign name is required.")
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_campaigns
        (ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents, lifetime_budget_cents, spent_cents, start_at, end_at, created_at, updated_at)
        VALUES (?, ?, ?, 'draft', ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            account_id,
            campaign_name,
            objective,
            budget_type,
            safe_int(payload.get("daily_budget_cents"), 0, 0, 10_000_000),
            safe_int(payload.get("lifetime_budget_cents"), 0, 0, 100_000_000),
            clean_text(payload.get("start_at"), 40),
            clean_text(payload.get("end_at"), 40),
            now,
            now,
        ),
    )
    campaign_id = cur.lastrowid
    # Was `payload.get("placements") or ["feed_inline"]`. A campaign created
    # without a placement choice was quietly put into the feed — a surface the
    # advertiser had not picked, and would then pay for. A draft with no
    # placement is a legitimate state: `_campaign_activation_blocker` refuses to
    # activate it and says why, so the campaign cannot spend until a real choice
    # is made.
    attach_campaign_placements(conn, campaign_id, payload.get("placements"))
    audit_log(conn, owner_user_id, "ad_campaign_created", "pulse_ad_campaigns", campaign_id, after={"campaign_name": campaign_name})
    conn.commit()
    return get_campaign(conn, owner_user_id, campaign_id)


def get_campaign(conn, owner_user_id, campaign_id) -> dict:
    return _owned_campaign(conn, owner_user_id, campaign_id)


def list_campaigns(conn, owner_user_id) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.* FROM pulse_ad_campaigns c
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        WHERE a.owner_user_id=?
        ORDER BY c.id DESC LIMIT 100
        """,
        (owner_user_id,),
    )
    campaigns = []
    for row in cur.fetchall():
        item = row_to_dict(row)
        item["objective_canonical"] = canonical_objective(item.get("objective"))
        campaigns.append(item)
    return campaigns


MAX_CAMPAIGN_PLACEMENTS = 8


def resolve_placement_keys(conn, placement_keys) -> list[tuple[str, int]]:
    """Turn requested placement keys into `(key, placement_id)` pairs, or raise.

    Separated from `attach_campaign_placements` so a caller that has to destroy
    state before writing — `update_campaign` deletes every existing placement row
    first — can find out the request is bad *before* the delete rather than
    halfway through the insert. Whether an exception mid-write rolls back depends
    on the connection's transaction mode, and a campaign's placement set is not
    something to leave to that.

    Duplicates collapse, order is preserved, and an empty request resolves to an
    empty list rather than to a default nobody asked for.
    """
    if isinstance(placement_keys, str):
        placement_keys = [placement_keys]
    cleaned: list[str] = []
    for key in placement_keys or []:
        text = clean_text(key, 80)
        if text and text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > MAX_CAMPAIGN_PLACEMENTS:
        raise PulseAdsError(
            f"A campaign can run in at most {MAX_CAMPAIGN_PLACEMENTS} placements. You chose {len(cleaned)}."
        )
    cur = conn.cursor()
    resolved: list[tuple[str, int]] = []
    for key in cleaned:
        cur.execute("SELECT id FROM pulse_ad_placements WHERE placement_key=? AND is_active=1", (key,))
        row = cur.fetchone()
        if not row:
            raise PulseAdsError(f"'{key}' is not a placement your campaigns can run in.", 400)
        resolved.append((key, row_to_dict(row).get("id")))
    return resolved


def attach_campaign_placements(conn, campaign_id, placement_keys) -> None:
    """Attach a campaign to the placements the advertiser chose — those and no others.

    This used to fail silently in three separate ways, all of which ended with a
    campaign running somewhere other than where it was told to run.

    An unknown or deactivated placement key hit `continue`. Ask for four
    placements, misspell one, and the campaign was attached to three with no
    error anywhere — the advertiser's spend went to a narrower set of surfaces
    than they chose and nothing told them so.

    More than eight keys were silently truncated by `cleaned[:8]`. There are
    twelve placements. An advertiser selecting all of them got the first eight in
    whatever order the client happened to send, and the four that fell off were
    the four at the end of a list, not the four they cared least about.

    Worst, an empty list was replaced with `["feed_inline"]` — literally
    delivering in a placement the advertiser did not pick, and charging them for
    it. The empty list now attaches nothing, which is the honest outcome: the
    campaign is undeliverable and `_campaign_activation_blocker` reports
    `no_placement` before it can go live, so nobody is stranded and nobody is
    billed for a surface they never chose.

    Every key is resolved before the first insert — see `resolve_placement_keys`
    — so a request naming one bad placement writes nothing at all rather than
    attaching the good ones and abandoning the rest.
    """
    cur = conn.cursor()
    for _key, placement_id in resolve_placement_keys(conn, placement_keys):
        cur.execute(
            "SELECT campaign_id FROM pulse_ad_campaign_placements WHERE campaign_id=? AND placement_id=?",
            (campaign_id, placement_id),
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO pulse_ad_campaign_placements (campaign_id, placement_id, created_at) VALUES (?, ?, ?)",
                (campaign_id, placement_id, now_iso()),
            )


def policy_review(conn, creative_id, payload: dict) -> dict:
    result = ad_policy_engine.evaluate_ad(
        {
            "category": clean_text(payload.get("category") or payload.get("contextual_category") or "creator_sponsorship", 80),
            "headline": payload.get("title"),
            "body": payload.get("body"),
            "destination_url": payload.get("destination_url"),
        }
    )
    cur = conn.cursor()
    for reason in result.get("reasons") or []:
        severity = "high" if str(reason).lower().startswith("blocked") else "medium"
        cur.execute(
            """
            INSERT INTO pulse_ad_policy_flags (creative_id, flag_type, severity, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (creative_id, "policy_review", severity, clean_text(reason, 500), now_iso()),
        )
    return result


def create_creative(conn, owner_user_id, payload: dict) -> dict:
    campaign_id = safe_int(payload.get("campaign_id"), minimum=1)
    campaign = _owned_campaign(conn, owner_user_id, campaign_id)
    ad_account_id = safe_int(campaign.get("ad_account_id"), minimum=1)
    creative_type = clean_text(payload.get("creative_type") or "text", 30).lower()
    if creative_type not in VALID_CREATIVE_TYPES:
        raise PulseAdsError("Unsupported creative type.")
    if payload.get("media_url") or payload.get("thumbnail_url"):
        raise PulseAdsError("Upload media through PulseSoc Creative Studio instead of pasting media URLs.")
    content_ref = {}
    content_ref_type = clean_text(payload.get("content_ref_type"), 30).lower()
    content_ref_id = safe_int(payload.get("content_ref_id"), 0)
    if creative_type in CONTENT_CREATIVE_TYPES and not content_ref_type:
        raise PulseAdsError(f"A {creative_type} creative must reference existing content via content_ref_type/content_ref_id.")
    if content_ref_type or content_ref_id:
        content_ref = resolve_content_ref(conn, owner_user_id, content_ref_type, content_ref_id)
        if creative_type in CONTENT_CREATIVE_TYPES and content_ref.get("content_ref_type") != creative_type:
            raise PulseAdsError(f"A {creative_type} creative must reference {creative_type} content.")
    title = clean_text(payload.get("title") or (content_ref.get("title") if content_ref else ""), TEXT_LIMITS["title"])
    body = clean_text(payload.get("body"), TEXT_LIMITS["body"])
    headline = clean_text(payload.get("headline"), TEXT_LIMITS["headline"])
    primary_text = clean_text(payload.get("primary_text"), TEXT_LIMITS["primary_text"])
    aspect_ratio = clean_text(payload.get("aspect_ratio"), 20)
    if content_ref and not payload.get("destination_url"):
        destination_url = content_ref.get("destination_url") or ""
    else:
        destination_url = validate_destination_url(payload.get("destination_url"), required=True)
    if not title:
        raise PulseAdsError("Creative title is required.")
    media_asset = {}
    thumbnail_asset = {}
    media_asset_id = safe_int(payload.get("media_asset_id"), 0)
    thumbnail_asset_id = safe_int(payload.get("thumbnail_asset_id"), 0)
    if media_asset_id:
        media_asset = _owned_ad_media_asset(conn, owner_user_id, ad_account_id, media_asset_id, allowed_kinds={"creative_media", "companion_image"})
        if not _asset_type_allowed(creative_type, media_asset.get("media_type")):
            raise PulseAdsError(f"{creative_type.title()} creatives require a matching uploaded {creative_type} asset.")
    elif creative_type in AD_MEDIA_REQUIRED_TYPES:
        raise PulseAdsError(f"Upload a {creative_type} asset before creating this creative.")
    if thumbnail_asset_id:
        thumbnail_asset = _owned_ad_media_asset(conn, owner_user_id, ad_account_id, thumbnail_asset_id, allowed_kinds={"thumbnail", "companion_image"})
        if clean_text(thumbnail_asset.get("media_type"), 40).lower() not in {"image", "gif"}:
            raise PulseAdsError("Custom thumbnails must be uploaded as images.")
    media_public = _ad_asset_public(media_asset)
    thumb_public = _ad_asset_public(thumbnail_asset)
    media_metadata = {
        "media_asset": {k: media_public.get(k) for k in ("media_type", "mime_type", "width", "height", "duration_seconds", "file_size") if media_public.get(k) not in ("", None, 0)},
        "thumbnail_asset": {k: thumb_public.get(k) for k in ("media_type", "mime_type", "width", "height", "file_size") if thumb_public.get(k) not in ("", None, 0)},
    }
    # Media derived from the referenced content when no ad asset was uploaded.
    media_url = media_public.get("public_url") or ""
    thumbnail_url = thumb_public.get("thumbnail_url") or media_public.get("thumbnail_url") or ""
    if content_ref:
        media_url = media_url or clean_text(content_ref.get("media_url"), 1000)
        thumbnail_url = thumbnail_url or clean_text(content_ref.get("thumbnail_url"), 1000)
        media_metadata["content_ref"] = {"type": content_ref.get("content_ref_type"), "id": content_ref.get("content_ref_id")}
    media_ready = 1 if (creative_type == "text" or media_asset or (content_ref and creative_type in CONTENT_CREATIVE_TYPES)) else 0
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_creatives
        (ad_account_id, campaign_id, creative_type, title, body, media_url, thumbnail_url, media_asset_id, thumbnail_asset_id,
         destination_url, call_to_action, status, moderation_status, media_ready, media_metadata_json,
         content_ref_type, content_ref_id, headline, primary_text, aspect_ratio, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ad_account_id,
            campaign_id,
            creative_type,
            title,
            body,
            media_url,
            thumbnail_url,
            media_asset.get("id") if media_asset else None,
            thumbnail_asset.get("id") if thumbnail_asset else None,
            destination_url,
            clean_text(payload.get("call_to_action") or "Learn more", TEXT_LIMITS["call_to_action"]),
            media_ready,
            clean_json(media_metadata),
            content_ref.get("content_ref_type") or "",
            safe_int(content_ref.get("content_ref_id"), 0),
            headline,
            primary_text,
            aspect_ratio,
            now,
            now,
        ),
    )
    creative_id = cur.lastrowid
    result = policy_review(conn, creative_id, payload)
    audit_log(conn, owner_user_id, "ad_creative_created", "pulse_ad_creatives", creative_id, after={"title": title, "policy_status": result.get("status")})
    conn.commit()
    return get_creative(conn, owner_user_id, creative_id)


def get_creative(conn, owner_user_id, creative_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cr.* FROM pulse_ad_creatives cr
        JOIN pulse_ad_accounts a ON a.id=cr.ad_account_id
        WHERE cr.id=? AND a.owner_user_id=?
        """,
        (creative_id, owner_user_id),
    )
    creative = row_to_dict(cur.fetchone())
    if not creative:
        raise PulseAdsError("Creative not found.", 404)
    return attach_creative_media(conn, creative)


def list_creatives(conn, owner_user_id) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cr.* FROM pulse_ad_creatives cr
        JOIN pulse_ad_accounts a ON a.id=cr.ad_account_id
        WHERE a.owner_user_id=?
        ORDER BY cr.id DESC LIMIT 100
        """,
        (owner_user_id,),
    )
    return [attach_creative_media(conn, row_to_dict(row)) for row in cur.fetchall()]


def attach_creative_media(conn, creative: dict) -> dict:
    item = dict(creative or {})
    cur = conn.cursor()
    media_asset_id = safe_int(item.get("media_asset_id"), 0)
    thumbnail_asset_id = safe_int(item.get("thumbnail_asset_id"), 0)
    if media_asset_id:
        cur.execute("SELECT * FROM pulse_ad_media_assets WHERE id=? AND COALESCE(deleted_at, '')=''", (media_asset_id,))
        media_asset = row_to_dict(cur.fetchone())
        if media_asset:
            item["media_asset"] = _ad_asset_public(media_asset)
            item["media_url"] = item["media_asset"].get("public_url") or item.get("media_url") or ""
            item["playback_url"] = item["media_asset"].get("playback_url") or ""
            item["media_moderation_status"] = media_asset.get("moderation_status") or ""
    if thumbnail_asset_id:
        cur.execute("SELECT * FROM pulse_ad_media_assets WHERE id=? AND COALESCE(deleted_at, '')=''", (thumbnail_asset_id,))
        thumbnail_asset = row_to_dict(cur.fetchone())
        if thumbnail_asset:
            item["thumbnail_asset"] = _ad_asset_public(thumbnail_asset)
            item["thumbnail_url"] = item["thumbnail_asset"].get("thumbnail_url") or item["thumbnail_asset"].get("public_url") or item.get("thumbnail_url") or ""
            item["thumbnail_moderation_status"] = thumbnail_asset.get("moderation_status") or ""
    if not item.get("thumbnail_url") and item.get("media_asset"):
        item["thumbnail_url"] = item["media_asset"].get("thumbnail_url") or ""
    item["media_ready"] = bool(
        item.get("media_asset_id")
        or item.get("creative_type") == "text"
        or (safe_int(item.get("content_ref_id"), 0) and item.get("creative_type") in CONTENT_CREATIVE_TYPES)
    )
    return item


def submit_creative_for_review(conn, owner_user_id, creative_id) -> dict:
    creative = get_creative(conn, owner_user_id, creative_id)
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_creatives SET status='pending_review', moderation_status='pending', updated_at=? WHERE id=?",
        (now, creative_id),
    )
    cur.execute(
        "INSERT INTO pulse_ad_moderation_queue (creative_id, submitted_by, status, risk_score, created_at) VALUES (?, ?, 'pending', ?, ?)",
        (creative_id, owner_user_id, 50, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_review_board
        (campaign_id, creative_id, review_status, risk_score, automated_review_status, human_review_status, review_reason, created_at, updated_at)
        VALUES (?, ?, 'pending', 50, 'needs_review', 'pending', '', ?, ?)
        """,
        (creative.get("campaign_id"), creative_id, now, now),
    )
    audit_log(conn, owner_user_id, "ad_creative_submitted", "pulse_ad_creatives", creative_id, before=creative, after={"moderation_status": "pending"})
    conn.commit()
    return get_creative(conn, owner_user_id, creative_id)


def review_board(conn, limit=100) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rb.id AS review_id, rb.review_status, rb.risk_score, rb.automated_review_status, rb.human_review_status,
               rb.review_reason, rb.created_at, rb.reviewed_at, cr.id AS creative_id, cr.title, cr.body,
               cr.destination_url, cr.moderation_status, cr.creative_type, cr.media_asset_id, cr.thumbnail_asset_id,
               c.id AS campaign_id, c.campaign_name, a.business_name
        FROM pulse_ad_review_board rb
        JOIN pulse_ad_creatives cr ON cr.id=rb.creative_id
        JOIN pulse_ad_campaigns c ON c.id=rb.campaign_id
        JOIN pulse_ad_accounts a ON a.id=cr.ad_account_id
        ORDER BY rb.id DESC LIMIT ?
        """,
        (safe_int(limit, 100, 1, 250),),
    )
    rows = []
    for row in cur.fetchall():
        item = row_to_dict(row)
        item.pop("destination_url", None)
        rows.append(attach_creative_media(conn, item))
    return rows


def approve_creative(conn, admin_user_id, creative_id, notes="") -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    before = row_to_dict(cur.fetchone())
    if not before:
        raise PulseAdsError("Creative not found.", 404)
    now = now_iso()
    cur.execute(
        "UPDATE pulse_ad_creatives SET status='approved', moderation_status='approved', rejection_reason='', updated_at=? WHERE id=?",
        (now, creative_id),
    )
    for asset_id in (safe_int(before.get("media_asset_id"), 0), safe_int(before.get("thumbnail_asset_id"), 0)):
        if asset_id:
            cur.execute("UPDATE pulse_ad_media_assets SET moderation_status='approved', updated_at=? WHERE id=?", (now, asset_id))
    cur.execute(
        "UPDATE pulse_ad_moderation_queue SET status='approved', reviewer_id=?, notes=?, reviewed_at=? WHERE creative_id=?",
        (admin_user_id, clean_text(notes, TEXT_LIMITS["notes"]), now, creative_id),
    )
    cur.execute(
        """
        UPDATE pulse_ad_review_board
        SET review_status='approved', human_review_status='approved', reviewer_id=?, review_reason=?, reviewed_at=?, updated_at=?
        WHERE creative_id=?
        """,
        (admin_user_id, clean_text(notes, TEXT_LIMITS["notes"]), now, now, creative_id),
    )
    audit_log(conn, admin_user_id, "ad_creative_approved", "pulse_ad_creatives", creative_id, before=before, after={"moderation_status": "approved"})
    conn.commit()
    result = {"ok": True, "creative_id": creative_id, "moderation_status": "approved"}
    activation = _maybe_activate_reviewed_campaign(conn, admin_user_id, before.get("campaign_id"))
    if activation:
        result["campaign_activation"] = activation
    return result


def _maybe_activate_reviewed_campaign(conn, admin_user_id, campaign_id) -> dict | None:
    """Close the review loop after a creative approval.

    There was no transition out of `pending_review` at all: `approve_creative`
    decided the creative, resume refused `pending_review`, and delivery
    requires `active` — so a submitted campaign whose creatives were approved
    was stuck forever. Once every non-archived creative on a `pending_review`
    campaign is approved, the campaign itself activates through the same
    shared implementation the admin approve action uses.

    If the campaign can't activate (unfunded wallet, missing placement, …) it
    stays `pending_review` and the owner is told exactly what is blocking it —
    the creative approval itself still succeeds either way, which is why this
    runs after that commit and never raises past it.
    """
    campaign_id = safe_int(campaign_id, 0)
    if not campaign_id:
        return None
    # Local import: pulse_advertiser_portal imports this module at top level.
    from services import pulse_advertiser_portal
    try:
        campaign = pulse_advertiser_portal._campaign_with_owner(conn, campaign_id)
    except PulseAdsError:
        return None
    if clean_text(campaign.get("status"), 40).lower() != "pending_review":
        return None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM pulse_ad_creatives
        WHERE campaign_id=?
          AND COALESCE(status, '') != 'archived'
          AND COALESCE(moderation_status, '') != 'approved'
        """,
        (campaign_id,),
    )
    if safe_int(row_to_dict(cur.fetchone()).get("n"), 0):
        # Review isn't finished — sibling creatives are still undecided (or
        # rejected, which the advertiser resolves before the campaign runs).
        return None
    account_id = safe_int(campaign.get("ad_account_id"))
    owner_user_id = safe_int(campaign.get("account_owner_user_id"))
    gate = pulse_advertiser_portal.campaign_review_gate(conn, account_id, campaign)
    if gate is None:
        try:
            return pulse_advertiser_portal.activate_reviewed_campaign(conn, admin_user_id, campaign, owner_user_id)
        except PulseAdsError as exc:
            # `reserve_campaign_budget` holds a stricter spendable threshold
            # than the gate's `campaign_can_spend`; its refusal is a blocker
            # like any other, not a failure of the creative approval.
            gate = ("wallet_insufficient", str(exc))
    name = clean_text(campaign.get("campaign_name"), 120)
    pulse_advertiser_portal._add_notification(
        conn, account_id, campaign_id, None, owner_user_id, "campaign_activation_blocked",
        "Campaign approved — action needed", f"{name} passed review but isn't running yet: {gate[1]}",
    )
    conn.commit()
    return {"campaign_id": campaign_id, "status": "pending_review", "blocked_by": gate[0], "detail": gate[1]}


def reject_creative(conn, admin_user_id, creative_id, reason="") -> dict:
    reason = clean_text(reason or "Creative did not meet PulseSoc ad policy.", TEXT_LIMITS["rejection_reason"])
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    before = row_to_dict(cur.fetchone())
    if not before:
        raise PulseAdsError("Creative not found.", 404)
    now = now_iso()
    cur.execute(
        "UPDATE pulse_ad_creatives SET status='rejected', moderation_status='rejected', rejection_reason=?, updated_at=? WHERE id=?",
        (reason, now, creative_id),
    )
    for asset_id in (safe_int(before.get("media_asset_id"), 0), safe_int(before.get("thumbnail_asset_id"), 0)):
        if asset_id:
            cur.execute("UPDATE pulse_ad_media_assets SET moderation_status='rejected', updated_at=? WHERE id=?", (now, asset_id))
    cur.execute(
        "UPDATE pulse_ad_moderation_queue SET status='rejected', reviewer_id=?, notes=?, reviewed_at=? WHERE creative_id=?",
        (admin_user_id, reason, now, creative_id),
    )
    cur.execute(
        """
        UPDATE pulse_ad_review_board
        SET review_status='rejected', human_review_status='rejected', reviewer_id=?, review_reason=?, reviewed_at=?, updated_at=?
        WHERE creative_id=?
        """,
        (admin_user_id, reason, now, now, creative_id),
    )
    audit_log(conn, admin_user_id, "ad_creative_rejected", "pulse_ad_creatives", creative_id, before=before, after={"reason": reason})
    conn.commit()
    return {"ok": True, "creative_id": creative_id, "moderation_status": "rejected"}


def suspend_campaign(conn, admin_user_id, campaign_id, reason="") -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    before = row_to_dict(cur.fetchone())
    if not before:
        raise PulseAdsError("Campaign not found.", 404)
    cur.execute("UPDATE pulse_ad_campaigns SET status='suspended', updated_at=? WHERE id=?", (now_iso(), campaign_id))
    audit_log(conn, admin_user_id, "ad_campaign_suspended", "pulse_ad_campaigns", campaign_id, before=before, after={"reason": clean_text(reason, 300)})
    conn.commit()
    return {"ok": True, "campaign_id": campaign_id, "status": "suspended"}


def _candidate_placements(context: str, device_type: str) -> list[str]:
    keys = CONTEXT_PLACEMENTS.get(clean_text(context, 40).lower(), CONTEXT_PLACEMENTS["home"])
    if device_type == "mobile":
        return [key for key in keys if "desktop" not in key]
    if device_type == "desktop":
        return [key for key in keys if "mobile" not in key]
    return keys


def placement_metadata(context: str = "", device_type: str = "desktop") -> list[dict]:
    keys = _candidate_placements(context or "home", clean_text(device_type, 20).lower() or "desktop")
    return [dict(PLACEMENT_METADATA[key]) for key in keys if key in PLACEMENT_METADATA]


def normalize_delivery_context(payload: dict | None = None, **kwargs) -> dict:
    data = {}
    data.update(payload or {})
    data.update(kwargs)
    device_type = clean_text(data.get("device_type") or "desktop", 20).lower()
    if device_type not in VALID_DEVICE_TYPES:
        device_type = "desktop"
    country = clean_text(data.get("country") or "", 32).upper()
    if len(country) > 2:
        country = ""
    language = clean_text(data.get("language") or "", 12).lower()
    if not re.match(r"^[a-z]{2}(-[a-z]{2})?$", language or ""):
        language = ""
    return {
        "context": clean_text(data.get("context") or "home", 40).lower(),
        "device_type": device_type,
        "viewport": clean_text(data.get("viewport") or "", 80),
        "country": country,
        "language": language,
        "contextual_category": clean_text(data.get("contextual_category") or data.get("category") or "", 80).lower(),
        "search_query_hash": hash_value(clean_text(data.get("search_query") or "", 160)) if data.get("search_query") else "",
        "feed_context": clean_text(data.get("feed_context") or "", 80),
        "marketplace_context": clean_text(data.get("marketplace_context") or "", 80),
        "radio_context": clean_text(data.get("radio_context") or "", 80),
        "is_premium": 1 if str(data.get("is_premium") or "").lower() in {"1", "true", "yes"} else 0,
    }


def user_personalized_ads_opt_out(conn, user_id) -> bool:
    if not user_id:
        return True
    try:
        cur = conn.cursor()
        cur.execute("SELECT personalized_ads_opt_out FROM privacy_preferences WHERE user_id=?", (user_id,))
        row = row_to_dict(cur.fetchone())
        return safe_int(row.get("personalized_ads_opt_out"), 1) != 0
    except Exception:
        return True


def _frequency_allowed(conn, viewer_user_id, session_id, campaign_id, placement_key, max_frequency) -> bool:
    cur = conn.cursor()
    if viewer_user_id:
        cur.execute(
            """
            SELECT impressions_count FROM pulse_ad_frequency_caps
            WHERE viewer_user_id=? AND campaign_id=? AND placement_key=?
            """,
            (viewer_user_id, campaign_id, placement_key),
        )
    else:
        cur.execute(
            """
            SELECT impressions_count FROM pulse_ad_frequency_caps
            WHERE session_id=? AND campaign_id=? AND placement_key=?
            """,
            (session_id or "", campaign_id, placement_key),
        )
    row = row_to_dict(cur.fetchone())
    return safe_int(row.get("impressions_count"), 0) < safe_int(max_frequency, 4, 1, 50)


def bump_frequency(conn, viewer_user_id, session_id, campaign_id, placement_key) -> None:
    cur = conn.cursor()
    now = now_iso()
    if viewer_user_id:
        cur.execute(
            "SELECT id, impressions_count FROM pulse_ad_frequency_caps WHERE viewer_user_id=? AND campaign_id=? AND placement_key=?",
            (viewer_user_id, campaign_id, placement_key),
        )
    else:
        cur.execute(
            "SELECT id, impressions_count FROM pulse_ad_frequency_caps WHERE session_id=? AND campaign_id=? AND placement_key=?",
            (session_id or "", campaign_id, placement_key),
        )
    row = row_to_dict(cur.fetchone())
    if row:
        cur.execute(
            "UPDATE pulse_ad_frequency_caps SET impressions_count=?, last_seen_at=?, updated_at=? WHERE id=?",
            (safe_int(row.get("impressions_count"), 0) + 1, now, now, row.get("id")),
        )
    else:
        cur.execute(
            """
            INSERT INTO pulse_ad_frequency_caps
            (viewer_user_id, session_id, campaign_id, placement_key, impressions_count, last_seen_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (viewer_user_id, session_id or "", campaign_id, placement_key, now, now),
        )


def sanitize_ad_payload(row: dict) -> dict:
    payload = {
        "ad_id": row.get("creative_id"),
        "creative_id": row.get("creative_id"),
        "campaign_id": row.get("campaign_id"),
        "placement_key": row.get("placement_key"),
        "label": "Sponsored",
        "creative_type": row.get("creative_type"),
        "title": clean_text(row.get("title"), TEXT_LIMITS["title"]),
        "body": clean_text(row.get("body"), TEXT_LIMITS["body"]),
        "media_url": row.get("media_url") or "",
        "thumbnail_url": row.get("thumbnail_url") or "",
        "playback_url": row.get("playback_url") or "",
        "media_type": clean_text(row.get("media_type") or row.get("creative_type") or "", 40),
        "mime_type": clean_text(row.get("mime_type") or "", 120),
        "width": safe_int(row.get("width"), 0),
        "height": safe_int(row.get("height"), 0),
        "duration_seconds": float(row.get("duration_seconds") or 0),
        "file_size": safe_int(row.get("file_size"), 0),
        "destination_url": row.get("destination_url") or "",
        "call_to_action": clean_text(row.get("call_to_action") or "Learn more", TEXT_LIMITS["call_to_action"]),
        "card_style": clean_text(row.get("card_style") or PLACEMENT_METADATA.get(row.get("placement_key"), {}).get("card_style") or "signal-card", 80),
        "placement_type": clean_text(row.get("placement_type") or "", 40),
        "delivery_token": row.get("delivery_token") or "",
        "tracking_nonce": row.get("tracking_nonce") or "",
        "expires_at": row.get("expires_at") or "",
        "reportable": True,
    }
    return payload


def _compatible_creative(creative_type: str, supported: str) -> bool:
    allowed = {item.strip() for item in str(supported or "").split(",") if item.strip()} or VALID_CREATIVE_TYPES
    return clean_text(creative_type, 30).lower() in allowed


def _campaign_budget_available(conn, campaign: dict) -> bool:
    lifetime = safe_int(campaign.get("lifetime_budget_cents"), 0, 0)
    daily = safe_int(campaign.get("daily_budget_cents"), 0, 0)
    spent = safe_int(campaign.get("spent_cents"), 0, 0)
    if lifetime and spent >= lifetime:
        return False
    if daily:
        cur = conn.cursor()
        now = now_iso()
        today = now[:10]
        cur.execute(
            "SELECT COUNT(*) AS c FROM pulse_ad_impressions WHERE campaign_id=? AND created_at>=?",
            (campaign.get("campaign_id") or campaign.get("id"), today),
        )
        impressions_today = safe_int(row_to_dict(cur.fetchone()).get("c"), 0)
        estimated_daily_spend = impressions_today
        if estimated_daily_spend >= daily:
            return False
        # Pacing: spread the daily budget across the day instead of spending
        # it all at once. Deterministic and debuggable: allowed-so-far is the
        # time-elapsed share of the daily budget plus 25% headroom, with a
        # small floor so early-morning delivery is never starved. A campaign
        # ahead of pace simply skips this request — no charge, no state.
        try:
            hh, mm = int(now[11:13]), int(now[14:16])
            elapsed_fraction = max((hh * 60 + mm) / 1440.0, 0.001)
        except Exception:
            elapsed_fraction = 1.0
        allowed_so_far = max(int(daily * elapsed_fraction * 1.25), min(daily, 10))
        if estimated_daily_spend >= allowed_so_far:
            return False
    try:
        from services import pulse_ad_payments
        if clean_text(campaign.get("account_business_type"), 80) == "internal_promotion":
            return True
        if not pulse_ad_payments.campaign_can_spend(conn, campaign):
            return False
    except Exception:
        if safe_int(campaign.get("ad_account_id"), 0):
            return False
    return True


def _matches_targeting(target: dict, ctx: dict, personalized_opt_out: bool) -> bool:
    if not target:
        return True
    target_device = clean_text(target.get("device_type") or "all", 20).lower()
    if target_device not in {"", "all", ctx["device_type"]}:
        return False
    category = clean_text(target.get("contextual_category") or "", 80).lower()
    if category and ctx.get("contextual_category") and category != ctx.get("contextual_category"):
        return False
    if personalized_opt_out:
        return True
    country = clean_text(target.get("country") or "", 32).upper()
    if country and ctx.get("country") and country != ctx.get("country"):
        return False
    language = clean_text(target.get("language") or "", 12).lower()
    if language and ctx.get("language") and language != ctx.get("language"):
        return False
    premium = safe_int(target.get("premium_audience"), 0)
    if premium and not ctx.get("is_premium"):
        return False
    return True


def _recent_campaigns(conn, viewer_user_id, session_id, placement_key) -> set[int]:
    cur = conn.cursor()
    if viewer_user_id:
        cur.execute(
            """
            SELECT campaign_id FROM pulse_ad_impressions
            WHERE viewer_user_id=? AND placement_key=?
            ORDER BY id DESC LIMIT 3
            """,
            (viewer_user_id, placement_key),
        )
    else:
        cur.execute(
            """
            SELECT campaign_id FROM pulse_ad_impressions
            WHERE session_id=? AND placement_key=?
            ORDER BY id DESC LIMIT 3
            """,
            (session_id or "", placement_key),
        )
    return {safe_int(row_to_dict(row).get("campaign_id"), 0) for row in cur.fetchall()}


def _audience_filter_ready(conn) -> bool:
    """Probe whether pulse_ad_targeting carries the saved-audience columns so
    select_ads can honor audience_mode + saved/excluded audiences."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT t.audience_mode, t.saved_audience_ids_json, t.excluded_audience_ids_json
            FROM pulse_ad_targeting t WHERE 1=0
            """
        )
        cur.fetchall()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _viewer_follows_owner(conn, viewer_user_id, owner_user_id, cache: dict) -> bool:
    key = ("follows", viewer_user_id, owner_user_id)
    if key not in cache:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM pulse_follows WHERE follower_user_id=? AND followed_user_id=? LIMIT 1",
                (viewer_user_id, owner_user_id),
            )
            cache[key] = cur.fetchone() is not None
        except Exception:
            cache[key] = False
    return cache[key]


def _viewer_engaged_with_owner(conn, viewer_user_id, owner_user_id, cache: dict) -> bool:
    key = ("engaged", viewer_user_id, owner_user_id)
    if key not in cache:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM pulse_reactions r
                JOIN pulse_posts p ON p.id=r.post_id
                WHERE r.user_id=? AND p.user_id=? LIMIT 1
                """,
                (viewer_user_id, owner_user_id),
            )
            found = cur.fetchone() is not None
            if not found:
                cur.execute(
                    """
                    SELECT 1 FROM pulse_comments cm
                    JOIN pulse_posts p ON p.id=cm.post_id
                    WHERE cm.user_id=? AND p.user_id=? AND cm.deleted_at IS NULL LIMIT 1
                    """,
                    (viewer_user_id, owner_user_id),
                )
                found = cur.fetchone() is not None
            cache[key] = found
        except Exception:
            cache[key] = False
    return cache[key]


def _parse_id_list(raw) -> list[int]:
    try:
        values = json.loads(raw or "[]")
    except Exception:
        return []
    ids = []
    for value in values if isinstance(values, list) else []:
        parsed = safe_int(value, 0)
        if parsed > 0:
            ids.append(parsed)
    return ids


def _passes_audience_targeting(conn, viewer_user_id, personalized_opt_out, item, cache: dict) -> bool:
    """Honor audience_mode + saved/excluded audiences at delivery time.

    Fail-safe rules (deliberate, see slice-2 spec):
    - A membership that cannot be evaluated cheaply counts as NON-matching
      for include lists and as MATCHING for exclude lists — when in doubt we
      withhold the ad rather than violate the advertiser's constraint.
    - Viewers who opted out of personalized ads (and anonymous viewers) never
      receive audience-constrained ads, because evaluating membership would
      use their engagement history.
    All lookups are cached per select_ads call so the hot path stays cheap.
    """
    mode = clean_text(item.get("audience_mode") or "", 20).lower()
    saved_ids = _parse_id_list(item.get("saved_audience_ids_json"))
    excluded_ids = _parse_id_list(item.get("excluded_audience_ids_json"))
    if mode in ("", "everyone") and not saved_ids and not excluded_ids:
        return True
    if personalized_opt_out or not viewer_user_id:
        return False
    owner_user_id = safe_int(item.get("account_owner_user_id"), 0)
    try:
        if mode == "followers" and not _viewer_follows_owner(conn, viewer_user_id, owner_user_id, cache):
            return False
        if mode == "non_followers" and _viewer_follows_owner(conn, viewer_user_id, owner_user_id, cache):
            return False
        if mode == "engaged" and not _viewer_engaged_with_owner(conn, viewer_user_id, owner_user_id, cache):
            return False
        from services import pulse_ads_audiences
        for audience_id in excluded_ids:
            member = pulse_ads_audiences.audience_membership(conn, audience_id, viewer_user_id, cache)
            # Fail-safe: unevaluable exclusions count as matches -> withhold.
            if member is None or member:
                return False
        if saved_ids:
            for audience_id in saved_ids:
                member = pulse_ads_audiences.audience_membership(conn, audience_id, viewer_user_id, cache)
                # None (unevaluable) counts as non-matching for includes.
                if member:
                    break
            else:
                return False
        return True
    except Exception:
        # Constrained ad we could not evaluate: withhold it.
        return False


def _adset_status_filter_ready(conn) -> bool:
    """Probe whether pulse_ad_adsets exists so select_ads can filter paused/archived ad sets."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT cr.adset_id, ads.status
            FROM pulse_ad_creatives cr
            LEFT JOIN pulse_ad_adsets ads ON ads.id=cr.adset_id
            WHERE 1=0
            """
        )
        cur.fetchall()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def select_ads(conn, user_id=None, session_id="", context="home", device_type="desktop", limit=3, **context_kwargs) -> list[dict]:
    if not platform_ads_enabled(conn):
        return []
    ctx = normalize_delivery_context(context=context, device_type=device_type, **context_kwargs)
    placement_keys = _candidate_placements(ctx["context"], ctx["device_type"])
    placement_hint = clean_text(context_kwargs.get("placement_hint") or "", 80)
    if placement_hint and placement_hint in placement_keys:
        placement_keys = [placement_hint]
    if not placement_keys:
        return []
    placeholders = ",".join(["?"] * len(placement_keys))
    now = now_iso()
    adset_filter_ready = _adset_status_filter_ready(conn)
    adset_join = "LEFT JOIN pulse_ad_adsets adset ON adset.id=cr.adset_id" if adset_filter_ready else ""
    adset_clause = "AND (cr.adset_id IS NULL OR COALESCE(adset.status,'active')='active')" if adset_filter_ready else ""
    audience_ready = _audience_filter_ready(conn)
    audience_columns = (
        ", t.audience_mode, t.saved_audience_ids_json, t.excluded_audience_ids_json, a.owner_user_id AS account_owner_user_id"
        if audience_ready else ""
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT cr.id AS creative_id, cr.creative_type, cr.title, cr.body,
               COALESCE(NULLIF(ma.playback_url, ''), NULLIF(ma.public_url, ''), NULLIF(cr.media_url, ''), '') AS media_url,
               COALESCE(NULLIF(ta.public_url, ''), NULLIF(ma.thumbnail_url, ''), NULLIF(ma.poster_url, ''), NULLIF(cr.thumbnail_url, ''), '') AS thumbnail_url,
               COALESCE(NULLIF(ma.playback_url, ''), '') AS playback_url,
               COALESCE(ma.media_type, cr.creative_type, '') AS media_type,
               COALESCE(ma.mime_type, '') AS mime_type,
               COALESCE(ma.width, 0) AS width,
               COALESCE(ma.height, 0) AS height,
               COALESCE(ma.duration_seconds, 0) AS duration_seconds,
               COALESCE(ma.file_size, 0) AS file_size,
               cr.destination_url, cr.call_to_action, c.id AS campaign_id, c.ad_account_id, c.status AS campaign_status,
               c.budget_type, c.daily_budget_cents, c.lifetime_budget_cents, c.spent_cents,
               COALESCE(c.priority, 0) AS campaign_priority,
               a.status AS account_status, a.business_type AS account_business_type,
               p.placement_key, p.max_frequency, p.device_type, p.placement_type,
               COALESCE(p.priority, 0) AS placement_priority,
               COALESCE(p.supported_creative_types, '') AS supported_creative_types,
               COALESCE(p.card_style, '') AS card_style,
               t.country, t.language, t.device_type AS target_device_type, t.premium_audience, t.contextual_category
               {audience_columns}
        FROM pulse_ad_creatives cr
        JOIN pulse_ad_campaigns c ON c.id=cr.campaign_id
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        JOIN pulse_ad_campaign_placements cp ON cp.campaign_id=c.id
        JOIN pulse_ad_placements p ON p.id=cp.placement_id
        LEFT JOIN pulse_ad_media_assets ma ON ma.id=cr.media_asset_id AND COALESCE(ma.deleted_at, '')=''
        LEFT JOIN pulse_ad_media_assets ta ON ta.id=cr.thumbnail_asset_id AND COALESCE(ta.deleted_at, '')=''
        LEFT JOIN pulse_ad_targeting t ON t.campaign_id=c.id
        {adset_join}
        WHERE p.placement_key IN ({placeholders})
          AND p.is_active=1
          AND c.status='active'
          AND a.status='active'
          AND lower(COALESCE(a.verification_status,'')) IN ('verified','approved')
          AND cr.moderation_status='approved'
          AND cr.status='approved'
          AND (cr.creative_type NOT IN ('image','video','audio') OR cr.media_asset_id IS NOT NULL)
          AND (cr.media_asset_id IS NULL OR ma.moderation_status='approved')
          AND (cr.thumbnail_asset_id IS NULL OR ta.moderation_status='approved')
          AND (c.start_at IS NULL OR c.start_at='' OR c.start_at<=?)
          AND (c.end_at IS NULL OR c.end_at='' OR c.end_at>=?)
          AND (p.device_type='all' OR p.device_type=?)
          {adset_clause}
        ORDER BY placement_priority DESC, campaign_priority DESC, cr.id DESC LIMIT ?
        """,
        (*placement_keys, now, now, ctx["device_type"], safe_int(limit, 3, 1, 10) * 8),
    )
    personalized_opt_out = user_personalized_ads_opt_out(conn, user_id)
    candidates = []
    audience_cache: dict = {}
    seen_by_placement = {key: _recent_campaigns(conn, user_id, session_id, key) for key in placement_keys}
    for row in cur.fetchall():
        item = row_to_dict(row)
        target = {
            "country": item.get("country"),
            "language": item.get("language"),
            "device_type": item.get("target_device_type"),
            "premium_audience": item.get("premium_audience"),
            "contextual_category": item.get("contextual_category"),
        }
        if not _matches_targeting(target, ctx, personalized_opt_out):
            continue
        if audience_ready and not _passes_audience_targeting(conn, user_id, personalized_opt_out, item, audience_cache):
            continue
        if not _compatible_creative(item.get("creative_type"), item.get("supported_creative_types")):
            continue
        if not _campaign_budget_available(conn, item):
            continue
        if not _frequency_allowed(conn, user_id, session_id, item.get("campaign_id"), item.get("placement_key"), item.get("max_frequency")):
            continue
        recent_penalty = 50 if item.get("campaign_id") in seen_by_placement.get(item.get("placement_key"), set()) else 0
        rotation_hash = int(hashlib.sha256(f"{now[:13]}:{session_id}:{item.get('creative_id')}:{item.get('placement_key')}".encode("utf-8")).hexdigest()[:8], 16) % 20
        item["_score"] = safe_int(item.get("placement_priority"), 0) * 100 + safe_int(item.get("campaign_priority"), 0) + rotation_hash - recent_penalty
        candidates.append(item)
    ads = []
    used_campaigns = set()
    for item in sorted(candidates, key=lambda entry: entry.get("_score", 0), reverse=True):
        if item.get("campaign_id") in used_campaigns and len(candidates) > safe_int(limit, 3, 1, 10):
            continue
        token, nonce = make_delivery_token(item.get("creative_id"), item.get("campaign_id"), item.get("placement_key"), user_id, session_id)
        item["delivery_token"] = token
        item["tracking_nonce"] = nonce
        item["expires_at"] = datetime.fromtimestamp(int(time.time()) + DELIVERY_TOKEN_TTL_SECONDS, tz=timezone.utc).replace(microsecond=0).isoformat()
        ads.append(sanitize_ad_payload(item))
        used_campaigns.add(item.get("campaign_id"))
        if len(ads) >= safe_int(limit, 3, 1, 10):
            break
    return ads


def _assert_served_creative(conn, creative_id, campaign_id, placement_key="") -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cr.id AS creative_id, cr.campaign_id, cr.destination_url, cr.status, cr.moderation_status,
               cr.creative_type, c.status AS campaign_status, p.placement_key,
               a.status AS account_status, a.verification_status AS account_verification_status,
               COALESCE(p.supported_creative_types, '') AS supported_creative_types
        FROM pulse_ad_creatives cr
        JOIN pulse_ad_campaigns c ON c.id=cr.campaign_id
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        JOIN pulse_ad_campaign_placements cp ON cp.campaign_id=c.id
        JOIN pulse_ad_placements p ON p.id=cp.placement_id
        WHERE cr.id=? AND c.id=? AND (?='' OR p.placement_key=?)
        """,
        (creative_id, campaign_id, clean_text(placement_key, 80), clean_text(placement_key, 80)),
    )
    creative = row_to_dict(cur.fetchone())
    if not creative:
        raise PulseAdsError("Ad creative not found.", 404)
    if creative.get("status") != "approved" or creative.get("moderation_status") != "approved" or creative.get("campaign_status") != "active":
        raise PulseAdsError("Ad is not eligible for tracking.", 403)
    # Account verification gates tracking independently of the campaign. A campaign
    # can be approved while the account is still in review, but no impression or
    # click may be recorded against an unverified (or suspended) account — the
    # explicit fail-closed check the a.status coupling used to only imply.
    if clean_text(creative.get("account_status"), 40).lower() == "suspended":
        raise PulseAdsError("Ad account is suspended.", 403)
    if account_verification_state({"verification_status": creative.get("account_verification_status")}) != "verified":
        raise PulseAdsError("Ad account is not verified.", 403)
    if placement_key and not _compatible_creative(creative.get("creative_type"), creative.get("supported_creative_types")):
        raise PulseAdsError("Ad is not compatible with this placement.", 403)
    return creative


def _validate_tracking_delivery(payload, creative_id, campaign_id, placement_key, viewer_user_id=None, session_id="") -> dict:
    token_payload = verify_delivery_token(payload.get("delivery_token"), creative_id, campaign_id, placement_key, viewer_user_id, session_id)
    nonce = clean_text(payload.get("tracking_nonce"), 64)
    if nonce != clean_text(token_payload.get("nonce"), 64):
        raise PulseAdsError("Ad tracking nonce mismatch.", 403)
    return token_payload


def record_impression(conn, payload: dict, viewer_user_id=None, session_id="", device_type="", viewport="") -> dict:
    creative_id = safe_int(payload.get("creative_id") or payload.get("ad_id"), minimum=1)
    campaign_id = safe_int(payload.get("campaign_id"), minimum=1)
    placement_key = clean_text(payload.get("placement_key"), 80)
    token_payload = _validate_tracking_delivery(payload, creative_id, campaign_id, placement_key, viewer_user_id, session_id)
    _assert_served_creative(conn, creative_id, campaign_id, placement_key)
    token_hash = hash_value(str(payload.get("delivery_token") or ""))[:64]
    cur = conn.cursor()
    now = now_iso()
    cur.execute("SELECT id FROM pulse_ad_impressions WHERE delivery_token_hash=? AND request_fingerprint=?", (token_hash, token_payload.get("nonce")))
    existing = row_to_dict(cur.fetchone())
    if existing:
        return {"ok": True, "impression_id": existing.get("id"), "deduped": True}
    try:
        from services import pulse_ad_payments
        spend_result = pulse_ad_payments.record_spend_event(
            conn,
            campaign_id,
            creative_id,
            placement_key,
            amount_cents=1,
            idempotency_key=f"impression-token:{token_hash}:{token_payload.get('nonce')}",
        )
        if spend_result.get("paused"):
            raise PulseAdsError("Campaign wallet balance is exhausted.", 409)
    except PulseAdsError:
        raise
    except Exception:
        pass
    cur.execute(
        """
        INSERT INTO pulse_ad_impressions
        (campaign_id, creative_id, placement_key, viewer_user_id, session_id, device_type, viewport, rendered_at, visible_ms, viewable, created_at,
         delivery_token_hash, request_fingerprint, country, language, contextual_category)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            campaign_id,
            creative_id,
            placement_key,
            viewer_user_id,
            session_id or "",
            clean_text(device_type, 40),
            clean_text(viewport, 80),
            now,
            now,
            token_hash,
            token_payload.get("nonce"),
            clean_text(payload.get("country"), 32),
            clean_text(payload.get("language"), 12),
            clean_text(payload.get("contextual_category"), 80),
        ),
    )
    impression_id = cur.lastrowid
    bump_frequency(conn, viewer_user_id, session_id, campaign_id, placement_key)
    conn.commit()
    return {"ok": True, "impression_id": impression_id}


def record_viewability(conn, payload: dict, viewer_user_id=None) -> dict:
    impression_id = safe_int(payload.get("impression_id"), minimum=1)
    visible_ms = safe_int(payload.get("visible_ms"), 0, 0, 3600_000)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pulse_ad_impressions
        SET visible_ms=?, viewable=?
        WHERE id=? AND (viewer_user_id=? OR viewer_user_id IS NULL)
        """,
        (visible_ms, 1 if visible_ms >= 1000 else 0, impression_id, viewer_user_id),
    )
    conn.commit()
    return {"ok": True, "viewable": visible_ms >= 1000}


def record_click(conn, payload: dict, viewer_user_id=None, session_id="") -> dict:
    creative_id = safe_int(payload.get("creative_id") or payload.get("ad_id"), minimum=1)
    campaign_id = safe_int(payload.get("campaign_id"), minimum=1)
    placement_key = clean_text(payload.get("placement_key"), 80)
    _validate_tracking_delivery(payload, creative_id, campaign_id, placement_key, viewer_user_id, session_id)
    creative = _assert_served_creative(conn, creative_id, campaign_id, placement_key)
    token_hash = hash_value(str(payload.get("delivery_token") or ""))[:64]
    cur = conn.cursor()
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_clicks
        (campaign_id, creative_id, placement_key, viewer_user_id, session_id, clicked_at, destination_url, created_at, delivery_token_hash, request_fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (campaign_id, creative_id, placement_key, viewer_user_id, session_id or "", now, creative.get("destination_url"), now, token_hash, clean_text(payload.get("tracking_nonce"), 64)),
    )
    click_id = cur.lastrowid
    conn.commit()
    # Best-effort: nudge the ads worker to attribute purchases behind this
    # click soon. Hour-bucketed key dedupes the flood; the worker's periodic
    # attribution cycle is the safety net if this enqueue ever fails.
    try:
        from services import pulse_ads_worker_service
        pulse_ads_worker_service.enqueue_job(
            conn,
            "attribution",
            "attribute_conversions",
            {"campaign_id": campaign_id},
            idempotency_key=f"attr:camp:{campaign_id}:{now[:13]}",
        )
    except Exception:
        pass
    return {"ok": True, "click_id": click_id, "destination_url": creative.get("destination_url")}


def record_event(conn, payload: dict, viewer_user_id=None, session_id="") -> dict:
    event_type = clean_text(payload.get("event_type"), 40).lower()
    if event_type not in VALID_EVENTS:
        raise PulseAdsError("Unsupported ad event.")
    creative_id = safe_int(payload.get("creative_id") or payload.get("ad_id"), minimum=1)
    campaign_id = safe_int(payload.get("campaign_id"), minimum=1)
    placement_key = clean_text(payload.get("placement_key"), 80)
    _validate_tracking_delivery(payload, creative_id, campaign_id, placement_key, viewer_user_id, session_id)
    _assert_served_creative(conn, creative_id, campaign_id, placement_key)
    metadata = {
        "viewer_user_id_hash": hash_value(str(viewer_user_id)) if viewer_user_id else "",
        "placement_key": placement_key,
        "reason": clean_text(payload.get("reason"), 200),
        "delivery_token_hash": hash_value(str(payload.get("delivery_token") or ""))[:64],
    }
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_events (campaign_id, creative_id, event_type, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (campaign_id, creative_id, event_type, clean_json(metadata), now_iso()),
    )
    event_id = cur.lastrowid
    conn.commit()
    if event_type in ("conversion", "add_to_cart", "purchase"):
        # Best-effort attribution nudge; worker cycle is the safety net.
        try:
            from services import pulse_ads_worker_service
            pulse_ads_worker_service.enqueue_job(
                conn,
                "attribution",
                "attribute_conversions",
                {"campaign_id": campaign_id},
                idempotency_key=f"attr:camp:{campaign_id}:{now_iso()[:13]}",
            )
        except Exception:
            pass
    return {"ok": True, "event_id": event_id}


def advertiser_analytics(conn, owner_user_id, account_id=None) -> dict:
    cur = conn.cursor()
    params = [owner_user_id]
    account_clause = ""
    if account_id:
        account_clause = " AND a.id=?"
        params.append(safe_int(account_id, minimum=1))
    # Three LEFT JOINs off one campaign produce a cartesian product: a campaign
    # with 10 impressions, 5 clicks and 3 events yields 150 rows. Every other
    # aggregate here is COUNT(DISTINCT ...) and survives that; `viewable` was a
    # bare SUM over the fanned-out rows and did not. It reported 90 viewable
    # impressions out of 10 — a 15x inflation (clicks x events) and a figure
    # larger than the impression count it is a subset of, which is how the
    # viewability rate came out at 900%.
    cur.execute(
        f"""
        SELECT a.id AS account_id, a.business_name, c.id AS campaign_id, c.campaign_name, c.status,
               COUNT(DISTINCT i.id) AS impressions,
               COUNT(DISTINCT CASE WHEN i.viewable=1 THEN i.id END) AS viewable_impressions,
               COUNT(DISTINCT cl.id) AS clicks,
               COUNT(DISTINCT CASE WHEN e.event_type='hide' THEN e.id END) AS hides,
               COUNT(DISTINCT CASE WHEN e.event_type='report' THEN e.id END) AS reports,
               COUNT(DISTINCT CASE WHEN e.event_type='conversion' THEN e.id END) AS conversions,
               COALESCE(c.spent_cents, 0) AS spent_cents
        FROM pulse_ad_accounts a
        LEFT JOIN pulse_ad_campaigns c ON c.ad_account_id=a.id
        LEFT JOIN pulse_ad_impressions i ON i.campaign_id=c.id
        LEFT JOIN pulse_ad_clicks cl ON cl.campaign_id=c.id
        LEFT JOIN pulse_ad_events e ON e.campaign_id=c.id
        WHERE a.owner_user_id=?{account_clause}
        GROUP BY a.id, a.business_name, c.id, c.campaign_name, c.status, c.spent_cents
        ORDER BY c.id DESC
        LIMIT 100
        """,
        tuple(params),
    )
    campaigns = []
    for row in cur.fetchall():
        item = row_to_dict(row)
        impressions = safe_int(item.get("impressions"), 0)
        clicks = safe_int(item.get("clicks"), 0)
        item["ctr"] = round((clicks / impressions) * 100, 2) if impressions else 0
        item["spend"] = f"${safe_int(item.get('spent_cents')) / 100:,.2f}"
        item["estimated_cpc"] = round((safe_int(item.get("spent_cents")) / 100) / clicks, 2) if clicks else 0
        item["estimated_cpm"] = round((safe_int(item.get("spent_cents")) / 100) / impressions * 1000, 2) if impressions else 0
        campaigns.append(item)
    totals = {
        "impressions": sum(safe_int(item.get("impressions"), 0) for item in campaigns),
        "viewable_impressions": sum(safe_int(item.get("viewable_impressions"), 0) for item in campaigns),
        "clicks": sum(safe_int(item.get("clicks"), 0) for item in campaigns),
        "hides": sum(safe_int(item.get("hides"), 0) for item in campaigns),
        "reports": sum(safe_int(item.get("reports"), 0) for item in campaigns),
        # `conversion` has been an accepted event type in VALID_EVENTS since the
        # start and `record_event` writes it, but nothing has ever read it back.
        # A count is the whole of the attribution model that exists: there is no
        # order link, no value, and therefore no attributed revenue to report or
        # to adjust after a refund. Clients must present it as a count of
        # reported events, never as revenue.
        "conversions": sum(safe_int(item.get("conversions"), 0) for item in campaigns),
        "spend_cents": sum(safe_int(item.get("spent_cents"), 0) for item in campaigns),
    }
    totals["ctr"] = round((totals["clicks"] / totals["impressions"]) * 100, 2) if totals["impressions"] else 0
    totals["spend"] = f"${totals['spend_cents'] / 100:,.2f}"
    totals["estimated_cpc"] = round((totals["spend_cents"] / 100) / totals["clicks"], 2) if totals["clicks"] else 0
    totals["estimated_cpm"] = round((totals["spend_cents"] / 100) / totals["impressions"] * 1000, 2) if totals["impressions"] else 0
    return {"totals": totals, "campaigns": campaigns}
