#!/usr/bin/env python3
"""Audit Pulse Status viewer and video playback integration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"
MEDIA_CSS = ROOT / "static/css/pulse_cinematic_media.css"
SHARED_VIEWER = ROOT / "static/js/pulse_status_viewer.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    status_css = STATUS_CSS.read_text(encoding="utf-8")
    media_css = MEDIA_CSS.read_text(encoding="utf-8")
    shared_viewer = SHARED_VIEWER.read_text(encoding="utf-8")

    for token in [
        "data-status-viewer",
        "openStatusViewer(",
        "renderStatusViewer(",
        "statusViewerMediaHtml",
        "statusPreviewMediaHtml",
        "hydrateStatusCardVideos",
        "data-status-preview-seconds=\"10\"",
        "data-status-viewer-progress",
        "data-status-viewer-prev",
        "data-status-viewer-next",
        "PulseMediaRenderer.renderMedia",
        "PulseMediaRenderer?.hydrate(viewer)",
        "PulseMediaRenderer?.playVisibleVideo",
    ]:
        expect(token in bot, f"status viewer includes {token}")

    expect("media.mux_playback_id?`https://stream.mux.com/${media.mux_playback_id}.m3u8`" in bot, "status viewer forces Mux HLS before raw media")
    expect("Status opened." not in bot, "status cards do not show toast-only open behavior")
    expect("pulse-status-card-media" in bot, "status cards show readable previews before opening")
    expect("pulse-status-open-cue" in status_css and "Tap to open" in bot, "status cards show an easy open cue")
    expect("pulse-status-card-video-preview" in bot, "video status cards use teaser preview playback")
    expect("pulse-media-surface-status" in media_css, "status viewer uses shared media surface styling")
    expect("window.PulseStatusViewer" in shared_viewer, "shared StatusViewer component is available")
    expect("window.PulseStatusViewer?.render(item)" in bot, "home and dedicated viewers delegate to shared StatusViewer")
    expect(bot.count("window.PulseStatusViewer?.render(item)") >= 2, "both Status entry points use the shared renderer")
    expect("height: min(90dvh, 900px)" in status_css and "aspect-ratio: 9 / 16" in status_css, "desktop viewer uses a large story frame")
    expect("width: 100%" in status_css and "min-height: 100%" in status_css, "text status fills the story frame")
    for action in ["Like", "Comment", "Share", "Save", "More"]:
        expect(f">{action}<" in bot, f"status viewer exposes {action} action")
    compact_status_css = "".join(status_css.split())
    expect(".pulse-status-story-viewer" in status_css and "position:fixed" in compact_status_css, "status viewer is a full-screen overlay")
    expect("flex: 0 0 auto" in status_css and ".pulse-status-full-tabs" in status_css, "mobile status tabs avoid squeezed/cut-off buttons")


if __name__ == "__main__":
    main()
