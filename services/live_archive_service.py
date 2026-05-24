"""Replay/VOD lifecycle helpers for Pulse Live."""

from __future__ import annotations

from datetime import datetime


def replay_manifest(session=None, chat_messages=None):
    session = session or {}
    chat_messages = chat_messages or []
    live_id = int(session.get("id") or session.get("live_id") or 0)
    return {
        "ok": True,
        "live_id": live_id,
        "status": "recording" if (session.get("status") or "") == "live" else "ready" if session.get("ended_at") else "pending",
        "replay_url": session.get("replay_url") or "",
        "thumbnail_url": session.get("thumbnail_url") or "",
        "chat_replay_events": len(chat_messages),
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
