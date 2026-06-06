#!/usr/bin/env python3
"""Audit Reels keep immersive full-size video layout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_cinematic_media.css").read_text(encoding="utf-8")
REELS = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


final = CSS[CSS.rfind("Pulse all-video full-width contract"):]
expect(".pulse-media-surface-reels video" in final, "shared contract covers Reels surface")
expect(".reel-card video" in final, "Reel cards are included")
expect("object-fit: cover !important" in final, "Reels use immersive cover fit")
expect("reels-stage" in REELS or "reel-card" in REELS, "Reels stylesheet remains present")
print("reels video layout audit ok")
