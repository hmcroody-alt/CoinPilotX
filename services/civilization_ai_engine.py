"""Phase 4 autonomous AI civilization layer."""

from __future__ import annotations


AGENTS = ["creator_advisor", "safety_ai", "education_ai", "moderation_ai", "economy_ai", "infrastructure_ai", "livestream_ai", "community_ai", "trend_ai"]


def ecosystem_recommendations(signals=None) -> list[str]:
    signals = signals or {}
    recommendations = []
    if int(signals.get("safety_risk") or 0) >= 70:
        recommendations.append("Escalate safety AI and reduce risky distribution.")
    if int(signals.get("creator_burnout") or 0) >= 60:
        recommendations.append("Offer creator recovery guidance and lighter posting goals.")
    if int(signals.get("education_demand") or 0) >= 60:
        recommendations.append("Boost teacher content and generate study maps.")
    return recommendations or ["Ecosystem stable. Continue monitoring."]
