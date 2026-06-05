#!/usr/bin/env python3
"""Audit feed video sound persistence and controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    feed_block = bot[bot.find("function mediaHtml(items)"):bot.find("function mediaTypePlaceholder")]

    expect("data-pulse-video-player controls playsinline webkit-playsinline" in feed_block, "feed videos do not ship with default muted")
    expect("data-pulse-media-sound hidden>Tap for sound</button>${mediaFallbackHtml(m)}" in feed_block, "feed videos include tap-for-sound prompt")
    expect("button,a,video,audio,[data-pulse-media-sound]" in bot, "feed video native controls do not open lightbox")
    expect("playVisibleVideo(vid, soundEnabled())" in renderer, "feed observer honors saved sound preference")
    expect("video.defaultMuted = false" in renderer, "feed hydration clears default muted")
    expect("setSoundEnabled(false);" not in renderer[renderer.find("async function playVisibleVideo"):renderer.find("function preloadNextVideo")], "feed autoplay fallback does not save muted preference")
    print("pulse feed sound persistence audit ok")


if __name__ == "__main__":
    main()
