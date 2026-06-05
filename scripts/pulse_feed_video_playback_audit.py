#!/usr/bin/env python3
"""Audit Pulse feed video rendering and desktop sizing."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
MEDIA_CSS = ROOT / "static/css/pulse_cinematic_media.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    css = MEDIA_CSS.read_text(encoding="utf-8")

    for token in [
        "function mediaHtml(items)",
        "pulse-unified-video-player",
        "data-pulse-video-player",
        "muted controls playsinline webkit-playsinline preload",
        "window.PulseMediaRenderer?.hydrate(document)",
        "pulse_media_renderer.js?v=video-frame-repair-20260603",
    ]:
        expect(token in bot, f"feed page includes {token}")

    for token in [
        ".post.has-media .media-grid",
        "max-width: 1220px",
        ".post.is-video .media-grid",
        "max-width: 1180px",
        "max-height: min(78vh, 760px)",
        "margin-left: auto",
        "margin-right: auto",
    ]:
        expect(token in css, f"desktop feed video CSS includes {token}")

    expect("object-fit: contain" in css, "feed videos preserve aspect ratio")
    expect("calc(100%" in css, "mobile/card media bleed styles remain available for non-desktop layouts")


if __name__ == "__main__":
    main()
