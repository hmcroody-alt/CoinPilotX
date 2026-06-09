#!/usr/bin/env python3
"""Audit that Pulse playback surfaces do not force mute after playback starts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
BOT = ROOT / "bot.py"
STATUS_VIEWER = ROOT / "static/js/pulse_status_viewer.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    bot = BOT.read_text(encoding="utf-8")
    status = STATUS_VIEWER.read_text(encoding="utf-8")
    combined = renderer + "\n" + bot + "\n" + status
    feed_block = bot[bot.find("function mediaHtml(items)"):bot.find("function mediaTypePlaceholder")]
    videos_block = bot[bot.find("function videoMedia(v)"):bot.find("async function loadVideos")]

    expect('const SOUND_KEY = "pulseMediaSoundEnabled"' in renderer, "single shared Pulse media sound key exists")
    expect("video.defaultMuted = false" in renderer, "defaultMuted is cleared before playback")
    expect('removeAttribute("muted")' in renderer and "removeAttribute('muted')" in bot, "real playback removes muted attributes before autoplay")
    expect("setVideoMuted(video, !shouldTrySound, \"autoplay\")" in renderer, "autoplay mute is programmatic and per-attempt")
    expect("setVideoMuted(video, true, \"autoplay-fallback\")" in renderer, "blocked unmuted autoplay falls back per-attempt")
    expect("setSoundEnabled(false);" not in renderer[renderer.find("async function playVisibleVideo"):renderer.find("function preloadNextVideo")], "fallback never persists muted preference")
    expect("_pulseSoundProgrammaticUntil" not in combined, "no timeout-based sound guard remains")
    expect("data-pulse-video-player muted" not in renderer + "\n" + feed_block + "\n" + videos_block, "shared playback video markup is not default muted")
    expect("<video muted playsinline" not in videos_block, "videos page playback is not default muted")
    expect("controls autoplay muted playsinline" in status, "status viewer fallback is default muted by status-only policy")
    print("pulse video no forced mute audit ok")


if __name__ == "__main__":
    main()
