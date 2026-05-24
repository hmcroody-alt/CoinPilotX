#!/usr/bin/env python3
"""Audit Pulse Camera media upload, R2/CDN resolver integration, and render safety."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from werkzeug.datastructures import FileStorage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name, email, created_at) VALUES (?, ?, ?, ?, ?)",
        (950021, "pulse_camera_media_audit", "Pulse Camera Media Audit", "pulse-camera-media-audit@example.test", now),
    )
    conn.commit()
    conn.close()


def main():
    bot.init_db()
    ensure_user()
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
        b"\xff?\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    upload = FileStorage(stream=BytesIO(tiny_png), filename="pulse-camera-audit.png", content_type="image/png")
    result, status = media_service.save_upload(950021, upload, context_type="pulse_camera", context_id="audit")
    expect(status in {200, 201}, "camera media upload accepted", str(result))
    expect(result.get("ok"), "media service returned ok")
    media = result.get("media") or {}
    expect(bool(media.get("media_url")), "canonical media URL returned")
    expect(not str(media.get("media_url")).startswith("/Users/"), "no local filesystem path leaks")
    resolved = media_service.resolve_media(media)
    expect(bool(resolved.get("fallback_url")), "fallback URL available")
    expect(resolved.get("media_type") in {"image", "video", "file", ""}, "media type resolved")
    storage = media_storage.storage_status()
    expect("provider" in storage, "storage provider status available")
    source = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")
    for token in ["context_type\", \"pulse_camera", "filter_name", "effect_key", "data-publish-destination"]:
        expect(token in source, f"camera upload metadata token exists: {token}")
    print("pulse camera media pipeline audit ok")


if __name__ == "__main__":
    main()
