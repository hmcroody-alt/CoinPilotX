"""Lightweight stability scoring for admin audit dashboards."""

from __future__ import annotations


def stability_snapshot(metrics=None) -> dict:
    metrics = metrics or {}
    route_failures = int(metrics.get("route_failures") or 0)
    api_failures = int(metrics.get("api_failures") or 0)
    worker_warnings = int(metrics.get("worker_warnings") or 0)
    raw_errors = int(metrics.get("raw_errors") or 0)
    score = max(0, 100 - route_failures * 18 - api_failures * 12 - worker_warnings * 6 - raw_errors * 10)
    return {
        "score": score,
        "state": "critical" if score < 55 else "degraded" if score < 78 else "healthy",
        "recommendations": [
            "Prioritize routes returning 500.",
            "Use trace IDs for all failed APIs.",
            "Keep optional systems from blocking core Pulse.",
        ] if score < 90 else ["Platform stability looks healthy from the sampled audit."],
    }
