#!/usr/bin/env python3
"""Audit Pulse Status approved music attachment wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("/api/pulse/status/music/search" in source, "Status music search exists")
    require("genre=genre" in source and "topic=topic" in source and "length=length" in source, "Status music search supports AI filters")
    require("music_not_approved" in source, "Status rejects unapproved music")
    require("pulse_attach_music_to_content(cur, content_type=\"status\"" in source, "Status snapshots approved music license")
    print("pulse status music audit ok")


if __name__ == "__main__":
    main()
