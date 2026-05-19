"""Privacy-first data classification for CoinPilotXAI intelligence products."""

from __future__ import annotations

PRIVATE_NEVER_SELL = {
    "email",
    "phone",
    "billing_details",
    "private_chats",
    "precise_location",
    "internal_user_id",
    "stripe_data",
    "telegram_chat_id",
    "wallet_secrets",
    "seed_phrases",
    "private_keys",
    "private_media",
    "support_messages",
}

PRODUCT_IMPROVEMENT = {
    "feature_usage_counts",
    "anonymous_sessions",
    "aggregate_retention",
    "topic_trends",
    "reaction_counts",
    "scam_category_trends",
}

AGGREGATE_ONLY = {
    "trending_topics",
    "scam_trend_reports",
    "public_market_sentiment",
    "anonymized_engagement_categories",
    "public_creator_rankings",
    "regional_aggregate_trends",
}


def data_policy_summary():
    return {
        "private_never_sell": sorted(PRIVATE_NEVER_SELL),
        "product_improvement": sorted(PRODUCT_IMPROVEMENT),
        "monetizable_only_in_aggregate": sorted(AGGREGATE_ONLY),
        "principle": "Monetize intelligence, utility, trust, and creator value, not private identity.",
    }


def classify_field(field_name):
    normalized = (field_name or "").strip().lower()
    if normalized in PRIVATE_NEVER_SELL:
        return "private_never_sell"
    if normalized in PRODUCT_IMPROVEMENT:
        return "product_improvement"
    if normalized in AGGREGATE_ONLY:
        return "aggregate_only"
    return "review_required"


def aggregate_guard(payload):
    """Return a privacy-safe copy by dropping known private keys."""
    payload = dict(payload or {})
    for key in list(payload):
        if classify_field(key) == "private_never_sell":
            payload.pop(key, None)
    payload["privacy_mode"] = "aggregate_only"
    return payload
