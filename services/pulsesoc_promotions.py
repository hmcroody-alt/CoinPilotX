"""Owner-scoped universal PulseSoc content promotions.

Promotions remain drafts until content ownership, policy, advertiser billing,
and wallet funding are all verified. Delivery and analytics are delegated to
the existing PulseSoc ads services so this module never invents reach or spend.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
import json

from services import ad_policy_engine, pulse_ad_payments, pulse_ads_service


PROMOTION_STATUSES = {
    "draft",
    "pending_review",
    "promoting",
    "paused",
    "rejected",
    "completed",
    "failed",
    "canceled",
}
ACTIVE_CONTENT_STATUSES = {"", "active", "approved", "published", "ready", "review_ready", "live", "ended"}
CONTENT_ALIASES = {
    "listing": "marketplace_listing",
    "marketplace": "marketplace_listing",
    "music": "music_release",
    "live": "live_stream",
    "photo_post": "photo",
    "video_post": "video",
}
POST_CONTENT_TYPES = {"post", "photo", "business_post", "creator_content", "article", "blog", "podcast", "event"}
GOALS_BY_CONTENT = {
    "reel": {"more_views", "more_followers", "more_engagement", "more_music_plays"},
    "post": {"more_views", "more_followers", "more_profile_visits", "more_engagement", "more_messages"},
    "photo": {"more_views", "more_followers", "more_profile_visits", "more_engagement"},
    "video": {"more_views", "more_followers", "more_engagement"},
    "story": {"more_views", "more_profile_visits", "more_engagement"},
    "status": {"more_views", "more_profile_visits", "more_engagement"},
    "live_stream": {"more_views", "more_followers", "more_engagement", "more_messages"},
    "marketplace_listing": {"more_marketplace_visits", "more_messages", "more_product_sales"},
    "event": {"more_event_responses", "more_profile_visits", "more_engagement"},
    "music_release": {"more_music_plays", "more_followers", "more_engagement"},
    "article": {"more_views", "more_profile_visits", "more_engagement", "more_website_clicks"},
    "blog": {"more_views", "more_profile_visits", "more_engagement", "more_website_clicks"},
    "podcast": {"more_views", "more_followers", "more_engagement", "more_music_plays"},
    "business_post": {"more_views", "more_messages", "more_website_clicks", "more_engagement"},
    "creator_content": {"more_views", "more_followers", "more_profile_visits", "more_engagement"},
}
GOAL_LABELS = {
    "more_views": "More Views",
    "more_followers": "More Followers",
    "more_profile_visits": "More Profile Visits",
    "more_website_clicks": "More Website Clicks",
    "more_messages": "More Messages",
    "more_marketplace_visits": "More Marketplace Visits",
    "more_music_plays": "More Music Plays",
    "more_engagement": "More Engagement",
    "more_event_responses": "More Event Responses",
    "more_product_sales": "More Product Sales",
    "more_community_joins": "More Community Joins",
}
OBJECTIVE_BY_GOAL = {
    "more_views": "awareness",
    "more_followers": "creator_growth",
    "more_profile_visits": "creator_promotion",
    "more_website_clicks": "traffic",
    "more_messages": "engagement",
    "more_marketplace_visits": "marketplace",
    "more_music_plays": "music_promotion",
    "more_engagement": "engagement",
    "more_event_responses": "event_promotion",
    "more_product_sales": "marketplace_sales",
    "more_community_joins": "engagement",
}
PLACEMENTS_BY_CONTENT = {
    "reel": ["video_pre_roll", "feed_inline"],
    "video": ["video_pre_roll", "feed_inline"],
    "status": ["status_interstitial"],
    "story": ["status_interstitial"],
    "marketplace_listing": ["marketplace_sponsor"],
    "music_release": ["pulse_radio_sponsor", "feed_inline"],
}
MIN_BUDGET_CENTS = 500
MAX_BUDGET_CENTS = 500_000
MAX_DURATION_DAYS = 30


class PromotionError(ValueError):
    def __init__(self, message: str, status_code: int = 400, *, promotion: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.promotion = promotion or {}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(row: Any) -> dict:
    return pulse_ads_service.row_to_dict(row)


def _text(value: Any, limit: int = 240) -> str:
    return pulse_ads_service.clean_text(value, limit)


def _int(value: Any, default: int = 0) -> int:
    return pulse_ads_service.safe_int(value, default)


def normalize_content_type(value: Any) -> str:
    content_type = _text(value, 50).lower().replace("-", "_").replace(" ", "_")
    content_type = CONTENT_ALIASES.get(content_type, content_type)
    if content_type in POST_CONTENT_TYPES or content_type in GOALS_BY_CONTENT:
        return content_type
    raise PromotionError("This content type is not supported for promotion.")


def ensure_tables(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_content_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            content_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            audience_type TEXT DEFAULT 'automatic',
            audience_json TEXT DEFAULT '{}',
            budget_type TEXT DEFAULT 'total',
            daily_budget_cents INTEGER DEFAULT 0,
            total_budget_cents INTEGER DEFAULT 0,
            start_date TEXT,
            end_date TEXT,
            duration_days INTEGER DEFAULT 1,
            placement TEXT DEFAULT 'auto',
            status TEXT DEFAULT 'draft',
            policy_status TEXT DEFAULT 'pending',
            policy_reason TEXT,
            billing_status TEXT DEFAULT 'not_ready',
            ad_account_id INTEGER,
            ad_campaign_id INTEGER,
            ad_creative_id INTEGER,
            created_at TEXT,
            updated_at TEXT,
            submitted_at TEXT,
            canceled_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_content_promotion_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER,
            actor_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT DEFAULT '{}',
            after_json TEXT DEFAULT '{}',
            created_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_promotions_owner ON pulse_content_promotions(owner_user_id, updated_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_promotions_content ON pulse_content_promotions(content_type, content_id, owner_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_promotion_audit ON pulse_content_promotion_audit(promotion_id, created_at)")
    conn.commit()


def _query_content(conn: Any, content_type: str, content_id: int) -> dict:
    cur = conn.cursor()
    try:
        if content_type == "reel":
            cur.execute(
                """
                SELECT r.id, r.user_id AS owner_user_id, COALESCE(p.title,'PulseSoc Reel') AS title,
                       COALESCE(r.caption,p.body,'') AS body, COALESCE(p.visibility,'public') AS visibility,
                       COALESCE(p.moderation_status,r.moderation_status,'approved') AS moderation_status,
                       COALESCE(r.status,'active') AS status
                FROM pulse_reels r LEFT JOIN pulse_posts p ON p.id=r.post_id
                WHERE r.id=? AND COALESCE(r.status,'active')!='deleted' LIMIT 1
                """,
                (content_id,),
            )
        elif content_type in POST_CONTENT_TYPES:
            cur.execute(
                """
                SELECT id, user_id AS owner_user_id, COALESCE(title,'PulseSoc Post') AS title, COALESCE(body,'') AS body,
                       COALESCE(visibility,'public') AS visibility, COALESCE(moderation_status,'approved') AS moderation_status,
                       COALESCE(status,'published') AS status, COALESCE(post_type,'post') AS source_type
                FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1
                """,
                (content_id,),
            )
        elif content_type == "video":
            cur.execute("SELECT id, owner_user_id, COALESCE(title,'PulseSoc Video') AS title, COALESCE(description,'') AS body, visibility, moderation_status, status FROM pulse_videos WHERE id=? LIMIT 1", (content_id,))
        elif content_type == "story":
            cur.execute("SELECT id, user_id AS owner_user_id, 'PulseSoc Story' AS title, COALESCE(body,'') AS body, visibility, 'approved' AS moderation_status, CASE WHEN deleted_at IS NULL THEN 'active' ELSE 'deleted' END AS status FROM pulse_stories WHERE id=? LIMIT 1", (content_id,))
        elif content_type == "status":
            cur.execute("SELECT id, user_id AS owner_user_id, 'PulseSoc Status' AS title, COALESCE(body,'') AS body, visibility, 'approved' AS moderation_status, CASE WHEN deleted_at IS NULL THEN 'active' ELSE 'deleted' END AS status FROM pulse_status WHERE id=? LIMIT 1", (content_id,))
        elif content_type == "live_stream":
            cur.execute("SELECT id, user_id AS owner_user_id, COALESCE(title,'PulseSoc Live') AS title, COALESCE(category,'') AS body, COALESCE(audience,'public') AS visibility, COALESCE(moderation_status,'clear') AS moderation_status, COALESCE(status,'active') AS status FROM pulse_live_sessions WHERE id=? LIMIT 1", (content_id,))
        elif content_type == "marketplace_listing":
            cur.execute("SELECT id, seller_user_id AS owner_user_id, COALESCE(title,'PulseSoc Listing') AS title, COALESCE(description,'') AS body, 'public' AS visibility, COALESCE(approval_status,'pending_review') AS moderation_status, COALESCE(status,'active') AS status FROM marketplace_listings WHERE id=? LIMIT 1", (content_id,))
        elif content_type == "music_release":
            cur.execute("SELECT id, uploader_user_id AS owner_user_id, COALESCE(title,'PulseSoc Music') AS title, COALESCE(description,artist,'') AS body, 'public' AS visibility, COALESCE(safety_status,'pending') AS moderation_status, CASE WHEN COALESCE(active,0)=1 AND removed_at IS NULL THEN 'active' ELSE 'disabled' END AS status FROM pulse_audio_tracks WHERE id=? LIMIT 1", (content_id,))
        else:
            return {}
        return _row(cur.fetchone())
    except Exception:
        return {}


def _destination(content_type: str, content_id: int) -> str:
    if content_type == "reel":
        return f"/pulse/reels/{content_id}"
    if content_type in POST_CONTENT_TYPES:
        return f"/pulse/post/{content_id}"
    if content_type == "video":
        return f"/pulse/videos/{content_id}"
    if content_type in {"story", "status"}:
        return f"/pulse/status?status={content_id}"
    if content_type == "live_stream":
        return f"/pulse/live/{content_id}"
    if content_type == "marketplace_listing":
        return f"/pulse/marketplace?listing={content_id}"
    if content_type == "music_release":
        return f"/pulse/music?track={content_id}"
    return "/pulse"


def assert_content_owner(conn: Any, content_type: Any, content_id: Any, current_user_id: Any) -> dict:
    normalized = normalize_content_type(content_type)
    identifier = _int(content_id)
    if identifier <= 0:
        raise PromotionError("Content not found.", 404)
    content = _query_content(conn, normalized, identifier)
    if not content:
        raise PromotionError("Content not found.", 404)
    if _int(content.get("owner_user_id")) != _int(current_user_id):
        raise PromotionError("Only the content owner can manage this promotion.", 403)
    content["content_type"] = normalized
    content["content_id"] = identifier
    content["destination_url"] = _destination(normalized, identifier)
    return content


def _content_eligibility(content: dict) -> tuple[bool, str]:
    if _text(content.get("visibility") or "public", 30).lower() != "public":
        return False, "Only public content can be promoted."
    status = _text(content.get("status"), 40).lower()
    if status not in ACTIVE_CONTENT_STATUSES:
        return False, "This content is not active and cannot be promoted."
    moderation = _text(content.get("moderation_status") or "approved", 40).lower()
    if moderation not in {"approved", "clear", "review_ready", "safe"}:
        return False, "Content must pass safety review before promotion."
    return True, "Content is eligible for promotion review."


def billing_readiness(conn: Any, owner_user_id: Any) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, status, verification_status FROM pulse_ad_accounts
        WHERE owner_user_id=? ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, id DESC LIMIT 1
        """,
        (_int(owner_user_id),),
    )
    account = _row(cur.fetchone())
    if not account:
        return {"ready": False, "state": "account_required", "message": "Create and verify a PulseSoc advertiser account before launching.", "ad_account_id": 0}
    if _text(account.get("status"), 40) != "active" or _text(account.get("verification_status"), 40) not in {"verified", "approved"}:
        return {"ready": False, "state": "verification_required", "message": "Advertiser verification is required before launch.", "ad_account_id": _int(account.get("id"))}
    if not pulse_ad_payments.billing_enabled():
        return {"ready": False, "state": "billing_disabled", "message": "Promotion billing is not enabled yet. Your promotion can still be saved as a draft.", "ad_account_id": _int(account.get("id"))}
    spendable = pulse_ad_payments.spendable_balance_cents(conn, account.get("id"))
    if spendable < MIN_BUDGET_CENTS:
        return {"ready": False, "state": "funding_required", "message": "Fund the advertiser wallet before launching this promotion.", "ad_account_id": _int(account.get("id")), "funding_available": pulse_ad_payments.stripe_ready()}
    return {"ready": True, "state": "ready", "message": "Billing and advertiser wallet are ready for review submission.", "ad_account_id": _int(account.get("id")), "spendable_balance_cents": spendable}


def eligibility(conn: Any, owner_user_id: Any, content_type: Any, content_id: Any) -> dict:
    ensure_tables(conn)
    content = assert_content_owner(conn, content_type, content_id, owner_user_id)
    eligible, reason = _content_eligibility(content)
    content_type = content["content_type"]
    supported = GOALS_BY_CONTENT.get(content_type, GOALS_BY_CONTENT["post"])
    goals = [{"key": key, "label": GOAL_LABELS[key], "enabled": key in supported, "reason": "" if key in supported else "Not supported for this content type."} for key in GOAL_LABELS]
    return {
        "ok": True,
        "eligible": eligible,
        "reason": reason,
        "content": {"content_type": content_type, "content_id": content["content_id"], "title": content.get("title") or "PulseSoc content", "destination_url": content["destination_url"]},
        "goals": goals,
        "audiences": [
            {"key": "automatic", "label": "Automatic Audience", "enabled": True, "reason": "Contextual delivery without sensitive targeting."},
            {"key": "custom", "label": "Custom Audience", "enabled": False, "reason": "Custom targeting is unavailable until privacy-safe audience controls are configured."},
            {"key": "lookalike", "label": "Followers Lookalike", "enabled": False, "reason": "Lookalike targeting is not enabled."},
        ],
        "estimated_reach": None,
        "forecasting_state": "unavailable",
        "forecasting_message": "Estimated reach is unavailable because no approved forecasting provider is configured.",
        "billing": billing_readiness(conn, owner_user_id),
    }


def _parse_date(value: Any, fallback: date) -> date:
    raw = _text(value, 20)
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise PromotionError("Use valid promotion dates.") from exc


def _validated_payload(content: dict, payload: dict) -> dict:
    content_type = content["content_type"]
    goal = _text(payload.get("goal"), 50).lower()
    if goal not in GOALS_BY_CONTENT.get(content_type, set()):
        raise PromotionError("That promotion goal is not supported for this content type.")
    audience = payload.get("audience") if isinstance(payload.get("audience"), dict) else {}
    audience_type = _text(audience.get("type") or payload.get("audience_type") or "automatic", 30).lower()
    if audience_type != "automatic":
        raise PromotionError("Custom audience targeting is not enabled. Choose Automatic Audience.")
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    budget_type = _text(budget.get("type") or payload.get("budget_type") or "total", 20).lower()
    if budget_type not in {"daily", "total"}:
        raise PromotionError("Choose a daily or total budget.")
    amount = _int(budget.get("amount_cents") or payload.get("budget_cents") or payload.get("total_budget_cents") or payload.get("daily_budget_cents"))
    if amount < MIN_BUDGET_CENTS or amount > MAX_BUDGET_CENTS:
        raise PromotionError("Promotion budget must be between $5.00 and $5,000.00.")
    duration = payload.get("duration") if isinstance(payload.get("duration"), dict) else {}
    duration_days = _int(duration.get("days") or payload.get("duration_days") or 1)
    if duration_days < 1 or duration_days > MAX_DURATION_DAYS:
        raise PromotionError("Promotion duration must be between 1 and 30 days.")
    start = _parse_date(duration.get("start_date") or payload.get("start_date"), date.today())
    end = _parse_date(duration.get("end_date") or payload.get("end_date"), start + timedelta(days=duration_days - 1))
    if end < start or (end - start).days + 1 > MAX_DURATION_DAYS:
        raise PromotionError("Promotion dates must cover 1 to 30 days.")
    policy = ad_policy_engine.evaluate_ad({"category": content_type, "headline": content.get("title"), "body": content.get("body"), "destination_url": content.get("destination_url")})
    if _text(policy.get("status"), 40).lower() in {"blocked", "rejected"}:
        raise PromotionError("This content did not pass promotion policy review.", 403)
    return {
        "goal": goal,
        "audience_type": audience_type,
        "audience_json": json.dumps({"type": "automatic"}, ensure_ascii=True),
        "budget_type": budget_type,
        "daily_budget_cents": amount if budget_type == "daily" else 0,
        "total_budget_cents": amount if budget_type == "total" else amount * duration_days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "duration_days": duration_days,
        "placement": "auto",
        "policy_status": _text(policy.get("status") or "needs_review", 40),
        "policy_reason": "; ".join(_text(reason, 180) for reason in (policy.get("reasons") or []))[:600],
    }


def _audit(conn: Any, promotion_id: int, actor_user_id: int, action: str, before: dict | None = None, after: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO pulse_content_promotion_audit (promotion_id, actor_user_id, action, before_json, after_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (promotion_id, actor_user_id, _text(action, 80), json.dumps(before or {}, default=str)[:6000], json.dumps(after or {}, default=str)[:6000], _now()),
    )


def _safe(row: dict) -> dict:
    item = dict(row or {})
    for key in ("audience_json", "policy_reason"):
        item.pop(key, None)
    item["promotion_id"] = _int(item.get("id"))
    item["daily_budget"] = f"${_int(item.get('daily_budget_cents')) / 100:,.2f}"
    item["total_budget"] = f"${_int(item.get('total_budget_cents')) / 100:,.2f}"
    item["estimated_reach"] = None
    item["analytics_available"] = bool(item.get("ad_campaign_id"))
    return item


def get_promotion(conn: Any, owner_user_id: Any, promotion_id: Any) -> dict:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_content_promotions WHERE id=? AND owner_user_id=? LIMIT 1", (_int(promotion_id), _int(owner_user_id)))
    promotion = _row(cur.fetchone())
    if not promotion:
        raise PromotionError("Promotion not found.", 404)
    return _safe(promotion)


def list_promotions(conn: Any, owner_user_id: Any, content_type: Any = "", content_id: Any = 0) -> list[dict]:
    ensure_tables(conn)
    cur = conn.cursor()
    if content_type and _int(content_id):
        normalized = normalize_content_type(content_type)
        cur.execute("SELECT * FROM pulse_content_promotions WHERE owner_user_id=? AND content_type=? AND content_id=? ORDER BY id DESC LIMIT 100", (_int(owner_user_id), normalized, str(_int(content_id))))
    else:
        cur.execute("SELECT * FROM pulse_content_promotions WHERE owner_user_id=? ORDER BY id DESC LIMIT 100", (_int(owner_user_id),))
    return [_safe(_row(row)) for row in cur.fetchall()]


def content_status(conn: Any, owner_user_id: Any, content_type: Any, content_id: Any) -> dict:
    promotions = list_promotions(conn, owner_user_id, content_type, content_id)
    if not promotions:
        return {}
    return promotions[0]


def _launch(conn: Any, owner_user_id: int, promotion: dict, content: dict) -> dict:
    billing = billing_readiness(conn, owner_user_id)
    if not billing.get("ready"):
        conn.execute("UPDATE pulse_content_promotions SET billing_status=?, updated_at=? WHERE id=? AND owner_user_id=?", (billing.get("state") or "not_ready", _now(), promotion["id"], owner_user_id))
        conn.commit()
        blocked = get_promotion(conn, owner_user_id, promotion["id"])
        raise PromotionError(billing.get("message") or "Promotion billing is unavailable.", 409, promotion=blocked)
    amount = _int(promotion.get("daily_budget_cents") or promotion.get("total_budget_cents"))
    campaign = pulse_ads_service.create_campaign(
        conn,
        owner_user_id,
        {
            "ad_account_id": billing["ad_account_id"],
            "campaign_name": f"Promote {content.get('title') or content['content_type']}"[:120],
            "objective": OBJECTIVE_BY_GOAL[promotion["goal"]],
            "budget_type": "daily" if promotion["budget_type"] == "daily" else "lifetime",
            "daily_budget_cents": amount if promotion["budget_type"] == "daily" else 0,
            "lifetime_budget_cents": _int(promotion.get("total_budget_cents")),
            "start_at": promotion["start_date"],
            "end_at": promotion["end_date"],
            "placements": PLACEMENTS_BY_CONTENT.get(content["content_type"], ["feed_inline"]),
        },
    )
    creative = pulse_ads_service.create_creative(
        conn,
        owner_user_id,
        {
            "campaign_id": campaign["id"],
            "creative_type": "text",
            "title": content.get("title") or "PulseSoc content",
            "body": content.get("body") or "View this PulseSoc content.",
            "destination_url": content["destination_url"],
            "call_to_action": "View",
            "category": content["content_type"],
        },
    )
    submitted = pulse_ads_service.submit_creative_for_review(conn, owner_user_id, creative["id"])
    pulse_ad_payments.reserve_campaign_budget(conn, owner_user_id, campaign["id"])
    now = _now()
    conn.execute(
        """
        UPDATE pulse_content_promotions
        SET status='pending_review', billing_status='reserved', ad_account_id=?, ad_campaign_id=?, ad_creative_id=?, submitted_at=?, updated_at=?
        WHERE id=? AND owner_user_id=?
        """,
        (billing["ad_account_id"], campaign["id"], submitted["id"], now, now, promotion["id"], owner_user_id),
    )
    _audit(conn, promotion["id"], owner_user_id, "promotion_submitted", promotion, {"status": "pending_review", "campaign_id": campaign["id"]})
    conn.commit()
    return get_promotion(conn, owner_user_id, promotion["id"])


def create_promotion(conn: Any, owner_user_id: Any, payload: dict) -> dict:
    ensure_tables(conn)
    content = assert_content_owner(conn, payload.get("content_type"), payload.get("content_id"), owner_user_id)
    eligible, reason = _content_eligibility(content)
    if not eligible:
        raise PromotionError(reason, 403)
    data = _validated_payload(content, payload)
    now = _now()
    billing = billing_readiness(conn, owner_user_id)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_content_promotions
        (owner_user_id, content_type, content_id, goal, audience_type, audience_json, budget_type,
         daily_budget_cents, total_budget_cents, start_date, end_date, duration_days, placement, status,
         policy_status, policy_reason, billing_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
        """,
        (_int(owner_user_id), content["content_type"], str(content["content_id"]), data["goal"], data["audience_type"], data["audience_json"], data["budget_type"], data["daily_budget_cents"], data["total_budget_cents"], data["start_date"], data["end_date"], data["duration_days"], data["placement"], data["policy_status"], data["policy_reason"], billing.get("state") or "not_ready", now, now),
    )
    promotion_id = _int(cur.lastrowid)
    _audit(conn, promotion_id, _int(owner_user_id), "promotion_draft_created", after={"content_type": content["content_type"], "content_id": content["content_id"], "goal": data["goal"]})
    conn.commit()
    promotion = _row(conn.execute("SELECT * FROM pulse_content_promotions WHERE id=?", (promotion_id,)).fetchone())
    if bool(payload.get("launch")):
        return _launch(conn, _int(owner_user_id), promotion, content)
    return get_promotion(conn, owner_user_id, promotion_id)


def update_promotion(conn: Any, owner_user_id: Any, promotion_id: Any, payload: dict) -> dict:
    current = get_promotion(conn, owner_user_id, promotion_id)
    if current.get("status") != "draft":
        raise PromotionError("Only draft promotions can be edited.", 409)
    content = assert_content_owner(conn, current["content_type"], current["content_id"], owner_user_id)
    merged = {**current, **payload, "content_type": current["content_type"], "content_id": current["content_id"]}
    data = _validated_payload(content, merged)
    now = _now()
    conn.execute(
        """
        UPDATE pulse_content_promotions
        SET goal=?, audience_type=?, audience_json=?, budget_type=?, daily_budget_cents=?, total_budget_cents=?,
            start_date=?, end_date=?, duration_days=?, policy_status=?, policy_reason=?, updated_at=?
        WHERE id=? AND owner_user_id=?
        """,
        (data["goal"], data["audience_type"], data["audience_json"], data["budget_type"], data["daily_budget_cents"], data["total_budget_cents"], data["start_date"], data["end_date"], data["duration_days"], data["policy_status"], data["policy_reason"], now, _int(promotion_id), _int(owner_user_id)),
    )
    _audit(conn, _int(promotion_id), _int(owner_user_id), "promotion_draft_updated", current, data)
    conn.commit()
    updated = get_promotion(conn, owner_user_id, promotion_id)
    if bool(payload.get("launch")):
        return _launch(conn, _int(owner_user_id), {**updated, "id": updated["promotion_id"]}, content)
    return updated


def transition(conn: Any, owner_user_id: Any, promotion_id: Any, action: str) -> dict:
    current = get_promotion(conn, owner_user_id, promotion_id)
    action = _text(action, 30).lower()
    allowed = {
        "pause": ({"promoting"}, "paused"),
        "resume": ({"paused"}, "promoting"),
        "cancel": ({"draft", "pending_review", "promoting", "paused", "failed"}, "canceled"),
    }
    if action not in allowed:
        raise PromotionError("Unsupported promotion action.")
    valid_from, next_status = allowed[action]
    if current.get("status") not in valid_from:
        raise PromotionError(f"This promotion cannot be {action}d from its current state.", 409)
    now = _now()
    conn.execute(
        "UPDATE pulse_content_promotions SET status=?, canceled_at=?, updated_at=? WHERE id=? AND owner_user_id=?",
        (next_status, now if next_status == "canceled" else None, now, _int(promotion_id), _int(owner_user_id)),
    )
    _audit(conn, _int(promotion_id), _int(owner_user_id), f"promotion_{action}", current, {"status": next_status})
    conn.commit()
    return get_promotion(conn, owner_user_id, promotion_id)


def analytics(conn: Any, owner_user_id: Any, promotion_id: Any) -> dict:
    promotion = get_promotion(conn, owner_user_id, promotion_id)
    campaign_id = _int(promotion.get("ad_campaign_id"))
    if not campaign_id:
        return {"ok": True, "available": False, "message": "Promotion analytics are unavailable until a campaign enters review.", "promotion_id": promotion["promotion_id"], "metrics": None}
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(spent_cents,0) AS spent_cents, status, start_at, end_at FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    campaign = _row(cur.fetchone())
    cur.execute("SELECT COUNT(*) AS total FROM pulse_ad_impressions WHERE campaign_id=?", (campaign_id,))
    impressions = _int(_row(cur.fetchone()).get("total"))
    cur.execute("SELECT COUNT(*) AS total FROM pulse_ad_clicks WHERE campaign_id=?", (campaign_id,))
    clicks = _int(_row(cur.fetchone()).get("total"))
    return {
        "ok": True,
        "available": True,
        "promotion_id": promotion["promotion_id"],
        "metrics": {
            "spend_cents": _int(campaign.get("spent_cents")),
            "views_from_promotion": impressions,
            "clicks": clicks,
            "engagement": None,
            "followers_gained": None,
            "messages_started": None,
            "marketplace_visits": None,
            "music_plays": None,
            "cost_per_result_cents": round(_int(campaign.get("spent_cents")) / clicks) if clicks else None,
            "status": promotion.get("status"),
            "start_date": campaign.get("start_at") or promotion.get("start_date"),
            "end_date": campaign.get("end_at") or promotion.get("end_date"),
        },
        "unavailable_metrics": ["engagement", "followers_gained", "messages_started", "marketplace_visits", "music_plays"],
    }
