#!/usr/bin/env python3
"""Audit mobile HLS selection for Pulse Feed, Reels, and Status videos."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
BOT = ROOT / "bot.py"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    bot = BOT.read_text(encoding="utf-8")

    expect("nativeHlsSupported(video" in renderer, "native HLS is detected from the active video element")
    expect("application/vnd.apple.mpegurl" in renderer, "Apple HLS MIME type is used")
    expect("application/x-mpegURL" in renderer, "alternate HLS MIME type is accepted")
    expect("https://stream.mux.com/${id}.m3u8" in renderer, "Mux playback IDs become canonical HLS URLs")
    expect("canonicalMuxHlsUrl || muxHlsUrlValue || item.playback_url || directUrl" in renderer, "canonical Mux HLS wins before first-party stream fallback")
    expect("nativeHlsSupported(video)" in renderer and "return;" in renderer[renderer.index("nativeHlsSupported(video)") : renderer.index("loadHlsLibrary()", renderer.index("nativeHlsSupported(video)"))], "native HLS exits before HLS.js loading")
    expect("loadHlsLibrary()" in renderer and "Hls?.isSupported?.()" in renderer, "HLS.js remains desktop/non-native fallback")
    expect("const muxHls=m.mux_playback_id?`https://stream.mux.com/${m.mux_playback_id}.m3u8`:(m.mux_hls_url||'')" in bot, "legacy feed renderer forces canonical Mux HLS")
    expect("src.includes('.m3u8')?'application/vnd.apple.mpegurl'" in bot, "legacy feed renderer marks Mux HLS MIME")


if __name__ == "__main__":
    main()
