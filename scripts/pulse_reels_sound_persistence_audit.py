#!/usr/bin/env python3
"""Audit Reels sound persistence follows the shared Pulse preference."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    reels_block = bot[bot.find("const reelsFeed=document.getElementById('reelsFeed')"):bot.find("function renderRail(activeLane")]
    play_block = bot[bot.find("async function playReelVideo"):bot.find("function scheduleReelRetry")]

    for token in [
        "PULSE_MEDIA_SOUND_KEY='pulseMediaSoundEnabled'",
        "function pulseSoundPreference()",
        "if(saved==='false')return false",
        "let reelsSoundEnabled=pulseSoundPreference()",
        "localStorage.setItem(PULSE_MEDIA_SOUND_KEY,String(reelsSoundEnabled))",
        "window.PulseMediaRenderer?.setSoundEnabled?.(reelsSoundEnabled)",
        "playReelVideo(v,reelsSoundEnabled)",
        "showReelSoundPrompt(card,true)",
    ]:
        expect(token in reels_block, f"Reels sound persistence includes {token}")

    expect("setReelsSound(false)" not in play_block, "blocked unmuted Reels autoplay does not persist muted preference")
    expect("video.muted=true" in play_block, "blocked unmuted Reels autoplay falls back to muted playback")
    print("pulse reels sound persistence audit ok")


if __name__ == "__main__":
    main()
