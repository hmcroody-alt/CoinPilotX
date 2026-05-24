"""Pulse Live stream health scoring and recovery hints."""

from __future__ import annotations

from datetime import datetime


def score_stream(session=None, viewer_count=0, chat_count=0):
    session = session or {}
    status = (session.get("status") or "starting").lower()
    bitrate = max(0, int(session.get("bitrate_kbps") or 0))
    fps = max(0, int(session.get("fps") or 0))
    if status in {"ended", "offline"}:
        score = 0
        level = "ended"
    else:
        score = 46
        score += 24 if bitrate >= 2500 else 14 if bitrate >= 1000 else 5 if bitrate else 0
        score += 18 if fps >= 30 else 10 if fps else 0
        score += min(12, max(0, int(viewer_count or 0)) * 2)
        score += min(8, max(0, int(chat_count or 0)))
        score = min(100, score)
        level = "excellent" if score >= 82 else "stable" if score >= 58 else "warming"
    return {
        "score": score,
        "level": level,
        "bitrate_kbps": bitrate,
        "fps": fps,
        "latency_ms": 1800 if bitrate else 0,
        "dropped_frames": 0 if fps else None,
        "cdn_health": "ready" if status not in {"ended", "offline"} else "archived",
        "websocket_health": "connected",
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def recovery_hints(health=None):
    health = health or {}
    hints = []
    if int(health.get("bitrate_kbps") or 0) == 0:
        hints.append("Start browser camera or connect OBS to begin broadcast output.")
    if int(health.get("fps") or 0) == 0:
        hints.append("Camera preview is ready; stream frames have not reached ingest yet.")
    if int(health.get("score") or 0) < 58:
        hints.append("Keep the studio open while Pulse stabilizes playback and chat sync.")
    return hints or ["Stream health looks stable."]
