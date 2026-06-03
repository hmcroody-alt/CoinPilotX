#!/usr/bin/env python3
"""Audit Reels mobile playback behavior."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")

    for token in [
        "PulseMediaRenderer.renderMedia",
        "playReelVideo",
        "primaryReelVideo",
        "syncPlayback",
        "preloadNextReel",
        "pulseReelsSoundEnabled",
        "pulseMediaSoundEnabled",
    ]:
        expect(token in bot, f"Reels includes {token}")

    expect("if(visible&&v===primaryReelVideo(card))" in bot, "only the active visible Reel plays")
    expect("else{v.pause();v.preload='metadata'}" in bot, "offscreen Reels pause and reduce preload")
    expect("next.dataset.reelLightPreloaded==='1'" in bot, "Reels preload is single-shot")
    expect("Pulse reel stream failed without cache-busting retry" in bot, "Reels avoid infinite stream retry loops")
    expect("playsinline webkit-playsinline" in bot + renderer, "Reels videos have mobile inline playback attributes")
    expect("nativeHlsSupported(video)" in renderer and "pulseNativeHls" in renderer, "Reels inherit native HLS handling")


if __name__ == "__main__":
    main()
