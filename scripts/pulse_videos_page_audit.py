#!/usr/bin/env python3
"""Audit the Pulse Videos page and API surface."""

from pathlib import Path

text = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in [
    '@webhook_app.route("/pulse/videos"',
    '@webhook_app.route("/api/pulse/videos"',
    "All Videos",
    "Following",
    "Trending",
    "Live",
    "Replays",
    "My Videos",
    "Saved Videos",
    "videos-grid",
]:
    assert token in text, f"missing {token}"
    print(f"PASS: {token}")
print("pulse videos page audit ok")
