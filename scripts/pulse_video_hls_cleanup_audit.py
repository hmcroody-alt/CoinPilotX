#!/usr/bin/env python3
"""Audit HLS.js lifecycle cleanup."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
renderer = (root / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")

for token in [
    "let activeHlsVideo = null",
    "function destroyHls(video)",
    "activeHlsVideo && activeHlsVideo !== video",
    "activeHlsVideo = video",
    "window.addEventListener(\"pagehide\"",
    "new MutationObserver",
    "destroyHls(activeHlsVideo)",
]:
    assert token in renderer, f"missing HLS cleanup token: {token}"
    print(f"PASS: {token}")

print("pulse video HLS cleanup audit ok")
