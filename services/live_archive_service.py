"""Replay/VOD lifecycle helpers for PulseSoc Live."""

from __future__ import annotations

from datetime import datetime, timezone

from . import mux_live_service


def recording_allowed(session):
    return str(session.get("record_replay", 1)).lower() not in {"0", "false", "no", "off"}


def publication_allowed(session):
    return recording_allowed(session) and str(session.get("replay_publish_enabled", 1)).lower() not in {"0", "false", "no", "off"}


def replay_ready(session):
    return recording_allowed(session) and session.get("recording_status") in {"mux_asset_ready", "replay_ready"} and bool(session.get("replay_url"))


def replay_age_seconds(session):
    try:
        ended = datetime.fromisoformat(str(session.get("ended_at") or "").replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - ended.replace(tzinfo=ended.tzinfo or timezone.utc)).total_seconds()))
    except (ValueError, TypeError):
        return 0


def replay_manifest(session=None, chat_messages=None):
    session = session or {}
    chat_messages = chat_messages or []
    live_id = int(session.get("id") or session.get("live_id") or 0)
    recording_status = (session.get("recording_status") or "").strip().lower()
    replay_url = mux_live_service.refresh_signed_replay_url(session.get("replay_url") or "") if replay_ready(session) else ""
    status = (session.get("status") or "").strip().lower()
    if not recording_allowed(session):
        replay_state = "unavailable"
    elif status == "live":
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
    age = replay_age_seconds(session)
    processing = replay_state == "processing_recording"
    message = "Replay is delayed. We’re checking the recording." if processing and age >= 300 else "Replay is still processing" if processing and age >= 120 else "Replay processing" if processing else "Replay unavailable" if replay_state in {"failed", "unavailable"} else ""
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
        "processing_seconds": age,
        "delayed": processing and age >= 300,
        "message": message,
        "retry_after_seconds": 30 if age >= 120 else 10,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def publish_replay_payload(session=None, peak_viewers=0, engagement=0):
    session = session or {}
    return {
        "title": (session.get("title") or "PulseSoc Live Replay")[:140],
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
