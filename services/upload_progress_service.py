"""Unified upload progress and media staging helpers for Pulse publishing."""

from __future__ import annotations

import os
import secrets
from typing import Any

from . import media_service, media_storage


VIDEO_MIME_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-m4v"}
IMAGE_MIME_PREFIX = "image/"
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}


def trace_id() -> str:
    return secrets.token_hex(6)


def progress_payload(stage: str, percent: int, message: str, *, trace_id: str = "", retryable: bool = False, media: dict | None = None) -> dict:
    payload = {
        "stage": stage,
        "percent": max(0, min(int(percent or 0), 100)),
        "message": message,
        "trace_id": trace_id or globals()["trace_id"](),
        "retryable": bool(retryable),
    }
    if media is not None:
        payload["media"] = media
    return payload


def default_stages(media_type: str = "media") -> list[dict[str, Any]]:
    label = "video" if media_type == "video" else "image" if media_type in {"image", "gif"} else "media"
    return [
        {"stage": "starting", "percent": 3, "message": "Upload starting..."},
        {"stage": "uploading", "percent": 76, "message": f"Uploading {label}..."},
        {"stage": "processing", "percent": 88, "message": "Processing media..."},
        {"stage": "publishing", "percent": 96, "message": "Publishing..."},
        {"stage": "complete", "percent": 100, "message": "Posted successfully"},
    ]


def validate_media_file(file_storage) -> dict:
    if not file_storage or not getattr(file_storage, "filename", ""):
        return {"ok": False, "message": "Choose an image or video to upload.", "status": 400}
    filename = file_storage.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_type = (getattr(file_storage, "mimetype", "") or "").lower()
    media_type = "video" if ext in VIDEO_EXTENSIONS or mime_type in VIDEO_MIME_TYPES else "image"
    if media_type == "video" and mime_type and not (mime_type.startswith("video/") or mime_type == "application/octet-stream"):
        return {"ok": False, "message": "That video type is not supported.", "status": 400}
    if media_type != "video" and mime_type and not (mime_type.startswith(IMAGE_MIME_PREFIX) or mime_type == "application/octet-stream"):
        return {"ok": False, "message": "Upload a supported image or video file.", "status": 400}
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = int(file_storage.stream.tell() or 0)
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    max_video = int(float(os.getenv("MEDIA_UPLOAD_MAX_VIDEO_MB", "25")) * 1024 * 1024)
    max_image = int(float(os.getenv("MEDIA_UPLOAD_MAX_IMAGE_MB", os.getenv("MAX_UPLOAD_MB", "12"))) * 1024 * 1024)
    if media_type == "video" and size and size > max_video:
        return {"ok": False, "message": "Video is too large. Please upload a shorter or compressed clip.", "status": 400}
    if media_type != "video" and size and size > max_image:
        return {"ok": False, "message": "Image is too large. Please upload a smaller image.", "status": 400}
    return {"ok": True, "media_type": media_type, "mime_type": mime_type, "size": size}


def verify_media(media: dict) -> dict:
    resolved = media_service.resolve_media(media or {}, check_remote=False)
    available = bool(resolved.get("is_available") and (resolved.get("valid_url") or resolved.get("media_url")))
    if media_storage.provider() in {"r2", "s3"}:
        base = (os.getenv("R2_PUBLIC_BASE_URL") or "").rstrip("/")
        url = resolved.get("valid_url") or resolved.get("media_url") or ""
        available = bool(available and (not base or url.startswith(base)))
    return {**resolved, "verified": available}


def stage_upload(user_id: int, file_storage, *, context_type: str = "pulse_upload", context_id: str = "") -> tuple[dict, int]:
    tid = trace_id()
    validation = validate_media_file(file_storage)
    if not validation.get("ok"):
        return {
            "ok": False,
            "message": validation.get("message") or "Upload could not be validated.",
            "trace_id": tid,
            "progress": progress_payload("failed", 0, validation.get("message") or "Upload failed.", trace_id=tid, retryable=True),
        }, int(validation.get("status") or 400)
    result, status = media_service.save_upload(user_id, file_storage, context_type=context_type, context_id=context_id)
    media = result.get("media") if isinstance(result, dict) else {}
    if not result.get("ok"):
        return {
            **(result or {}),
            "trace_id": tid,
            "progress": progress_payload("failed", 0, (result or {}).get("message") or "Upload failed.", trace_id=tid, retryable=True),
        }, status
    verified = verify_media(media or {})
    media = {**(media or {}), **verified}
    media_type = media.get("media_type") or validation.get("media_type") or "media"
    return {
        "ok": True,
        "message": "Media uploaded and verified.",
        "trace_id": tid,
        "media": media,
        "progress": progress_payload("complete", 100, "Upload complete. Ready to publish.", trace_id=tid, media=media),
        "stages": default_stages(media_type),
        "storage": {
            "provider": media.get("storage_provider") or media_storage.provider(),
            "cdn_base": os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/"),
            "verified": bool(media.get("verified")),
        },
    }, 200
