#!/usr/bin/env python3
"""Audit mobile-safe Pulse video tag attributes and diagnostics."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
BOT = ROOT / "bot.py"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    bot = BOT.read_text(encoding="utf-8")

    for token in [
        "data-pulse-video-player${controls}${loop} playsinline webkit-playsinline",
        "video.setAttribute(\"playsinline\", \"\")",
        "video.setAttribute(\"webkit-playsinline\", \"\")",
        "x-webkit-airplay",
        "preload=\"metadata\"",
        "<source src=",
    ]:
        expect(token in renderer, f"shared renderer includes {token}")

    for token in [
        "ready_state",
        "network_state",
        "current_src",
        "source_mime",
        "Pulse video request HEAD",
        "content_type",
        "status: response.status",
    ]:
        expect(token in renderer, f"diagnostics include {token}")

    expect("controls autoplay playsinline webkit-playsinline preload=\"metadata\"" in bot + (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8"), "Status viewer inline videos are mobile-safe")
    expect("controls playsinline webkit-playsinline preload" in bot, "Feed inline videos are mobile-safe")
    expect("autoplay playsinline webkit-playsinline" in bot, "Reels/live inline videos are mobile-safe without forced mute")


if __name__ == "__main__":
    main()
