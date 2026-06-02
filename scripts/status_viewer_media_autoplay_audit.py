#!/usr/bin/env python3
"""Audit Pulse Status viewer and shared media autoplay wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
MEDIA_RENDERER = ROOT / "static/js/pulse_media_renderer.js"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    bot = BOT.read_text(encoding="utf-8")
    media = MEDIA_RENDERER.read_text(encoding="utf-8")
    css = STATUS_CSS.read_text(encoding="utf-8")

    require("data-status-viewer" in bot, "Pulse Status page includes a dedicated viewer modal")
    require("openStatusViewer(" in bot and "renderStatusViewer(" in bot, "Status cards open the story viewer")
    require("Status opened." not in bot, "Status card clicks no longer show only a toast")
    require("data-status-viewer-progress" in bot and "data-status-viewer-prev" in bot and "data-status-viewer-next" in bot, "Viewer includes progress and navigation controls")
    require("statusViewerMediaHtml" in bot and "<video" in bot and "<img" in bot, "Viewer supports text, photo, and video statuses")
    require("pulse-status-card-media" in bot, "Recent Status cards render readable media previews")
    require("pulseMediaSoundEnabled" in media, "Shared Pulse media sound preference is implemented")
    require("IntersectionObserver" in media and "playVisibleVideo" in media, "Feed media uses IntersectionObserver autoplay")
    require("pauseOtherVideos" in media, "Only one shared media video plays at a time")
    require("data-pulse-media-sound" in media, "Tap-for-sound control exists for feed media")
    require("pulseMediaSoundEnabled" in bot and "pulseReelsSoundEnabled" in bot, "Reels shares the global media sound preference")
    require(".pulse-status-story-viewer" in css, "Status viewer CSS is present")
    require("flex: 0 0 auto" in css and ".pulse-status-full-tabs" in css, "Mobile Status tab rail avoids squeezed buttons")


if __name__ == "__main__":
    main()
