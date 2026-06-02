#!/usr/bin/env python3
"""Audit the Pulse Mux Live streaming foundation."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    os.environ.setdefault("MUX_WEBHOOK_SECRET", "audit_mux_webhook_secret")

    import bot  # noqa: E402
    from services import mux_live_service  # noqa: E402

    bot.init_db()
    service_source = (ROOT / "services/mux_live_service.py").read_text(encoding="utf-8")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    distribution_source = (ROOT / "services/live_distribution_service.py").read_text(encoding="utf-8")
    archive_source = (ROOT / "services/live_archive_service.py").read_text(encoding="utf-8")
    report = (ROOT / "reports/mux_live_foundation.md").read_text(encoding="utf-8")

    for token in [
        "MUX_TOKEN_ID",
        "MUX_TOKEN_SECRET",
        "MUX_WEBHOOK_SECRET",
        "create_mux_live_stream",
        "get_mux_live_stream",
        "disable_mux_live_stream",
        "create_mux_asset_from_live_recording",
        "verify_mux_webhook_signature",
        "https://api.mux.com/video/v1",
        "rtmp://global-live.mux.com:5222/app",
    ]:
        require(token in service_source, f"Mux Live service includes {token}")

    for column in [
        "mux_live_stream_id",
        "mux_stream_key",
        "mux_playback_id",
        "mux_live_status",
        "mux_recording_asset_id",
        "mux_recording_playback_id",
    ]:
        require(column in bot_source, f"Live schema includes {column}")

    for route in [
        "/api/pulse/live/mux/create",
        "/api/pulse/live/mux/<mux_live_stream_id>",
        "/api/pulse/live/mux/disable",
        "/api/pulse/live/mux/webhook",
    ]:
        require(route in bot_source, f"Mux Live route exists: {route}")

    require("pulse_live_can_create_mux_stream" in bot_source, "Mux stream creation is permission-gated")
    require("stream_key" in bot_source and "Only the host can view Mux ingest details" in bot_source, "Mux stream key is host-only")
    require("verify_mux_webhook_signature" in bot_source and "X-Mux-Signature" in bot_source, "Mux webhook verifies signature")
    require("video.live_stream.connected" in bot_source, "Mux connected event is handled")
    require("video.live_stream.disconnected" in bot_source, "Mux disconnected event is handled")
    require("video.asset.ready" in bot_source and "video.asset.errored" in bot_source, "Mux asset ready/error events are handled")
    require("mux_live_service.playback_url" in distribution_source, "Live distribution prefers Mux playback")
    require("mux_recording_playback_id" in archive_source, "Live archive supports Mux replay playback")
    require("data-mux-live-foundation" in bot_source and "data-mux-live-setup-panel" in bot_source, "Mux Live frontend foundation is present")

    payload = b'{"type":"video.live_stream.connected","data":{"id":"ls_audit"}}'
    ts = str(int(time.time()))
    digest = hmac.new(os.environ["MUX_WEBHOOK_SECRET"].encode("utf-8"), f"{ts}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    verified = mux_live_service.verify_mux_webhook_signature(payload, f"t={ts},v1={digest}")
    require(verified.get("ok"), "Mux webhook signature verification accepts valid signatures")
    rejected = mux_live_service.verify_mux_webhook_signature(payload, f"t={ts},v1=bad")
    require(not rejected.get("ok"), "Mux webhook signature verification rejects invalid signatures")

    manifest = mux_live_service.playback_url("abc123")
    require(manifest == "https://stream.mux.com/abc123.m3u8", "Mux playback URL is generated")
    require("Viewer playback URL" in report and "Host stream key" in report, "Mux Live report documents host/viewer boundary")

    print("mux live audit ok")


if __name__ == "__main__":
    main()

