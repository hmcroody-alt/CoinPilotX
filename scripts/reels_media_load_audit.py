#!/usr/bin/env python3
"""Audit Reels media loading, poster hydration, retry, and mobile playback hooks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8") if (ROOT / "static/css/pulse_reels_experience.css").exists() else ""
    expect("/api/pulse/reels/feed" in source, "Reels feed API exists")
    expect("data-reel-media" in source, "Reels media elements are identifiable")
    expect("poster=" in source and "preload=\"metadata\"" in source, "Reels videos render poster and metadata preload")
    expect("playsinline" in source, "Reels videos are mobile Safari safe")
    expect("type=\"${esc(mime||'video/mp4')}\"" in source or "video/mp4" in source, "Reels video source includes MIME type")
    expect("retryPulseReelMedia" in source, "Reels media retry handler exists")
    expect("preloadNextReel" in source, "next Reel preload hook exists")
    expect("media_service.resolve_media" in source, "Reels payload uses canonical media resolver")
    expect("reels-shell" in source and "100dvh" in source + css, "Reels immersive viewport exists")
    expect("is-broken" in source and "Media could not load. Tap to retry." in source, "polished Reels media failure state exists")
    print("reels media load audit ok")


if __name__ == "__main__":
    main()
