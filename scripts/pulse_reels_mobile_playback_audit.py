#!/usr/bin/env python3
"""Audit Reels mobile playback behavior."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
REELS_CSS = ROOT / "static/css/pulse_reels_experience.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    reels_css = REELS_CSS.read_text(encoding="utf-8")

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

    expect("muxHls=media.mux_playback_id?`https://stream.mux.com/${media.mux_playback_id}.m3u8`" in bot, "Reels force canonical Mux HLS before raw media")
    expect("if(visible&&v===primaryReelVideo(card))" in bot, "only the active visible Reel plays")
    expect("else{v.pause();v.preload='metadata'}" in bot, "offscreen Reels pause and reduce preload")
    expect("next.dataset.reelLightPreloaded==='1'" in bot, "Reels preload is single-shot")
    expect("Pulse reel stream failed without cache-busting retry" in bot, "Reels avoid infinite stream retry loops")
    expect("playsinline webkit-playsinline" in bot + renderer, "Reels videos have mobile inline playback attributes")
    expect("nativeHlsSupported(video)" in renderer and "pulseNativeHls" in renderer, "Reels inherit native HLS handling")
    expect(".reel-details-panel" in reels_css and "display: none !important" in reels_css, "mobile Reels hide desktop details panels")
    expect(".reel-comments-preview" in reels_css and ".reel-inline-comment" in reels_css, "mobile Reels hide comments preview and inline input")
    expect(".reel-comments:not(.open)" in reels_css, "mobile Reels comments are hidden by default")


if __name__ == "__main__":
    main()
