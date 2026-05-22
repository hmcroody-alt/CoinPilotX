"""Creator and livestream analytics intelligence foundations."""

from __future__ import annotations


def creator_recommendations(metrics=None):
    metrics = metrics or {}
    tips = []
    if int(metrics.get("comments") or 0) < int(metrics.get("posts") or 0):
        tips.append("Ask one direct question at the end of each post.")
    if float(metrics.get("retention") or 0) < 0.35:
        tips.append("Move the strongest hook into the first two seconds.")
    if not tips:
        tips.append("Double down on your strongest topic and post a follow-up.")
    return tips


def livestream_summary(metrics=None):
    metrics = metrics or {}
    return {
        "viewer_retention": metrics.get("viewer_retention") or 0,
        "dropoff_moment": metrics.get("dropoff_moment") or "",
        "engagement_spikes": metrics.get("engagement_spikes") or [],
        "recommendations": creator_recommendations(metrics),
    }
