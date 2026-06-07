"""Reliability scoring for production trust surfaces."""

from __future__ import annotations

from datetime import UTC, datetime


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, float(value)))


def _score_from_failures(failures: int, penalty: int = 12) -> float:
    return _clamp(100 - max(0, int(failures or 0)) * penalty)


def snapshot(metrics: dict | None = None) -> dict:
    metrics = metrics or {}
    api_score = _score_from_failures(metrics.get("api_failures", 0), 10)
    websocket_score = _score_from_failures(metrics.get("websocket_failures", 0), 12)
    livestream_score = _score_from_failures(metrics.get("livestream_failures", 0), 18)
    payment_score = _score_from_failures(metrics.get("payment_failures", 0), 18)
    marketplace_score = _score_from_failures(metrics.get("marketplace_failures", 0), 12)
    ai_score = _score_from_failures(metrics.get("ai_failures", 0), 10)
    queue_score = _score_from_failures(metrics.get("queue_failures", 0), 8)
    latency_ms = float(metrics.get("realtime_latency_ms") or 0)
    latency_score = 100 if latency_ms <= 250 else _clamp(100 - ((latency_ms - 250) / 25))

    weighted = (
        api_score * 0.18
        + websocket_score * 0.13
        + livestream_score * 0.13
        + payment_score * 0.10
        + marketplace_score * 0.13
        + ai_score * 0.12
        + queue_score * 0.11
        + latency_score * 0.10
    )
    overall = round(_clamp(weighted), 1)
    if overall >= 92:
        state = "healthy"
    elif overall >= 78:
        state = "watch"
    elif overall >= 60:
        state = "degraded"
    else:
        state = "critical"

    recommendations = []
    if livestream_score < 85:
        recommendations.append("Keep PulseSoc Live labeled beta until stream start failures stay low.")
    if payment_score < 90:
        recommendations.append("Keep checkout and payouts hidden until payment state transitions are verified.")
    if marketplace_score < 85:
        recommendations.append("Review merchant/listing moderation before increasing marketplace exposure.")
    if websocket_score < 85:
        recommendations.append("Use polling fallback for realtime surfaces under websocket pressure.")
    if ai_score < 85:
        recommendations.append("Use guarded AI fallback responses and avoid premium AI promises until stable.")
    if not recommendations:
        recommendations.append("Core reliability is within the production trust range.")

    normalized_metrics = {
        "api_success_rate": round(api_score, 1),
        "websocket_stability": round(websocket_score, 1),
        "livestream_reliability": round(livestream_score, 1),
        "payment_success": round(payment_score, 1),
        "marketplace_health": round(marketplace_score, 1),
        "ai_response_quality": round(ai_score, 1),
        "queue_health": round(queue_score, 1),
        "realtime_latency_score": round(latency_score, 1),
        "realtime_latency_ms": latency_ms,
    }
    return {
        "overall_score": overall,
        "state": state,
        "metrics": normalized_metrics,
        "recommendations": recommendations,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

