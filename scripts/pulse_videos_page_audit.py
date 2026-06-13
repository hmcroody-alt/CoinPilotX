#!/usr/bin/env python3
"""Audit the Pulse Videos page and API surface."""

from pathlib import Path

text = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in [
    '@webhook_app.route("/pulse/videos"',
    '@webhook_app.route("/api/pulse/videos"',
    "data-video-tab='{key}'",
    "For You",
    "Following",
    "Trending",
    "New Creators",
    "AI Picks",
    "Local",
    "videos-grid",
    "data-videos-drawer-open",
    "data-videos-mobile-drawer",
    "videos-drawer-nav",
    "setVideosDrawer",
    "Creator Studio",
    "Marketplace",
]:
    assert token in text, f"missing {token}"
    print(f"PASS: {token}")
print("pulse videos page audit ok")
