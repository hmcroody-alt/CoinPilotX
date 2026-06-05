#!/usr/bin/env python3
"""Audit Pulse-wide video sound preference persistence."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
BOT = ROOT / "bot.py"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    bot = BOT.read_text(encoding="utf-8")
    combined = renderer + "\n" + bot

    for token in [
        'const SOUND_KEY = "pulseMediaSoundEnabled"',
        "window.localStorage?.getItem(SOUND_KEY)",
        "window.localStorage?.setItem(SOUND_KEY, String(!!enabled))",
        "const saved = window.localStorage?.getItem(SOUND_KEY)",
        "if (saved === \"false\") return false",
        "setSoundEnabled(false)",
        "setSoundEnabled(true)",
        "volumechange",
        "PulseMediaRenderer?.hydrate(document)",
    ]:
        expect(token in combined, f"sound preference includes {token}")

    expect("setSoundEnabled(false);" not in renderer[renderer.find("async function playVisibleVideo"):renderer.find("function preloadNextVideo")], "browser autoplay fallback does not persist muted preference")
    expect("playVisibleVideo(vid, soundEnabled())" in renderer, "in-view autoplay honors persisted sound preference")
    expect("playVisibleVideo(video, soundEnabled())" in renderer, "desktop hover playback honors persisted sound preference")
    expect("data-pulse-media-sound hidden>Tap for sound</button>${mediaFallbackHtml(m)}" in bot, "feed video posts include inline tap-for-sound prompt")
    expect("button,a,video,audio,[data-pulse-media-sound]" in bot, "feed video control taps do not open the lightbox")
    expect("_pulseSoundProgrammaticUntil" in renderer, "late programmatic mute events cannot overwrite the user preference")
    print("pulse video sound preference audit ok")


if __name__ == "__main__":
    main()
