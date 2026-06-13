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
        "status-viewer-autoplay",
        "status-media-tap",
    ]:
        expect(token in bot, f"Status viewer includes {token}")

    expect("media.mux_playback_id?`https://stream.mux.com/${media.mux_playback_id}.m3u8`" in bot, "Status viewer forces Mux HLS before raw media")
    expect("pulse-status-card-media" in bot, "photo/video status cards still render previews")
    expect("pulse-status-card-text" in bot and "text-story" in bot, "text status remains readable")
    expect("nativeHlsSupported(video)" in renderer, "Status videos inherit native mobile HLS")
    expect("window.PulseMediaRenderer?.soundEnabled?.()!==false" in bot, "Status viewer follows saved sound preference when actively opened")
    expect("video.defaultMuted=false" in bot and "video.removeAttribute('muted')" in bot, "Status viewer clears muted default before active playback")
    expect("player.defaultMuted=true" in bot and "player.muted=true" in bot, "Status card video previews stay muted")
    expect("setVideoMuted(video, !shouldTrySound, \"autoplay\")" in renderer, "General media preserves browser autoplay fallback")


if __name__ == "__main__":
    main()
