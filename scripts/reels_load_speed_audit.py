#!/usr/bin/env python3
"""Audit PulseSoc Reels instant-load safeguards.

This intentionally checks static wiring plus database/index readiness. Browser
first-frame timing still needs real-device QA because autoplay and media decode
policy differ by device.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main() -> None:
    bot = read("bot.py")
    migration = read("migrations/pulsesoc_reels_load_speed_indexes.sql")

    expect("include_preview_comments=False" in bot, "Reels feed defaults to lightweight comment previews")
    expect("include_comments" in bot and "pulse_reel_comment_payload" in bot, "Comment previews are opt-in, not removed")
    expect("/api/pulse/reels/feed?limit=8&light=1" in bot, "Initial Reels fetch is aggressively paginated")
    expect("firstChunk=reels.slice(0,3)" in bot and "runReelsIdle" in bot, "First Reels chunk renders before idle hydration")
    expect("data-reel-preload-priority" in bot and "preloadPriority" in bot, "Reels carry explicit preload priority")
    expect("active.nextElementSibling?.nextElementSibling" in bot and "active.previousElementSibling" in bot, "Next two and previous Reel window is managed")
    expect("releaseFarReelMedia" in bot and "preload='none'" in bot, "Far offscreen videos unload to protect memory")
    expect("videoMetadataMs" in bot and "videoCanplayMs" in bot and "firstFrameMs" in bot, "Client media timing diagnostics exist")
    expect("firstReelApiMs" in bot and "__pulseReelsLastApiMs" in bot, "First Reels API timing diagnostic exists")
    expect("reelMediaSkeleton" in bot, "Skeleton shimmer exists for non-ready video")
    expect("warmReelPoster" in bot, "Poster thumbnails are warmed for near Reels")
    expect("idx_pulse_posts_reels_feed" in bot and "idx_pulse_reels_status_score_created" in bot, "Runtime Reels indexes are registered")

    required_indexes = [
        "idx_pulse_posts_reels_feed",
        "idx_pulse_reels_post_status",
        "idx_pulse_reels_status_score_created",
        "idx_pulse_reels_user_created",
        "idx_pulse_comments_post_visible_created",
        "idx_chat_media_uploads_context_created",
    ]
    for index_name in required_indexes:
        expect(index_name in migration, f"Migration contains {index_name}")

    old_fetches = re.findall(r"/api/pulse/reels/feed\\?limit=12", bot)
    expect(not old_fetches, "No remaining first-page/load-more Reels fetch uses limit=12", str(old_fetches))
    print("reels load speed audit ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"reels load speed audit failed: {exc}", file=sys.stderr)
        sys.exit(1)
