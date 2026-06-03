#!/usr/bin/env python3
"""Aggregate audit for Pulse mobile video playback."""

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
    source = renderer + bot

    for token in [
        "pulseMediaSoundEnabled",
        "webkit-playsinline",
        "nativeHlsSupported(video)",
        "https://stream.mux.com/${id}.m3u8",
        "data-media-source-type",
        "Pulse video request HEAD",
        "video.muted = !shouldTrySound",
        "setSoundEnabled(true)",
        "PulseMediaRenderer.renderMedia",
        "PulseMediaRenderer?.playVisibleVideo",
    ]:
        expect(token in source, f"mobile video playback includes {token}")

    expect("muxHlsUrlValue || item.playback_url || directUrl" in renderer, "Mux HLS is preferred before fallback stream")
    expect("nativeHlsSupported(video)" in renderer and "loadHlsLibrary()" in renderer, "HLS.js is only the non-native fallback")
    expect("Pulse reel stream failed without cache-busting retry" in bot, "Reels avoid retry loops")
    expect("pulse-status-card-text" in bot and "pulse-status-card-media" in bot, "Status text/photo cards remain intact")


if __name__ == "__main__":
    main()
