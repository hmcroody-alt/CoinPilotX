#!/usr/bin/env python3
"""Audit Pulse video detail route coverage."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")

for token in [
    '@webhook_app.route("/pulse/videos/<int:video_id>"',
    "def pulse_video_detail_page(video_id):",
    "pulse_video_not_found_response",
    "video-detail-player",
    "https://stream.mux.com/{video.get('mux_playback_id')}.m3u8",
    "Preparing video...",
    "Video processing failed",
    "Related videos",
    "height:clamp(650px,72vh,800px)",
    "height:clamp(60vh,68vh,75vh)",
    "object-fit:contain",
]:
    assert token in bot, f"missing video detail route token: {token}"
    print(f"PASS: {token}")

print("pulse video detail route audit ok")
