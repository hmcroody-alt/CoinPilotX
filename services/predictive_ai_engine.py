"""Predictive AI scoring primitives for Phase 3."""

from __future__ import annotations

from datetime import datetime
from math import exp


def _sigmoid(value: float) -> float:
    return 1 / (1 + exp(-value))


def _age_hours(created_at="") -> float:
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0.0, (datetime.utcnow() - created).total_seconds() / 3600)
    except Exception:
        return 999.0


def forecast_post_trending(post=None) -> dict:
    post = post or {}
    engagement = float(post.get("reactions_count") or 0) + float(post.get("comments_count") or 0) * 1.7 + float(post.get("shares_count") or 0) * 2.4
    trust = float(post.get("trust_score") or post.get("author_trust_score") or 45)
    safety = float(post.get("safety_score") or 80)
    freshness = max(0, 36 - _age_hours(post.get("created_at"))) / 36
    raw = engagement * 0.08 + trust * 0.018 + safety * 0.012 + freshness * 2.1 - 3.2
    probability = int(_sigmoid(raw) * 100)
    return {"probability": max(1, min(99, probability)), "drivers": ["engagement", "trust", "freshness", "safety"]}


def forecast_creator_growth(stats=None) -> dict:
    stats = stats or {}
    follower_growth = float(stats.get("follower_growth_7d") or 0)
    posting_streak = float(stats.get("posting_streak") or 0)
    trust = float(stats.get("trust_score") or 50)
    quality = float(stats.get("content_quality_score") or 50)
    probability = int(_sigmoid(follower_growth * 0.08 + posting_streak * 0.12 + trust * 0.015 + quality * 0.02 - 3.1) * 100)
    return {"growth_probability": max(1, min(99, probability)), "growth_stage": "accelerating" if probability >= 70 else "building" if probability >= 40 else "early"}


def forecast_scam_risk(signal=None) -> dict:
    signal = signal or {}
    reports = float(signal.get("reports") or 0)
    link_entropy = float(signal.get("link_entropy") or 0)
    duplicate_posts = float(signal.get("duplicate_posts") or 0)
    trusted_reports = float(signal.get("trusted_reports") or 0)
    raw = reports * 0.45 + trusted_reports * 0.9 + duplicate_posts * 0.25 + link_entropy * 0.35 - 2.8
    risk = int(_sigmoid(raw) * 100)
    return {"risk_score": max(0, min(100, risk)), "recommended_action": "quarantine_review" if risk >= 80 else "reduce_visibility" if risk >= 55 else "monitor"}


def forecast_community_health(stats=None) -> dict:
    stats = stats or {}
    helpful = float(stats.get("helpful_posts") or 0)
    reports = float(stats.get("reports") or 0)
    churn = float(stats.get("churn_risk") or 0)
    active = float(stats.get("active_members") or 0)
    score = max(0, min(100, int(55 + helpful * 1.2 + active * 0.03 - reports * 3 - churn * 0.8)))
    return {"health_score": score, "status": "healthy" if score >= 75 else "watch" if score >= 50 else "at_risk"}
