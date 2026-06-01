#!/usr/bin/env python3
"""Audit Pulse Reels playback, audio controls, retry, and media normalization."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main() -> None:
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    service = (ROOT / "services/media_service.py").read_text(encoding="utf-8")
    worker = (ROOT / "media_worker.py").read_text(encoding="utf-8")

    for token in [
        "/pulse/reels",
        "/api/pulse/reels/feed",
        "data-toggle-reel-play",
        "data-toggle-reel-sound",
        "pulseReelsSoundEnabled",
        "Tap for sound",
        "No audio track",
        "reel-progress",
        "primaryReelVideo",
        "syncPlayback",
        "retryPulseReelMedia",
        "stalled",
        "waiting",
        "loadedmetadata",
        "canplay",
        "preloadNextReel",
    ]:
        expect(token in source, f"Reels playback hook present: {token}")

    expect("IntersectionObserver" in renderer, "shared renderer uses IntersectionObserver hydration")

    for token in [
        "mux_playback_id",
        "mux_hls_url",
        "mux_thumbnail_url",
        "playback_url",
        "cdn_url",
        "duration",
        "has_audio",
        "created_at",
        "nativeHlsSupported",
    ]:
        expect(token in service + renderer, f"normalized media field present: {token}")

    for token in [
        "ffmpeg_present",
        "ffmpeg_version",
        "RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg",
        "processing_blocked",
        "pending_unavailable",
        "MEDIA_ENGINE_VIDEO_PROCESSING_BLOCKED",
    ]:
        expect(token in worker + source, f"media engine diagnostic present: {token}")

    mux = media_service.resolve_media(
        {
            "media_url": "https://cdn.coinpilotx.app/example.mp4",
            "media_type": "video",
            "mux_playback_id": "abc123",
            "has_audio": 1,
        }
    )
    expect(mux["mux_hls_url"] == "https://stream.mux.com/abc123.m3u8", "Mux HLS URL generated")
    expect(mux["mux_thumbnail_url"] == "https://image.mux.com/abc123/thumbnail.jpg", "Mux thumbnail URL generated")
    expect(mux["has_audio"] is True, "has_audio survives normalization")

    client = bot.webhook_app.test_client()
    response = client.get("/pulse/reels")
    expect(response.status_code in {200, 302}, "/pulse/reels route responds", f"HTTP {response.status_code}")
    html = response.get_data(as_text=True)
    if response.status_code == 200:
        expect("reelsFeed" in html and "reelHtml" in html, "Reels page returns player shell")

    print("pulse reels media audit ok")


if __name__ == "__main__":
    main()
