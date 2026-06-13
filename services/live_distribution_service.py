"""Public playback/distribution helpers for PulseSoc Live."""

from __future__ import annotations

import os

from . import mux_live_service


def playback_manifest(session=None):
    session = session or {}
    mux_playback_id = session.get("mux_playback_id") or ""
    mux_url = mux_live_service.playback_url(mux_playback_id)
    mux_status = (session.get("mux_live_status") or "").lower()
    publish_state = (session.get("publish_state") or session.get("status") or "idle").lower()
    mux_public_live = mux_status in {"active", "live"}
    direct_mode = (
        publish_state in {"browser_live_livekit_direct", "livekit_direct"}
        or mux_status in {"egress_quota_exhausted", "livekit_direct"}
        or (session.get("stream_health") or "").lower() in {"livekit_direct", "egress_quota_exhausted"}
    )
    explicit_hls = mux_url or session.get("playback_url") or session.get("hls_url") or ""
    hls_url = explicit_hls if mux_public_live else ""
    stream_uuid = session.get("stream_uuid") or ""
    if not hls_url and stream_uuid and mux_public_live:
        base = os.getenv("PULSE_HLS_PLAYBACK_URL", "https://live.coinpilotxai.app/hls").rstrip("/")
        hls_url = f"{base}/{stream_uuid}.m3u8"
    supports_webrtc = bool(session.get("webrtc_room_id"))
    preferred_transport = "hls" if hls_url else "webrtc" if supports_webrtc else "waiting"
    return {
        "ok": True,
        "live_id": int(session.get("id") or session.get("live_id") or 0),
        "status": session.get("status") or "starting",
        "hls_url": hls_url,
        "playback_url": hls_url,
        "mux_playback_id": mux_playback_id,
        "mux_live_status": session.get("mux_live_status") or "",
        "webrtc_room_id": session.get("webrtc_room_id") or "",
        "rtmp_url": "",
        "poster_url": session.get("thumbnail_url") or "",
        "supports_hls": bool(hls_url),
        "supports_webrtc": supports_webrtc,
        "preferred_transport": preferred_transport,
        "direct_mode": direct_mode,
        "mux_public_live": mux_public_live,
        "latency_mode": "low-latency",
        "fallback_mode": "ambient-ready-state",
        "state_machine": session.get("publish_state") or session.get("status") or "idle",
    }


def discovery_card(session=None, creator_name="PulseSoc Creator"):
    session = session or {}
    return {
        "id": int(session.get("id") or 0),
        "title": session.get("title") or "PulseSoc Live",
        "creator_name": creator_name or "PulseSoc Creator",
        "status": session.get("status") or "starting",
        "viewer_count": int(session.get("viewer_count") or 0),
        "category": session.get("category") or "Community",
        "live_url": f"/pulse/live/{int(session.get('id') or 0)}",
        "studio_url": session.get("studio_url") or "",
        "playback": playback_manifest(session),
    }
