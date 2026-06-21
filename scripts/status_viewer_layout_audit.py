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
    require("Cinematic PulseSoc Status HUD" in CSS, "viewer declares cinematic HUD design contract")
    require(".pulse-status-story-shell::before" in CSS, "viewer has subtle ambient Pulse background layer")
    require(".pulse-status-story-media::before" in CSS, "media stage has ambient glow for unused aspect space")
    require(".pulse-status-story-footer" in CSS and "bottom: max(142px" in CSS and "grid-template-columns: 58px" in CSS, "creator identity is visible in the lower HUD")
    require(".pulse-status-story-actions" in CSS and "border-radius: 999px" in CSS and "opacity: .94 !important" in CSS, "engagement actions render as a visible sci-fi side rail")
    require(".pulse-status-story-sound-card" in CSS and "data-status-hud-sound" in JS, "music/sound HUD card is generated")
    require(".pulse-status-story-wave" in CSS and "@keyframes pulseStatusWave" in CSS, "sound card has lightweight animated waveform")
    require(".pulse-status-story-advance-hint" in CSS and "data-status-hud-hint" in JS, "viewer shows subtle tap-to-advance guidance")
    require("[data-status-story-count]" in CSS and "[data-status-viewer-count]" in CSS and "display: none !important" in CSS, "owner analytics remain hidden from public chrome")
    require("data-status-story-prev" in BOT and "data-status-story-next" in BOT and "data-status-story-close" in BOT, "home story viewer keeps navigation controls")
    require("data-status-viewer-prev" in BOT and "data-status-viewer-next" in BOT and "data-status-viewer-close" in BOT, "Status page viewer keeps navigation controls")
    require("display: none !important" in CSS and ".pulse-status-story-nav" in CSS, "visible next/back buttons are hidden")
    require("button.hidden=false" in BOT, "Sound control stays available after unmuting")
    require("revealStatusChrome" in (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8"), "viewer auto-hides chrome and reveals it on interaction")
    require("status-cinematic-hud-20260621a" in BOT, "status CSS/JS cache keys are bumped")
    require("padding:42px16px16px" not in compact and "linear-gradient(180deg,transparent,rgba(2,6,23,.88))" not in compact, "old large bottom metadata panel styling is removed")


if __name__ == "__main__":
    main()
