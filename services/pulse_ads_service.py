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
VALID_CREATIVE_TYPES = {"image", "video", "text", "hologram", "audio"}
VALID_OBJECTIVES = {"awareness", "traffic", "engagement", "creator_growth", "marketplace", "radio"}
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
        "supported_creative_types": ["image", "video", "text", "hologram", "audio"],
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
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise PulseAdsError("Destination URL must be http or https.")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        raise PulseAdsError("Local destination URLs are not allowed.")
    if any(cleaned.lower().startswith(prefix) for prefix in ("javascript:", "data:", "file:", "vbscript:")):
        raise PulseAdsError("Unsafe destination URL.")
    return cleaned


def validate_media_url(url: str) -> str:
    return validate_destination_url(url, required=False)


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
    placement_keys = payload.get("placements") or ["feed_inline"]
    attach_campaign_placements(conn, campaign_id, placement_keys)
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
    return [row_to_dict(row) for row in cur.fetchall()]


def attach_campaign_placements(conn, campaign_id, placement_keys) -> None:
    cur = conn.cursor()
    if isinstance(placement_keys, str):
        placement_keys = [placement_keys]
    cleaned = [clean_text(key, 80) for key in (placement_keys or []) if key]
    if not cleaned:
        cleaned = ["feed_inline"]
    for key in cleaned[:8]:
        cur.execute("SELECT id FROM pulse_ad_placements WHERE placement_key=? AND is_active=1", (key,))
        row = cur.fetchone()
        if not row:
            continue
        placement_id = row_to_dict(row).get("id")
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
    creative_type = clean_text(payload.get("creative_type") or "text", 30).lower()
    if creative_type not in VALID_CREATIVE_TYPES:
        raise PulseAdsError("Unsupported creative type.")
    title = clean_text(payload.get("title"), TEXT_LIMITS["title"])
    body = clean_text(payload.get("body"), TEXT_LIMITS["body"])
    destination_url = validate_destination_url(payload.get("destination_url"), required=True)
    if not title:
        raise PulseAdsError("Creative title is required.")
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_creatives
        (ad_account_id, campaign_id, creative_type, title, body, media_url, thumbnail_url, destination_url, call_to_action, status, moderation_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 'draft', ?, ?)
        """,
        (
            campaign.get("ad_account_id"),
            campaign_id,
            creative_type,
            title,
            body,
            validate_media_url(payload.get("media_url")),
            validate_media_url(payload.get("thumbnail_url")),
            destination_url,
            clean_text(payload.get("call_to_action") or "Learn more", TEXT_LIMITS["call_to_action"]),
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
    return creative


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
    return [row_to_dict(row) for row in cur.fetchall()]


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
               cr.destination_url, cr.moderation_status, c.id AS campaign_id, c.campaign_name, a.business_name
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
        rows.append(item)
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
    return {"ok": True, "creative_id": creative_id, "moderation_status": "approved"}


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
        today = now_iso()[:10]
        cur.execute(
            "SELECT COUNT(*) AS c FROM pulse_ad_impressions WHERE campaign_id=? AND created_at>=?",
            (campaign.get("campaign_id") or campaign.get("id"), today),
        )
        impressions_today = safe_int(row_to_dict(cur.fetchone()).get("c"), 0)
        estimated_daily_spend = impressions_today
        if estimated_daily_spend >= daily:
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


def select_ads(conn, user_id=None, session_id="", context="home", device_type="desktop", limit=3, **context_kwargs) -> list[dict]:
    if not platform_ads_enabled(conn):
        return []
    ctx = normalize_delivery_context(context=context, device_type=device_type, **context_kwargs)
    placement_keys = _candidate_placements(ctx["context"], ctx["device_type"])
    if not placement_keys:
        return []
    placeholders = ",".join(["?"] * len(placement_keys))
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT cr.id AS creative_id, cr.creative_type, cr.title, cr.body, cr.media_url, cr.thumbnail_url,
               cr.destination_url, cr.call_to_action, c.id AS campaign_id, c.status AS campaign_status,
               c.budget_type, c.daily_budget_cents, c.lifetime_budget_cents, c.spent_cents,
               COALESCE(c.priority, 0) AS campaign_priority,
               a.status AS account_status,
               p.placement_key, p.max_frequency, p.device_type, p.placement_type,
               COALESCE(p.priority, 0) AS placement_priority,
               COALESCE(p.supported_creative_types, '') AS supported_creative_types,
               COALESCE(p.card_style, '') AS card_style,
               t.country, t.language, t.device_type AS target_device_type, t.premium_audience, t.contextual_category
        FROM pulse_ad_creatives cr
        JOIN pulse_ad_campaigns c ON c.id=cr.campaign_id
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        JOIN pulse_ad_campaign_placements cp ON cp.campaign_id=c.id
        JOIN pulse_ad_placements p ON p.id=cp.placement_id
        LEFT JOIN pulse_ad_targeting t ON t.campaign_id=c.id
        WHERE p.placement_key IN ({placeholders})
          AND p.is_active=1
          AND c.status='active'
          AND a.status='active'
          AND cr.moderation_status='approved'
          AND cr.status='approved'
          AND (c.start_at IS NULL OR c.start_at='' OR c.start_at<=?)
          AND (c.end_at IS NULL OR c.end_at='' OR c.end_at>=?)
          AND (p.device_type='all' OR p.device_type=?)
        ORDER BY placement_priority DESC, campaign_priority DESC, cr.id DESC LIMIT ?
        """,
        (*placement_keys, now, now, ctx["device_type"], safe_int(limit, 3, 1, 10) * 8),
    )
    personalized_opt_out = user_personalized_ads_opt_out(conn, user_id)
    candidates = []
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
               COALESCE(p.supported_creative_types, '') AS supported_creative_types
        FROM pulse_ad_creatives cr
        JOIN pulse_ad_campaigns c ON c.id=cr.campaign_id
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
    return {"ok": True, "event_id": event_id}


def advertiser_analytics(conn, owner_user_id, account_id=None) -> dict:
    cur = conn.cursor()
    params = [owner_user_id]
    account_clause = ""
    if account_id:
        account_clause = " AND a.id=?"
        params.append(safe_int(account_id, minimum=1))
    cur.execute(
        f"""
        SELECT a.id AS account_id, a.business_name, c.id AS campaign_id, c.campaign_name, c.status,
               COUNT(DISTINCT i.id) AS impressions,
               SUM(CASE WHEN i.viewable=1 THEN 1 ELSE 0 END) AS viewable_impressions,
               COUNT(DISTINCT cl.id) AS clicks,
               COUNT(DISTINCT CASE WHEN e.event_type='hide' THEN e.id END) AS hides,
               COUNT(DISTINCT CASE WHEN e.event_type='report' THEN e.id END) AS reports
        FROM pulse_ad_accounts a
        LEFT JOIN pulse_ad_campaigns c ON c.ad_account_id=a.id
        LEFT JOIN pulse_ad_impressions i ON i.campaign_id=c.id
        LEFT JOIN pulse_ad_clicks cl ON cl.campaign_id=c.id
        LEFT JOIN pulse_ad_events e ON e.campaign_id=c.id
        WHERE a.owner_user_id=?{account_clause}
        GROUP BY a.id, a.business_name, c.id, c.campaign_name, c.status
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
        campaigns.append(item)
    totals = {
        "impressions": sum(safe_int(item.get("impressions"), 0) for item in campaigns),
        "viewable_impressions": sum(safe_int(item.get("viewable_impressions"), 0) for item in campaigns),
        "clicks": sum(safe_int(item.get("clicks"), 0) for item in campaigns),
        "hides": sum(safe_int(item.get("hides"), 0) for item in campaigns),
        "reports": sum(safe_int(item.get("reports"), 0) for item in campaigns),
    }
    totals["ctr"] = round((totals["clicks"] / totals["impressions"]) * 100, 2) if totals["impressions"] else 0
    return {"totals": totals, "campaigns": campaigns}
