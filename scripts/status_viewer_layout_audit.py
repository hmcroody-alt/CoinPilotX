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
    require("dy >= 76" in JS and "closeViewer(viewer)" in JS, "downward swipe exits the viewer")
    require("absX >= 52" in JS and "navigateStory(dx < 0 ? 1 : -1)" in JS, "horizontal swipe navigates stories")
    require("ensureImmersiveStatusHud" in JS and "pulse-status-now-playing" in CSS, "viewer includes reference-style sound identity strip")
    require("statusSoundToggledAt" in JS and "statusSoundToggledAt" in BOT, "tap sound toggle is guarded against duplicate click handlers")
    require(".pulse-status-story-sound-card" in CSS and "display: none !important" in CSS, "extra sound card HUD is hidden")
    require(".pulse-status-story-advance-hint" in CSS and "display: none !important" in CSS, "tap-to-advance hint is hidden")
    require(".pulse-status-story-actions" in CSS and "width: 54px !important" in CSS and "opacity: .9 !important" in CSS, "side reactions are compact")
    require("[data-status-story-count]" in CSS and "[data-status-viewer-count]" in CSS and "display: none !important" in CSS, "owner analytics remain hidden from public chrome")
    require("data-status-story-prev" in BOT and "data-status-story-next" in BOT and "data-status-story-close" in BOT, "home story viewer keeps navigation controls")
    require("data-status-viewer-prev" in BOT and "data-status-viewer-next" in BOT and "data-status-viewer-close" in BOT, "Status page viewer keeps navigation controls")
    require("display: none !important" in CSS and ".pulse-status-story-nav" in CSS, "visible next/back buttons are hidden")
    require("button.hidden=false" in BOT, "Sound control stays available after unmuting")
    require("revealStatusChrome" in JS, "viewer can briefly reveal lightweight controls")
    require("status-immersive-story-20260621a" in BOT, "status CSS/JS cache keys are bumped")
    require("padding:42px16px16px" not in compact and "linear-gradient(180deg,transparent,rgba(2,6,23,.88))" not in compact, "old large bottom metadata panel styling is removed")


if __name__ == "__main__":
    main()
