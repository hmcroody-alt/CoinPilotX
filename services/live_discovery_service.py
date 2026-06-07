"""Discovery payloads for PulseSoc Live surfaces."""

from __future__ import annotations

from . import live_distribution_service


def live_card(session: dict, creator_name: str = "PulseSoc Creator") -> dict:
    item = dict(session or {})
    playback = live_distribution_service.playback_manifest(item)
    live_id = int(item.get("id") or item.get("live_id") or 0)
    return {
        "id": live_id,
        "type": "live",
        "title": item.get("title") or "PulseSoc Live",
        "creator_name": creator_name,
        "thumbnail_url": item.get("preview_url") or item.get("thumbnail_url") or playback.get("poster_url") or "",
        "status": item.get("publish_state") or item.get("status") or "live",
        "viewer_count": int(item.get("viewer_count") or 0),
        "category": item.get("category") or "Community",
        "live_url": f"/pulse/live/{live_id}",
        "autoplay_preview": bool(playback.get("playback_url")),
        "playback": playback,
        "surfaces": ["pulse_feed", "creator_profile", "reels_live", "live_discovery", "notifications"],
    }


def ranking_score(session: dict) -> int:
    item = dict(session or {})
    viewers = int(item.get("viewer_count") or 0)
    engagement = int(item.get("engagement_score") or 0)
    live_bonus = 80 if (item.get("status") or "") == "live" else 0
    return live_bonus + min(80, viewers * 4) + min(60, engagement)
