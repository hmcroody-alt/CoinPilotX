#!/usr/bin/env python3
"""Audit the platform-wide Pulse video sound policy."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
LIVE_JS = ROOT / "static/js/pulse_live_studio.js"
MESSAGES_JS = ROOT / "static/js/pulse_messages_v2.js"
STATUS_VIEWER = ROOT / "static/js/pulse_status_viewer.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    live_js = LIVE_JS.read_text(encoding="utf-8")
    messages_js = MESSAGES_JS.read_text(encoding="utf-8")
    status_viewer = STATUS_VIEWER.read_text(encoding="utf-8")
    play_visible = renderer[renderer.find("async function playVisibleVideo"):renderer.find("function preloadNextVideo")]
    reels_play = bot[bot.find("async function playReelVideo"):bot.find("function scheduleReelRetry")]
    reels_block = bot[bot.find("const reelsFeed=document.getElementById('reelsFeed')"):bot.find("function renderRail(activeLane")]
    status_play = bot[bot.find("async function playStatusViewerVideo"):bot.find("function renderStatusViewer")]
    status_previews = bot[bot.find("function hydrateStatusCardVideos"):bot.find("function renderStatusCard")]

    expect('const SOUND_KEY = "pulseMediaSoundEnabled"' in renderer, "shared sound key is pulseMediaSoundEnabled")
    expect("return true;" in renderer[renderer.find("function soundEnabled"):renderer.find("function autoplayAllowed")], "shared default sound preference is true")
    expect("video.defaultMuted = false" in renderer, "shared renderer clears defaultMuted")
    expect("video.removeAttribute(\"muted\")" in renderer, "shared renderer removes muted attribute")
    expect("function isManagedReelVideo" in renderer, "shared renderer detects Reels managed by the Reels controller")
    expect("if (isManagedReelVideo(video)) return;" in renderer, "shared renderer never remutes managed Reels")
    expect("setReelVideoMuted(card,video,muted,'restore_state',false)" in reels_play, "Reels clear muted default through guarded setter before play")
    expect("playReelVideo(v,true)" in reels_block, "Reels scroll autoplay asks for sound")
    expect("playReelVideo(v,false)" not in bot, "Reels never request muted autoplay for active cards")
    expect("setSoundEnabled(false);" not in play_visible, "browser fallback never saves muted preference")
    expect("browser_policy_fallback" in reels_play and "setReelsSound(false)" not in reels_play, "Reels fallback is temporary")
    expect("data-pulse-video-player muted" not in renderer, "shared video player markup is not muted by default")
    expect("window.PulseMediaRenderer?.soundEnabled?.()!==false" in status_play, "Status viewer follows saved sound preference when actively opened")
    expect("video.defaultMuted=false" in status_play and "video.removeAttribute('muted')" in status_play, "Status viewer clears muted default before active playback")
    expect("player.defaultMuted=true" in status_previews and "player.muted=true" in status_previews, "Status card previews autoplay muted")
    expect("status-media-tap" in bot, "Status viewer tap explicitly enables sound")
    expect("video.defaultMuted = true" in live_js and "video.volume = 0" in live_js, "Live host preview is the required silent exception")
    expect('livePlayer?.dataset?.liveHostViewer === "1"' in live_js and "livePlayer.volume = 0" in live_js, "Live host viewer playback remains silent")
    expect("<video src=\"${escapeAttr(item.previewUrl)}\" muted" not in messages_js, "message video previews are not muted by default")
    print("pulse all video sound policy audit ok")


if __name__ == "__main__":
    main()
