"""Ecamm/Restream-style multistream facade for Pulse Live."""

from __future__ import annotations

from . import live_destination_service, live_restream_service

OAUTH_PLATFORMS = {"facebook", "youtube", "twitch", "kick", "tiktok", "x_twitter", "linkedin"}


def supported_platforms() -> list[str]:
    return ["pulse", "facebook", "youtube", "twitch", "kick", "tiktok", "x_twitter", "linkedin", "custom_rtmp"]


def platform_metadata(platform: str, title: str = "", description: str = "", thumbnail: str = "") -> dict:
    platform = live_destination_service.normalize_platform(platform)
    return {
        "platform": platform,
        "title": title[:140] if title else "",
        "description": description[:500] if description else "",
        "thumbnail": thumbnail[:700] if thumbnail else "",
        "requires_oauth": platform in OAUTH_PLATFORMS,
        "requires_rtmp": platform == "custom_rtmp",
    }


def prepare_targets(cur, *, live_id: int, user_id: int, destinations=None, custom_rtmp_url: str = "", custom_stream_key: str = "") -> list[dict]:
    return live_restream_service.prepare_restream_targets(
        cur,
        live_id=live_id,
        user_id=user_id,
        destinations=destinations,
        custom_rtmp_url=custom_rtmp_url,
        custom_stream_key=custom_stream_key,
    )


def health_summary(targets: list[dict] | None = None) -> dict:
    targets = targets or []
    failed = [item for item in targets if item.get("status") == "failed"]
    live = [item for item in targets if item.get("status") in {"live", "connecting"}]
    return {
        "total": len(targets),
        "active_or_connecting": len(live),
        "failed": len(failed),
        "pulse_safe": any(item.get("platform") == "pulse" and item.get("status") == "live" for item in targets),
        "destination_isolation": True,
        "bitrate_balancing": "planned",
        "reconnect_recovery": True,
    }
