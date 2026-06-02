#!/usr/bin/env python3
"""Audit Pulse image/video upload staging for posts, Reels, and Status."""

from __future__ import annotations

import base64
import os
import sys
import time
from io import BytesIO
from pathlib import Path

from werkzeug.datastructures import FileStorage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage, upload_progress_service  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
WEBM_BYTES = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def file_storage(name: str, content_type: str, data: bytes) -> FileStorage:
    return FileStorage(stream=BytesIO(data), filename=name, content_type=content_type)


AUDIT_USER_ID = 900000 + int(time.time()) % 100000


def assert_upload(name: str, content_type: str, data: bytes, context: str, expected_type: str):
    result, status = upload_progress_service.stage_upload(AUDIT_USER_ID, file_storage(name, content_type, data), context_type=context, context_id="audit")
    expect(status == 200 and result.get("ok"), f"{context} {expected_type} upload stages", str(result))
    media = result.get("media") or {}
    expect(media.get("media_type") == expected_type, f"{context} stores {expected_type} media type", str(media))
    expect(media.get("media_url") or media.get("valid_url"), f"{context} returns canonical media URL", str(media))
    expect(result.get("progress", {}).get("percent") == 100, f"{context} returns complete progress payload")
    expect(any(s.get("stage") == "processing" for s in result.get("stages") or []), f"{context} includes processing state")
    expect(any(s.get("stage") == "publishing" for s in result.get("stages") or []), f"{context} includes publishing state")
    if media_storage.provider() == "r2":
        base = os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/")
        expect(bool(base), "R2 public base URL configured")
        expect((media.get("valid_url") or media.get("media_url") or "").startswith(base), f"{context} URL uses R2 CDN", str(media))
    return media


def main():
    bot.init_db()
    expect("mp4" in media_service.VIDEO_EXTS and "webm" in media_service.VIDEO_EXTS, "media service allows safe video extensions")
    assert_upload("pulse-audit.png", "image/png", PNG_BYTES, "pulse", "image")
    assert_upload("pulse-audit.webm", "video/webm", WEBM_BYTES, "pulse", "video")
    assert_upload("status-audit.png", "image/png", PNG_BYTES, "pulse_status", "image")
    assert_upload("status-audit.webm", "video/webm", WEBM_BYTES, "pulse_status", "video")
    assert_upload("reel-audit.webm", "video/webm", WEBM_BYTES, "pulse_reel", "video")
    mov_result, mov_status = upload_progress_service.stage_upload(
        AUDIT_USER_ID + 91,
        file_storage("status-audit.mov", "video/quicktime", b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  "),
        context_type="pulse",
        context_id="audit",
    )
    expect(mov_status == 200 and mov_result.get("ok"), "MOV uploads are accepted as Pulse video media", str(mov_result))
    expect((mov_result.get("media") or {}).get("media_type") == "video", "MOV upload stores video media type", str(mov_result))
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("/api/pulse/reels/create" in source, "Reel creation endpoint exists")
    expect("/api/pulse/status" in source, "Status creation endpoint exists")
    expect("/api/pulse/posts" in source, "Pulse post endpoint exists")
    print("pulse video upload audit ok")


if __name__ == "__main__":
    main()
