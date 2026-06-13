#!/usr/bin/env python3
"""Audit Pulse Reels playback, sound persistence, and stream retry safety."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
REELS_CSS = ROOT / "static/css/pulse_reels_experience.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    reels_css = REELS_CSS.read_text(encoding="utf-8")

    for token in [
        "PulseMediaRenderer.renderMedia",
        "pulseReelsSoundEnabled",
        "pulseMediaSoundEnabled",
        "setReelsSound",
        "playReelVideo",
        "syncPlayback",
        "primaryReelVideo",
        "preloadNextReel",
        "scheduleReelsPlayback",
        "reelAutoplayBlocked",
        "showReelSoundPrompt",
        "pulseReelStreamUrl",
        "data-pulse-video-player",
        "No audio track",
        "logReelAudioState",
        "setReelVideoMuted",
        "updateReelMediaHealth",
        "reelMediaUsable",
    ]:
        expect(token in bot, f"Reels playback includes {token}")

    expect("if(visible&&v===primaryReelVideo(card))" in bot, "only active visible Reel video plays")
    expect("playReelVideo(v,true)" in bot, "visible Reel autoplay requests sound by default")
    expect("video.volume=1" in bot and "setReelVideoMuted(card,video,muted,'restore_state',false)" in bot, "Reels attempt unmuted playback before fallback")
    expect("scheduleReelsPlayback('loadReels')" in bot and "window.addEventListener('pageshow'" in bot, "Reels startup scheduler runs after load and browser restore")
    expect("data-reel-sound-label=\"${id}\">${hasAudio?'Audio':'Silent'}" in bot and "blocked?'Tap for sound':'Audio'" in bot, "persistent sound control avoids permanent muted bubble")
    expect("offscreen_pause" in bot and "v.pause()}v.preload='metadata'" in bot, "offscreen Reel videos pause and drop to metadata preload")
    preload_block = bot[bot.find("function preloadNextReel"):bot.find("function renderRail")]
    expect("active.nextElementSibling?.nextElementSibling" in preload_block and "media.preload='auto'" in preload_block, "next two Reels preload video source")
    tap_block = bot[bot.find("function handleReelMediaTap"):bot.find("reelsFeed.addEventListener('dblclick'")]
    expect("card.dataset.reelAutoplayBlocked==='1'" in tap_block and "setReelsSound(true)" in tap_block, "single tap unlocks blocked autoplay sound before toggling mute")
    expect("setReelsSound(!reelsSoundEnabled)" in tap_block and "video.pause()" not in tap_block, "single tap toggles sound instead of play/pause")
    expect("Pulse reel stream failed without cache-busting retry" in bot, "stream URL retry spam is blocked")
    expect("reel-sound-badge.is-hidden" in bot + reels_css, "Reels sound badge can be hidden")
    expect("reelUserSoundUnlocked" in bot and "reelSuppressMutedFallbackUntil" in bot, "user unmuted state suppresses non-user remute fallback")
    expect("blockedByUserSound:true" in bot and "volumechange-auto-remute" in bot, "unexpected non-user remutes are restored after user sound-on")
    expect("browser_policy_fallback" in bot and "offscreen_pause" in bot and "media_error" in bot, "mute/media reasons are logged")
    expect(".reel-card.is-media-ready .reel-fallback" in reels_css, "ready Reels hide false media fallback")
    expect("reels-media-stage" in bot and "reel-details-panel" in bot, "mobile stage and desktop details panel exist")


if __name__ == "__main__":
    main()
