"""Global social energy visualization data."""

from __future__ import annotations


def global_energy(metrics=None) -> dict:
    metrics = metrics or {}
    activity = int(metrics.get("activity") or 0)
    emotion = int(metrics.get("positive_emotion") or 50) - int(metrics.get("panic") or 0)
    live = int(metrics.get("live_viewers") or 0)
    score = max(0, min(100, activity * 0.2 + emotion * 0.4 + live * 0.05))
    return {"energy_score": int(score), "visual_state": "storm" if score >= 80 else "wave" if score >= 45 else "calm"}
