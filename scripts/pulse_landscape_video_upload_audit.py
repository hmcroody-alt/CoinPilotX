#!/usr/bin/env python3
"""Audit landscape video upload and Mux playback readiness."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
MEDIA_SERVICE = ROOT / "services/media_service.py"
MEDIA_RENDERER = ROOT / "static/js/pulse_media_renderer.js"
MEDIA_CSS = ROOT / "static/css/pulse_cinematic_media.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    service = MEDIA_SERVICE.read_text(encoding="utf-8")
    renderer = MEDIA_RENDERER.read_text(encoding="utf-8")
    css = MEDIA_CSS.read_text(encoding="utf-8")

    for token in ["video/mp4", "video/webm", "video/quicktime", ".mov", ".m4v"]:
        expect(token in service or token in bot, f"Upload pipeline accepts {token}")

    for token in [
        "MUX_SOURCE_BASE_URL",
        "R2_MUX_SOURCE_BASE_URL",
        "MUX_SOURCE_FETCH_STATUS",
        "PULSE_MUX_ASSET_CREATED",
        "mux_playback_id",
        "https://stream.mux.com/",
    ]:
        expect(token in service or token in bot or token in renderer, f"Mux upload/playback includes {token}")

    for token in [
        "data-media-orientation",
        "data-media-aspect-ratio",
        "landscape",
        "portrait",
        "ultrawide",
    ]:
        expect(token in renderer, f"Renderer preserves aspect metadata: {token}")

    expect(".pulse-media-wrap.is-landscape" in css, "Landscape feed video layout is explicit")
    expect(".pulse-media-wrap.is-portrait" in css, "Portrait feed video layout is explicit")


if __name__ == "__main__":
    main()
