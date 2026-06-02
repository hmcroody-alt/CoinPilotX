#!/usr/bin/env python3
"""Audit Pulse Status viewer and video playback integration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"
MEDIA_CSS = ROOT / "static/css/pulse_cinematic_media.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    status_css = STATUS_CSS.read_text(encoding="utf-8")
    media_css = MEDIA_CSS.read_text(encoding="utf-8")

    for token in [
        "data-status-viewer",
        "openStatusViewer(",
        "renderStatusViewer(",
        "statusViewerMediaHtml",
        "data-status-viewer-progress",
        "data-status-viewer-prev",
        "data-status-viewer-next",
        "PulseMediaRenderer.renderMedia",
        "PulseMediaRenderer?.hydrate(viewer)",
        "PulseMediaRenderer?.playVisibleVideo",
    ]:
        expect(token in bot, f"status viewer includes {token}")

    expect("Status opened." not in bot, "status cards do not show toast-only open behavior")
    expect("pulse-status-card-media" in bot, "status cards show readable previews before opening")
    expect("pulse-media-surface-status" in media_css, "status viewer uses shared media surface styling")
    compact_status_css = "".join(status_css.split())
    expect(".pulse-status-story-viewer" in status_css and "position:fixed" in compact_status_css, "status viewer is a full-screen overlay")
    expect("flex: 0 0 auto" in status_css and ".pulse-status-full-tabs" in status_css, "mobile status tabs avoid squeezed/cut-off buttons")


if __name__ == "__main__":
    main()
