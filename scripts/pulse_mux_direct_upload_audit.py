#!/usr/bin/env python3
"""Static audit for Pulse large-video direct Mux uploads."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    media_service = (ROOT / "services" / "media_service.py").read_text(encoding="utf-8")
    upload_js = (ROOT / "static" / "js" / "pulse_upload_manager.js").read_text(encoding="utf-8")

    require("/api/pulse/media/mux/direct-upload" in bot, "direct Mux upload start endpoint exists")
    require("/api/pulse/media/mux/direct-upload/complete" in bot, "direct Mux upload completion endpoint exists")
    require("mux_upload_id" in bot and '("mux_upload_id", "TEXT")' in bot, "chat media stores Mux upload ID")
    require("video.upload.asset_created" in bot, "Mux upload asset-created webhook updates media rows")
    require("create_mux_direct_upload" in media_service, "media service can create Mux direct uploads")
    require("get_mux_direct_upload" in media_service and "get_mux_asset" in media_service, "media service can inspect Mux upload and asset status")
    require("DIRECT_VIDEO_THRESHOLD_BYTES" in upload_js, "frontend has a large video threshold")
    require("shouldUseDirectMux" in upload_js, "frontend routes large videos away from standard upload")
    require("/api/pulse/media/mux/direct-upload" in upload_js, "frontend calls direct Mux upload endpoint")
    require("xhr.open(\"PUT\", startData.upload_url" in upload_js, "browser uploads video bytes directly to Mux")
    require("Large video uploads need Mux" in bot, "backend returns clear Mux configuration error")
    require("This upload is too large for the standard upload lane" in upload_js, "standard-lane size errors explain the direct upload lane")


if __name__ == "__main__":
    main()
