"""Pulse feed ranking signals with safe chronological fallback."""

from __future__ import annotations

from datetime import datetime


def _age_hours(created_at: str = "") -> float:
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0.0, (datetime.utcnow() - created).total_seconds() / 3600)
    except Exception:
        return 999.0


def score_post(post=None, viewer_context=None) -> dict:
    post = post or {}
    viewer_context = viewer_context or {}
    author_id = int(post.get("user_id") or 0)
    engagement = float(post.get("engagement_score") or 0)
    trust = float(post.get("author_trust_score") or post.get("trust_score") or 0)
    safety = max(0.0, 100.0 - float(post.get("risk_score") or 0))
    freshness = max(0.0, 60.0 - _age_hours(post.get("created_at")) * 2.2)
    relevance = 0.0
    if author_id in set(viewer_context.get("followed_user_ids") or []):
        relevance += 24
    if author_id in set(viewer_context.get("friend_user_ids") or []):
        relevance += 18
    if set(post.get("tags") or []) & set(viewer_context.get("topics") or []):
        relevance += 12
    post_type = str(post.get("post_type") or "")
    if post_type in {"scam_report", "scam_warning"}:
        relevance += 10
    if post_type in {"lesson", "question", "teacher_post"}:
        relevance += 6
    trend = engagement + relevance + freshness + trust * 0.2 + safety * 0.08
    return {
        "quality_score": min(100, max(0, int(trust * 0.45 + engagement * 0.35 + safety * 0.2))),
        "safety_score": min(100, max(0, int(safety))),
        "relevance_score": min(100, max(0, int(relevance + freshness * 0.35))),
        "trend_score": min(100, max(0, int(trend))),
    }


def rank_posts(posts=None, viewer_context=None) -> list[dict]:
    ranked = []
    for post in posts or []:
        item = dict(post)
        item.update(score_post(item, viewer_context))
        ranked.append(item)
    return sorted(ranked, key=lambda row: (row.get("trend_score", 0), row.get("created_at") or ""), reverse=True)
