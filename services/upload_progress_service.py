"""Unified upload progress and media staging helpers for Pulse publishing."""

from __future__ import annotations

import os
import secrets
import logging
import shutil
from typing import Any

from . import media_service, media_storage


VIDEO_MIME_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-m4v"}
AUDIO_MIME_TYPES = {"audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/ogg", "audio/webm", "application/ogg"}
IMAGE_MIME_PREFIX = "image/"
FILE_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "ogg"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
FILE_EXTENSIONS = {"pdf", "txt", "doc", "docx"}


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
    label = "video" if media_type == "video" else "audio" if media_type == "audio" else "image" if media_type in {"image", "gif"} else "file" if media_type == "file" else "media"
    return [
        {"stage": "starting", "percent": 3, "message": "Upload starting..."},
        {"stage": "uploading", "percent": 76, "message": f"Uploading {label}..."},
        {"stage": "processing", "percent": 88, "message": "Processing media..."},
        {"stage": "publishing", "percent": 96, "message": "Publishing..."},
        {"stage": "complete", "percent": 100, "message": "Posted successfully"},
    ]


def validate_media_file(file_storage) -> dict:
    if not file_storage or not getattr(file_storage, "filename", ""):
        return {"ok": False, "message": "Choose an image, video, audio clip, or safe file to upload.", "status": 400}
    filename = file_storage.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_type = (getattr(file_storage, "mimetype", "") or "").lower()
    if ext in VIDEO_EXTENSIONS or mime_type in VIDEO_MIME_TYPES:
        media_type = "video"
    elif ext in AUDIO_EXTENSIONS or mime_type in AUDIO_MIME_TYPES:
        media_type = "audio"
    elif ext in FILE_EXTENSIONS or mime_type in FILE_MIME_TYPES:
        media_type = "file"
    else:
        media_type = "gif" if ext == "gif" else "image"
    mov_without_transcode = (
        ext == "mov" or mime_type in {"video/quicktime", "application/quicktime"}
    ) and not shutil.which("ffmpeg")
    if mov_without_transcode:
        logging.info(
            "PULSE_UPLOAD_MOV_STORED_WITHOUT_TRANSCODE filename=%s mime_type=%s",
            filename[:180],
            mime_type,
        )
    if media_type == "video" and mime_type and not (mime_type.startswith("video/") or mime_type == "application/octet-stream"):
        return {"ok": False, "message": "That video type is not supported.", "status": 400}
    if media_type == "audio" and mime_type and not (mime_type.startswith("audio/") or mime_type in {"application/octet-stream", "application/ogg", "video/webm"}):
        return {"ok": False, "message": "That audio type is not supported.", "status": 400}
    if media_type == "file" and mime_type and not (mime_type in FILE_MIME_TYPES or mime_type == "application/octet-stream"):
        return {"ok": False, "message": "That file type is not supported.", "status": 400}
    if media_type != "video" and mime_type and not (mime_type.startswith(IMAGE_MIME_PREFIX) or mime_type == "application/octet-stream"):
        if media_type not in {"audio", "file"}:
            return {"ok": False, "message": "Upload a supported image, video, audio clip, or safe file.", "status": 400}
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = int(file_storage.stream.tell() or 0)
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    max_video = int(float(os.getenv("MEDIA_UPLOAD_MAX_VIDEO_MB", "25")) * 1024 * 1024)
    max_audio = int(float(os.getenv("MEDIA_UPLOAD_MAX_AUDIO_MB", "15")) * 1024 * 1024)
    max_file = int(float(os.getenv("MEDIA_UPLOAD_MAX_FILE_MB", "12")) * 1024 * 1024)
    max_image = int(float(os.getenv("MEDIA_UPLOAD_MAX_IMAGE_MB", os.getenv("MAX_UPLOAD_MB", "12"))) * 1024 * 1024)
    if media_type == "video" and size and size > max_video:
        return {"ok": False, "message": "Video is too large. Please upload a shorter or compressed clip.", "status": 400}
    if media_type == "audio" and size and size > max_audio:
        return {"ok": False, "message": "Audio is too large. Please upload a shorter or compressed clip.", "status": 400}
    if media_type == "file" and size and size > max_file:
        return {"ok": False, "message": "File is too large. Please upload a smaller file.", "status": 400}
    if media_type in {"image", "gif"} and size and size > max_image:
        return {"ok": False, "message": "Image is too large. Please upload a smaller image.", "status": 400}
    return {
        "ok": True,
        "media_type": media_type,
        "mime_type": mime_type,
        "size": size,
        "requires_transcode": bool(mov_without_transcode),
    }


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
        logging.warning("PULSE_UPLOAD_VALIDATION_FAILED trace_id=%s user_id=%s context_type=%s message=%s", tid, user_id, context_type, validation.get("message"))
        return {
            "ok": False,
            "message": validation.get("message") or "Upload could not be validated.",
            "trace_id": tid,
            "progress": progress_payload("failed", 0, validation.get("message") or "Upload failed.", trace_id=tid, retryable=True),
        }, int(validation.get("status") or 400)
    logging.info(
        "PULSE_UPLOAD_STAGE_START trace_id=%s user_id=%s context_type=%s context_id=%s media_type=%s mime_type=%s size=%s provider=%s mux_configured=%s ffmpeg_present=%s",
        tid,
        user_id,
        context_type,
        context_id,
        validation.get("media_type"),
        validation.get("mime_type"),
        validation.get("size"),
        media_storage.provider(),
        media_service.mux_diagnostics().get("configured"),
        bool(shutil.which("ffmpeg")),
    )
    result, status = media_service.save_upload(user_id, file_storage, context_type=context_type, context_id=context_id)
    media = result.get("media") if isinstance(result, dict) else {}
    if not result.get("ok"):
        logging.error(
            "PULSE_UPLOAD_STAGE_FAILED trace_id=%s user_id=%s context_type=%s status=%s message=%s",
            tid,
            user_id,
            context_type,
            status,
            (result or {}).get("message"),
        )
        return {
            **(result or {}),
            "trace_id": tid,
            "progress": progress_payload("failed", 0, (result or {}).get("message") or "Upload failed.", trace_id=tid, retryable=True),
        }, status
    verified = verify_media(media or {})
    media = {**(media or {}), **verified}
    if validation.get("requires_transcode"):
        media["processing_note"] = "MOV uploaded. Browser playback may vary until video transcoding is enabled."
    if media_storage.provider() in {"r2", "s3"} and not media.get("verified"):
        logging.error(
            "PULSE_UPLOAD_R2_VERIFY_FAILED trace_id=%s user_id=%s context_type=%s media_id=%s media_url=%s provider=%s",
            tid,
            user_id,
            context_type,
            media.get("id"),
            media.get("media_url"),
            media_storage.provider(),
        )
        return {
            "ok": False,
            "message": "Upload could not be verified in durable storage. Please retry.",
            "trace_id": tid,
            "media": media,
            "progress": progress_payload("failed", 0, "R2 verification failed. Retry upload.", trace_id=tid, retryable=True),
            "storage": {
                "provider": media.get("storage_provider") or media_storage.provider(),
                "cdn_base": os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/"),
                "verified": False,
            },
        }, 502
    media_type = media.get("media_type") or validation.get("media_type") or "media"
    logging.info(
        "PULSE_UPLOAD_STAGE_COMPLETE trace_id=%s user_id=%s context_type=%s media_id=%s media_type=%s mime_type=%s file_size=%s storage_provider=%s storage_key=%s media_url=%s valid_url=%s verified=%s processing_status=%s mux_playback_id=%s mux_configured=%s ffmpeg_present=%s",
        tid,
        user_id,
        context_type,
        media.get("id"),
        media_type,
        media.get("mime_type") or validation.get("mime_type") or "",
        media.get("file_size_bytes") or validation.get("size") or 0,
        media.get("storage_provider") or media_storage.provider(),
        media.get("storage_key") or "",
        media.get("media_url"),
        media.get("valid_url") or "",
        bool(media.get("verified")),
        media.get("processing_status") or "ready",
        media.get("mux_playback_id") or "",
        media_service.mux_diagnostics().get("configured"),
        bool(shutil.which("ffmpeg")),
    )
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
