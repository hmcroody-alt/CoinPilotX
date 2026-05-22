"""Meta-intelligence coordination layer.

Coordinates multiple AI systems, arbitrates confidence, and produces safe
admin-facing recommendations. It deliberately avoids automatic irreversible
actions; owner approval remains required for dangerous operations.
"""

from __future__ import annotations

from datetime import datetime


def arbitrate(recommendations=None) -> dict:
    recommendations = recommendations or []
    if not recommendations:
        return {
            "decision": "monitor",
            "confidence": 0.5,
            "reason": "No subsystem recommendations were available.",
            "requires_owner_approval": False,
        }
    ranked = sorted(recommendations, key=lambda item: (priority_weight(item.get("priority")), float(item.get("confidence") or 0)), reverse=True)
    winner = ranked[0]
    dangerous = any(word in str(winner.get("decision") or winner.get("recommendation") or "").lower() for word in ("ban", "delete", "pricing", "spend", "mass marketing"))
    return {
        "decision": winner.get("decision") or winner.get("recommendation") or "monitor",
        "confidence": round(float(winner.get("confidence") or 0.5), 3),
        "reason": winner.get("reason") or "Highest-confidence subsystem recommendation.",
        "priority": winner.get("priority") or "normal",
        "requires_owner_approval": dangerous,
        "alternatives": ranked[1:5],
        "decided_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def priority_weight(priority: str) -> int:
    return {"critical": 4, "high": 3, "watch": 2, "normal": 1, "low": 0}.get(str(priority or "normal").lower(), 1)


def coordinate(fabric_reasoning=None, system_health=None, event_bus=None) -> dict:
    fabric_reasoning = fabric_reasoning or {}
    system_health = system_health or {}
    event_bus = event_bus or {}
    recommendations = [
        {
            "system": "universal_intelligence_fabric",
            "recommendation": fabric_reasoning.get("recommendation"),
            "confidence": fabric_reasoning.get("confidence") or 0.5,
            "priority": fabric_reasoning.get("priority") or "normal",
            "reason": "Cross-system correlation result.",
        }
    ]
    if system_health.get("failsafe", {}).get("enabled"):
        recommendations.append({
            "system": "system_health_engine",
            "recommendation": "Use failsafe mode until health recovers.",
            "confidence": 0.9,
            "priority": "critical" if system_health.get("state") == "critical" else "high",
            "reason": "Health engine detected degraded platform state.",
        })
    if int(event_bus.get("dead_letters") or 0) > 0:
        recommendations.append({
            "system": "event_bus_engine",
            "recommendation": "Review dead-letter events before expanding realtime traffic.",
            "confidence": 0.78,
            "priority": "watch",
            "reason": "Failed realtime events require operator inspection.",
        })
    decision = arbitrate(recommendations)
    return {
        "decision": decision,
        "recommendations": recommendations,
        "coordination_score": max(0, min(100, int((fabric_reasoning.get("confidence") or 0.5) * 70 + len(recommendations) * 6))),
        "systems_coordinated": [item.get("system") for item in recommendations],
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def command_questions(coordination=None) -> list[dict]:
    coordination = coordination or {}
    decision = coordination.get("decision") or {}
    return [
        {"question": "What should I fix first?", "answer": decision.get("decision") or "Monitor the platform."},
        {"question": "What is risky today?", "answer": decision.get("reason") or "No major risk detected."},
        {"question": "Does this require owner approval?", "answer": "Yes" if decision.get("requires_owner_approval") else "No"},
    ]
