"""Replay/VOD lifecycle helpers for Pulse Live."""

from __future__ import annotations

from datetime import datetime

from . import mux_live_service


def replay_manifest(session=None, chat_messages=None):
    session = session or {}
    chat_messages = chat_messages or []
    live_id = int(session.get("id") or session.get("live_id") or 0)
    recording_status = (session.get("recording_status") or "").strip().lower()
    replay_url = session.get("replay_url") or mux_live_service.playback_url(session.get("mux_recording_playback_id") or "")
    status = (session.get("status") or "").strip().lower()
    if status == "live":
        replay_state = "recording"
    elif replay_url:
        replay_state = "ready"
    elif recording_status in {"replay_unavailable", "unavailable"}:
        replay_state = "unavailable"
    elif recording_status in {"replay_failed", "failed"}:
        replay_state = "failed"
    elif session.get("ended_at"):
        replay_state = "processing_recording"
    else:
        replay_state = "pending"
    return {
        "ok": True,
        "live_id": live_id,
        "status": replay_state,
        "replay_url": replay_url,
        "mux_recording_asset_id": session.get("mux_recording_asset_id") or "",
        "mux_recording_playback_id": session.get("mux_recording_playback_id") or "",
        "thumbnail_url": session.get("thumbnail_url") or "",
        "chat_replay_events": len(chat_messages),
        "recording_status": recording_status or replay_state,
        "recording_error": session.get("recording_error") or "",
        "replay_available": bool(replay_url and replay_state == "ready"),
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def publish_replay_payload(session=None, peak_viewers=0, engagement=0):
    session = session or {}
    return {
        "title": (session.get("title") or "Pulse Live Replay")[:140],
        "duration_seconds": int(session.get("duration_seconds") or 0),
        "peak_viewers": int(peak_viewers or 0),
        "engagement_score": int(engagement or 0),
        "visibility": "public" if (session.get("audience") or "public") == "public" else "scoped",
    }


def post_live_actions():
    return [
        "publish_replay",
        "save_private",
        "clip_highlights",
        "convert_to_reels",
        "post_to_groups",
        "download_mp4",
        "delete_replay",
    ]
