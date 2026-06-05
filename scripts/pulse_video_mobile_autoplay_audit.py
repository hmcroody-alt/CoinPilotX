#!/usr/bin/env python3
"""Audit mobile muted in-view autoplay and layout guards."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")
renderer = (root / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")

for token in [
    "playsinline webkit-playsinline",
    "video.preload = \"auto\"",
    "video.muted = !shouldTrySound",
    "autoplayAllowed()",
    "connection?.saveData",
    "prefers-reduced-motion: reduce",
    "grid-template-columns:1fr",
    "overflow-x:hidden",
]:
    assert token in bot + renderer, f"missing mobile autoplay/layout token: {token}"
    print(f"PASS: {token}")

print("pulse video mobile autoplay audit ok")
