#!/usr/bin/env python3
"""Audit desktop hover autoplay preview behavior."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
renderer = (root / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")

for token in [
    "desktopPointer()",
    "pointerenter",
    "pointerleave",
    "hoverVideo = video",
    "playVisibleVideo(video, false)",
    "video.muted = true",
    "pauseOtherVideos(video)",
]:
    assert token in renderer, f"missing hover autoplay token: {token}"
    print(f"PASS: {token}")

print("pulse video hover autoplay audit ok")
