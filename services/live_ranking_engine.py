"""Pulse Live discovery ranking for homepage LIVE NOW surfaces."""

from __future__ import annotations

import math
from datetime import datetime


def _age_minutes(started_at: str = "") -> float:
    if not started_at:
        return 0.0
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00").replace("+00:00", ""))
        return max(0.0, (datetime.utcnow() - started).total_seconds() / 60)
    except Exception:
        return 0.0


def score_stream(stream: dict) -> dict:
    viewers = int(stream.get("viewer_count") or 0)
    peak = max(viewers, int(stream.get("peak_viewers") or 0))
    reactions = int(stream.get("reaction_count") or stream.get("reactions") or 0)
    comments = int(stream.get("chat_count") or stream.get("comment_count") or 0)
    trust = int(stream.get("creator_trust") or stream.get("trust_score") or 72)
    age = _age_minutes(stream.get("started_at") or stream.get("created_at") or "")
    velocity = (reactions * 1.8 + comments * 2.4 + viewers * 3.2) / max(1.0, math.sqrt(age + 1))
    retention = min(100, 42 + peak * 3 + comments)
    score = round(min(100, velocity + trust * 0.32 + retention * 0.18), 2)
    return {
        "score": score,
        "momentum": "surging" if score >= 78 else "rising" if score >= 52 else "warming",
        "viewer_count": viewers,
        "engagement_velocity": round(velocity, 2),
        "retention_estimate": round(retention, 2),
        "ai_rating": "A" if score >= 80 else "B" if score >= 60 else "C",
    }


def ranked_live_cards(streams: list[dict], limit: int = 8) -> list[dict]:
    cards = []
    for stream in streams:
        card = dict(stream)
        card["ranking"] = score_stream(stream)
        card["ai_rating"] = card["ranking"]["ai_rating"]
        card["momentum"] = card["ranking"]["momentum"]
        card["engagement_pulse"] = card["ranking"]["engagement_velocity"]
        cards.append(card)
    cards.sort(key=lambda item: item["ranking"]["score"], reverse=True)
    return cards[: max(1, min(int(limit or 8), 24))]


def empty_live_card() -> dict:
    return {
        "id": 0,
        "title": "Pulse Live is ready",
        "creator_name": "CoinPilotXAI",
        "category": "Live discovery",
        "viewer_count": 0,
        "ai_rating": "Ready",
        "momentum": "waiting",
        "engagement_pulse": 0,
        "status": "idle",
        "message": "When a creator goes live, the stream appears here and in the main feed.",
    }
