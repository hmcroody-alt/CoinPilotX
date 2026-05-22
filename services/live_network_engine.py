"""Advanced livestream network primitives."""

from __future__ import annotations


def live_room_state(session=None, viewers=None, chat_events=None) -> dict:
    session = session or {}
    viewers = viewers or []
    chat_events = chat_events or []
    return {
        "live_id": session.get("id") or session.get("live_id"),
        "status": session.get("status") or "draft",
        "viewer_count": len(viewers),
        "chat_rate": len(chat_events),
        "supports_guests": True,
        "supports_ai_clips": True,
        "supports_premium_rooms": True,
    }


def audience_segment(viewer=None) -> str:
    viewer = viewer or {}
    if viewer.get("premium"):
        return "premium"
    if int(viewer.get("trust_score") or 0) >= 70:
        return "trusted"
    return "community"
