#!/usr/bin/env python3
"""Static and route audit for PulseSoc predictive media preloading."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text()
    required_renderer_markers = [
        "PRELOAD_WINDOW = Object.freeze({ previous: 1, next: 2 })",
        "predictiveMediaCache",
        "preloadControllers",
        "schedulePredictivePreload",
        "observePredictivePreload",
        "requestIdleCallback",
        "connectionConstrained",
        "warmMediaWrap",
        "warmVideo",
        "warmImage",
    ]
    for marker in required_renderer_markers:
        require(marker in renderer, f"missing renderer marker: {marker}")

    require('video.preload = connectionConstrained() && priority !== "current" ? "metadata" : "auto"' in renderer, "current/nearby video preload decision missing")
    require("PRELOAD_MAX_CACHE = 72" in renderer, "bounded preload cache missing")
    require("cancelStalePreloads" in renderer, "stale preload cancellation missing")

    bot_source = (ROOT / "bot.py").read_text()
    template_source = (ROOT / "templates/pulse_messages_v2.html").read_text()
    require("pulse_media_renderer.js?v=predictive-preload-20260612" in bot_source, "Pulse shell renderer cache bust missing")
    require("pulse_media_renderer.js?v=predictive-preload-20260612" in template_source, "Messages renderer cache bust missing")

    bot.init_db()
    with bot.webhook_app.test_client() as client:
        with client.session_transaction() as session:
            session["account_user_id"] = -920871340

        videos = client.get("/api/pulse/videos?limit=5&safe=1")
        require(videos.status_code == 200, f"videos API returned {videos.status_code}")
        video_payload = videos.get_json() or {}
        rows = video_payload.get("videos") or []
        require(rows, "videos API returned no preloadable media rows")
        sample = rows[0]
        for field in ("permalink", "duration_seconds", "owner_name"):
            require(field in sample, f"videos API missing preload metadata field: {field}")
        require(sample.get("media_url") or sample.get("playback_url") or sample.get("thumbnail_url"), "videos API missing media/thumbnail preload URL")

        page = client.get("/pulse/videos")
        require(page.status_code == 200, f"videos page returned {page.status_code}")
        require(b"data-media-src" in page.data or b"data-media-url" in page.data, "videos page missing media data attributes")

    print("PASS pulse predictive media preload audit")
    print("window previous=1 current=1 next=2")
    print("api_videos", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
