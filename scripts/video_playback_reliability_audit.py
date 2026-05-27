#!/usr/bin/env python3
"""Audit Pulse video upload, CDN URL, playback markup, and renderer diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage, upload_progress_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    renderer = (ROOT / "static" / "js" / "pulse_media_renderer.js").read_text(encoding="utf-8")
    upload_service = (ROOT / "services" / "upload_progress_service.py").read_text(encoding="utf-8")
    storage = (ROOT / "services" / "media_storage.py").read_text(encoding="utf-8")
    desktop_css = (ROOT / "static" / "css" / "pulse_desktop_feed.css").read_text(encoding="utf-8")

    for token in [
        "<video muted controls playsinline",
        "<source src=",
        "data-media-mime",
        "preload=\"metadata\"",
        "media-kind-",
        "Trace media-",
    ]:
        expect(token in source, f"feed video markup includes {token}")

    for token in [
        "function setMediaSource",
        "media.querySelector(\"source\")",
        "loadedmetadata",
        "Pulse video diagnostic",
        "Pulse video CDN HEAD",
        "private R2 URL",
        "content-type",
        "accept-ranges",
        "videoErrorDetails",
    ]:
        expect(token in renderer, f"video renderer diagnostic/retry support includes {token}")

    for token in [
        "PULSE_UPLOAD_STAGE_START",
        "PULSE_UPLOAD_STAGE_COMPLETE",
        "mime_type=%s",
        "storage_key=%s",
        "valid_url=%s",
        "MEDIA_ALLOW_UNTRANSCODED_MOV",
    ]:
        expect(token in upload_service, f"upload diagnostics include {token}")

    for token in ["ContentType", "CacheControl", "MEDIA_R2_UPLOAD_COMPLETE", "MEDIA_R2_UPLOAD_FAILED"]:
        expect(token in storage, f"R2 upload path logs/verifies {token}")

    expect("pulse-cinematic-media-shell" in source + desktop_css, "cinematic shell wraps feed media")
    expect("pulse-media-backdrop" in source + desktop_css, "cinematic shell has blurred media backdrop layer")
    expect("pulse-media-aura" in source + desktop_css, "cinematic shell has glow aura layer")
    expect("pulse-media-depth-layer" in source + desktop_css, "cinematic shell has depth gradient layer")

    resolved = media_service.resolve_media(
        {
            "id": 54,
            "media_type": "video",
            "mime_type": "video/mp4",
            "storage_key": "pulse_media/audit/video.mp4",
            "media_url": "pulse_media/audit/video.mp4",
        }
    )
    if media_storage.provider() == "r2":
        expect((resolved.get("media_url") or "").startswith("https://cdn.coinpilotx.app/"), "R2 video storage key resolves to CDN URL", str(resolved))
    else:
        expect(bool(resolved.get("media_url")), "video storage key resolves to render URL", str(resolved))

    validation = upload_progress_service.validate_media_file(
        type(
            "FileStub",
            (),
            {
                "filename": "clip.mp4",
                "mimetype": "video/mp4",
                "stream": type(
                    "StreamStub",
                    (),
                    {
                        "_pos": 0,
                        "seek": lambda self, pos, whence=0: setattr(self, "_pos", pos),
                        "tell": lambda self: 1024,
                    },
                )(),
            },
        )()
    )
    expect(validation.get("ok") and validation.get("media_type") == "video", "MP4 upload validates as video", str(validation))
    print("video playback reliability audit ok")


if __name__ == "__main__":
    main()
