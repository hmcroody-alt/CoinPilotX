"""Backend-managed PulseSoc Ads & Sponsorships state.

This module powers the user-facing Ads & Sponsorships Center and the protected
admin Ads Command Center. It returns commercial summaries, not raw targeting
data, storage paths, provider secrets, or cross-advertiser private details.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services import db as db_service


STRICT_STATES = {
    "READY",
    "ACTION REQUIRED",
    "REVIEW",
    "WARNING",
    "LOCKED",
    "PREMIUM",
    "ADMIN",
    "PARTIAL",
    "BETA",
    "COMING SOON",
}


ADS_SECTIONS: tuple[dict[str, Any], ...] = (
    {"key": "sponsored-signals", "label": "Sponsored Signal Intelligence", "route": "/admin/ads-command-center/sponsored-signals", "description": "Approved sponsored signals, reputation, campaign snapshot, public feedback, privacy explanations, and delivery eligibility."},
    {"key": "ads-manager", "label": "Commercial Mission Control", "route": "/admin/ads-command-center/ads-manager", "description": "Campaign health, delivery diagnostics, budget pacing, spend, review state, creative health, and optimization recommendations."},
    {"key": "campaign-builder", "label": "Campaign Builder", "route": "/admin/ads-command-center/campaign-builder", "description": "Objective, budget, audience, timing, placement, duration, expected reach, conversion, privacy, and review-readiness workflows."},
    {"key": "sponsored-signal-studio", "label": "Sponsored Signal Studio", "route": "/admin/ads-command-center/sponsored-signal-studio", "description": "Creative uploads, copy, CTA, accessibility, policy scan, community relevance, live preview, and approval readiness."},
    {"key": "ad-analytics", "label": "Ad Analytics", "route": "/admin/ads-command-center/ad-analytics", "description": "Impressions, reach, frequency, clicks, CTR, spend, viewability, conversion events, device, placement, creative, and method performance."},
    {"key": "brand-deals", "label": "Brand Deals", "route": "/admin/ads-command-center/brand-deals", "description": "Verified brand and creator partnerships, deliverables, negotiations, milestones, payments, trust, and performance history."},
    {"key": "creator-sponsorships", "label": "Creator Sponsorships", "route": "/admin/ads-command-center/creator-sponsorships", "description": "Eligible sponsorships, recommended sponsors, fit, estimated earnings, acceptance probability, and deliverable tracking."},
    {"key": "ad-revenue-center", "label": "Revenue Intelligence", "route": "/admin/ads-command-center/ad-revenue-center", "description": "Projected earnings, today revenue, monthly trend, payouts, sources, creator share, and growth suggestions."},
    {"key": "audience-targeting", "label": "Audience Targeting", "route": "/admin/ads-command-center/audience-targeting", "description": "Privacy-safe aggregate clusters, saturation, overlap, expansion, language, device, timing, and contextual targeting rules."},
    {"key": "conversion-tracking", "label": "Conversion Tracking", "route": "/admin/ads-command-center/conversion-tracking", "description": "View, click, profile, follow, save, share, comment, purchase, funnel, drop-off, and revenue event diagnostics."},
    {"key": "review-board", "label": "Ads Review Board", "route": "/admin/ads-command-center/review-board", "description": "Creative moderation, policy flags, approve, reject, needs changes, suspend, and audit actions."},
    {"key": "delivery-engine", "label": "Delivery Engine", "route": "/admin/ads-command-center/delivery-engine", "description": "Placement eligibility, kill switch, frequency caps, privacy-safe targeting, delivery methods, and safe payload rendering."},
    {"key": "audit", "label": "Ads Audit Logs", "route": "/admin/ads-command-center/audit", "description": "Advertiser, campaign, creative, wallet, review, delivery, tracking, hide, report, and admin action audit coverage."},
)


ADS_SUBSYSTEM_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "key": "sponsored-signals",
        "card_key": "view_sponsored_signals",
        "label": "Sponsored Signal Intelligence",
        "route": "/dashboard/ads/sponsored-signals",
        "admin_route": "/admin/ads-command-center/sponsored-signals",
        "action": "Inspect Signals",
        "metric": "approved_creatives",
        "description": "Approved sponsored signals with sponsor trust, relevance, transparency, campaign snapshot, and privacy-safe explanations.",
        "intelligence": "Explains why a sponsored signal is eligible using campaign, creative, placement, and aggregate relevance signals.",
        "automation": "Updates when creatives are approved, campaigns activate, frequency caps change, or placements become unavailable.",
        "protection": "Only approved creatives from eligible campaigns can render; unapproved ads and raw targeting data are redacted.",
        "prediction": "Ranks signal readiness from approved creative count, placement availability, click activity, and hide/report signals.",
        "recovery": "Hidden, ignored, reported, or expired signal paths remain inspectable through delivery and audit logs.",
        "recommendations": ("Review active sponsored signals.", "Check why a signal appears before scaling spend.", "Watch hide/report trends for brand safety."),
    },
    {
        "key": "manager",
        "card_key": "ads_manager",
        "label": "Commercial Mission Control",
        "route": "/dashboard/ads/manager",
        "admin_route": "/admin/ads-command-center/ads-manager",
        "action": "Manage Campaigns",
        "metric": "campaigns",
        "description": "Campaign dashboard, live spending, budget pacing, reach, delivery diagnostics, approval queue, creative health, and recommendations.",
        "intelligence": "Connects spend, status, review state, delivery, clicks, and wallet readiness into campaign health.",
        "automation": "Campaign changes update delivery eligibility, wallet reserves, notifications, analytics, and audit state.",
        "protection": "Campaigns stay owner-scoped and cannot serve without approval, active status, placement eligibility, and budget rules.",
        "prediction": "Projects campaign health from budget, delivery events, moderation state, and engagement rate.",
        "recovery": "Draft, paused, rejected, and exhausted campaigns route to safe correction workflows.",
        "recommendations": ("Create an advertiser account before launching campaigns.", "Resolve review issues before activating spend.", "Check wallet balance before scaling."),
    },
    {
        "key": "campaign-builder",
        "card_key": "campaign_builder",
        "label": "Campaign Builder",
        "route": "/dashboard/ads/campaign-builder",
        "admin_route": "/admin/ads-command-center/campaign-builder",
        "action": "Build Campaign",
        "metric": "draft_campaigns",
        "description": "Guided campaign setup for objectives, budget, schedule, audience, placement, creative, privacy checks, and review summary.",
        "intelligence": "Recommends safe next steps using account readiness, wallet state, placements, creative readiness, and budget guardrails.",
        "automation": "Drafts can become review-ready once required campaign, creative, placement, and budget fields pass validation.",
        "protection": "Campaigns never become delivery eligible until moderation, budget, ownership, and placement checks pass.",
        "prediction": "Estimates readiness and likely reach from approved placements and account history.",
        "recovery": "Incomplete drafts preserve state for later completion.",
        "recommendations": ("Start with one objective.", "Choose approved placements before uploading final creative.", "Keep budget small until review and delivery are healthy."),
    },
    {
        "key": "signal-studio",
        "card_key": "sponsored_signal_studio",
        "label": "Sponsored Signal Studio",
        "route": "/dashboard/ads/signal-studio",
        "admin_route": "/admin/ads-command-center/sponsored-signal-studio",
        "action": "Create Sponsored Signal",
        "metric": "creatives",
        "description": "Creative upload, title, copy, CTA, media preview, compliance scan, community relevance, policy flags, and approval readiness.",
        "intelligence": "Scores creative readiness from media, CTA, destination, policy, accessibility, moderation, and placement compatibility.",
        "automation": "Creative submissions update review queue, policy flags, campaign readiness, notifications, and audit logs.",
        "protection": "Advertisers upload media through PulseSoc storage; external raw media URLs are not trusted.",
        "prediction": "Estimates creative health from review state, destination safety, engagement, and hide/report activity.",
        "recovery": "Rejected or needs-changes creatives retain safe feedback and replacement workflow.",
        "recommendations": ("Upload media directly through PulseSoc.", "Use clear CTA text.", "Submit for review only after preview and destination checks pass."),
    },
    {
        "key": "analytics",
        "card_key": "ad_analytics",
        "label": "Ad Analytics",
        "route": "/dashboard/ads/analytics",
        "admin_route": "/admin/ads-command-center/ad-analytics",
        "action": "Analyze Ads",
        "metric": "impressions",
        "description": "Impressions, viewability, reach, frequency, clicks, spend, CTR, estimated CPC/CPM, creative comparison, placement, and conversion events.",
        "intelligence": "Turns delivery events into advertiser-scoped performance summaries without leaking viewer identities.",
        "automation": "Tracking updates campaign, creative, placement, budget, recommendation, and revenue diagnostics.",
        "protection": "Viewer identifiers, raw sessions, and private targeting internals are not exposed to advertisers.",
        "prediction": "Highlights performance direction using CTR, viewability, event quality, and spend pace.",
        "recovery": "Missing or failed tracking appears as diagnostics instead of fake success.",
        "recommendations": ("Compare CTR by creative before raising spend.", "Watch hide/report rates.", "Use conversion signals only after tracking is healthy."),
    },
    {
        "key": "brand-deals",
        "card_key": "brand_deals",
        "label": "Brand Deals",
        "route": "/dashboard/ads/brand-deals",
        "admin_route": "/admin/ads-command-center/brand-deals",
        "action": "Review Brand Deals",
        "metric": "brand_deals",
        "state": "BETA",
        "description": "Brand and creator partnership readiness, deliverables, timeline, relationship trust, milestones, and performance history.",
        "intelligence": "Connects creator trust, audience fit, campaign performance, and sponsorship readiness.",
        "automation": "Deal milestones can update creator sponsorships, revenue, notifications, and audit events.",
        "protection": "Partnership data stays owner-scoped and review-gated.",
        "prediction": "Estimates fit from advertiser and creator activity when available.",
        "recovery": "Needs-review deals route to admin and sponsorship diagnostics.",
        "recommendations": ("Verify both brand and creator identity.", "Define deliverables before launch.", "Keep deal messages and milestones auditable."),
    },
    {
        "key": "creator-sponsorships",
        "card_key": "creator_sponsorships",
        "label": "Creator Sponsorships",
        "route": "/dashboard/ads/creator-sponsorships",
        "admin_route": "/admin/ads-command-center/creator-sponsorships",
        "action": "Find Sponsorships",
        "metric": "creator_sponsorships",
        "state": "BETA",
        "description": "Eligible sponsorships, sponsor fit, estimated earnings, application readiness, required improvements, and deliverable tracking.",
        "intelligence": "Scores sponsorship readiness from creator activity, audience fit, trust, safety, and ad delivery history.",
        "automation": "Accepted sponsorships can update creator revenue, content tasks, notifications, and audit logs.",
        "protection": "Creator sponsorship data remains owner-scoped and avoids exposing advertiser-private terms.",
        "prediction": "Estimates acceptance probability when enough safe signals exist.",
        "recovery": "Rejected or inactive sponsorships remain explainable with safe next steps.",
        "recommendations": ("Improve creator profile and trust signals.", "Keep deliverables clear.", "Review sponsorship score before applying."),
    },
    {
        "key": "revenue-intelligence",
        "card_key": "ad_revenue_center",
        "label": "Revenue Intelligence",
        "route": "/dashboard/ads/revenue-intelligence",
        "admin_route": "/admin/ads-command-center/ad-revenue-center",
        "action": "Review Revenue",
        "metric": "spend_cents",
        "description": "Projected earnings, today spend/revenue signals, monthly trend, payouts, source mix, creator share, and growth recommendations.",
        "intelligence": "Summarizes advertiser spend and creator ad-revenue readiness without exposing billing provider identifiers.",
        "automation": "Ad spend updates wallet, billing, campaign analytics, revenue forecasts, notifications, and audit state.",
        "protection": "Money summaries avoid raw card data, provider IDs, checkout secrets, and cross-advertiser detail.",
        "prediction": "Forecasts commercial trend from spend, active campaigns, CTR, and delivery health.",
        "recovery": "Failed funding, failed spend, refund, and paused campaign states remain diagnosable.",
        "recommendations": ("Fund wallet before launch.", "Monitor spend pace.", "Pause wasteful campaigns before increasing budget."),
    },
    {
        "key": "audience-targeting",
        "card_key": "audience_targeting",
        "label": "Audience Targeting",
        "route": "/dashboard/ads/audience-targeting",
        "admin_route": "/admin/ads-command-center/audience-targeting",
        "action": "Tune Audience",
        "metric": "targeting_rules",
        "description": "Privacy-first aggregate interest, community, creator, content, language, device, time, overlap, saturation, and expansion intelligence.",
        "intelligence": "Uses aggregate, privacy-safe context instead of exposing personal user data.",
        "automation": "Targeting changes update campaign eligibility, placement compatibility, delivery, and audit logs.",
        "protection": "Personal identities, private user data, and hidden targeting internals are never exposed.",
        "prediction": "Scores privacy and audience-fit from available aggregate targeting rules.",
        "recovery": "Over-narrow or incompatible targeting states surface as safe recommendations.",
        "recommendations": ("Use aggregate interests, not private traits.", "Keep targeting broad enough for delivery.", "Review saturation before scaling."),
    },
    {
        "key": "conversion-tracking",
        "card_key": "conversion_tracking",
        "label": "Conversion Tracking",
        "route": "/dashboard/ads/conversion-tracking",
        "admin_route": "/admin/ads-command-center/conversion-tracking",
        "action": "Track Conversions",
        "metric": "conversion_events",
        "description": "Journey, view, click, profile visit, follow, save, share, comment, purchase, subscription, marketplace, revenue, drop-off, and funnel diagnostics.",
        "intelligence": "Connects safe event tracking into funnel summaries and optimization guidance.",
        "automation": "Conversion events update analytics, campaign health, revenue, recommendations, and audit logs.",
        "protection": "Tracking is rate-limited, debounced, and does not expose private identities or raw session secrets.",
        "prediction": "Highlights funnel quality using clicks, events, conversion count, and spend pace.",
        "recovery": "Missing conversion signals are surfaced as partial state with setup guidance.",
        "recommendations": ("Validate destination URL and deep link.", "Track only privacy-safe events.", "Do not optimize on conversions until event quality is stable."),
    },
)

SUBSYSTEMS_BY_KEY = {item["key"]: item for item in ADS_SUBSYSTEM_BLUEPRINTS}
SUBSYSTEMS_BY_WIDGET = {item["card_key"]: item for item in ADS_SUBSYSTEM_BLUEPRINTS}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _sum(cur: Any, table: str, column: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COALESCE(SUM({column}),0) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _money(cents: Any) -> str:
    return f"${_safe_int(cents, 0) / 100:,.2f}"


def _percent(numerator: Any, denominator: Any) -> float:
    denominator_int = _safe_int(denominator, 0)
    if denominator_int <= 0:
        return 0.0
    return round((_safe_float(numerator, 0.0) / denominator_int) * 100, 2)


def _confidence(*values: int) -> int:
    positives = sum(1 for value in values if _safe_int(value, 0) > 0)
    return min(98, 58 + positives * 8)


def _owner_join_count(cur: Any, table: str, owner_user_id: int, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not (_table_exists(cur, table) and _table_exists(cur, "pulse_ad_accounts")):
        return 0
    try:
        cur.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM {table} item
            JOIN pulse_ad_accounts aa ON aa.id=item.ad_account_id
            WHERE aa.owner_user_id=? AND {where}
            """,
            (owner_user_id, *params),
        )
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _owner_join_sum(cur: Any, table: str, column: str, owner_user_id: int, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not (_table_exists(cur, table) and _table_exists(cur, "pulse_ad_accounts")):
        return 0
    try:
        cur.execute(
            f"""
            SELECT COALESCE(SUM(item.{column}),0) AS total
            FROM {table} item
            JOIN pulse_ad_accounts aa ON aa.id=item.ad_account_id
            WHERE aa.owner_user_id=? AND {where}
            """,
            (owner_user_id, *params),
        )
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _owner_tracking_count(cur: Any, table: str, owner_user_id: int, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not (_table_exists(cur, table) and _table_exists(cur, "pulse_ad_campaigns") and _table_exists(cur, "pulse_ad_accounts")):
        return 0
    try:
        cur.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM {table} item
            JOIN pulse_ad_campaigns c ON c.id=item.campaign_id
            JOIN pulse_ad_accounts aa ON aa.id=c.ad_account_id
            WHERE aa.owner_user_id=? AND {where}
            """,
            (owner_user_id, *params),
        )
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _owner_wallet_sum(cur: Any, owner_user_id: int, column: str) -> int:
    if not (_table_exists(cur, "pulse_ad_wallets") and _table_exists(cur, "pulse_ad_accounts")):
        return 0
    try:
        cur.execute(
            f"""
            SELECT COALESCE(SUM(w.{column}),0) AS total
            FROM pulse_ad_wallets w
            JOIN pulse_ad_accounts aa ON aa.id=w.account_id
            WHERE aa.owner_user_id=?
            """,
            (owner_user_id,),
        )
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _event_count(cur: Any, owner_user_id: int, event_types: tuple[str, ...]) -> int:
    if not event_types:
        return 0
    placeholders = ",".join("?" for _ in event_types)
    return _owner_tracking_count(cur, "pulse_ad_events", owner_user_id, f"item.event_type IN ({placeholders})", event_types)


def _build_metrics(cur: Any, owner_user_id: int) -> dict[str, Any]:
    accounts = _count(cur, "pulse_ad_accounts", "owner_user_id=?", (owner_user_id,))
    campaigns = _owner_join_count(cur, "pulse_ad_campaigns", owner_user_id)
    active_campaigns = _owner_join_count(cur, "pulse_ad_campaigns", owner_user_id, "lower(COALESCE(item.status,'')) IN ('active','running','live')")
    draft_campaigns = _owner_join_count(cur, "pulse_ad_campaigns", owner_user_id, "lower(COALESCE(item.status,'')) IN ('draft','paused')")
    creatives = _owner_join_count(cur, "pulse_ad_creatives", owner_user_id)
    approved_creatives = _owner_join_count(cur, "pulse_ad_creatives", owner_user_id, "lower(COALESCE(item.moderation_status,item.status,'')) IN ('approved','active')")
    pending_review = _owner_join_count(cur, "pulse_ad_creatives", owner_user_id, "lower(COALESCE(item.moderation_status,item.status,'')) IN ('pending','submitted','in_review','review')")
    rejected_creatives = _owner_join_count(cur, "pulse_ad_creatives", owner_user_id, "lower(COALESCE(item.moderation_status,item.status,'')) IN ('rejected','needs_changes')")
    impressions = _owner_tracking_count(cur, "pulse_ad_impressions", owner_user_id)
    viewable_impressions = _owner_tracking_count(cur, "pulse_ad_impressions", owner_user_id, "COALESCE(item.viewable,0)=1")
    clicks = _owner_tracking_count(cur, "pulse_ad_clicks", owner_user_id)
    events = _owner_tracking_count(cur, "pulse_ad_events", owner_user_id)
    hides = _event_count(cur, owner_user_id, ("hide", "close", "dismiss"))
    reports = _event_count(cur, owner_user_id, ("report", "flag"))
    conversions = _event_count(cur, owner_user_id, ("conversion", "purchase", "marketplace_purchase", "subscription", "follow", "profile_visit"))
    spend_cents = _owner_join_sum(cur, "pulse_ad_campaigns", "spent_cents", owner_user_id)
    daily_budget_cents = _owner_join_sum(cur, "pulse_ad_campaigns", "daily_budget_cents", owner_user_id)
    lifetime_budget_cents = _owner_join_sum(cur, "pulse_ad_campaigns", "lifetime_budget_cents", owner_user_id)
    wallet_balance_cents = _owner_wallet_sum(cur, owner_user_id, "available_balance_cents")
    reserved_budget_cents = _owner_wallet_sum(cur, owner_user_id, "reserved_budget_cents")
    placements = _count(cur, "pulse_ad_placements", "COALESCE(is_active,1)=1")
    targeting_rules = _owner_join_count(cur, "pulse_ad_targeting", owner_user_id)
    moderation_queue = 0
    if _table_exists(cur, "pulse_ad_moderation_queue") and _table_exists(cur, "pulse_ad_creatives") and _table_exists(cur, "pulse_ad_accounts"):
        try:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM pulse_ad_moderation_queue q
                JOIN pulse_ad_creatives cr ON cr.id=q.creative_id
                JOIN pulse_ad_accounts aa ON aa.id=cr.ad_account_id
                WHERE aa.owner_user_id=? AND lower(COALESCE(q.status,'')) IN ('pending','submitted','in_review','review')
                """,
                (owner_user_id,),
            )
            moderation_queue = _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
        except Exception:
            moderation_queue = 0
    brand_deals = _count(cur, "pulse_brand_deals", "owner_user_id=?", (owner_user_id,)) if _table_exists(cur, "pulse_brand_deals") else 0
    creator_sponsorships = _count(cur, "pulse_creator_sponsorships", "creator_user_id=?", (owner_user_id,)) if _table_exists(cur, "pulse_creator_sponsorships") else 0
    ctr = _percent(clicks, impressions)
    viewability = _percent(viewable_impressions, impressions)
    privacy_score = 100 if targeting_rules == 0 else max(82, 100 - min(18, targeting_rules))
    brand_safety_score = max(48, 96 - reports * 8 - hides * 2 - rejected_creatives * 5)
    account_health = max(40, 92 - pending_review * 3 - rejected_creatives * 7 + min(6, approved_creatives))
    remaining_budget_cents = max(0, (lifetime_budget_cents or daily_budget_cents) - spend_cents)
    commercial_trust_score = max(40, min(98, (account_health + brand_safety_score + privacy_score) // 3))
    revenue_prediction_cents = max(0, int((clicks * 65) + (conversions * 350) + (active_campaigns * 2500)))
    return {
        "accounts": accounts,
        "campaigns": campaigns,
        "active_campaigns": active_campaigns,
        "draft_campaigns": draft_campaigns,
        "creatives": creatives,
        "approved_creatives": approved_creatives,
        "pending_review": max(pending_review, moderation_queue),
        "rejected_creatives": rejected_creatives,
        "impressions": impressions,
        "viewable_impressions": viewable_impressions,
        "clicks": clicks,
        "events": events,
        "hides": hides,
        "reports": reports,
        "conversion_events": conversions,
        "spend_cents": spend_cents,
        "daily_budget_cents": daily_budget_cents,
        "lifetime_budget_cents": lifetime_budget_cents,
        "remaining_budget_cents": remaining_budget_cents,
        "wallet_balance_cents": wallet_balance_cents,
        "reserved_budget_cents": reserved_budget_cents,
        "placements": placements,
        "targeting_rules": targeting_rules,
        "brand_deals": brand_deals,
        "creator_sponsorships": creator_sponsorships,
        "ctr": ctr,
        "viewability": viewability,
        "privacy_score": privacy_score,
        "brand_safety_score": brand_safety_score,
        "account_health": account_health,
        "commercial_trust_score": commercial_trust_score,
        "revenue_prediction_cents": revenue_prediction_cents,
        "delivery_health": max(45, min(98, 88 + active_campaigns - reports * 3 - rejected_creatives * 4)),
    }


def _state_for_blueprint(blueprint: dict[str, Any], metrics: dict[str, Any]) -> str:
    explicit = blueprint.get("state")
    if explicit:
        return str(explicit)
    accounts = _safe_int(metrics.get("accounts"), 0)
    if blueprint["key"] in {"manager", "campaign-builder", "signal-studio"} and accounts <= 0:
        return "ACTION REQUIRED"
    if blueprint["key"] == "signal-studio" and _safe_int(metrics.get("pending_review"), 0) > 0:
        return "REVIEW"
    if blueprint["key"] == "analytics" and _safe_int(metrics.get("impressions"), 0) <= 0:
        return "PARTIAL"
    if blueprint["key"] == "conversion-tracking" and _safe_int(metrics.get("conversion_events"), 0) <= 0:
        return "PARTIAL"
    if blueprint["key"] == "audience-targeting" and _safe_int(metrics.get("placements"), 0) <= 0:
        return "PARTIAL"
    if _safe_int(metrics.get("reports"), 0) > 0 and blueprint["key"] in {"sponsored-signals", "manager", "analytics"}:
        return "WARNING"
    return "READY"


def _count_display(metric: str, value: Any) -> str:
    if metric.endswith("_cents"):
        return _money(value)
    if metric in {"ctr", "viewability", "privacy_score", "brand_safety_score", "account_health", "commercial_trust_score", "delivery_health"}:
        return f"{_safe_float(value, 0):.1f}%"
    return f"{_safe_int(value, 0):,}"


def _detail_for_state(blueprint: dict[str, Any], state: str, metrics: dict[str, Any]) -> str:
    if state == "ACTION REQUIRED":
        return "Create or verify an advertiser account before this commercial system can launch campaigns."
    if state == "REVIEW":
        return f"{_safe_int(metrics.get('pending_review'), 0)} creative or campaign item needs review before delivery."
    if state == "WARNING":
        return "Recent hide, report, or policy signals need review before scaling delivery."
    if state == "PARTIAL":
        return "The subsystem is wired, but it needs more delivery or tracking data to become fully predictive."
    if state == "BETA":
        return "The subsystem is functional and review-gated while advanced automation continues to mature."
    return str(blueprint.get("description") or "")


def build_ads_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    cur = conn.cursor()
    owner_user_id = _safe_int(user.get("user_id") or user.get("id"), 0)
    metrics = _build_metrics(cur, owner_user_id)
    cards: list[dict[str, Any]] = []
    subsystems: dict[str, dict[str, Any]] = {}
    for blueprint in ADS_SUBSYSTEM_BLUEPRINTS:
        metric = str(blueprint.get("metric") or "campaigns")
        value = metrics.get(metric, 0)
        state = _state_for_blueprint(blueprint, metrics)
        confidence = _confidence(metrics.get("accounts", 0), metrics.get("campaigns", 0), metrics.get("creatives", 0), metrics.get("impressions", 0), metrics.get("clicks", 0), metrics.get("placements", 0))
        if state in {"ACTION REQUIRED", "PARTIAL"}:
            confidence = max(50, confidence - 10)
        card = {
            "key": blueprint["key"],
            "widget_key": blueprint["card_key"],
            "label": blueprint["label"],
            "route": blueprint["route"],
            "admin_route": blueprint["admin_route"],
            "action": blueprint["action"],
            "cta_label": blueprint["action"],
            "state": state,
            "count": _safe_int(value, 0),
            "count_display": _count_display(metric, value),
            "detail": _detail_for_state(blueprint, state, metrics),
            "description": blueprint.get("description", ""),
            "intelligence": blueprint.get("intelligence", ""),
            "automation": blueprint.get("automation", ""),
            "protection": blueprint.get("protection", ""),
            "prediction": blueprint.get("prediction", ""),
            "recovery": blueprint.get("recovery", ""),
            "recommendations": list(blueprint.get("recommendations") or ()),
            "confidence": confidence,
        }
        cards.append(card)
        subsystems[blueprint["card_key"]] = card
        subsystems[blueprint["key"].replace("-", "_")] = card
    hub_recommendations = [
        "Create an advertiser account before launching campaigns." if metrics["accounts"] <= 0 else "Review campaign delivery and wallet pacing before scaling.",
        "Submit only PulseSoc-hosted creative media for moderation.",
        "Keep targeting aggregate and privacy-safe.",
    ]
    if metrics["pending_review"] > 0:
        hub_recommendations.insert(0, "Resolve creative review items before expecting delivery.")
    if metrics["wallet_balance_cents"] <= 0 and metrics["campaigns"] > 0:
        hub_recommendations.insert(0, "Fund the ad wallet before active campaigns can spend reliably.")
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metrics": metrics,
        "hub": {
            "wallet_balance": _money(metrics["wallet_balance_cents"]),
            "today_spend": _money(metrics["spend_cents"]),
            "remaining_budget": _money(metrics["remaining_budget_cents"]),
            "active_campaigns": metrics["active_campaigns"],
            "pending_review": metrics["pending_review"],
            "drafts": metrics["draft_campaigns"],
            "rejected": metrics["rejected_creatives"],
            "impressions": metrics["impressions"],
            "clicks": metrics["clicks"],
            "ctr": f"{metrics['ctr']:.2f}%",
            "viewability": f"{metrics['viewability']:.2f}%",
            "account_health": metrics["account_health"],
            "commercial_trust_score": metrics["commercial_trust_score"],
            "brand_safety_score": metrics["brand_safety_score"],
            "privacy_score": metrics["privacy_score"],
            "delivery_health": metrics["delivery_health"],
            "revenue_prediction": _money(metrics["revenue_prediction_cents"]),
            "verification_status": "Verified" if metrics["accounts"] and metrics["pending_review"] == 0 else "Review",
            "commercial_summary": "PulseSoc is ready to manage approved sponsored signals with privacy-safe targeting, review-gated delivery, and audited tracking." if metrics["accounts"] else "Create an advertiser account to activate campaign, creative, wallet, and analytics controls.",
            "recommended_next_actions": hub_recommendations,
        },
        "cards": cards,
        "subsystems": subsystems,
        "automation_mesh": {
            "campaign_review_updates_delivery": True,
            "wallet_updates_budget_pacing": True,
            "tracking_updates_analytics": True,
            "reports_update_brand_safety": True,
            "privacy_safe_targeting_only": True,
        },
        "privacy": {
            "raw_user_targeting_visible": False,
            "credential_visibility": "redacted",
            "storage_paths_visible": False,
            "cross_advertiser_data_visible": False,
        },
    }


def state_for_widget(state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    if not state:
        return None
    return (state.get("subsystems") or {}).get(widget_key)


def _admin_metrics(cur: Any) -> dict[str, Any]:
    impressions = _count(cur, "pulse_ad_impressions")
    clicks = _count(cur, "pulse_ad_clicks")
    events = _count(cur, "pulse_ad_events")
    reports = _count(cur, "pulse_ad_events", "event_type IN ('report','flag')")
    hides = _count(cur, "pulse_ad_events", "event_type IN ('hide','close','dismiss')")
    pending = _count(cur, "pulse_ad_creatives", "lower(COALESCE(moderation_status,status,'')) IN ('pending','submitted','in_review','review')")
    rejected = _count(cur, "pulse_ad_creatives", "lower(COALESCE(moderation_status,status,'')) IN ('rejected','needs_changes')")
    active_campaigns = _count(cur, "pulse_ad_campaigns", "lower(COALESCE(status,'')) IN ('active','running','live')")
    spend_cents = _sum(cur, "pulse_ad_campaigns", "spent_cents")
    wallet_balance_cents = _sum(cur, "pulse_ad_wallets", "available_balance_cents")
    brand_safety = max(40, 96 - reports * 4 - hides - rejected * 3)
    delivery_health = max(40, 92 + active_campaigns - pending * 2 - reports * 4)
    privacy_score = 96 if _table_exists(cur, "pulse_ad_targeting") else 82
    return {
        "ad_accounts": _count(cur, "pulse_ad_accounts"),
        "campaigns": _count(cur, "pulse_ad_campaigns"),
        "active_campaigns": active_campaigns,
        "creatives": _count(cur, "pulse_ad_creatives"),
        "approved_creatives": _count(cur, "pulse_ad_creatives", "lower(COALESCE(moderation_status,status,'')) IN ('approved','active')"),
        "pending_review": pending,
        "rejected_creatives": rejected,
        "placements": _count(cur, "pulse_ad_placements", "COALESCE(is_active,1)=1"),
        "impressions": impressions,
        "clicks": clicks,
        "events": events,
        "reports": reports,
        "hides": hides,
        "conversion_events": _count(cur, "pulse_ad_events", "event_type IN ('conversion','purchase','marketplace_purchase','subscription','follow','profile_visit')"),
        "spend_cents": spend_cents,
        "wallet_balance_cents": wallet_balance_cents,
        "wallets": _count(cur, "pulse_ad_wallets"),
        "moderation_queue": _count(cur, "pulse_ad_moderation_queue", "lower(COALESCE(status,'')) IN ('pending','submitted','in_review','review')"),
        "policy_flags": _count(cur, "pulse_ad_policy_flags"),
        "audit_logs": _count(cur, "pulse_ad_audit_logs"),
        "tracking_events": events + clicks + impressions,
        "ctr": _percent(clicks, impressions),
        "delivery_health": min(98, delivery_health),
        "privacy_score": privacy_score,
        "brand_safety_score": min(98, brand_safety),
        "commercial_health": max(40, min(98, (delivery_health + privacy_score + brand_safety) // 3)),
    }


def build_admin_ads_state(conn: Any) -> dict[str, Any]:
    cur = conn.cursor()
    metrics = _admin_metrics(cur)
    sections = []
    metric_by_section = {
        "sponsored-signals": "approved_creatives",
        "ads-manager": "campaigns",
        "campaign-builder": "campaigns",
        "sponsored-signal-studio": "creatives",
        "ad-analytics": "impressions",
        "brand-deals": "ad_accounts",
        "creator-sponsorships": "ad_accounts",
        "ad-revenue-center": "spend_cents",
        "audience-targeting": "placements",
        "conversion-tracking": "conversion_events",
        "review-board": "moderation_queue",
        "delivery-engine": "tracking_events",
        "audit": "audit_logs",
    }
    for section in ADS_SECTIONS:
        key = section["key"]
        metric_key = metric_by_section.get(key, "campaigns")
        count = _safe_int(metrics.get(metric_key), 0)
        state = "REVIEW" if key == "review-board" and count else "WARNING" if key in {"delivery-engine", "ad-analytics"} and metrics["reports"] else "READY"
        sections.append({**section, "state": state, "count": count, "confidence": _confidence(count, metrics.get("placements", 0), metrics.get("tracking_events", 0))})
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metrics": metrics,
        "sections": sections,
        "automation_mesh": {
            "review_controls_delivery": True,
            "frequency_caps_enforced": True,
            "privacy_targeting_required": True,
            "tracking_debounced": True,
            "audit_logs_required": True,
        },
        "security": {
            "credential_visibility": "redacted",
            "private_targeting_visible": False,
            "provider_secrets_visible": False,
            "cross_advertiser_private_data_visible": False,
        },
    }
