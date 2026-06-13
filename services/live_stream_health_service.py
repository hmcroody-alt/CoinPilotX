"""PulseSoc Live stream health scoring and recovery hints."""

from __future__ import annotations

from datetime import datetime


def score_stream(session=None, viewer_count=0, chat_count=0):
    session = session or {}
    status = (session.get("status") or "starting").lower()
    mux_status = (session.get("mux_live_status") or "").lower()
    stream_health = (session.get("stream_health") or "").lower()
    publish_state = (session.get("publish_state") or "").lower()
    mux_ingest_active = mux_status in {"active", "live"}
    livekit_direct = (
        mux_status in {"egress_quota_exhausted", "livekit_direct"}
        or stream_health in {"livekit_direct", "egress_quota_exhausted"}
        or publish_state in {"browser_live_livekit_direct", "livekit_direct"}
    )
    ingest_active = mux_ingest_active
    bitrate = max(0, int(session.get("bitrate_kbps") or 0))
    fps = max(0, int(session.get("fps") or 0))
    if status in {"ended", "offline"}:
        score = 0
        level = "ended"
    else:
        score = 46
        score += 24 if bitrate >= 2500 else 14 if bitrate >= 1000 else 5 if bitrate else 0
        score += 18 if fps >= 30 else 10 if fps else 0
        if ingest_active and not bitrate:
            score += 12
        if ingest_active and not fps:
            score += 10
        score += min(12, max(0, int(viewer_count or 0)) * 2)
        score += min(8, max(0, int(chat_count or 0)))
        score = min(100, score)
        level = "excellent" if score >= 82 else "stable" if score >= 58 else "warming"
    return {
        "score": score,
        "level": level,
        "bitrate_kbps": bitrate,
        "fps": fps,
        "bitrate_label": f"{bitrate} kbps" if bitrate else ("Mux active" if mux_ingest_active else "LiveKit direct - Mux unavailable" if livekit_direct else "0 kbps"),
        "fps_label": f"{fps} FPS" if fps else ("Mux active" if mux_ingest_active else "LiveKit direct - Mux unavailable" if livekit_direct else "0 FPS"),
        "ingest_active": ingest_active,
        "ingest_source": "mux" if mux_ingest_active else "livekit-direct-unpublished" if livekit_direct else "local",
        "latency_ms": 1800 if bitrate else 0,
        "dropped_frames": 0 if fps else None,
        "cdn_health": "ready" if status not in {"ended", "offline"} else "archived",
        "websocket_health": "connected",
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def recovery_hints(health=None):
    health = health or {}
    if health.get("ingest_active"):
        if health.get("ingest_source") == "livekit-direct":
            return ["LiveKit direct playback is active. Mux replay resumes after egress minutes are available."]
        return ["Mux ingest is active and public HLS playback is available."]
    hints = []
    if int(health.get("bitrate_kbps") or 0) == 0:
        hints.append("Start browser camera or connect OBS to begin broadcast output.")
    if int(health.get("fps") or 0) == 0:
        hints.append("Camera preview is ready; stream frames have not reached ingest yet.")
    if int(health.get("score") or 0) < 58:
        hints.append("Keep the studio open while PulseSoc stabilizes playback and chat sync.")
    return hints or ["Stream health looks stable."]
