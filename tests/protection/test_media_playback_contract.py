#!/usr/bin/env python3
"""Protect Reels, Videos, Statuses, and mobile navigation contracts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
RENDERER = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
CAMERA = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    reels_block = BOT[BOT.find("const reelsFeed=document.getElementById('reelsFeed')"):BOT.find("function renderRail(activeLane")]
    status_play = BOT[BOT.find("async function playStatusViewerVideo"):BOT.find("function renderStatusViewer")]
    status_previews = BOT[BOT.find("function hydrateStatusCardVideos"):BOT.find("function renderStatusCard")]

    expect("playReelVideo(v,true)" in reels_block, "active Reels autoplay with sound preference")
    expect("playReelVideo(v,false)" not in BOT, "Reels do not request muted active autoplay")
    expect("active.nextElementSibling?.nextElementSibling" in BOT and "reelLightPreloaded'+(idx+1)" in BOT, "next two Reels are preloaded")
    expect("canHoverPreview = desktopPointer() && !isReelSurface" in RENDERER, "desktop hover preview no longer gates Reels playback")
    expect("pointerdown" in BOT and "show-reaction-menu" in BOT, "long-press reaction affordance remains wired")
    expect("dblclick" in BOT and "fireReel" in BOT, "double-tap/double-click like remains wired")
    expect("reel-sound-badge is-hidden" in BOT, "Reels do not show persistent sound bubbles")
    expect("window.PulseMediaRenderer?.soundEnabled?.()!==false" in status_play, "active Status viewer follows saved sound policy")
    expect("player.defaultMuted=true" in status_previews and "player.muted=true" in status_previews, "Status rail previews stay muted")
    expect("data-videos-drawer-open" in BOT and "data-videos-mobile-drawer" in BOT, "mobile Videos drawer exists")
    expect("setVideosDrawer" in BOT and "videos-drawer-nav" in BOT, "mobile Videos drawer behavior exists")
    expect("Creator Studio" in BOT and "Marketplace" in BOT, "mobile Videos drawer includes full navigation")
    expect("width: { ideal: 1920 }" in CAMERA and "width: { ideal: 1280 }" in CAMERA, "camera quality profiles include 1080p and 720p fallback")
    expect("safeTrackSettings" in CAMERA and "maskDeviceId" in CAMERA, "camera diagnostics are safe")
    expect("Banuba Active" in CAMERA and "Banuba Failed" in CAMERA and "Using Native Camera" in CAMERA, "Banuba runtime status is explicit")
    print("media playback protection contract ok")


if __name__ == "__main__":
    main()
