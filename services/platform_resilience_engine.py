"""Advanced security and resilience foundation."""

from __future__ import annotations


def resilience_score(signals=None) -> dict:
    signals = signals or {}
    degraded = int(signals.get("degraded_services") or 0)
    attacks = int(signals.get("attack_signals") or 0)
    queue = int(signals.get("queue_backlog") or 0)
    score = max(0, min(100, 100 - degraded * 12 - attacks * 8 - min(30, queue // 100)))
    return {"resilience_score": score, "status": "healthy" if score >= 80 else "degraded" if score >= 50 else "critical"}
