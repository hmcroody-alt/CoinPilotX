"""Operational health scoring for CoinPilotXAI nervous-system dashboards.

This module is intentionally dependency-light. It turns raw counters from the
app/database into stable health states, warnings, and failsafe suggestions.
"""

from __future__ import annotations

from datetime import datetime


def score_component(value: float, warn_at: float, critical_at: float, inverse: bool = True) -> int:
    value = float(value or 0)
    if not inverse:
        if value >= critical_at:
            return 100
        if value <= warn_at:
            return 45
        return int(45 + ((value - warn_at) / max(1, critical_at - warn_at)) * 55)
    if value >= critical_at:
        return 15
    if value >= warn_at:
        return int(70 - ((value - warn_at) / max(1, critical_at - warn_at)) * 55)
    return 100


def health_state(score: int) -> str:
    score = int(score or 0)
    if score >= 82:
        return "healthy"
    if score >= 58:
        return "watch"
    if score >= 34:
        return "degraded"
    return "critical"


def snapshot(metrics=None) -> dict:
    metrics = metrics or {}
    components = {
        "realtime": score_component(metrics.get("realtime_failures", 0), 5, 30),
        "queue": score_component(metrics.get("queue_backlog", 0), 100, 1000),
        "workers": score_component(metrics.get("stale_workers", 0), 1, 4),
        "database": score_component(metrics.get("db_latency_ms", 0), 250, 1500),
        "ai": score_component(metrics.get("ai_failures", 0), 3, 20),
        "media": score_component(metrics.get("media_failures", 0), 5, 50),
        "livestream": score_component(metrics.get("livestream_errors", 0), 2, 15),
    }
    overall = int(sum(components.values()) / max(1, len(components)))
    warnings = []
    for name, score in components.items():
        if score < 58:
            warnings.append(f"{name.replace('_', ' ').title()} is {health_state(score)}")
    failsafe = {
        "enabled": overall < 58 or bool(warnings),
        "mode": "core_only" if overall < 34 else "degraded_ai" if overall < 58 else "normal",
        "recommendations": failsafe_recommendations(metrics, components),
    }
    return {
        "overall_score": overall,
        "state": health_state(overall),
        "components": components,
        "warnings": warnings,
        "failsafe": failsafe,
        "measured_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def failsafe_recommendations(metrics=None, components=None) -> list[str]:
    metrics = metrics or {}
    components = components or {}
    recommendations = []
    if components.get("queue", 100) < 58:
        recommendations.append("Pause non-critical AI jobs and drain queue backlog.")
    if components.get("realtime", 100) < 58:
        recommendations.append("Switch live widgets to polling fallback until realtime stabilizes.")
    if components.get("workers", 100) < 58:
        recommendations.append("Inspect stale workers and restart only the affected service.")
    if components.get("database", 100) < 58:
        recommendations.append("Reduce expensive graph refresh frequency and use cached snapshots.")
    if components.get("ai", 100) < 58:
        recommendations.append("Use local summaries while AI providers recover.")
    if int(metrics.get("security_events", 0) or 0) > 25:
        recommendations.append("Open Security Monitor and review attack clusters.")
    return recommendations or ["System is operating normally."]


def mobile_alerts(health=None) -> list[dict]:
    health = health or {}
    alerts = []
    for warning in health.get("warnings", []):
        alerts.append({"severity": "warning", "message": warning})
    if health.get("failsafe", {}).get("enabled"):
        alerts.append({"severity": "critical" if health.get("overall_score", 100) < 34 else "warning", "message": f"Failsafe mode: {health.get('failsafe', {}).get('mode')}"})
    return alerts
