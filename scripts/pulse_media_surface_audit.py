#!/usr/bin/env python3
"""Audit shared Pulse media renderer coverage across core surfaces."""

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

    for token in [
        "function normalizeMedia",
        "function renderMedia",
        "media-kind-${media.type}",
        "media.type === \"audio\"",
        "pulse-media-fallback",
        "data-retry-media",
        "data-media-cdn",
        "data-media-has-audio",
        "data-media-mux-playback-id",
        "mediaDebugEnabled",
    ]:
        expect(token in renderer, f"shared renderer supports {token}")

    for token in [
        "resolve_media",
        "media_type",
        "media_url",
        "cdn_url",
        "thumbnail_url",
        "poster_url",
        "mux_playback_id",
        "mime_type",
        "duration",
        "width",
        "height",
        "has_audio",
        "created_at",
    ]:
        expect(token in service, f"canonical media service exposes {token}")

    for token in [
        "/pulse",
        "/pulse/status",
        "/pulse/profile",
        "/pulse/messages",
        "/pulse/groups",
        "/pulse/reels",
        "/api/pulse/media/upload",
    ]:
        expect(token in source, f"Pulse media surface present: {token}")

    resolved = media_service.resolve_media({"media_url": "/missing/example.mp4", "media_type": "video", "has_audio": 0})
    expect(resolved["media_type"] == "video", "video type preserved")
    expect(resolved["has_audio"] is False, "false has_audio survives normalization")
    expect(resolved["fallback_url"].endswith("media-unavailable.svg"), "fallback asset returned")

    client = bot.webhook_app.test_client()
    for route in ["/pulse", "/pulse/status", "/pulse/reels"]:
        response = client.get(route)
        expect(response.status_code in {200, 302}, f"{route} responds", f"HTTP {response.status_code}")

    print("pulse media surface audit ok")


if __name__ == "__main__":
    main()
