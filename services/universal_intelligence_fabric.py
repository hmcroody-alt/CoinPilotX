"""Universal intelligence fabric for CoinPilotXAI.

This is the shared reasoning layer that correlates signals from trust,
moderation, creator, economy, discovery, realtime, and infrastructure systems.
It does not take autonomous destructive actions; it produces explainable,
auditable recommendations for admin and AI orchestration layers.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime


INTELLIGENCE_DOMAINS = (
    "creator_ai",
    "moderation_ai",
    "economy_ai",
    "discovery_ai",
    "livestream_ai",
    "recommendation_ai",
    "trust_ai",
    "infrastructure_ai",
    "governance_ai",
)


def normalize_signal(system: str, signal_type: str, value=0, confidence: float = 0.5, metadata=None) -> dict:
    return {
        "system": str(system or "unknown")[:80],
        "signal_type": str(signal_type or "signal")[:100],
        "value": value,
        "confidence": max(0.0, min(1.0, float(confidence or 0))),
        "metadata": metadata or {},
        "observed_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def correlate_signals(signals=None) -> dict:
    signals = signals or []
    by_system = Counter(signal.get("system") for signal in signals)
    by_type = Counter(signal.get("signal_type") for signal in signals)
    confidence = sum(float(s.get("confidence") or 0) for s in signals) / max(1, len(signals))
    anomalies = [
        s for s in signals
        if str(s.get("signal_type") or "").lower() in {"risk", "scam", "failure", "overload", "toxicity", "stale_worker"}
        or (isinstance(s.get("value"), (int, float)) and float(s.get("value") or 0) >= 75 and "risk" in str(s.get("signal_type") or "").lower())
    ]
    return {
        "signal_count": len(signals),
        "systems": by_system.most_common(),
        "signal_types": by_type.most_common(10),
        "average_confidence": round(confidence, 3),
        "anomalies": anomalies[:20],
        "correlation_strength": min(100, int(len(signals) * confidence * 9 + len(anomalies) * 6)),
    }


def reason(snapshot=None) -> dict:
    snapshot = snapshot or {}
    summary = snapshot.get("summary") or {}
    health = summary.get("health") or {}
    system_health = snapshot.get("system_health") or {}
    predictions = snapshot.get("predictions") or {}
    event_bus = snapshot.get("event_bus") or {}
    health_metrics = snapshot.get("health_metrics") or {}

    signals = [
        normalize_signal("trust_ai", "trust_heat", health.get("trust_score", 0), 0.82),
        normalize_signal("moderation_ai", "scam_cluster_count", len(summary.get("scam_clusters") or []), 0.78),
        normalize_signal("infrastructure_ai", "system_health", system_health.get("overall_score", 0), 0.9),
        normalize_signal("infrastructure_ai", "queue_backlog", health_metrics.get("queue_backlog", 0), 0.86),
        normalize_signal("realtime_ai", "event_throughput", event_bus.get("events_per_minute", 0), 0.72),
        normalize_signal("community_ai", "community_health", (predictions.get("community") or {}).get("health_score", 0), 0.74),
        normalize_signal("social_energy_ai", "social_energy", (predictions.get("energy") or {}).get("energy_score", 0), 0.68),
        normalize_signal("resilience_ai", "resilience", (predictions.get("resilience") or {}).get("resilience_score", 0), 0.82),
    ]
    correlation = correlate_signals(signals)
    recommendation = "Keep monitoring. The intelligence fabric sees stable cross-system alignment."
    priority = "normal"
    if system_health.get("state") in {"critical", "degraded"}:
        recommendation = "Activate degraded-mode discipline: protect core posting, messaging, and alerts while reducing expensive AI refreshes."
        priority = "critical" if system_health.get("state") == "critical" else "high"
    elif summary.get("scam_clusters"):
        recommendation = "Review scam clusters and temporarily prioritize trusted voices in affected areas."
        priority = "high"
    elif (predictions.get("community") or {}).get("status") == "at_risk":
        recommendation = "Open Trust & Safety and inject educational context before social instability grows."
        priority = "high"
    elif int(event_bus.get("dead_letters") or 0) > 0:
        recommendation = "Inspect event bus dead letters and replay safe events."
        priority = "watch"
    return {
        "domains": list(INTELLIGENCE_DOMAINS),
        "signals": signals,
        "correlation": correlation,
        "priority": priority,
        "recommendation": recommendation,
        "confidence": correlation["average_confidence"],
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def explain_decision(reasoning=None) -> dict:
    reasoning = reasoning or {}
    factors = []
    for signal in reasoning.get("signals", []):
        factors.append({
            "system": signal.get("system"),
            "factor": signal.get("signal_type"),
            "value": signal.get("value"),
            "confidence": signal.get("confidence"),
        })
    return {
        "recommendation": reasoning.get("recommendation") or "No recommendation generated.",
        "priority": reasoning.get("priority") or "normal",
        "confidence": reasoning.get("confidence") or 0,
        "contributing_factors": factors,
    }
