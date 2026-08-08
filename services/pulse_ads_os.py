"""PulseSoc Advertising OS — aggregation and orchestration layer.

Everything the new /api/pulse/ads Advertising OS endpoints do lives here so
bot.py stays a thin routing layer. This module deliberately reuses the
existing engine — pulse_ads_service for accounts/campaigns/creatives,
pulse_ad_payments for money, pulse_advertiser_portal for lifecycle — and adds
targeting, saved audiences, content inventory, one-shot campaign creation,
wallet depth, reports, insights and the policy center on top of it.

Analytics rules of the house: numbers come from real rows or they are zero.
No estimate is ever inflated, no metric is ever invented.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from services import pulse_ad_payments, pulse_ads_service, pulse_advertiser_portal
from services import pulsesoc_promotions
from services.pulse_ads_service import (
    PulseAdsError,
    canonical_objective,
    clean_json,
    clean_text,
    now_iso,
    row_to_dict,
    safe_int,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def ensure_schema(conn) -> None:
    """Idempotent slice-2 additions: appeal decision columns on
    pulse_ad_appeals. Safe to call from bot.init_db and from tests."""
    cur = conn.cursor()
    for column, decl in (
        ("appeal_type", "TEXT DEFAULT ''"),
        ("reason_json", "TEXT DEFAULT '{}'"),
        ("decision", "TEXT DEFAULT ''"),
        ("decision_reason", "TEXT DEFAULT ''"),
        ("decided_by_user_id", "INTEGER DEFAULT 0"),
        ("decided_at", "TEXT DEFAULT ''"),
    ):
        try:
            cur.execute(f"ALTER TABLE pulse_ad_appeals ADD COLUMN {column} {decl}")
        except Exception:
            pass  # column already exists (or table absent on legacy schemas)
    try:
        conn.commit()
    except Exception:
        pass


AUDIENCE_MODES = {"everyone", "followers", "non_followers", "engaged"}
TARGETING_DEVICE_TYPES = {"all", "mobile", "desktop"}
PAID_TRANSACTION_STATUSES = ("paid", "completed", "succeeded", "settled", "released", "delivered")
SPEND_DESCRIPTION_PREFIX = "Ad delivery spend for "

# Placements picked automatically when a full-create request sends an empty
# placements list. Keyed by canonical objective; every key here exists in
# pulse_ads_service.PLACEMENTS.
AUTO_PLACEMENTS_BY_OBJECTIVE = {
    "awareness": ["feed_inline", "feed_inline_ufo_mobile"],
    "engagement": ["feed_inline", "feed_inline_ufo_mobile"],
    "video_views": ["video_pre_roll", "feed_inline"],
    "website_traffic": ["feed_inline", "search_sponsored_result"],
    "messages": ["feed_inline", "feed_inline_ufo_mobile"],
    "marketplace_sales": ["marketplace_sponsor", "feed_inline"],
    "app_activity": ["feed_inline", "feed_inline_ufo_mobile"],
    "lead_generation": ["feed_inline", "feed_inline_ufo_mobile"],
    "event_promotion": ["feed_inline", "feed_inline_ufo_mobile"],
    "profile_growth": ["profile_sponsor", "feed_inline"],
    "live_promotion": ["feed_inline", "status_interstitial"],
}


def _parse_list(value, max_items=50, max_len=80, lower=False, upper=False) -> list:
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    items = []
    for raw in value or []:
        text = clean_text(raw, max_len)
        if lower:
            text = text.lower()
        if upper:
            text = text.upper()
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _parse_int_list(value, max_items=25) -> list[int]:
    items = []
    for raw in value if isinstance(value, (list, tuple)) else []:
        number = safe_int(raw, 0)
        if number > 0 and number not in items:
            items.append(number)
        if len(items) >= max_items:
            break
    return items


def _json_list(text) -> list:
    try:
        parsed = json.loads(text or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _json_dict(text) -> dict:
    try:
        parsed = json.loads(text or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_ts(value):
    text = clean_text(value, 40)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


class _DeferredCommit:
    """Connection proxy whose commit() is a no-op.

    The existing service functions each commit as they go. The full-create
    orchestrator needs all of their writes to land in ONE transaction, so it
    hands them this proxy and performs the single real commit (or rollback)
    itself.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)

    def commit(self):
        return None

    def cursor(self):
        return self._conn.cursor()

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# Item 2 — Targeting
# ---------------------------------------------------------------------------

def _targeting_row(conn, campaign_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_targeting WHERE campaign_id=? ORDER BY id DESC LIMIT 1",
        (campaign_id,),
    )
    return row_to_dict(cur.fetchone())


def _targeting_public(campaign_id, row: dict) -> dict:
    row = row or {}
    return {
        "campaign_id": campaign_id,
        "countries": _parse_list(_json_list(row.get("countries_json")), upper=True) or ([row.get("country").upper()] if clean_text(row.get("country"), 8) else []),
        "languages": _parse_list(_json_list(row.get("languages_json")), lower=True) or ([row.get("language").lower()] if clean_text(row.get("language"), 12) else []),
        "min_age": safe_int(row.get("min_age"), 0) or None,
        "max_age": safe_int(row.get("max_age"), 0) or None,
        "device_type": clean_text(row.get("device_type"), 20).lower() or "all",
        "interests": _parse_list(_json_list(row.get("interests_json")), lower=True),
        "keywords": _parse_list(_json_list(row.get("keywords_json")), lower=True),
        "audience_mode": clean_text(row.get("audience_mode"), 20).lower() or "everyone",
        "saved_audience_ids": _parse_int_list(_json_list(row.get("saved_audience_ids_json"))),
        "excluded_audience_ids": _parse_int_list(_json_list(row.get("excluded_audience_ids_json"))),
        "updated_at": row.get("updated_at") or "",
    }


def _dob_bounds(min_age, max_age) -> tuple[str, str]:
    """Date-of-birth string bounds for an age range, today-anchored.

    Returns (dob_min, dob_max) — someone aged within [min_age, max_age] has a
    date of birth between dob_min and dob_max (ISO date strings compare
    correctly as text).
    """
    today = datetime.now(timezone.utc).date()
    dob_max = ""
    dob_min = ""
    if min_age:
        try:
            dob_max = today.replace(year=today.year - min_age).isoformat()
        except ValueError:  # Feb 29
            dob_max = today.replace(year=today.year - min_age, day=28).isoformat()
    if max_age:
        try:
            dob_min = today.replace(year=today.year - max_age - 1).isoformat()
        except ValueError:
            dob_min = today.replace(year=today.year - max_age - 1, day=28).isoformat()
    return dob_min, dob_max


def _estimate_audience(conn, owner_user_id, targeting: dict) -> dict:
    """Real-count audience estimate. Only filters dimensions we actually store
    (country, preferred_language, date_of_birth, follow/engagement graph).
    Device, interests and keywords are not stored per-user, so they do not
    narrow the estimate — better honest-broad than invented-narrow. The
    estimate is never inflated: estimated_max is the literal matched count.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        base = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    except Exception:
        base = 0
    where = ["1=1"]
    params: list = []
    countries = targeting.get("countries") or []
    if countries:
        marks = ",".join("?" for _ in countries)
        where.append(f"UPPER(COALESCE(country,'')) IN ({marks})")
        params.extend(countries)
    languages = targeting.get("languages") or []
    if languages:
        marks = ",".join("?" for _ in languages)
        where.append(f"LOWER(COALESCE(preferred_language,'')) IN ({marks})")
        params.extend(languages)
    dob_min, dob_max = _dob_bounds(safe_int(targeting.get("min_age"), 0), safe_int(targeting.get("max_age"), 0))
    if dob_max:
        where.append("COALESCE(date_of_birth,'') != '' AND date_of_birth <= ?")
        params.append(dob_max)
    if dob_min:
        where.append("COALESCE(date_of_birth,'') != '' AND date_of_birth >= ?")
        params.append(dob_min)
    mode = clean_text(targeting.get("audience_mode"), 20).lower() or "everyone"
    if mode == "followers":
        where.append("user_id IN (SELECT follower_user_id FROM pulse_follows WHERE followed_user_id=?)")
        params.append(owner_user_id)
    elif mode == "non_followers":
        where.append("user_id NOT IN (SELECT follower_user_id FROM pulse_follows WHERE followed_user_id=?)")
        params.append(owner_user_id)
    elif mode == "engaged":
        where.append(
            """user_id IN (
                SELECT r.user_id FROM pulse_reactions r
                JOIN pulse_posts p ON p.id=r.post_id WHERE p.user_id=?
                UNION
                SELECT cm.user_id FROM pulse_comments cm
                JOIN pulse_posts p2 ON p2.id=cm.post_id
                WHERE p2.user_id=? AND cm.deleted_at IS NULL
            )"""
        )
        params.extend([owner_user_id, owner_user_id])
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM users WHERE {' AND '.join(where)}", tuple(params))
        matched = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    except Exception:
        matched = 0
    if matched < 1000:
        band = "narrow"
    elif base and matched > base * 0.5:
        band = "broad"
    else:
        band = "good"
    return {
        "estimated_min": int(matched * 0.8),
        "estimated_max": matched,
        "band": band,
    }


def get_targeting(conn, user_id, campaign_id) -> dict:
    pulse_ads_service._owned_campaign(conn, user_id, campaign_id)
    public = _targeting_public(campaign_id, _targeting_row(conn, campaign_id))
    public["estimate"] = _estimate_audience(conn, user_id, public)
    return public


def _validate_audience_ids(conn, account_id, ids: list[int]) -> list[int]:
    if not ids:
        return []
    cur = conn.cursor()
    valid = []
    for audience_id in ids:
        cur.execute(
            "SELECT id FROM pulse_ad_saved_audiences WHERE id=? AND account_id=? AND COALESCE(archived_at,'')=''",
            (audience_id, account_id),
        )
        if not cur.fetchone():
            raise PulseAdsError(f"Saved audience {audience_id} does not exist on this ad account.", 404)
        valid.append(audience_id)
    return valid


def put_targeting(conn, user_id, campaign_id, payload: dict, *, commit=True) -> dict:
    campaign = pulse_ads_service._owned_campaign(conn, user_id, campaign_id)
    payload = payload or {}
    countries = _parse_list(payload.get("countries"), max_items=50, max_len=8, upper=True)
    languages = _parse_list(payload.get("languages"), max_items=50, max_len=12, lower=True)
    min_age = safe_int(payload.get("min_age"), 0, 0, 120)
    max_age = safe_int(payload.get("max_age"), 0, 0, 120)
    if min_age and min_age < 13:
        raise PulseAdsError("Ads cannot target users under 13.")
    if min_age and max_age and max_age < min_age:
        raise PulseAdsError("max_age must be greater than or equal to min_age.")
    device_type = clean_text(payload.get("device_type") or "all", 20).lower()
    if device_type not in TARGETING_DEVICE_TYPES:
        raise PulseAdsError("device_type must be one of all, mobile, desktop.")
    interests = _parse_list(payload.get("interests"), max_items=50, lower=True)
    keywords = _parse_list(payload.get("keywords"), max_items=50, lower=True)
    audience_mode = clean_text(payload.get("audience_mode") or "everyone", 20).lower()
    if audience_mode not in AUDIENCE_MODES:
        raise PulseAdsError("audience_mode must be one of everyone, followers, non_followers, engaged.")
    account_id = safe_int(campaign.get("ad_account_id"), minimum=1)
    saved_ids = _validate_audience_ids(conn, account_id, _parse_int_list(payload.get("saved_audience_ids")))
    excluded_ids = _validate_audience_ids(conn, account_id, _parse_int_list(payload.get("excluded_audience_ids")))
    now = now_iso()
    cur = conn.cursor()
    existing = _targeting_row(conn, campaign_id)
    values = (
        countries[0] if countries else "",
        languages[0] if languages else "",
        clean_json(interests, 4000),
        clean_json(keywords, 4000),
        device_type,
        min_age or None,
        max_age or None,
        audience_mode,
        clean_json(countries, 4000),
        clean_json(languages, 4000),
        clean_json(saved_ids, 2000),
        clean_json(excluded_ids, 2000),
        now,
    )
    if existing:
        cur.execute(
            """
            UPDATE pulse_ad_targeting
            SET country=?, language=?, interests_json=?, keywords_json=?, device_type=?,
                min_age=?, max_age=?, audience_mode=?, countries_json=?, languages_json=?,
                saved_audience_ids_json=?, excluded_audience_ids_json=?, updated_at=?
            WHERE id=?
            """,
            values + (existing.get("id"),),
        )
        # One row per campaign: collapse legacy duplicates.
        cur.execute("DELETE FROM pulse_ad_targeting WHERE campaign_id=? AND id!=?", (campaign_id, existing.get("id")))
    else:
        cur.execute(
            """
            INSERT INTO pulse_ad_targeting
            (country, language, interests_json, keywords_json, device_type, min_age, max_age,
             audience_mode, countries_json, languages_json, saved_audience_ids_json,
             excluded_audience_ids_json, updated_at, campaign_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values + (campaign_id, now),
        )
    pulse_ads_service.audit_log(conn, user_id, "ad_targeting_updated", "pulse_ad_targeting", campaign_id, after={"audience_mode": audience_mode})
    if commit:
        conn.commit()
    public = _targeting_public(campaign_id, _targeting_row(conn, campaign_id))
    public["estimate"] = _estimate_audience(conn, user_id, public)
    return public


# ---------------------------------------------------------------------------
# Item 3 — Saved audiences + engagement presets
# ---------------------------------------------------------------------------

def _audience_public(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "account_id": row.get("account_id"),
        "name": row.get("name") or "",
        "kind": row.get("kind") or "saved",
        "definition": _json_dict(row.get("definition_json")),
        "estimated_size": safe_int(row.get("estimated_size"), 0),
        "archived": bool(clean_text(row.get("archived_at"), 40)),
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }


def _engagement_presets(conn, owner_user_id) -> list[dict]:
    """Live counts from real engagement sources. A preset whose source query
    cannot be confirmed (missing table/column) is omitted, not zeroed-in-fake.
    """
    cur = conn.cursor()
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    presets = []
    try:
        cur.execute(
            """
            SELECT COUNT(DISTINCT engaged_user) AS n FROM (
                SELECT r.user_id AS engaged_user FROM pulse_reactions r
                JOIN pulse_posts p ON p.id=r.post_id
                WHERE p.user_id=? AND COALESCE(r.created_at,'') >= ?
                UNION
                SELECT cm.user_id AS engaged_user FROM pulse_comments cm
                JOIN pulse_posts p2 ON p2.id=cm.post_id
                WHERE p2.user_id=? AND cm.deleted_at IS NULL AND COALESCE(cm.created_at,'') >= ?
            )
            """,
            (owner_user_id, cutoff_30d, owner_user_id, cutoff_30d),
        )
        presets.append({
            "key": "engaged_30d",
            "name": "Engaged with your content (30 days)",
            "estimated_size": safe_int(row_to_dict(cur.fetchone()).get("n"), 0),
        })
    except Exception:
        pass
    try:
        cur.execute(
            """
            SELECT COUNT(DISTINCT vv.viewer_user_id) AS n
            FROM pulse_video_views vv
            JOIN pulse_videos v ON v.id=vv.video_id
            WHERE v.owner_user_id=? AND vv.viewer_user_id IS NOT NULL
            """,
            (owner_user_id,),
        )
        presets.append({
            "key": "video_viewers",
            "name": "Watched your videos",
            "estimated_size": safe_int(row_to_dict(cur.fetchone()).get("n"), 0),
        })
    except Exception:
        pass
    try:
        cur.execute("SELECT COUNT(*) AS n FROM pulse_follows WHERE followed_user_id=?", (owner_user_id,))
        presets.append({
            "key": "profile_engaged",
            "name": "Follow your profile",
            "estimated_size": safe_int(row_to_dict(cur.fetchone()).get("n"), 0),
        })
    except Exception:
        pass
    try:
        marks = ",".join("?" for _ in PAID_TRANSACTION_STATUSES)
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT buyer_user_id) AS n FROM seller_transactions
            WHERE seller_user_id=? AND item_type='marketplace_product'
              AND LOWER(COALESCE(status,'')) IN ({marks})
            """,
            (owner_user_id, *PAID_TRANSACTION_STATUSES),
        )
        presets.append({
            "key": "previous_customers",
            "name": "Bought from you before",
            "estimated_size": safe_int(row_to_dict(cur.fetchone()).get("n"), 0),
        })
    except Exception:
        pass
    return presets


def list_audiences(conn, user_id, account_id) -> dict:
    pulse_ads_service._owned_account(conn, user_id, account_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_saved_audiences WHERE account_id=? AND COALESCE(archived_at,'')='' ORDER BY id DESC LIMIT 200",
        (account_id,),
    )
    audiences = [_audience_public(row_to_dict(row)) for row in cur.fetchall()]
    return {"audiences": audiences, "engagement_presets": _engagement_presets(conn, user_id)}


def create_audience(conn, user_id, payload: dict) -> dict:
    payload = payload or {}
    account_id = safe_int(payload.get("account_id"), minimum=1)
    pulse_ads_service._owned_account(conn, user_id, account_id)
    name = clean_text(payload.get("name"), 120)
    if not name:
        raise PulseAdsError("Audience name is required.")
    kind = clean_text(payload.get("kind") or "saved", 40).lower() or "saved"
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    estimated_size = safe_int(payload.get("estimated_size"), 0, 0)
    # Custom first-party kinds and lookalikes never trust a client-sent
    # estimate: the count comes from the live source (or the dedicated
    # lookalike endpoint) or it is zero.
    from services import pulse_ads_audiences as _audiences
    if kind == "lookalike":
        raise PulseAdsError("Create lookalike audiences through the lookalike endpoint so the seed can be validated.")
    if kind in _audiences.CUSTOM_AUDIENCE_SOURCES:
        definition = _audiences.validate_custom_definition(kind, definition)
        estimated_size = _audiences.estimate_for_audience(conn, user_id, kind, definition)["estimated_size"]
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_saved_audiences
        (account_id, name, kind, definition_json, estimated_size, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (account_id, name, kind, clean_json(definition, 6000), estimated_size, now, now),
    )
    audience_id = cur.lastrowid
    pulse_ads_service.audit_log(conn, user_id, "ad_audience_created", "pulse_ad_saved_audiences", audience_id, after={"name": name})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_saved_audiences WHERE id=?", (audience_id,))
    return _audience_public(row_to_dict(cur.fetchone()))


def _owned_audience(conn, user_id, audience_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sa.* FROM pulse_ad_saved_audiences sa
        JOIN pulse_ad_accounts a ON a.id=sa.account_id
        WHERE sa.id=? AND a.owner_user_id=?
        """,
        (audience_id, user_id),
    )
    audience = row_to_dict(cur.fetchone())
    if not audience:
        raise PulseAdsError("Saved audience not found.", 404)
    return audience


def update_audience(conn, user_id, audience_id, payload: dict) -> dict:
    audience = _owned_audience(conn, user_id, audience_id)
    payload = payload or {}
    name = clean_text(payload.get("name"), 120) if "name" in payload else audience.get("name")
    if not name:
        raise PulseAdsError("Audience name is required.")
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else _json_dict(audience.get("definition_json"))
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_saved_audiences SET name=?, definition_json=?, estimated_size=?, updated_at=? WHERE id=?",
        (
            name,
            clean_json(definition, 6000),
            safe_int(payload.get("estimated_size"), safe_int(audience.get("estimated_size"), 0), 0),
            now_iso(),
            audience_id,
        ),
    )
    pulse_ads_service.audit_log(conn, user_id, "ad_audience_updated", "pulse_ad_saved_audiences", audience_id, after={"name": name})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_saved_audiences WHERE id=?", (audience_id,))
    return _audience_public(row_to_dict(cur.fetchone()))


def archive_audience(conn, user_id, audience_id) -> dict:
    _owned_audience(conn, user_id, audience_id)
    cur = conn.cursor()
    now = now_iso()
    cur.execute("UPDATE pulse_ad_saved_audiences SET archived_at=?, updated_at=? WHERE id=?", (now, now, audience_id))
    pulse_ads_service.audit_log(conn, user_id, "ad_audience_archived", "pulse_ad_saved_audiences", audience_id, after={})
    conn.commit()
    return {"audience_id": audience_id, "archived": True}


# ---------------------------------------------------------------------------
# Item 4 — Content inventory
# ---------------------------------------------------------------------------

INVENTORY_KINDS = ("post", "reel", "video", "event", "listing")
_KIND_TO_PROMOTION_TYPE = {
    "post": "post",
    "reel": "reel",
    "video": "video",
    "event": "event",
    "listing": "marketplace_listing",
}


def _inventory_ids(conn, user_id, kind: str, limit: int) -> list[dict]:
    cur = conn.cursor()
    try:
        if kind == "post":
            cur.execute(
                """
                SELECT id, created_at FROM pulse_posts
                WHERE user_id=? AND deleted_at IS NULL AND COALESCE(post_type,'post') != 'event'
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        elif kind == "event":
            cur.execute(
                """
                SELECT id, created_at FROM pulse_posts
                WHERE user_id=? AND deleted_at IS NULL AND COALESCE(post_type,'post') = 'event'
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        elif kind == "reel":
            cur.execute(
                """
                SELECT id, created_at FROM pulse_reels
                WHERE user_id=? AND COALESCE(status,'active') != 'deleted'
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        elif kind == "video":
            cur.execute(
                "SELECT id, created_at FROM pulse_videos WHERE owner_user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
        elif kind == "listing":
            cur.execute(
                """
                SELECT id, created_at FROM marketplace_listings
                WHERE seller_user_id=? AND LOWER(COALESCE(status,'')) IN ('active','review_ready')
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        else:
            return []
        return [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        return []


def _content_metrics(conn, kind: str, content_id: int) -> dict:
    """Real engagement counts where a source exists; zeros where it does not."""
    cur = conn.cursor()
    views = likes = comments = 0
    try:
        if kind in {"post", "event", "reel"}:
            post_id = content_id
            if kind == "reel":
                cur.execute("SELECT post_id FROM pulse_reels WHERE id=?", (content_id,))
                post_id = safe_int(row_to_dict(cur.fetchone()).get("post_id"), 0)
            if post_id:
                cur.execute("SELECT COUNT(*) AS n FROM pulse_reactions WHERE post_id=?", (post_id,))
                likes = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
                cur.execute("SELECT COUNT(*) AS n FROM pulse_comments WHERE post_id=? AND deleted_at IS NULL", (post_id,))
                comments = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
        elif kind == "video":
            cur.execute("SELECT COALESCE(view_count,0) AS n FROM pulse_videos WHERE id=?", (content_id,))
            views = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    except Exception:
        pass
    return {"views": views, "likes": likes, "comments": comments}


def content_inventory(conn, user_id, kinds=None, limit=25) -> dict:
    limit = safe_int(limit, 25, 1, 50)
    requested = [k for k in _parse_list(kinds, lower=True) if k in INVENTORY_KINDS] or list(INVENTORY_KINDS)
    items = []
    for kind in requested:
        promo_type = _KIND_TO_PROMOTION_TYPE[kind]
        for stub in _inventory_ids(conn, user_id, kind, limit):
            content_id = safe_int(stub.get("id"), 0)
            content = pulsesoc_promotions._query_content(conn, promo_type, content_id)
            if not content or safe_int(content.get("owner_user_id"), 0) != safe_int(user_id, 0):
                continue
            eligible, reason = pulsesoc_promotions._content_eligibility(content)
            _media_url, thumbnail_url = pulse_ads_service._content_ref_media(
                conn, "listing" if kind == "listing" else kind, content_id
            )
            items.append({
                "kind": kind,
                "id": content_id,
                "title": clean_text(content.get("title"), 140),
                "thumbnail_url": thumbnail_url,
                "created_at": stub.get("created_at") or "",
                "metrics": _content_metrics(conn, kind, content_id),
                "eligible": bool(eligible),
                "ineligible_reason": "" if eligible else reason,
            })
    return {"items": items}


# ---------------------------------------------------------------------------
# Item 6 — Full campaign create (single transaction, idempotent)
# ---------------------------------------------------------------------------

def _idempotency_lookup(conn, scope: str, key: str) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT result_json FROM pulse_ad_idempotency WHERE scope=? AND idem_key=?", (scope, key))
    row = row_to_dict(cur.fetchone())
    if not row:
        return {}
    return _json_dict(row.get("result_json"))


def _auto_placements(conn, objective: str, creative_payload: dict) -> list[str]:
    picks = list(AUTO_PLACEMENTS_BY_OBJECTIVE.get(canonical_objective(objective), ["feed_inline"]))
    creative_type = clean_text((creative_payload or {}).get("creative_type"), 30).lower()
    allowed = pulse_ads_service.CONTENT_CREATIVE_PLACEMENTS.get(creative_type)
    if allowed:
        compatible = [key for key in picks if key in allowed]
        picks = compatible or sorted(allowed)[:2]
    cur = conn.cursor()
    existing = []
    for key in picks:
        cur.execute("SELECT id FROM pulse_ad_placements WHERE placement_key=? AND is_active=1", (key,))
        if cur.fetchone():
            existing.append(key)
    return existing or ["feed_inline"]


def create_campaign_full(conn, user_id, payload: dict) -> dict:
    payload = payload or {}
    idem_key = clean_text(payload.get("idempotency_key"), 180)
    if not idem_key:
        raise PulseAdsError("idempotency_key is required for full campaign creation.")
    scope = "campaign_full"
    replay = _idempotency_lookup(conn, scope, idem_key)
    if replay:
        return {**replay, "ok": True, "duplicate": True}
    account_id = safe_int(payload.get("ad_account_id"), minimum=1)
    pulse_ads_service._owned_account(conn, user_id, account_id)
    campaign_payload = payload.get("campaign") if isinstance(payload.get("campaign"), dict) else {}
    targeting_payload = payload.get("targeting") if isinstance(payload.get("targeting"), dict) else None
    creative_payload = payload.get("creative") if isinstance(payload.get("creative"), dict) else None
    placements = payload.get("placements") if isinstance(payload.get("placements"), list) else []
    placements = [clean_text(key, 80) for key in placements if clean_text(key, 80)]
    if not placements:
        placements = _auto_placements(conn, campaign_payload.get("objective") or "awareness", creative_payload or {})
    proxy = _DeferredCommit(conn)
    try:
        campaign = pulse_ads_service.create_campaign(
            proxy, user_id, {**campaign_payload, "ad_account_id": account_id, "placements": placements}
        )
        campaign_id = safe_int(campaign.get("id"), minimum=1)
        targeting = None
        if targeting_payload is not None:
            targeting = put_targeting(proxy, user_id, campaign_id, targeting_payload, commit=False)
        creative = None
        if creative_payload is not None:
            creative = pulse_ads_service.create_creative(proxy, user_id, {**creative_payload, "campaign_id": campaign_id})
        if payload.get("submit"):
            # Reuse the existing submit lifecycle: campaign draft -> pending_review,
            # creative submitted into the moderation + review-board queues.
            pulse_advertiser_portal.campaign_action(proxy, user_id, campaign_id, "submit")
            if creative:
                creative = pulse_ads_service.submit_creative_for_review(proxy, user_id, safe_int(creative.get("id"), minimum=1))
            campaign = pulse_ads_service.get_campaign(proxy, user_id, campaign_id)
        campaign["placements"] = pulse_advertiser_portal._campaign_placements(conn, campaign_id)
        gate = pulse_advertiser_portal.activation_blocker(conn, account_id, campaign)
        blockers = [{"code": gate[0], "message": gate[1]}] if gate else []
        result = {
            "campaign": campaign,
            "creative": creative,
            "targeting": targeting,
            "blockers": blockers,
        }
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pulse_ad_idempotency (scope, idem_key, result_json, created_at) VALUES (?, ?, ?, ?)",
            (scope, idem_key, json.dumps(result, default=str), now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # A concurrent request with the same key won the race. Serve its result.
        conn.rollback()
        replay = _idempotency_lookup(conn, scope, idem_key)
        if replay:
            return {**replay, "ok": True, "duplicate": True}
        raise
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Server-side campaign drafts (autosave / resume)
# ---------------------------------------------------------------------------

def _draft_campaign_by_key(conn, account_id: int, draft_key: str) -> dict:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM pulse_ad_campaigns WHERE ad_account_id=? AND draft_key=? ORDER BY id DESC LIMIT 1",
            (account_id, draft_key),
        )
        return row_to_dict(cur.fetchone())
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}


def save_campaign_draft(conn, user_id, payload: dict, _retry: bool = False) -> dict:
    """Idempotent draft upsert keyed by (ad_account_id, draft_key).

    Reuses the same building blocks as create_campaign_full but with draft-only
    semantics: the campaign never leaves status='draft', is never submitted for
    review, and no budget is ever reserved or charged. Safe to call repeatedly
    from client autosave — the same draft_key always lands on the same campaign.
    """
    payload = payload or {}
    draft_key = clean_text(payload.get("draft_key"), 160)
    if not draft_key:
        raise PulseAdsError("draft_key is required to save a campaign draft.")
    account_id = safe_int(payload.get("ad_account_id"), minimum=1)
    pulse_ads_service._owned_account(conn, user_id, account_id)
    campaign_payload = payload.get("campaign") if isinstance(payload.get("campaign"), dict) else {}
    targeting_payload = payload.get("targeting") if isinstance(payload.get("targeting"), dict) else None
    creative_payload = payload.get("creative") if isinstance(payload.get("creative"), dict) else None
    placements = payload.get("placements") if isinstance(payload.get("placements"), list) else None
    if placements is not None:
        placements = [clean_text(key, 80) for key in placements if clean_text(key, 80)]

    existing = _draft_campaign_by_key(conn, account_id, draft_key)
    if existing and clean_text(existing.get("status"), 40) != "draft":
        raise PulseAdsError("This draft has already been submitted and can no longer be autosaved.", 409)

    proxy = _DeferredCommit(conn)
    try:
        if existing:
            campaign_id = safe_int(existing.get("id"), minimum=1)
            update_payload = dict(campaign_payload)
            if placements is not None:
                update_payload["placements"] = placements
            campaign = pulse_advertiser_portal.update_campaign(proxy, user_id, campaign_id, update_payload)
        else:
            create_payload = {
                **campaign_payload,
                "ad_account_id": account_id,
                "campaign_name": clean_text(campaign_payload.get("campaign_name"), 120) or "Untitled draft",
            }
            if placements is not None:
                create_payload["placements"] = placements
            campaign = pulse_ads_service.create_campaign(proxy, user_id, create_payload)
            campaign_id = safe_int(campaign.get("id"), minimum=1)
            cur = conn.cursor()
            cur.execute(
                "UPDATE pulse_ad_campaigns SET draft_key=? WHERE id=?",
                (draft_key, campaign_id),
            )
        targeting = None
        if targeting_payload is not None:
            targeting = put_targeting(proxy, user_id, campaign_id, targeting_payload, commit=False)
        creative = None
        creative_error = ""
        if creative_payload is not None:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS n FROM pulse_ad_creatives WHERE campaign_id=? AND COALESCE(archived_at,'')=''",
                (campaign_id,),
            )
            has_creative = safe_int(row_to_dict(cur.fetchone()).get("n"), 0) > 0
            if not has_creative:
                try:
                    creative = pulse_ads_service.create_creative(
                        proxy, user_id, {**creative_payload, "campaign_id": campaign_id}
                    )
                except PulseAdsError as exc:
                    # A half-filled creative must not lose the rest of the draft.
                    creative_error = str(exc)
        pulse_ads_service.audit_log(
            conn, user_id, "ad_campaign_draft_saved", "pulse_ad_campaigns", campaign_id,
            after={"draft_key": draft_key},
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Concurrent autosave with the same draft_key won the unique-index race.
        conn.rollback()
        if _retry:
            raise
        winner = _draft_campaign_by_key(conn, account_id, draft_key)
        if winner:
            return save_campaign_draft(conn, user_id, payload, _retry=True)
        raise
    except Exception:
        conn.rollback()
        raise
    campaign = pulse_ads_service.get_campaign(conn, user_id, campaign_id)
    campaign["placements"] = pulse_advertiser_portal._campaign_placements(conn, campaign_id)
    result = {
        "draft_key": draft_key,
        "campaign": campaign,
        "targeting": targeting if targeting is not None else _targeting_public(campaign_id, _targeting_row(conn, campaign_id)),
        "creative": creative,
        "status": "draft",
    }
    if creative_error:
        result["creative_error"] = creative_error
    return result


def list_campaign_drafts(conn, user_id) -> dict:
    account_ids = pulse_advertiser_portal._account_ids_for_user(conn, user_id)
    if not account_ids:
        return {"drafts": []}
    marks = ",".join("?" for _ in account_ids)
    archived_guard = (
        " AND COALESCE(archived_at,'')=''"
        if pulse_advertiser_portal._has_column(conn, "pulse_ad_campaigns", "archived_at")
        else ""
    )
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT * FROM pulse_ad_campaigns
            WHERE ad_account_id IN ({marks})
              AND status='draft'
              AND COALESCE(draft_key,'') != ''{archived_guard}
            ORDER BY updated_at DESC, id DESC LIMIT 50
            """,
            tuple(account_ids),
        )
        rows = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"drafts": []}
    drafts = []
    for item in rows:
        campaign_id = safe_int(item.get("id"), 0)
        cur.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_creatives WHERE campaign_id=? AND COALESCE(archived_at,'')=''",
            (campaign_id,),
        )
        creative_count = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
        item["objective_canonical"] = canonical_objective(item.get("objective"))
        drafts.append({
            **item,
            "creative_count": creative_count,
            "targeting": _targeting_public(campaign_id, _targeting_row(conn, campaign_id)),
            "placements": pulse_advertiser_portal._campaign_placements(conn, campaign_id),
        })
    return {"drafts": drafts}


# ---------------------------------------------------------------------------
# Item 7 — Wallet depth
# ---------------------------------------------------------------------------

def _paged(conn, user_id, account_id, table: str, limit, before_id) -> dict:
    pulse_ads_service._owned_account(conn, user_id, account_id)
    limit = safe_int(limit, 50, 1, 100)
    before_id = safe_int(before_id, 0)
    cur = conn.cursor()
    if before_id:
        cur.execute(
            f"SELECT * FROM {table} WHERE account_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (account_id, before_id, limit),
        )
    else:
        cur.execute(f"SELECT * FROM {table} WHERE account_id=? ORDER BY id DESC LIMIT ?", (account_id, limit))
    rows = [row_to_dict(row) for row in cur.fetchall()]
    next_before_id = rows[-1]["id"] if len(rows) == limit else None
    return {"rows": rows, "next_before_id": next_before_id}


def wallet_transactions(conn, user_id, account_id, limit=50, before_id=0) -> dict:
    page = _paged(conn, user_id, account_id, "pulse_ad_wallet_transactions", limit, before_id)
    return {"transactions": page["rows"], "next_before_id": page["next_before_id"]}


def wallet_invoices(conn, user_id, account_id, limit=50, before_id=0) -> dict:
    page = _paged(conn, user_id, account_id, "pulse_ad_invoices", limit, before_id)
    return {"invoices": page["rows"], "next_before_id": page["next_before_id"]}


def wallet_receipts(conn, user_id, account_id, limit=50, before_id=0) -> dict:
    page = _paged(conn, user_id, account_id, "pulse_ad_receipts", limit, before_id)
    return {"receipts": page["rows"], "next_before_id": page["next_before_id"]}


def _limit_value(payload: dict, key: str) -> int:
    """None/null clears the limit (stored as 0 = no limit)."""
    if key not in payload or payload.get(key) is None:
        return 0
    value = safe_int(payload.get(key), -1)
    if value < 0:
        raise PulseAdsError(f"{key} must be a non-negative amount in cents, or null to clear it.")
    return value


def set_spending_limit(conn, user_id, account_id, payload: dict) -> dict:
    pulse_ads_service._owned_account(conn, user_id, account_id)
    payload = payload or {}
    daily = _limit_value(payload, "daily_limit_cents")
    lifetime = _limit_value(payload, "lifetime_limit_cents")
    wallet = pulse_ad_payments.ensure_wallet(conn, account_id)
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_wallets SET daily_limit_cents=?, lifetime_limit_cents=?, updated_at=? WHERE id=?",
        (daily, lifetime, now_iso(), wallet.get("id")),
    )
    pulse_ads_service.audit_log(
        conn, user_id, "ad_wallet_spending_limit_set", "pulse_ad_wallets", wallet.get("id"),
        after={"daily_limit_cents": daily, "lifetime_limit_cents": lifetime},
    )
    conn.commit()
    return {"daily_limit_cents": daily, "lifetime_limit_cents": lifetime}


def set_auto_topup(conn, user_id, account_id, payload: dict) -> dict:
    """Stores the advertiser's auto-topup preference. Settings only — no charge
    is ever initiated here; funding still goes through the explicit Stripe
    funding-session flow.
    """
    pulse_ads_service._owned_account(conn, user_id, account_id)
    payload = payload or {}
    enabled = 1 if payload.get("enabled") else 0
    threshold = safe_int(payload.get("threshold_cents"), 0, 0, 10_000_000)
    amount = safe_int(payload.get("amount_cents"), 0, 0, 10_000_000)
    if enabled and (threshold <= 0 or amount <= 0):
        raise PulseAdsError("Enabling auto-topup requires a positive threshold_cents and amount_cents.")
    wallet = pulse_ad_payments.ensure_wallet(conn, account_id)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pulse_ad_wallets
        SET auto_topup_enabled=?, auto_topup_threshold_cents=?, auto_topup_amount_cents=?, updated_at=?
        WHERE id=?
        """,
        (enabled, threshold, amount, now_iso(), wallet.get("id")),
    )
    pulse_ads_service.audit_log(
        conn, user_id, "ad_wallet_auto_topup_set", "pulse_ad_wallets", wallet.get("id"),
        after={"enabled": bool(enabled), "threshold_cents": threshold, "amount_cents": amount},
    )
    conn.commit()
    return {"enabled": bool(enabled), "threshold_cents": threshold, "amount_cents": amount}


# ---------------------------------------------------------------------------
# Item 8 — Reports
# ---------------------------------------------------------------------------

REPORT_BREAKDOWNS = {"campaign", "creative", "placement", "date", "objective"}


def _date_clause(alias: str, date_from: str, date_to: str) -> tuple[str, list]:
    clause = ""
    params: list = []
    if date_from:
        clause += f" AND substr({alias}.created_at, 1, 10) >= ?"
        params.append(date_from)
    if date_to:
        clause += f" AND substr({alias}.created_at, 1, 10) <= ?"
        params.append(date_to)
    return clause, params


def _campaign_meta(conn, account_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, campaign_name, objective FROM pulse_ad_campaigns WHERE ad_account_id=?",
        (account_id,),
    )
    meta = {}
    for row in cur.fetchall():
        item = row_to_dict(row)
        meta[safe_int(item.get("id"))] = {
            "name": item.get("campaign_name") or f"Campaign {item.get('id')}",
            "objective": canonical_objective(item.get("objective")),
        }
    return meta


def _purchase_attributions(conn, account_id, date_from: str, date_to: str) -> list[dict]:
    """7-day post-click purchase attribution.

    A purchase is attributed when the clicker later bought the exact
    marketplace listing the creative promotes (creative content_ref listing id
    == seller_transactions.item_id, buyer == click viewer) within 7 days of the
    click. Each transaction is attributed at most once, to its earliest
    qualifying click. Anything unattributable stays at zero.
    """
    cur = conn.cursor()
    marks = ",".join("?" for _ in PAID_TRANSACTION_STATUSES)
    try:
        cur.execute(
            f"""
            SELECT k.id AS click_id, k.campaign_id, k.creative_id, k.placement_key,
                   k.created_at AS clicked_at, st.id AS txn_id,
                   st.amount_cents, st.created_at AS purchased_at
            FROM pulse_ad_clicks k
            JOIN pulse_ad_campaigns c ON c.id=k.campaign_id AND c.ad_account_id=?
            JOIN pulse_ad_creatives cr ON cr.id=k.creative_id
                 AND cr.content_ref_type='listing' AND COALESCE(cr.content_ref_id, 0) > 0
            JOIN seller_transactions st ON st.buyer_user_id=k.viewer_user_id
                 AND st.item_type='marketplace_product'
                 AND CAST(st.item_id AS INTEGER)=cr.content_ref_id
                 AND LOWER(COALESCE(st.status,'')) IN ({marks})
            WHERE k.viewer_user_id IS NOT NULL
            ORDER BY k.created_at ASC
            """,
            (account_id, *PAID_TRANSACTION_STATUSES),
        )
        rows = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        return []
    attributed = []
    seen_txns = set()
    for row in rows:
        txn_id = row.get("txn_id")
        if txn_id in seen_txns:
            continue
        clicked = _parse_ts(row.get("clicked_at"))
        purchased = _parse_ts(row.get("purchased_at"))
        if not clicked or not purchased:
            continue
        if purchased < clicked or purchased > clicked + timedelta(days=7):
            continue
        day = clean_text(row.get("clicked_at"), 40)[:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        seen_txns.add(txn_id)
        attributed.append({
            "campaign_id": safe_int(row.get("campaign_id")),
            "creative_id": safe_int(row.get("creative_id")),
            "placement_key": row.get("placement_key") or "",
            "date": day,
            "amount_cents": safe_int(row.get("amount_cents"), 0),
        })
    return attributed


def _spend_rows(conn, account_id, date_from: str, date_to: str) -> list[dict]:
    cur = conn.cursor()
    clause, params = _date_clause("t", date_from, date_to)
    cur.execute(
        f"""
        SELECT t.campaign_id, t.creative_id, t.amount_cents, t.description,
               substr(t.created_at, 1, 10) AS day
        FROM pulse_ad_wallet_transactions t
        WHERE t.account_id=? AND t.transaction_type='spend' AND t.status='posted' {clause}
        """,
        (account_id, *params),
    )
    rows = []
    for raw in cur.fetchall():
        row = row_to_dict(raw)
        description = clean_text(row.get("description"), 240)
        placement = ""
        if description.startswith(SPEND_DESCRIPTION_PREFIX):
            placement = description[len(SPEND_DESCRIPTION_PREFIX):]
        rows.append({
            "campaign_id": safe_int(row.get("campaign_id")),
            "creative_id": safe_int(row.get("creative_id")),
            "placement_key": placement,
            "date": row.get("day") or "",
            "amount_cents": safe_int(row.get("amount_cents"), 0),
        })
    return rows


def _delivery_aggregates(conn, account_id, table: str, date_from: str, date_to: str) -> list[dict]:
    """Per (campaign, creative, placement, day) counts from a delivery table."""
    cur = conn.cursor()
    clause, params = _date_clause("i", date_from, date_to)
    cur.execute(
        f"""
        SELECT i.campaign_id, i.creative_id, i.placement_key,
               substr(i.created_at, 1, 10) AS day, COUNT(*) AS n
        FROM {table} i
        JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=? {clause}
        GROUP BY i.campaign_id, i.creative_id, i.placement_key, day
        """,
        (account_id, *params),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def _reach_by_key(conn, account_id, key_expr: str, date_from: str, date_to: str) -> dict:
    cur = conn.cursor()
    clause, params = _date_clause("i", date_from, date_to)
    try:
        cur.execute(
            f"""
            SELECT {key_expr} AS k,
                   COUNT(DISTINCT COALESCE(i.viewer_user_id, i.session_id)) AS reach
            FROM pulse_ad_impressions i
            JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=? {clause}
            GROUP BY k
            """,
            (account_id, *params),
        )
        return {row_to_dict(row).get("k"): safe_int(row_to_dict(row).get("reach"), 0) for row in cur.fetchall()}
    except Exception:
        return {}


def _total_reach(conn, account_id, date_from: str, date_to: str) -> int:
    cur = conn.cursor()
    clause, params = _date_clause("i", date_from, date_to)
    try:
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT COALESCE(i.viewer_user_id, i.session_id)) AS reach
            FROM pulse_ad_impressions i
            JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=? {clause}
            """,
            (account_id, *params),
        )
        return safe_int(row_to_dict(cur.fetchone()).get("reach"), 0)
    except Exception:
        return 0


def _video_view_counts(conn, account_id, date_from: str, date_to: str) -> dict:
    """Recorded video_start events per campaign — the honest video-view count."""
    cur = conn.cursor()
    clause, params = _date_clause("e", date_from, date_to)
    try:
        cur.execute(
            f"""
            SELECT e.campaign_id, COUNT(*) AS n
            FROM pulse_ad_events e
            JOIN pulse_ad_campaigns c ON c.id=e.campaign_id AND c.ad_account_id=?
            WHERE e.event_type='video_start' {clause}
            GROUP BY e.campaign_id
            """,
            (account_id, *params),
        )
        return {safe_int(row_to_dict(row).get("campaign_id")): safe_int(row_to_dict(row).get("n"), 0) for row in cur.fetchall()}
    except Exception:
        return {}


def _report_key(breakdown: str, campaign_id: int, creative_id: int, placement_key: str, day: str, meta: dict):
    if breakdown == "campaign":
        return campaign_id
    if breakdown == "creative":
        return creative_id
    if breakdown == "placement":
        return placement_key or "unknown"
    if breakdown == "date":
        return day or "unknown"
    return (meta.get(campaign_id) or {}).get("objective", "awareness")


def _zero_row(key, label) -> dict:
    return {
        "key": key,
        "label": label,
        "spend_cents": 0,
        "impressions": 0,
        "reach": 0,
        "frequency": 0,
        "clicks": 0,
        "ctr": 0,
        "cpc_cents": 0,
        "results": 0,
        "cost_per_result_cents": 0,
        "purchases": 0,
        "revenue_cents": 0,
        "roas": 0,
    }


def _creative_labels(conn, account_id) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM pulse_ad_creatives WHERE ad_account_id=?", (account_id,))
    return {safe_int(row_to_dict(row).get("id")): row_to_dict(row).get("title") or "" for row in cur.fetchall()}


def build_report(conn, user_id, account_id, date_from="", date_to="", breakdown="campaign") -> dict:
    pulse_ads_service._owned_account(conn, user_id, account_id)
    breakdown = clean_text(breakdown or "campaign", 20).lower()
    if breakdown not in REPORT_BREAKDOWNS:
        raise PulseAdsError("breakdown must be one of campaign, creative, placement, date, objective.")
    date_from = clean_text(date_from, 10)
    date_to = clean_text(date_to, 10)
    meta = _campaign_meta(conn, account_id)
    creative_labels = _creative_labels(conn, account_id)
    impressions = _delivery_aggregates(conn, account_id, "pulse_ad_impressions", date_from, date_to)
    clicks = _delivery_aggregates(conn, account_id, "pulse_ad_clicks", date_from, date_to)
    spend = _spend_rows(conn, account_id, date_from, date_to)
    purchases = _purchase_attributions(conn, account_id, date_from, date_to)
    video_views = _video_view_counts(conn, account_id, date_from, date_to)

    key_exprs = {
        "campaign": "i.campaign_id",
        "creative": "i.creative_id",
        "placement": "i.placement_key",
        "date": "substr(i.created_at, 1, 10)",
        "objective": "i.campaign_id",  # canonicalised in Python below
    }
    raw_reach = _reach_by_key(conn, account_id, key_exprs[breakdown], date_from, date_to)
    reach_by_key: dict = {}
    if breakdown == "objective":
        # Reach per campaign cannot simply be summed per objective (a viewer can
        # see two campaigns with the same objective), so this is an upper-bound
        # aggregation only when a single campaign carries the objective;
        # otherwise the per-campaign distinct counts are summed — the closest
        # value computable without a per-objective distinct query.
        for campaign_id, value in raw_reach.items():
            objective = (meta.get(safe_int(campaign_id)) or {}).get("objective", "awareness")
            reach_by_key[objective] = reach_by_key.get(objective, 0) + value
    else:
        reach_by_key = {k: v for k, v in raw_reach.items()}

    rows: dict = {}

    def row_for(key):
        if key not in rows:
            if breakdown == "campaign":
                label = (meta.get(key) or {}).get("name", f"Campaign {key}")
            elif breakdown == "creative":
                label = creative_labels.get(key, f"Creative {key}")
            else:
                label = str(key)
            rows[key] = _zero_row(key, label)
        return rows[key]

    per_key_objective_clicks: dict = {}
    for agg in impressions:
        key = _report_key(breakdown, safe_int(agg.get("campaign_id")), safe_int(agg.get("creative_id")), agg.get("placement_key") or "", agg.get("day") or "", meta)
        row = row_for(key)
        count = safe_int(agg.get("n"), 0)
        row["impressions"] += count
        objective = (meta.get(safe_int(agg.get("campaign_id"))) or {}).get("objective", "awareness")
        if objective == "awareness":
            row["results"] += count
    for agg in clicks:
        key = _report_key(breakdown, safe_int(agg.get("campaign_id")), safe_int(agg.get("creative_id")), agg.get("placement_key") or "", agg.get("day") or "", meta)
        row = row_for(key)
        count = safe_int(agg.get("n"), 0)
        row["clicks"] += count
        objective = (meta.get(safe_int(agg.get("campaign_id"))) or {}).get("objective", "awareness")
        per_key_objective_clicks.setdefault(key, {}).setdefault(objective, 0)
        per_key_objective_clicks[key][objective] += count
        if objective not in {"awareness", "marketplace_sales", "video_views"}:
            row["results"] += count
    # Video-view results: real video_start events, attributable per campaign.
    for campaign_id, count in video_views.items():
        objective = (meta.get(campaign_id) or {}).get("objective", "awareness")
        if objective != "video_views":
            continue
        if breakdown == "campaign":
            row_for(campaign_id)["results"] += count
        elif breakdown == "objective":
            row_for("video_views")["results"] += count
        # For creative/placement/date breakdowns events carry no such dimension;
        # rather than invent an allocation, those rows keep results from clicks.
    if breakdown in {"creative", "placement", "date"}:
        for key, by_objective in per_key_objective_clicks.items():
            count = by_objective.get("video_views", 0)
            if count:
                rows[key]["results"] += count
    for item in spend:
        key = _report_key(breakdown, item["campaign_id"], item["creative_id"], item["placement_key"], item["date"], meta)
        row_for(key)["spend_cents"] += item["amount_cents"]
    for item in purchases:
        key = _report_key(breakdown, item["campaign_id"], item["creative_id"], item["placement_key"], item["date"], meta)
        row = row_for(key)
        row["purchases"] += 1
        row["revenue_cents"] += item["amount_cents"]
        objective = (meta.get(item["campaign_id"]) or {}).get("objective", "awareness")
        if objective == "marketplace_sales":
            row["results"] += 1

    output = []
    for key in sorted(rows, key=lambda value: str(value)):
        row = rows[key]
        row["reach"] = safe_int(reach_by_key.get(key), 0)
        if row["reach"]:
            row["frequency"] = round(row["impressions"] / row["reach"], 2)
        if row["impressions"]:
            row["ctr"] = round(row["clicks"] / row["impressions"], 4)
        if row["clicks"]:
            row["cpc_cents"] = row["spend_cents"] // row["clicks"]
        if row["results"]:
            row["cost_per_result_cents"] = row["spend_cents"] // row["results"]
        if row["spend_cents"]:
            row["roas"] = round(row["revenue_cents"] / row["spend_cents"], 2)
        output.append(row)

    totals = _zero_row("totals", "Totals")
    for row in output:
        for field in ("spend_cents", "impressions", "clicks", "results", "purchases", "revenue_cents"):
            totals[field] += row[field]
    totals["reach"] = _total_reach(conn, account_id, date_from, date_to)
    if totals["reach"]:
        totals["frequency"] = round(totals["impressions"] / totals["reach"], 2)
    if totals["impressions"]:
        totals["ctr"] = round(totals["clicks"] / totals["impressions"], 4)
    if totals["clicks"]:
        totals["cpc_cents"] = totals["spend_cents"] // totals["clicks"]
    if totals["results"]:
        totals["cost_per_result_cents"] = totals["spend_cents"] // totals["results"]
    if totals["spend_cents"]:
        totals["roas"] = round(totals["revenue_cents"] / totals["spend_cents"], 2)
    return {"rows": output, "totals": totals, "breakdown": breakdown}


# ---------------------------------------------------------------------------
# Item 9 — Insights
# ---------------------------------------------------------------------------

def build_insights(conn, user_id, account_id) -> dict:
    """Rule-based recommendations from real delivery data. Advisory only —
    nothing here changes any campaign; the advertiser applies (or ignores)
    each suggestion themselves.
    """
    pulse_ads_service._owned_account(conn, user_id, account_id)
    cur = conn.cursor()
    recommendations = []
    now = datetime.now(timezone.utc)
    cutoff_3d = (now - timedelta(days=3)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_14d = (now - timedelta(days=14)).isoformat()

    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE ad_account_id=?", (account_id,))
    campaigns = [row_to_dict(row) for row in cur.fetchall()]
    active = [c for c in campaigns if c.get("status") == "active"]

    # Rule 1 — placement efficiency: a placement whose CPC beats the campaign
    # average by >= 30% with at least 50 clicks behind it.
    spend_rows = _spend_rows(conn, account_id, "", "")
    for campaign in active:
        campaign_id = safe_int(campaign.get("id"))
        campaign_spend = sum(r["amount_cents"] for r in spend_rows if r["campaign_id"] == campaign_id)
        cur.execute("SELECT placement_key, COUNT(*) AS n FROM pulse_ad_clicks WHERE campaign_id=? GROUP BY placement_key", (campaign_id,))
        clicks_by_placement = {row_to_dict(r).get("placement_key"): safe_int(row_to_dict(r).get("n"), 0) for r in cur.fetchall()}
        total_clicks = sum(clicks_by_placement.values())
        if not total_clicks or not campaign_spend:
            continue
        campaign_cpc = campaign_spend / total_clicks
        for placement_key, placement_clicks in clicks_by_placement.items():
            if placement_clicks < 50:
                continue
            placement_spend = sum(
                r["amount_cents"] for r in spend_rows
                if r["campaign_id"] == campaign_id and r["placement_key"] == placement_key
            )
            if not placement_spend:
                continue
            placement_cpc = placement_spend / placement_clicks
            if placement_cpc <= campaign_cpc * 0.7:
                recommendations.append({
                    "id": f"placement_efficiency:{campaign_id}:{placement_key}",
                    "kind": "placement_efficiency",
                    "severity": "opportunity",
                    "title": f"'{placement_key}' delivers cheaper clicks",
                    "why": (
                        f"In '{campaign.get('campaign_name')}', {placement_key} averages "
                        f"{placement_cpc / 100:.2f} per click across {placement_clicks} clicks — at least 30% below "
                        f"the campaign average of {campaign_cpc / 100:.2f}."
                    ),
                    "suggested_action": f"Shift more of this campaign's budget toward the {placement_key} placement.",
                    "campaign_id": campaign_id,
                })

    # Rule 2 — zero delivery: an active campaign at least 3 days old with no
    # impressions in the last 3 days.
    for campaign in active:
        campaign_id = safe_int(campaign.get("id"))
        created = clean_text(campaign.get("created_at"), 40)
        if not created or created > cutoff_3d:
            continue
        cur.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_impressions WHERE campaign_id=? AND created_at>=?",
            (campaign_id, cutoff_3d),
        )
        if safe_int(row_to_dict(cur.fetchone()).get("n"), 0):
            continue
        gate = pulse_advertiser_portal.activation_blocker(conn, account_id, campaign)
        why = "This active campaign has recorded no impressions in the last 3 days."
        action = "Review the campaign's placements, budget and creative approval status."
        if gate:
            why += f" Blocker found: {gate[1]}"
            action = gate[1]
        recommendations.append({
            "id": f"zero_delivery:{campaign_id}",
            "kind": "zero_delivery",
            "severity": "warning",
            "title": f"'{campaign.get('campaign_name')}' is not delivering",
            "why": why,
            "suggested_action": action,
            "campaign_id": campaign_id,
        })

    # Rule 3 — budget exhaustion: >= 90% of a lifetime budget spent.
    for campaign in campaigns:
        lifetime_budget = safe_int(campaign.get("lifetime_budget_cents"), 0)
        spent = safe_int(campaign.get("spent_cents"), 0)
        if lifetime_budget > 0 and spent >= lifetime_budget * 0.9 and campaign.get("status") in {"active", "paused"}:
            recommendations.append({
                "id": f"budget_exhaustion:{campaign.get('id')}",
                "kind": "budget_exhaustion",
                "severity": "warning",
                "title": f"'{campaign.get('campaign_name')}' has nearly exhausted its budget",
                "why": f"{spent} of {lifetime_budget} lifetime budget cents ({spent * 100 // lifetime_budget}%) is already spent.",
                "suggested_action": "Raise the lifetime budget or plan for the campaign to stop delivering.",
                "campaign_id": safe_int(campaign.get("id")),
            })

    # Rule 4 — creative fatigue: CTR down >= 40% week-over-week with at least
    # 1000 impressions in each window.
    cur.execute("SELECT id, title, campaign_id FROM pulse_ad_creatives WHERE ad_account_id=? AND status NOT IN ('archived')", (account_id,))
    for raw in cur.fetchall():
        creative = row_to_dict(raw)
        creative_id = safe_int(creative.get("id"))
        windows = {}
        for name, start, end in (("last", cutoff_7d, ""), ("prior", cutoff_14d, cutoff_7d)):
            clause = "AND created_at>=?"
            params = [creative_id, start]
            if end:
                clause += " AND created_at<?"
                params.append(end)
            cur.execute(f"SELECT COUNT(*) AS n FROM pulse_ad_impressions WHERE creative_id=? {clause}", tuple(params))
            impressions = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
            cur.execute(f"SELECT COUNT(*) AS n FROM pulse_ad_clicks WHERE creative_id=? {clause}", tuple(params))
            clicks_count = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
            windows[name] = (impressions, clicks_count)
        last_impr, last_clicks = windows["last"]
        prior_impr, prior_clicks = windows["prior"]
        if last_impr < 1000 or prior_impr < 1000 or not prior_clicks:
            continue
        last_ctr = last_clicks / last_impr
        prior_ctr = prior_clicks / prior_impr
        if last_ctr <= prior_ctr * 0.6:
            recommendations.append({
                "id": f"creative_fatigue:{creative_id}",
                "kind": "creative_fatigue",
                "severity": "warning",
                "title": f"Creative '{creative.get('title')}' is fatiguing",
                "why": (
                    f"CTR fell from {prior_ctr:.2%} to {last_ctr:.2%} week over week "
                    f"({prior_impr} then {last_impr} impressions)."
                ),
                "suggested_action": "Refresh this ad with new media or copy, or rotate in a new creative.",
                "campaign_id": safe_int(creative.get("campaign_id")),
            })

    # Rule 5 — empty wallet while campaigns are active.
    if active and pulse_ad_payments.spendable_balance_cents(conn, account_id) <= 0:
        recommendations.append({
            "id": f"empty_wallet:{account_id}",
            "kind": "empty_wallet",
            "severity": "warning",
            "title": "Wallet is empty while campaigns are active",
            "why": f"{len(active)} active campaign(s) have no spendable balance behind them; delivery will pause.",
            "suggested_action": "Add funds to the ad wallet to keep these campaigns delivering.",
        })

    return {"recommendations": recommendations}


# ---------------------------------------------------------------------------
# Item 10 — Policy center + appeals + rejected-creative editing
# ---------------------------------------------------------------------------

def _appeal_public(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "account_id": row.get("account_id"),
        "creative_id": row.get("creative_id"),
        "campaign_id": row.get("campaign_id"),
        "message": row.get("message") or "",
        "status": row.get("status") or "open",
        "appeal_type": row.get("appeal_type") or "",
        "reason": _json_dict(row.get("reason_json")),
        "decision": row.get("decision") or "",
        "decision_reason": row.get("decision_reason") or "",
        "decided_at": row.get("decided_at") or "",
        "resolution_notes": row.get("resolution_notes") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }


# Which part of the ad a rejection touches, derived from the review reason
# text plus policy flag types. Order matters: destination beats media beats
# targeting; anything unclassified is creative text.
_COMPONENT_KEYWORDS = (
    ("destination", ("url", "link", "landing", "destination", "redirect", "domain")),
    ("media", ("image", "video", "media", "thumbnail", "audio", "visual", "photo")),
    ("targeting", ("target", "audience", "age", "geo", "country", "minor")),
)


def _affected_component(reason: str, flags: list[dict]) -> str:
    combined = " ".join(
        [clean_text(reason, 500).lower()]
        + [clean_text(flag.get("flag_type"), 120).lower() for flag in flags or []]
        + [clean_text(flag.get("details"), 300).lower() for flag in flags or []]
    )
    for component, keywords in _COMPONENT_KEYWORDS:
        if any(keyword in combined for keyword in keywords):
            return component
    return "creative_text"


def policy_center(conn, user_id, account_id) -> dict:
    account = pulse_ads_service._owned_account(conn, user_id, account_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT status, COUNT(*) AS n FROM pulse_ad_creatives WHERE ad_account_id=? GROUP BY status",
        (account_id,),
    )
    by_status = {row_to_dict(r).get("status"): safe_int(row_to_dict(r).get("n"), 0) for r in cur.fetchall()}
    try:
        cur.execute(
            """
            SELECT COUNT(DISTINCT pf.creative_id) AS n
            FROM pulse_ad_policy_flags pf
            JOIN pulse_ad_creatives cr ON cr.id=pf.creative_id
            WHERE cr.ad_account_id=? AND LOWER(COALESCE(pf.severity,'')) IN ('high', 'critical', 'block')
            """,
            (account_id,),
        )
        restricted = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    except Exception:
        restricted = 0
    cur.execute(
        """
        SELECT id, campaign_id, title, creative_type, status, moderation_status, updated_at
        FROM pulse_ad_creatives WHERE ad_account_id=? AND status='rejected'
        ORDER BY id DESC LIMIT 100
        """,
        (account_id,),
    )
    rejected = []
    for raw in cur.fetchall():
        item = row_to_dict(raw)
        cur.execute(
            "SELECT review_reason FROM pulse_ad_review_board WHERE creative_id=? ORDER BY id DESC LIMIT 1",
            (item.get("id"),),
        )
        board = row_to_dict(cur.fetchone())
        item["rejection_reason"] = board.get("review_reason") or ""
        rejected.append(item)
    try:
        cur.execute(
            """
            SELECT creative_id FROM pulse_ad_appeals
            WHERE account_id=? AND status='open'
            """,
            (account_id,),
        )
        open_appeal_creative_ids = {safe_int(row_to_dict(row).get("creative_id"), 0) for row in cur.fetchall()}
    except Exception:
        open_appeal_creative_ids = set()
    cur.execute(
        "SELECT * FROM pulse_ad_appeals WHERE account_id=? ORDER BY id DESC LIMIT 100",
        (account_id,),
    )
    appeals = [_appeal_public(row_to_dict(row)) for row in cur.fetchall()]
    try:
        cur.execute(
            """
            SELECT pf.creative_id, pf.flag_type, pf.severity, pf.details, pf.created_at
            FROM pulse_ad_policy_flags pf
            JOIN pulse_ad_creatives cr ON cr.id=pf.creative_id
            WHERE cr.ad_account_id=? ORDER BY pf.id DESC LIMIT 100
            """,
            (account_id,),
        )
        restrictions = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        restrictions = []
    flags_by_creative: dict = {}
    for flag in restrictions:
        flags_by_creative.setdefault(safe_int(flag.get("creative_id"), 0), []).append(flag)
    for item in rejected:
        creative_id = safe_int(item.get("id"), 0)
        item["affected_component"] = _affected_component(
            item.get("rejection_reason"), flags_by_creative.get(creative_id, []))
        item["appealable"] = creative_id not in open_appeal_creative_ids
    return {
        "account_status": account.get("status") or "",
        "verification_status": pulse_ads_service.account_verification_state(account),
        "counts": {
            "in_review": by_status.get("pending_review", 0),
            "approved": by_status.get("approved", 0),
            "rejected": by_status.get("rejected", 0),
            "restricted": restricted,
        },
        "rejected": rejected,
        "appeals": appeals,
        "restrictions": restrictions,
    }


def create_appeal(conn, user_id, creative_id, payload: dict) -> dict:
    creative = pulse_ads_service.get_creative(conn, user_id, creative_id)
    message = clean_text((payload or {}).get("message"), 2000)
    if not message:
        raise PulseAdsError("An appeal needs a message explaining why the decision should be reviewed.")
    account_id = safe_int(creative.get("ad_account_id"), minimum=1)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM pulse_ad_appeals WHERE creative_id=? AND status='open'",
        (creative_id,),
    )
    if cur.fetchone():
        raise PulseAdsError("An appeal for this creative is already open. Wait for it to be decided.", 409)
    now = now_iso()
    # Snapshot of the rejection at appeal time, so the reviewer sees what the
    # advertiser was actually appealing even if the creative changes later.
    cur.execute(
        "SELECT review_reason FROM pulse_ad_review_board WHERE creative_id=? ORDER BY id DESC LIMIT 1",
        (creative_id,),
    )
    board = row_to_dict(cur.fetchone())
    try:
        cur.execute(
            "SELECT flag_type, severity FROM pulse_ad_policy_flags WHERE creative_id=? ORDER BY id DESC LIMIT 5",
            (creative_id,),
        )
        flags = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        flags = []
    reason_payload = {
        "message": message,
        "snapshot": {
            "status": creative.get("status") or "",
            "moderation_status": creative.get("moderation_status") or "",
            "rejection_reason": creative.get("rejection_reason") or board.get("review_reason") or "",
            "policy_flags": flags,
        },
    }
    try:
        cur.execute(
            """
            INSERT INTO pulse_ad_appeals
            (account_id, creative_id, campaign_id, submitted_by_user_id, message, status,
             appeal_type, reason_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', 'creative_rejection', ?, ?, ?)
            """,
            (account_id, creative_id, safe_int(creative.get("campaign_id"), 0), user_id, message,
             clean_json(reason_payload, 6000), now, now),
        )
    except sqlite3.OperationalError:
        # Legacy schema without slice-2 columns (ensure_schema not run yet).
        cur.execute(
            """
            INSERT INTO pulse_ad_appeals
            (account_id, creative_id, campaign_id, submitted_by_user_id, message, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (account_id, creative_id, safe_int(creative.get("campaign_id"), 0), user_id, message, now, now),
        )
    appeal_id = cur.lastrowid
    pulse_advertiser_portal._add_notification(
        conn,
        account_id,
        safe_int(creative.get("campaign_id"), 0) or None,
        creative_id,
        user_id,
        "creative_appeal_submitted",
        "Appeal submitted",
        f"Your appeal for creative '{creative.get('title')}' was received and is in the review queue.",
    )
    pulse_ads_service.audit_log(conn, user_id, "ad_creative_appeal_created", "pulse_ad_appeals", appeal_id, after={"creative_id": creative_id})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_appeals WHERE id=?", (appeal_id,))
    return _appeal_public(row_to_dict(cur.fetchone()))


def list_appeals(conn, user_id, account_id=0) -> dict:
    """Appeals across the user's ad accounts (or one account when given)."""
    cur = conn.cursor()
    account_id = safe_int(account_id, 0)
    if account_id:
        pulse_ads_service._owned_account(conn, user_id, account_id)
        cur.execute(
            "SELECT * FROM pulse_ad_appeals WHERE account_id=? ORDER BY id DESC LIMIT 200",
            (account_id,),
        )
    else:
        cur.execute(
            """
            SELECT ap.* FROM pulse_ad_appeals ap
            JOIN pulse_ad_accounts a ON a.id=ap.account_id
            WHERE a.owner_user_id=? ORDER BY ap.id DESC LIMIT 200
            """,
            (user_id,),
        )
    return {"appeals": [_appeal_public(row_to_dict(row)) for row in cur.fetchall()]}


APPEAL_DECISIONS = {"approved", "rejected"}


def admin_decide_appeal(conn, admin_user_id, appeal_id, decision, reason="") -> dict:
    """Decide an open appeal. An approved appeal flips the creative back to
    approved through the existing moderation function (which also restores
    its media assets and writes the audit trail)."""
    decision = clean_text(decision, 40).lower()
    if decision in ("approve", "grant", "uphold"):
        decision = "approved"
    if decision in ("reject", "deny", "denied"):
        decision = "rejected"
    if decision not in APPEAL_DECISIONS:
        raise PulseAdsError("decision must be 'approved' or 'rejected'.")
    reason = clean_text(reason, 1000)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_appeals WHERE id=?", (appeal_id,))
    appeal = row_to_dict(cur.fetchone())
    if not appeal:
        raise PulseAdsError("Appeal not found.", 404)
    if (appeal.get("status") or "open") != "open":
        raise PulseAdsError("This appeal has already been decided.", 409)
    creative_id = safe_int(appeal.get("creative_id"), 0)
    now = now_iso()
    if decision == "approved":
        pulse_ads_service.approve_creative(
            conn, admin_user_id, creative_id,
            notes=reason or f"Appeal #{appeal_id} approved.",
        )
    try:
        cur.execute(
            """
            UPDATE pulse_ad_appeals
            SET status=?, decision=?, decision_reason=?, resolution_notes=?,
                decided_by_user_id=?, decided_at=?, updated_at=?
            WHERE id=?
            """,
            (decision, decision, reason, reason, admin_user_id, now, now, appeal_id),
        )
    except sqlite3.OperationalError:
        cur.execute(
            "UPDATE pulse_ad_appeals SET status=?, resolution_notes=?, updated_at=? WHERE id=?",
            (decision, reason, now, appeal_id),
        )
    try:
        pulse_advertiser_portal._add_notification(
            conn,
            safe_int(appeal.get("account_id"), 0),
            safe_int(appeal.get("campaign_id"), 0) or None,
            creative_id,
            safe_int(appeal.get("submitted_by_user_id"), 0) or None,
            "creative_appeal_decided",
            f"Appeal {decision}",
            (f"Your appeal was approved and the creative is approved again."
             if decision == "approved"
             else f"Your appeal was reviewed and the original decision stands. {reason}".strip()),
        )
    except Exception:
        pass
    pulse_ads_service.audit_log(
        conn, admin_user_id, "ad_appeal_decided", "pulse_ad_appeals", appeal_id,
        before={"status": "open"}, after={"decision": decision, "reason": reason},
    )
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_appeals WHERE id=?", (appeal_id,))
    return _appeal_public(row_to_dict(cur.fetchone()))


EDITABLE_CREATIVE_STATUSES = {"draft", "rejected"}
EDITABLE_CREATIVE_FIELDS = ("title", "body", "headline", "primary_text", "call_to_action", "destination_url")


def update_creative(conn, user_id, creative_id, payload: dict) -> dict:
    """Edit a draft or rejected creative's text fields. Editing a rejected
    creative resets it to draft so it can be resubmitted through the existing
    creative_action 'submit' lifecycle (which re-queues moderation).
    """
    creative = pulse_ads_service.get_creative(conn, user_id, creative_id)
    payload = payload or {}
    status = clean_text(creative.get("status"), 40)
    if status not in EDITABLE_CREATIVE_STATUSES:
        raise PulseAdsError("Only draft or rejected creatives can be edited. Duplicate this creative to change it.", 409)
    updates = {}
    if "title" in payload:
        title = clean_text(payload.get("title"), pulse_ads_service.TEXT_LIMITS["title"])
        if not title:
            raise PulseAdsError("Creative title is required.")
        updates["title"] = title
    if "body" in payload:
        updates["body"] = clean_text(payload.get("body"), pulse_ads_service.TEXT_LIMITS["body"])
    if "headline" in payload:
        updates["headline"] = clean_text(payload.get("headline"), pulse_ads_service.TEXT_LIMITS["headline"])
    if "primary_text" in payload:
        updates["primary_text"] = clean_text(payload.get("primary_text"), pulse_ads_service.TEXT_LIMITS["primary_text"])
    if "call_to_action" in payload:
        updates["call_to_action"] = clean_text(payload.get("call_to_action"), pulse_ads_service.TEXT_LIMITS["call_to_action"])
    if "destination_url" in payload:
        updates["destination_url"] = pulse_ads_service.validate_destination_url(payload.get("destination_url"), required=True)
    if not updates:
        raise PulseAdsError("Nothing to update. Send at least one editable field.")
    now = now_iso()
    set_parts = [f"{column}=?" for column in updates]
    params = list(updates.values())
    set_parts.extend(["status='draft'", "moderation_status='draft'", "updated_at=?"])
    params.extend([now, creative_id])
    cur = conn.cursor()
    cur.execute(f"UPDATE pulse_ad_creatives SET {', '.join(set_parts)} WHERE id=?", tuple(params))
    pulse_ads_service.audit_log(
        conn, user_id, "ad_creative_edited", "pulse_ad_creatives", creative_id,
        before={"status": status}, after={key: updates[key] for key in updates},
    )
    conn.commit()
    return pulse_ads_service.get_creative(conn, user_id, creative_id)
