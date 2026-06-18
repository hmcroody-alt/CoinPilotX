#!/usr/bin/env python3
"""Audit homepage PulseSoc Status viewer sound and navigation behavior."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text(encoding="utf-8")
viewer = (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


require("function unmuteStatusStoryVideo()" in bot, "homepage Status viewer can unmute on direct media tap")
require("status-story-user-unmute" in bot, "homepage Status unmute uses explicit user action reason")
require("setSoundEnabled?.(true)" in bot, "homepage Status unmute persists shared sound preference")
require("data-status-story-media" in bot and "unmuteStatusStoryVideo();" in bot, "homepage Status media tap routes to unmute before navigation")
require("window.PulseStatusViewer?.scheduleStoryProgress?.(statusStoryViewer)" in bot, "homepage Status viewer schedules auto-next progress")
require("data-status-story-next" in bot and "renderStatusStory(statusDraft.storyIndex+1)" in bot, "homepage Status viewer has next-story button wiring")
require("absX >= 52" in viewer and "navigateStory(dx < 0 ? 1 : -1)" in viewer, "shared Status viewer supports horizontal swipe navigation")
require("video.addEventListener(\"ended\", () => navigateStory(1)" in viewer, "video Status auto-advances when playback ends")
require("function unmuteViewerVideo" in viewer and "unmuteViewerVideo(viewer)" in viewer, "shared Status viewer tap-to-unmute is active")

print("pulse home status interaction audit ok")
