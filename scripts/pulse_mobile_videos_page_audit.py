#!/usr/bin/env python3
"""Audit the PulseSoc mobile Videos redesign without external services."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


PLACEHOLDER_TITLE = re.compile(r"^(PulseSoc Video|PulseSoc Live|Untitled Video)$", re.I)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    source = (ROOT / "bot.py").read_text()
    markers = [
        "videos-mobile-header",
        "mobileTrendingCreators",
        "mobileTopVideos",
        "hydrateVideosBottomNav",
        "pulseVideosResponsiveRedesign",
        "data-mobile-video-search",
        "pulse-video-hub-media",
    ]
    for marker in markers:
        require(marker in source, f"missing source marker: {marker}")

    bot.init_db()
    with bot.webhook_app.test_client() as client:
        with client.session_transaction() as session:
            session["account_user_id"] = -920871340

        page = client.get("/pulse/videos")
        require(page.status_code == 200, f"/pulse/videos returned {page.status_code}")
        html = page.data.decode("utf-8", "ignore")
        for marker in markers:
            require(marker in html, f"missing rendered marker: {marker}")

        require("Videos</h1>" in html, "mobile Videos header title missing")
        require("Discover high-quality videos from creators around the world." in html, "mobile subtitle missing")
        require("/pulse/videos" in html and "/pulse/messages" in html, "mobile bottom nav targets missing")

        response = client.get("/api/pulse/videos?limit=8&safe=1")
        require(response.status_code == 200, f"/api/pulse/videos returned {response.status_code}")
        payload = response.get_json() or {}
        videos = payload.get("videos") or []
        require(len(videos) > 0, "expected real backend videos for audit account")
        for video in videos:
            title = (video.get("title") or "").strip()
            require(not PLACEHOLDER_TITLE.match(title), f"placeholder title returned: {title}")
            require(video.get("permalink"), f"video {video.get('id')} missing permalink")
            require(video.get("owner_name") or video.get("author_name"), f"video {video.get('id')} missing creator name")

    print("PASS pulse mobile videos page audit")
    print("markers", ", ".join(markers))
    print("api_videos", len(videos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
