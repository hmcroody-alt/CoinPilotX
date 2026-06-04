#!/usr/bin/env python3
"""Verify canonical Pulse content routing rules."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
FEED = (ROOT / "services/pulse_feed_engine.py").read_text(encoding="utf-8")


def expect(value, label):
    if not value:
        raise AssertionError(label)
    print(f"PASS: {label}")


expect('"feed_video"' in BOT and "pulse_video_index_upsert(" in BOT, "regular videos are indexed")
expect("video_media = next" in BOT and 'media_type") or "").lower() == "video"' in BOT, "only video media enters feed video index")
expect('"reel_only"' in FEED, "Reels have a feed-hidden compatibility visibility")
expect('reel_visibility = ' in BOT and 'else "reel_only"' in BOT, "Reels default to Reel-only routing")
expect('"status_video"' not in BOT[BOT.find("PULSE_VIDEO_SOURCE_TYPES"):BOT.find("def pulse_video_index_upsert")], "Statuses are excluded from the video library")
print("pulse media routing audit ok")
