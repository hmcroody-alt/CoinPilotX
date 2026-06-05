#!/usr/bin/env python3
"""Audit unmuted autoplay fallback behavior for Pulse videos."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
CSS = ROOT / "static/css/pulse_cinematic_media.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    play_block = renderer[renderer.find("async function playVisibleVideo"):renderer.find("function preloadNextVideo")]

    for token in [
        "const shouldTrySound = !!preferSound && soundEnabled()",
        "setVideoMuted(video, !shouldTrySound, \"autoplay\")",
        "setVideoMuted(video, true, \"autoplay-fallback\")",
        "showSoundPrompt(wrap, true, true)",
        "data-pulse-media-sound",
        "Tap for sound",
        ".pulse-media-sound-unlock",
    ]:
        expect(token in renderer + css, f"autoplay fallback includes {token}")

    expect("setSoundEnabled(false)" not in play_block, "fallback to muted autoplay keeps user sound preference intact")
    expect("await video.play();" in play_block and "catch (error)" in play_block, "unmuted autoplay attempt falls back safely")
    print("pulse video unmuted autoplay fallback audit ok")


if __name__ == "__main__":
    main()
