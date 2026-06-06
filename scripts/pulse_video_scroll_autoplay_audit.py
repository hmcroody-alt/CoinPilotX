#!/usr/bin/env python3
"""Audit scroll/in-view autoplay preview behavior."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
renderer = (root / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")

for token in [
    "IntersectionObserver(entries =>",
    "entry.intersectionRatio < .58",
    "playVisibleVideo(vid, false)",
    "preloadNextVideo(vid)",
    "targetWrap?.classList.add(\"is-active-media\")",
    "desktopPointer() && hoverVideo",
]:
    assert token in renderer, f"missing scroll autoplay token: {token}"
    print(f"PASS: {token}")

print("pulse video scroll autoplay audit ok")
