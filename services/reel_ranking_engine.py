"""Trust-balanced ranking for PulseSoc Reels.

This intentionally avoids a pure outrage/retention loop. Reels are scored for
watch quality, safety, creator trust, and educational value.
"""

from __future__ import annotations

from datetime import datetime


def _age_hours(created_at=""):
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0.0, (datetime.utcnow() - created).total_seconds() / 3600)
    except Exception:
        return 999.0


def score_reel(reel=None):
    reel = reel or {}
    watch_duration = float(reel.get("watch_duration_ms") or 0) / 1000
    completion = float(reel.get("completion_rate") or 0)
    replay_count = float(reel.get("replay_count") or 0)
    share_rate = float(reel.get("share_rate") or 0)
    trust_score = float(reel.get("trust_score") or reel.get("author_trust_score") or 0)
    creator_score = float(reel.get("creator_score") or 0)
    safety_score = float(reel.get("safety_score") or max(0, 100 - float(reel.get("risk_score") or 0)))
    educational_value = float(reel.get("educational_value") or 0)
    engagement_velocity = float(reel.get("engagement_velocity") or reel.get("engagement_score") or 0)
    premium_boost = 5 if reel.get("premium_mark") else 0
    freshness = max(0, 40 - _age_hours(reel.get("created_at")) * 1.8)
    retention = min(30, watch_duration * 0.8) + min(24, completion * 24) + min(10, replay_count * 2)
    quality = min(30, educational_value * 0.3 + trust_score * 0.16 + creator_score * 0.14)
    distribution = retention + quality + min(18, share_rate * 18) + min(20, engagement_velocity) + freshness + premium_boost
    penalty = 30 if safety_score < 60 else 0
    return {
        "reel_score": max(0, min(100, int(distribution - penalty))),
        "safety_score": max(0, min(100, int(safety_score))),
        "educational_score": max(0, min(100, int(educational_value or trust_score * 0.35))),
        "retention_score": max(0, min(100, int(retention * 2))),
    }


def rank_reels(reels=None):
    ranked = []
    for reel in reels or []:
        item = dict(reel)
        item.update(score_reel(item))
        ranked.append(item)
    return sorted(ranked, key=lambda item: (item.get("reel_score", 0), item.get("created_at") or ""), reverse=True)
