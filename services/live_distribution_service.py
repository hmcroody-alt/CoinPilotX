"""Public playback/distribution helpers for PulseSoc Live."""

from __future__ import annotations

import os

from . import mux_live_service, live_archive_service


def playback_manifest(session=None):
    session = session or {}
    status = str(session.get("status") or "starting").lower()
    finished = status in {"ended", "offline", "archived", "finished", "complete"}
    # Live playback and replay playback are deliberately separate provider
    # identities.  Once a session ends, the durable VOD is stored in the
    # recording fields; continuing to read only mux_playback_id/playback_url
    # makes the state API advertise an empty or expired live input even though
    # finalization successfully produced a replay.
    mux_playback_id = (
        session.get("mux_recording_playback_id") if finished else session.get("mux_playback_id")
    ) or ""
    if finished and not live_archive_service.replay_ready(session):
        mux_playback_id = ""
    mux_url = mux_live_service.playback_url(mux_playback_id)
    mux_status = (session.get("mux_live_status") or "").lower()
    publish_state = (session.get("publish_state") or session.get("status") or "idle").lower()
    mux_public_live = mux_status in {"active", "live"}
    direct_mode = (
        publish_state in {"browser_live_livekit_direct", "livekit_direct"}
        or mux_status in {"egress_quota_exhausted", "livekit_direct"}
        or (session.get("stream_health") or "").lower() in {"livekit_direct", "egress_quota_exhausted"}
    )
    replay_url = mux_live_service.refresh_signed_replay_url(session.get("replay_url") or "") if finished and live_archive_service.replay_ready(session) else ""
    if finished and "token=" in (session.get("replay_url") or ""):
        mux_url = ""
        if not replay_url:
            mux_playback_id = ""
    explicit_hls = (replay_url or mux_url) if finished else (mux_url or session.get("playback_url") or session.get("hls_url") or "")
    direct_hls_ready = bool(explicit_hls) and (
        direct_mode
        or publish_state in {"live", "active", "started"}
        or not mux_status
    )
    hls_url = explicit_hls if finished or mux_public_live or direct_hls_ready else ""
    stream_uuid = session.get("stream_uuid") or ""
    if not finished and not hls_url and stream_uuid and mux_public_live:
        base = os.getenv("PULSE_HLS_PLAYBACK_URL", "https://live.coinpilotxai.app/hls").rstrip("/")
        hls_url = f"{base}/{stream_uuid}.m3u8"
    supports_webrtc = not finished and bool(session.get("webrtc_room_id"))
    preferred_transport = "hls" if hls_url else "webrtc" if supports_webrtc else "waiting"
    effective_status = status
    if effective_status not in {"ended", "offline", "archived", "deleted", "failed"} and supports_webrtc:
        track_count = int(session.get("audio_tracks") or 0) + int(session.get("video_tracks") or 0)
        if track_count > 0 or publish_state in {
            "browser_live_egress",
            "browser_live_livekit_direct",
            "livekit_direct",
            "livekit_room_active",
            "livekit_participant_joined",
            "livekit_tracks_published",
            "mux_live",
        }:
            effective_status = "live"
    return {
        "ok": True,
        "live_id": int(session.get("id") or session.get("live_id") or 0),
        "status": effective_status,
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
