#!/usr/bin/env python3
"""Audit Pulse Videos approved music attachment wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("pulse_content_music" in source, "shared content music table exists")
    require("content_type=\"video\"" in source, "video content can receive approved music snapshot")
    require("music_track_id" in source, "video/post publish payload supports music track id")
    require("Music track is not approved for Pulse use" in source, "unsafe video music attachment is blocked")
    print("pulse videos music audit ok")


if __name__ == "__main__":
    main()
