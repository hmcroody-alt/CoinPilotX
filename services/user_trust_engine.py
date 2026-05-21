"""Privacy-safe user trust scoring helpers for CoinPilotXAI."""

from __future__ import annotations

from datetime import datetime


def _parse_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def calculate_trust_score(profile=None, metrics=None):
    profile = profile or {}
    metrics = metrics or {}
    score = 10

    created_at = _parse_iso(profile.get("created_at"))
    if created_at:
        account_days = max(0, (datetime.utcnow() - created_at).days)
        score += min(20, account_days // 7)

    if profile.get("display_name") or profile.get("username"):
        score += 8
    if profile.get("avatar_url"):
        score += 8
    if profile.get("bio"):
        score += 6
    if profile.get("email_verified"):
        score += 10
    if profile.get("phone_verified"):
        score += 8

    score += min(20, int(metrics.get("successful_referrals") or 0) * 2)
    score += min(15, int(metrics.get("helpful_posts") or 0))
    score += min(10, int(metrics.get("positive_reactions") or 0) // 5)

    if metrics.get("teacher_verified"):
        score += 8
    if metrics.get("seller_verified"):
        score += 8
    if metrics.get("creator_verified"):
        score += 8

    score -= min(35, int(metrics.get("moderation_strikes") or 0) * 12)
    score -= min(25, int(metrics.get("reports_received") or 0) * 3)
    score -= 20 if metrics.get("referral_fraud_flags") else 0

    return max(0, min(100, int(score)))


def trust_band(score):
    score = int(score or 0)
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Strong"
    if score >= 50:
        return "Growing"
    if score >= 30:
        return "New"
    return "Needs trust signals"
