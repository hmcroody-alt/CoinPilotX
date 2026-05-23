"""Media storage abstraction for local dev and R2/S3 production URLs."""

from __future__ import annotations

import mimetypes
import os
import secrets
from pathlib import Path
from werkzeug.utils import secure_filename


PUBLIC_UPLOAD_ROOT = Path(os.getenv("MEDIA_UPLOAD_DIR", "static/uploads")).resolve()
PRIVATE_UPLOAD_ROOT = Path(os.getenv("PRIVATE_MEDIA_UPLOAD_DIR", "instance/private_uploads")).resolve()


def provider():
    return os.getenv("MEDIA_STORAGE_PROVIDER", "local").strip().lower() or "local"


def storage_status():
    current = provider()
    if current in {"r2", "s3"}:
        return {
            "provider": current,
            "configured": bool(os.getenv("R2_BUCKET") and os.getenv("R2_PUBLIC_BASE_URL")),
            "public_base_url": os.getenv("R2_PUBLIC_BASE_URL", "").strip(),
        }
    return {"provider": "local", "configured": True, "public_root": str(PUBLIC_UPLOAD_ROOT)}


def safe_media_name(filename):
    original = secure_filename(filename or "upload")
    stem, ext = os.path.splitext(original)
    ext = ext.lower()[:12]
    return f"{stem[:48] or 'media'}-{secrets.token_hex(8)}{ext}"


def public_media_url(relative_path):
    relative = str(relative_path or "").lstrip("/")
    base = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if provider() in {"r2", "s3"} and base:
        return f"{base}/{relative}"
    return f"/static/uploads/{relative}"


def save_public_file(file_storage, folder="media"):
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise ValueError("No media file provided.")
    safe_name = safe_media_name(file_storage.filename)
    folder = secure_filename(folder or "media") or "media"
    relative = f"{folder}/{safe_name}"
    target_dir = PUBLIC_UPLOAD_ROOT / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    file_storage.save(target)
    mime_type = getattr(file_storage, "mimetype", "") or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return {
        "provider": "local",
        "media_url": public_media_url(relative),
        "storage_key": relative,
        "mime_type": mime_type,
        "file_size": target.stat().st_size if target.exists() else 0,
    }


def private_document_path(filename, folder="documents"):
    folder = secure_filename(folder or "documents") or "documents"
    target_dir = PRIVATE_UPLOAD_ROOT / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / safe_media_name(filename)
