"""AI assisted administration local summaries."""

from __future__ import annotations


def summarize_incidents(metrics=None):
    metrics = metrics or {}
    issues = []
    if int(metrics.get("failed_events") or 0):
        issues.append("Realtime or worker failures need review.")
    if int(metrics.get("flagged_posts") or 0):
        issues.append("Trust & Safety has flagged PulseSoc content waiting.")
    if int(metrics.get("failed_messages") or 0):
        issues.append("PulseSoc Messenger delivery should be inspected.")
    return {"summary": " ".join(issues) if issues else "No urgent PulseSoc incidents detected.", "issues": issues}


def growth_opportunities(metrics=None):
    metrics = metrics or {}
    return ["Feature teachers with high trust scores.", "Clip strong livestream moments into Reels.", "Promote Scam Hunter missions."]
