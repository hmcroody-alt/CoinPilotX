#!/usr/bin/env python3
"""Audit deterministic Pulse media hydration across device render paths."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import embed_service, media_service, pulse_feed_engine  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    feed_source = (ROOT / "services/pulse_feed_engine.py").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")

    expect((ROOT / "services/embed_service.py").exists(), "canonical embed service exists")
    expect("pulse_media_renderer.js" in bot_source, "Pulse pages load deterministic media renderer")
    expect(
        "pulse_media_renderer.js?v=video-stage-size-20260605" in bot_source
        or "pulse_media_renderer.js?v=foundation-20260528" in bot_source,
        "Pulse media renderer is cache-busted",
    )
    expect(
        "pulse_desktop_feed.css?v=pulse-polish-20260603" in bot_source
        or "pulse_desktop_feed.css?v=edge-20260527" in bot_source,
        "Pulse desktop CSS is cache-busted",
    )
    expect("PulseMediaRenderer?.hydrate" in bot_source, "feed hydrates media after DOM inserts")
    expect("data-media-url" in bot_source and "data-media-id" in bot_source, "media DOM has canonical data attributes")
    for key in ["type", "media_url", "thumbnail_url", "poster_url", "width", "height", "aspect_ratio", "mime_type", "embed_type", "source_platform", "preload_priority"]:
        expect(f'"{key}"' in feed_source, f"feed media schema includes {key}")
    for token in ["IntersectionObserver", "MAX_RETRIES", "is-broken", "is-ready", "data-retry-media"]:
        expect(token in renderer, f"renderer supports {token}")
    expect("naturalWidth === 0" in renderer and "failMedia(wrap, media)" in renderer, "renderer retries images that failed before hydration")
    expect('localStorage?.getItem("pulseDebugMedia")' in renderer, "desktop media debug logging is available behind a local flag")
    desktop_css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
    expect("aspect-ratio: var(--media-ratio" in desktop_css, "desktop media reserves deterministic aspect-ratio space")
    expect("min-height: 180px" in desktop_css, "desktop media container cannot collapse to a blank shell")

    resolved = media_service.resolve_media({"media_url": "/static/img/media-unavailable.svg", "media_type": "image"})
    expect("media_url" in resolved and "thumbnail_url" in resolved and "poster_url" in resolved, "media service returns canonical URL fields")
    import os
    os.environ.setdefault("R2_BUCKET", "pulse-media2")
    os.environ.setdefault("R2_PUBLIC_BASE_URL", "https://cdn.coinpilotx.app")
    private_r2 = "https://39a18808eac44f79a4eccd35558151e6.r2.cloudflarestorage.com/pulse-media2/pulse_media/audit.jpg"
    private_resolved = media_service.resolve_media({"media_url": private_r2, "media_type": "image", "is_available": 1})
    expect(private_resolved.get("media_url") == "https://cdn.coinpilotx.app/pulse_media/audit.jpg", "private R2 endpoint is mapped to CDN URL", str(private_resolved))

    sample = embed_service.embed_from_text("Look at https://example.com/photo.jpg for the chart")
    expect(sample.get("embed_type") == "image", "embed service detects image URLs", str(sample))
    expect(sample.get("source_platform") == "example", "embed service normalizes source platform", str(sample))

    payload = pulse_feed_engine._canonical_media_payload({"id": 123}, resolved, index=0)
    expect(payload.get("preload_priority") == "high", "first media gets high preload priority", str(payload))
    expect(all(k in payload for k in ["media_url", "valid_url", "thumbnail_url", "poster_url", "mime_type", "source_platform"]), "canonical payload is complete", str(payload))

    print("cross-device feed audit ok")


if __name__ == "__main__":
    main()
