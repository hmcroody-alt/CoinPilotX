#!/usr/bin/env python3
"""Audit Pulse media metadata, resilient rendering, and fallback rules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage, pulse_feed_engine  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    service = (ROOT / "services/media_service.py").read_text(encoding="utf-8")
    storage = (ROOT / "services/media_storage.py").read_text(encoding="utf-8")
    for token in [
        "Media could not load.",
        "Media is being restored.",
        "data-retry-media",
        "loading=\"lazy\"",
        "decoding=\"async\"",
        "preload=\"metadata\"",
        "data-fit=\"smart\"",
        "srcset",
    ]:
        expect(token in source + service, f"media rendering rule present: {token}")
    cinematic_css = (
        (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
        + "\n"
        + (ROOT / "static/css/pulse_cinematic_media.css").read_text(encoding="utf-8")
    )
    cinematic_js = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    for token in [
        "pulse-cinematic-media-shell",
        "pulse-media-aura",
        "pulse-media-backdrop",
        "pulse-media-depth-layer",
        "--media-backdrop",
        "applyAmbientColor",
        "bindVideoAmbient",
        "Pulse video diagnostic",
        "Pulse video CDN HEAD",
        "setMediaSource",
        "data-media-mime",
        "<source src=",
        "Trace media-",
        "pulseMediaBreath",
        "pulseGalaxyDepth",
        "pulseGalaxyStreak",
        "radial-gradient(ellipse at 50% 105%",
        "pulse-media-galaxy-shell",
        "pulse-media-ambient-shell",
        "pulse-media-soft-glow",
        "pulse-media-color-orb",
        "pulse-media-vignette",
        "pulseAmbientBackdropDrift",
        "pulseAmbientGlowBreath",
        "pulseAmbientAuraBreath",
        "pulse-cinematic-media-css",
        "--pulse-media-secondary-rgb",
        "--pulse-media-accent-rgb",
        "--pulse-card-media-bleed",
        "edge-20260527",
        "border: 0 !important",
    ]:
        expect(token in source + cinematic_css + cinematic_js, f"cinematic media atmosphere present: {token}")
    upload_progress = (ROOT / "services/upload_progress_service.py").read_text(encoding="utf-8")
    storage = (ROOT / "services/media_storage.py").read_text(encoding="utf-8")
    for token in [
        "MOV videos need conversion before posting",
        "MEDIA_ALLOW_UNTRANSCODED_MOV",
        "_content_type_for_upload",
        "ContentType",
    ]:
        expect(token in upload_progress + storage, f"video upload hardening present: {token}")
    for token in [
        "def resolve_media",
        "def normalize_url",
        "def local_path_for_url",
        "is_available",
        "fallback_url",
        "R2_PUBLIC_BASE_URL",
        "CacheControl",
        "MEDIA_REQUIRE_DURABLE_UPLOAD",
    ]:
        expect(token in service + storage, f"canonical durable media primitive present: {token}")
    resolved = media_service.resolve_media({"media_url": "/definitely/missing/pulse-media.jpg", "media_type": "image"})
    expect(resolved["is_available"] is False, "missing local media resolves unavailable")
    expect(resolved["fallback_url"].endswith("media-unavailable.svg"), "fallback asset returned")
    expect(media_storage.storage_status().get("provider") in {"local", "r2", "s3"}, "storage provider status loads")
    media_css = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")
    for token in ["object-fit: contain", "data-orientation"]:
        expect(token in source + media_css, f"adaptive media/Reels rule present: {token}")
    for token in ["Adaptive playback", "poster first"]:
        expect(token not in source, f"internal Reels implementation text hidden: {token}")
    expect("@webhook_app.route(\"/admin/media-health\"" in source, "admin media health route exists")
    visible, reason = pulse_feed_engine.pulse_visibility_decision({"visibility": "public", "moderation_status": "approved", "status": "published", "deleted_at": None})
    expect(visible and reason == "public_approved", "canonical public visibility allows approved public posts")
    hidden, reason = pulse_feed_engine.pulse_visibility_decision({"visibility": "public", "moderation_status": "approved", "status": "published", "deleted_at": "2026-01-01"})
    expect(not hidden and reason == "deleted", "canonical visibility blocks deleted media posts")
    print("pulse media audit ok")


if __name__ == "__main__":
    main()
