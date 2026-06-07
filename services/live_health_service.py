"""Compatibility facade for PulseSoc Live health automation."""

from __future__ import annotations

from . import live_stream_health_service


def health_snapshot(session=None, viewer_count=0, chat_count=0):
    health = live_stream_health_service.score_stream(session, viewer_count=viewer_count, chat_count=chat_count)
    return {
        **health,
        "recovery_hints": live_stream_health_service.recovery_hints(health),
        "auto_recovery": {
            "restart_ffmpeg": False,
            "refresh_hls_manifest": bool(session and (session.get("status") == "live")),
            "reconnect_viewers": True,
        },
    }
