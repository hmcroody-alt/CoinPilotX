#!/usr/bin/env python3
"""Audit the shared Pulse video player contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
MEDIA_CSS = ROOT / "static/css/pulse_cinematic_media.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    css = MEDIA_CSS.read_text(encoding="utf-8")

    for token in [
        "pulseMediaSoundEnabled",
        "data-pulse-video-player",
        "pulse-unified-video-player",
        "IntersectionObserver",
        "playVisibleVideo",
        "pauseOtherVideos",
        "preloadNextVideo",
        "metadataCache",
        "showSoundPrompt",
        "setSoundEnabled",
        "soundEnabled",
        "nativeHlsSupported(video",
        "webkit-playsinline",
        "x-webkit-airplay",
        "data-media-source-type",
        "data-media-native-hls",
        "Pulse video request HEAD",
        "pulseNativeHls",
        "shouldTrySound",
        "window.PulseVideo = PulseVideo",
    ]:
        expect(token in renderer, f"shared renderer includes {token}")

    expect("https://stream.mux.com/${id}.m3u8" in renderer, "Mux playback IDs resolve to mobile-safe HLS URLs")
    expect("canonicalMuxHlsUrl || muxHlsUrlValue || item.playback_url || directUrl" in renderer, "video playback prefers canonical Mux HLS before first-party fallback")
    expect("nativeHlsSupported(video)" in renderer and "loadHlsLibrary()" in renderer, "native HLS is preferred before HLS.js")
    expect("data-pulse-media-sound hidden" in renderer, "tap-for-sound starts as a small hidden prompt")
    expect("setTimeout(() =>" in renderer and "3200" in renderer, "tap-for-sound prompt auto-hides")
    expect("isPulseStreamUrl(src)" in renderer, "first-party stream URLs are not cache-busted on retry")
    expect("retry=" in renderer and "isPulseStreamUrl(src)" in renderer, "non-stream media retry remains available")
    expect("video-frame-repair-20260603" in renderer + bot, "updated asset cache keys are wired")
    expect(".pulse-unified-video-player" in css, "unified video CSS class is styled")

    stream_start = bot.index('def api_pulse_media_stream')
    stream_end = bot.index('@webhook_app.route("/api/pulse/media/process"', stream_start)
    stream = bot[stream_start:stream_end]
    for token in [
        "PULSE_MEDIA_STREAM_MAX_CHUNK_BYTES",
        "Accept-Ranges",
        "Content-Range",
        "head_object(storage_key)",
        "get_object(storage_key, byte_range=byte_range)",
        "iter_chunks(chunk_size=1024 * 256)",
    ]:
        expect(token in stream, f"stream route supports {token}")
    expect("init_db()" not in stream, "stream route does not run schema initialization")


if __name__ == "__main__":
    main()
