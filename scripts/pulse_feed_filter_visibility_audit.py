#!/usr/bin/env python3
"""Audit Pulse feed filter visibility on mobile."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    for label in ["For You", "Following", "Trending", "Global", "AI Picks"]:
        expect(label in source, f"Feed filter exists: {label}")

    for token in [
        "#tabs",
        "#tabs .tabs",
        "overflow-x: auto",
        "flex-wrap: nowrap",
        "white-space: nowrap",
        "scroll-snap-align: start",
        "#tabs button.active",
    ]:
        expect(token in css, f"Feed filter CSS guard includes {token}")


if __name__ == "__main__":
    main()
