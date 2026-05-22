"""Reputation economy scoring for visibility, trust, and privileges."""

from __future__ import annotations


def reputation_delta(event_type: str, weight: float = 1) -> int:
    values = {
        "helpful_answer": 8,
        "accurate_scam_report": 12,
        "teacher_lesson": 10,
        "verified_prediction": 9,
        "community_moderation": 6,
        "livestream_value": 5,
        "marketplace_reliable_delivery": 7,
        "moderation_strike": -16,
        "false_report": -8,
        "spam": -12,
    }
    return int(values.get(str(event_type or "").lower(), 0) * float(weight or 1))


def economy_state(profile=None, events=None) -> dict:
    profile = profile or {}
    score = int(profile.get("reputation_score") or profile.get("trust_score") or 0)
    for event in events or []:
        score += reputation_delta(event.get("event_type"), event.get("weight", 1))
    score = max(0, min(1000, score))
    return {
        "reputation_score": score,
        "tier": "governance_eligible" if score >= 800 else "elite" if score >= 600 else "trusted" if score >= 300 else "member",
        "visibility_multiplier": round(1 + min(0.8, score / 1400), 2),
    }
