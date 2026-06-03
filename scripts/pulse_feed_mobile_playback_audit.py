#!/usr/bin/env python3
"""Audit Feed mobile video playback without affecting photos."""

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
        "function mediaHtml(items)",
        "pulse-unified-video-player",
        "data-pulse-video-player",
        "muted controls playsinline webkit-playsinline preload",
        "window.PulseMediaRenderer?.hydrate(document)",
        "data-media-source-type",
    ]:
        expect(token in bot + renderer, f"Feed video path includes {token}")

    expect("const muxHls=m.mux_hls_url||(m.mux_playback_id?`https://stream.mux.com/${m.mux_playback_id}.m3u8`:'')" in bot, "Feed videos prefer Mux HLS on mobile")
    expect("nativeHlsSupported(video)" in renderer and "loadHlsLibrary()" in renderer, "Feed videos prefer native HLS before HLS.js")
    expect("<img src=\"${esc(thumb||src)}\"" in bot, "Feed photo rendering remains separate")
    expect("loading=\"${m.preload_priority==='high'?'eager':'lazy'}\" decoding=\"async\"" in bot, "Feed photos keep existing loading behavior")


if __name__ == "__main__":
    main()
