"""Public playback/distribution helpers for Pulse Live."""

from __future__ import annotations

import os

from . import mux_live_service


def playback_manifest(session=None):
    session = session or {}
    mux_playback_id = session.get("mux_playback_id") or ""
    mux_url = mux_live_service.playback_url(mux_playback_id)
    hls_url = mux_url or session.get("playback_url") or session.get("hls_url") or ""
    stream_uuid = session.get("stream_uuid") or ""
    if not hls_url and stream_uuid:
        base = os.getenv("PULSE_HLS_PLAYBACK_URL", "https://live.coinpilotxai.app/hls").rstrip("/")
        hls_url = f"{base}/{stream_uuid}.m3u8"
    return {
        "ok": True,
        "live_id": int(session.get("id") or session.get("live_id") or 0),
        "status": session.get("status") or "starting",
        "hls_url": hls_url,
        "playback_url": hls_url,
        "mux_playback_id": mux_playback_id,
        "mux_live_status": session.get("mux_live_status") or "",
        "webrtc_room_id": session.get("webrtc_room_id") or "",
        "rtmp_url": session.get("rtmp_url") or "",
        "poster_url": session.get("thumbnail_url") or "",
        "supports_hls": bool(hls_url),
        "supports_webrtc": bool(session.get("webrtc_room_id")),
        "latency_mode": "low-latency",
        "fallback_mode": "ambient-ready-state",
        "state_machine": session.get("publish_state") or session.get("status") or "idle",
    }


def discovery_card(session=None, creator_name="Pulse Creator"):
    session = session or {}
    return {
        "id": int(session.get("id") or 0),
        "title": session.get("title") or "Pulse Live",
        "creator_name": creator_name or "Pulse Creator",
        "status": session.get("status") or "starting",
        "viewer_count": int(session.get("viewer_count") or 0),
        "category": session.get("category") or "Community",
        "live_url": f"/pulse/live/{int(session.get('id') or 0)}",
        "studio_url": session.get("studio_url") or "",
        "playback": playback_manifest(session),
    }
