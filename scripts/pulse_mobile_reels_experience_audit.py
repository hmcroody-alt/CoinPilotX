#!/usr/bin/env python3
"""Audit the mobile Reels experience guardrails.

This intentionally does not create fixture data. The audit verifies that the
Reels feed only returns media the backend can reasonably treat as playable.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    bot.init_db()

    with open(os.path.join(ROOT, "bot.py"), "r", encoding="utf-8") as handle:
        source = handle.read()

    required_markers = [
        "hydrateReelsBottomNav",
        "data-reels-title>Reels",
        "reel_media_source_is_playable",
        "reel_has_playable_video",
        "reel_prioritize_video_media",
        "getsize(path) >= 1024",
        "body:has(.reels-immersive) .mobile-bottom-nav{display:grid!important",
        "videoMedia=mediaList.find",
        "post_id IN (",
    ]
    for marker in required_markers:
        require(marker in source, f"Missing mobile Reels marker: {marker}")

    with bot.webhook_app.test_client() as client:
        with client.session_transaction() as session:
            session["account_user_id"] = -920871340
        response = client.get("/api/pulse/reels/feed?tab=for_you&limit=5")
        require(response.status_code == 200, f"Feed returned HTTP {response.status_code}")
        payload = response.get_json() or {}
        require(payload.get("ok") is True, "Feed payload did not return ok=true")
        for reel in payload.get("reels", []):
            require(bot.reel_has_playable_video(reel), f"Non-playable reel leaked into feed: {reel.get('reel_id') or reel.get('id')}")

    stale_sources = [
        "/static/uploads/pulse_media/2026/06/09/pulse-video-fcae01f77e035868.webm",
        "https://stream.mux.com/mux_playback_audit.m3u8",
        "https://cdn.coinpilotx.app/audit/search-reel.mp4",
    ]
    for src in stale_sources:
        require(not bot.reel_media_source_is_playable(src), f"Stale audit media source passed as playable: {src}")

    print("PASS pulse_mobile_reels_experience_audit")
    print(f"valid_reels_returned={len(payload.get('reels', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
