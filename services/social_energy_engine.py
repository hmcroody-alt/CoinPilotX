"""Social energy metrics for emotion, hype, and community momentum."""

from __future__ import annotations


def energy_score(metrics=None) -> dict:
    metrics = metrics or {}
    reactions = float(metrics.get("reactions") or 0)
    comments = float(metrics.get("comments") or 0)
    live_viewers = float(metrics.get("live_viewers") or 0)
    shares = float(metrics.get("shares") or 0)
    reports = float(metrics.get("reports") or 0)
    score = max(0, min(100, int(reactions * 0.8 + comments * 1.4 + live_viewers * 0.5 + shares * 2 - reports * 4)))
    return {"energy_score": score, "state": "surging" if score >= 75 else "active" if score >= 35 else "quiet"}
