#!/usr/bin/env python3
"""Audit immersive Pulse Status viewer layout contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    compact = "".join(CSS.split())
    require("height: min(96dvh, 940px)" in CSS, "desktop story frame consumes nearly all viewer height")
    require("height: 100dvh" in CSS and "min-height: 100dvh" in CSS, "mobile story frame fills viewport")
    require("Content-first PulseSoc Status viewer" in CSS, "viewer declares content-first design contract")
    require("Edge-to-edge PulseSoc Status gesture model" in CSS, "viewer declares edge-to-edge gesture contract")
    require("width: 100vw !important" in CSS and "height: 100dvh !important" in CSS, "open viewer fills the full screen")
    require("object-fit: cover !important" in CSS, "status content fills edge-to-edge")
    require("toggleViewerSound" in JS and "status-tap-toggle-sound" in JS, "tap toggles mute and unmute")
    require("data-status-music-audio" in JS and "viewerSoundMedia" in JS, "attached Status music is rendered and selected before video audio")
    require("status-attached-music" in JS and "playViewerMedia" in JS, "attached music keeps the underlying video muted")
    require("media.readyState === 0" in JS and "soundMedia.readyState === 0" in JS, "attached Status audio is explicitly loaded before playback")
    require("dy >= 76" in JS and "closeViewer(viewer)" in JS, "downward swipe exits the viewer")
    require("absX >= 52" in JS and "navigateCreator(dx < 0 ? 1 : -1)" in JS, "horizontal swipe navigates between creators")
    require("tapRatio <= 0.32" in JS and "tapRatio >= 0.68" in JS and "handleViewerTap" in JS, "left and right edge taps navigate the current story stack")
    require("return toggleViewerSound(viewer)" in JS and "statusGestureHandledAt" in JS, "middle tap toggles sound without falling through to duplicate click handlers")
    require("openStatusReply" in JS and "is-commenting" in CSS, "comment action opens a visible reply composer")
    require("pulse-status-reaction-burst" in JS and "pulseStatusReactionBurst" in CSS, "reactions animate on every click")
    require("pulse-status-creator-navigate" in BOT and "navigateStatusStoryCreator" in BOT and "navigateStatusPageCreator" in BOT, "Home and Status pages handle creator-level swipe navigation")
    require("ensureImmersiveStatusHud" in JS and "pulse-status-now-playing" in CSS, "viewer includes reference-style sound identity strip")
    require("statusSoundToggledAt" in JS and "statusSoundToggledAt" in BOT, "tap sound toggle is guarded against duplicate click handlers")
    require("ensurePulseStatusViewerRuntime" in BOT and "data-pulse-status-runtime" in BOT, "Home lazy-loads the shared viewer on first Status open")
    require("history.replaceState(null,'',`/pulse/status?status=${statusDraft.currentStatusId}`)" in BOT, "Home More keeps the active viewer instead of switching implementations")
    require(".pulse-status-story-sound-card" in CSS and "display: none !important" in CSS, "extra sound card HUD is hidden")
    require(".pulse-status-story-advance-hint" in CSS and "display: none !important" in CSS, "tap-to-advance hint is hidden")
    require(".pulse-status-story-actions" in CSS and "width: 54px !important" in CSS and "opacity: .9 !important" in CSS, "side reactions are compact")
    require("[data-status-story-count]" in CSS and "[data-status-viewer-count]" in CSS and "display: none !important" in CSS, "owner analytics remain hidden from public chrome")
    require("data-status-story-prev" in BOT and "data-status-story-next" in BOT and "data-status-story-close" in BOT, "home story viewer keeps navigation controls")
    require("data-status-viewer-prev" in BOT and "data-status-viewer-next" in BOT and "data-status-viewer-close" in BOT, "Status page viewer keeps navigation controls")
    require("display: none !important" in CSS and ".pulse-status-story-nav" in CSS, "visible next/back buttons are hidden")
    require("button.hidden = false" in JS, "Sound control stays available after unmuting")
    require("revealStatusChrome" in JS, "viewer can briefly reveal lightweight controls")
    require("status-gesture-command-20260621c" in BOT, "status CSS/JS cache keys are bumped")
    require("padding:42px16px16px" not in compact and "linear-gradient(180deg,transparent,rgba(2,6,23,.88))" not in compact, "old large bottom metadata panel styling is removed")


if __name__ == "__main__":
    main()
