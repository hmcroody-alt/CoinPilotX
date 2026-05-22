"""Autonomous safety signals for attack, abuse, and manipulation detection."""

from __future__ import annotations


def assess_actor(signals=None) -> dict:
    signals = signals or {}
    score = 0
    score += int(signals.get("failed_logins") or 0) * 4
    score += int(signals.get("duplicate_posts") or 0) * 3
    score += int(signals.get("reports") or 0) * 8
    score += int(signals.get("suspicious_referrals") or 0) * 7
    score += int(signals.get("unsafe_media") or 0) * 12
    score = max(0, min(100, score))
    action = "quarantine" if score >= 80 else "visibility_reduce" if score >= 55 else "monitor"
    return {"risk_score": score, "recommended_action": action}


def detect_attack_cluster(events=None) -> dict:
    events = events or []
    risky = [e for e in events if str(e.get("event_type") or "").lower() in {"xss", "rate_limit", "bot", "fake_engagement", "phishing"}]
    return {
        "detected": len(risky) >= 5,
        "risk_events": len(risky),
        "recommended_action": "alert_security" if len(risky) >= 5 else "monitor",
    }
