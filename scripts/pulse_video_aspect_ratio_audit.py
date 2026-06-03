#!/usr/bin/env python3
"""Audit Pulse video framing rules for portrait, square, and landscape media."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def expect(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    cinematic = (ROOT / "static/css/pulse_cinematic_media.css").read_text(encoding="utf-8")
    reels = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")
    status = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")

    for token in [
        "data-media-orientation",
        "data-media-aspect-ratio",
        "`is-${orientation}`",
        '"square"',
        '"ultrawide"',
        '"landscape"',
        '"portrait"',
    ]:
        expect(token in renderer, f"renderer exposes {token}")

    for ratio, label in [
        ("9 / 16", "9:16 portrait"),
        ("4 / 5", "4:5 portrait"),
        ("1 / 1", "1:1 square"),
        ("16 / 9", "16:9 landscape"),
        ("21 / 9", "21:9 ultrawide"),
    ]:
        expect(ratio in cinematic, f"feed framing handles {label}")

    expect(".pulse-media-surface-reels" in cinematic and "aspect-ratio: 9 / 16" in cinematic, "Reels shared player is locked to 9:16")
    expect(".pulse-media-surface-reels img" in cinematic and "object-fit: cover !important" in cinematic, "Reels shared player uses cover")
    expect(".reel-blur-bg" in reels and "display: none !important" in reels, "Reels blurred side panels are disabled")
    expect("object-fit: cover" in reels and "aspect-ratio: 9 / 16" in reels, "Reels page uses full-frame 9:16 cover")

    expect(".pulse-media-surface-status img" in cinematic and ".pulse-media-surface-status video" in cinematic, "Status shared player is explicitly framed")
    expect(".pulse-status-story-media img" in status and "object-fit: cover" in status, "Status story fallback uses full-screen cover")

    expect(".media-kind-video.is-portrait" in cinematic, "Feed detects portrait video wrappers")
    expect("max-width: 480px" in cinematic, "Feed portrait video has centered portrait max width")
    expect(":has(.pulse-media-wrap.media-kind-video.is-portrait)" in cinematic, "Feed portrait cards are centered by wrapper detection")
    expect(".media-kind-video.is-landscape" in cinematic and "16 / 9" in cinematic, "Feed landscape video keeps responsive landscape frame")
    expect(".media-kind-video.is-ultrawide" in cinematic and "21 / 9" in cinematic, "Feed ultrawide video keeps responsive ultrawide frame")

    expect("video-frame-repair-20260603" in renderer, "renderer cache tag uses video frame repair version")
    expect("video-frame-repair-20260603" in bot, "Pulse pages load video frame repair assets")
    expect("object-fit:contain" not in reels.replace(" ", ""), "Reels CSS has no visible contain fallback")

    print("pulse video aspect ratio audit ok")


if __name__ == "__main__":
    main()
