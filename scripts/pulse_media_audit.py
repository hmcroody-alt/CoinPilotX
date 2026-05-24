#!/usr/bin/env python3
"""Audit Pulse media metadata, resilient rendering, and fallback rules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token in [
        "Media could not load.",
        "data-retry-media",
        "loading=\"lazy\"",
        "decoding=\"async\"",
        "preload=\"metadata\"",
        "data-fit=\"smart\"",
        "object-fit:contain",
    ]:
        expect(token in source, f"media rendering rule present: {token}")
    media_css = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")
    for token in ["poster first", "object-fit: contain", "data-orientation", "Adaptive playback"]:
        expect(token in source + media_css, f"adaptive media/Reels rule present: {token}")
    visible, reason = pulse_feed_engine.pulse_visibility_decision({"visibility": "public", "moderation_status": "approved", "status": "published", "deleted_at": None})
    expect(visible and reason == "public_approved", "canonical public visibility allows approved public posts")
    hidden, reason = pulse_feed_engine.pulse_visibility_decision({"visibility": "public", "moderation_status": "approved", "status": "published", "deleted_at": "2026-01-01"})
    expect(not hidden and reason == "deleted", "canonical visibility blocks deleted media posts")
    print("pulse media audit ok")


if __name__ == "__main__":
    main()

