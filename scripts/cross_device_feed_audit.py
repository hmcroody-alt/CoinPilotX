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
    expect("PulseMediaRenderer?.hydrate" in bot_source, "feed hydrates media after DOM inserts")
    expect("data-media-url" in bot_source and "data-media-id" in bot_source, "media DOM has canonical data attributes")
    for key in ["type", "media_url", "thumbnail_url", "poster_url", "width", "height", "aspect_ratio", "mime_type", "embed_type", "source_platform", "preload_priority"]:
        expect(f'"{key}"' in feed_source, f"feed media schema includes {key}")
    for token in ["IntersectionObserver", "MAX_RETRIES", "is-broken", "is-ready", "data-retry-media"]:
        expect(token in renderer, f"renderer supports {token}")

    resolved = media_service.resolve_media({"media_url": "/static/img/media-unavailable.svg", "media_type": "image"})
    expect("media_url" in resolved and "thumbnail_url" in resolved and "poster_url" in resolved, "media service returns canonical URL fields")

    sample = embed_service.embed_from_text("Look at https://example.com/photo.jpg for the chart")
    expect(sample.get("embed_type") == "image", "embed service detects image URLs", str(sample))
    expect(sample.get("source_platform") == "example", "embed service normalizes source platform", str(sample))

    payload = pulse_feed_engine._canonical_media_payload({"id": 123}, resolved, index=0)
    expect(payload.get("preload_priority") == "high", "first media gets high preload priority", str(payload))
    expect(all(k in payload for k in ["media_url", "valid_url", "thumbnail_url", "poster_url", "mime_type", "source_platform"]), "canonical payload is complete", str(payload))

    print("cross-device feed audit ok")


if __name__ == "__main__":
    main()
