#!/usr/bin/env python3
"""Static audit for Pulse video Mux playback migration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    media_service = (ROOT / "services/media_service.py").read_text(encoding="utf-8")
    feed_engine = (ROOT / "services/pulse_feed_engine.py").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    report = (ROOT / "reports/mux_migration_truth.md").read_text(encoding="utf-8")

    for token in [
        "create_mux_asset_from_url",
        "https://api.mux.com/video/v1/assets",
        "mux_asset_id",
        "mux_playback_id",
        "mux_status",
        "https://stream.mux.com/{safe_id}.m3u8",
    ]:
        require(token in media_service, f"media service includes {token}")

    require('"playback_url": mux_playback_url or saved_playback_url or first_party_stream or source' in media_service, "Mux playback is preferred before R2 fallback")
    require("mux.get(\"asset_id\")" in media_service and "mux.get(\"playback_id\")" in media_service, "Mux asset and playback ids are stored")

    for token in ["mux_asset_id", "mux_playback_id", "mux_status", "mux_hls_url", "playback_mime_type"]:
        require(token in feed_engine, f"feed payload preserves {token}")

    require("loadHlsLibrary" in renderer, "shared renderer can load HLS.js")
    require("application/vnd.apple.mpegurl" in renderer, "shared renderer marks HLS playback MIME")
    require("muxHlsUrlValue || item.playback_url || directUrl" in renderer, "shared renderer prefers Mux HLS playback")
    require("attachHlsPlayback(wrap, media)" in renderer, "shared renderer attaches HLS playback during hydration")

    require("muxHls||m.playback_url||m.valid_url" in bot, "Feed video source prefers Mux/playback URL before raw media")
    require("media.playback_mime_type||(media.mux_hls_url?'application/vnd.apple.mpegurl'" in bot, "Reels/Status pass HLS MIME metadata")
    require('("mux_status", "TEXT")' in bot, "database schema adds mux_status")
    require("pulse_media_assets" in bot and "mux_playback_id" in bot, "media asset index stores Mux metadata")

    require("R2 should no longer be the primary user-facing playback path" in report, "Mux migration truth report documents R2 fallback boundary")
    print("mux migration audit ok")


if __name__ == "__main__":
    main()
