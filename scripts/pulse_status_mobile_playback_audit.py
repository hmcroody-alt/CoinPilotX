#!/usr/bin/env python3
"""Audit Status mobile video playback without regressing text/photo statuses."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")

    for token in [
        "data-status-viewer",
        "statusViewerMediaHtml",
        "PulseMediaRenderer.renderMedia",
        "PulseMediaRenderer?.hydrate(viewer)",
        "PulseMediaRenderer?.playVisibleVideo",
        "controls autoplay muted playsinline webkit-playsinline preload=\"metadata\"",
    ]:
        expect(token in bot, f"Status viewer includes {token}")

    expect("pulse-status-card-media" in bot, "photo/video status cards still render previews")
    expect("pulse-status-card-text" in bot and "text-story" in bot, "text status remains readable")
    expect("nativeHlsSupported(video)" in renderer, "Status videos inherit native mobile HLS")
    expect("video.muted = !shouldTrySound" in renderer, "Status autoplay can fall back to muted playback")


if __name__ == "__main__":
    main()
