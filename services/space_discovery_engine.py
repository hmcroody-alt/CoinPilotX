"""Pulse Spaces discovery and health scoring."""

from __future__ import annotations


def score_space(space=None, member_count=0) -> dict:
    space = space or {}
    featured = 14 if space.get("featured") else 0
    trust = int(space.get("trust_score") or 80)
    energy = int(space.get("energy_score") or 55)
    members = min(20, int(member_count or space.get("member_count") or 0) // 5)
    score = max(0, min(100, featured + trust * 0.35 + energy * 0.45 + members))
    return {
        "score": round(score, 1),
        "activity_score": energy,
        "trust_score": trust,
        "growth_velocity": min(100, members * 5 + featured),
        "creator_density": int(space.get("creator_density") or 35),
        "educational_quality": int(space.get("educational_quality") or 80),
        "moderation_quality": int(space.get("moderation_quality") or 82),
        "scam_risk": max(0, 100 - trust),
    }


def recommend_spaces(spaces=None, user_context=None, limit=12) -> list[dict]:
    spaces = spaces or []
    user_context = user_context or {}
    interests = {str(x).lower() for x in user_context.get("interests", [])}
    ranked = []
    for space in spaces:
        tags = {str(x).lower() for x in space.get("tags", [])}
        base = score_space(space, space.get("member_count") or 0)["score"]
        affinity = 15 if interests & tags else 0
        ranked.append((base + affinity, space))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [space for _, space in ranked[:limit]]
