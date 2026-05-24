"""Media storage abstraction for local dev and R2/S3 production URLs."""

from __future__ import annotations

import mimetypes
import os
import secrets
from datetime import datetime
from pathlib import Path
from werkzeug.utils import secure_filename


PUBLIC_UPLOAD_ROOT = Path(os.getenv("MEDIA_UPLOAD_DIR", "static/uploads")).resolve()
PRIVATE_UPLOAD_ROOT = Path(os.getenv("PRIVATE_MEDIA_UPLOAD_DIR", "instance/private_uploads")).resolve()


def provider():
    return os.getenv("MEDIA_STORAGE_PROVIDER", "local").strip().lower() or "local"


def storage_status():
    current = provider()
    if current in {"r2", "s3"}:
        required = {
            "R2_ACCESS_KEY_ID": bool(os.getenv("R2_ACCESS_KEY_ID")),
            "R2_SECRET_ACCESS_KEY": bool(os.getenv("R2_SECRET_ACCESS_KEY")),
            "R2_BUCKET": bool(os.getenv("R2_BUCKET")),
            "R2_PUBLIC_BASE_URL": bool(os.getenv("R2_PUBLIC_BASE_URL")),
        }
        if current == "r2":
            required["R2_ACCOUNT_ID"] = bool(os.getenv("R2_ACCOUNT_ID"))
        return {
            "provider": current,
            "configured": all(required.values()),
            "required": required,
            "public_base_url": os.getenv("R2_PUBLIC_BASE_URL", "").strip(),
            "bucket": os.getenv("R2_BUCKET", "").strip(),
        }
    return {
        "provider": "local",
        "configured": True,
        "public_root": str(PUBLIC_UPLOAD_ROOT),
        "production_warning": os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"),
    }


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


def _s3_endpoint():
    current = provider()
    if current == "r2":
        account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
        return os.getenv("R2_ENDPOINT_URL", "").strip() or (f"https://{account_id}.r2.cloudflarestorage.com" if account_id else "")
    return os.getenv("S3_ENDPOINT_URL", "").strip() or None


def _upload_to_object_storage(path, storage_key, mime_type):
    status = storage_status()
    if status.get("provider") not in {"r2", "s3"} or not status.get("configured"):
        return False, "object storage env is not fully configured"
    try:
        import boto3
    except Exception as exc:
        return False, f"boto3 unavailable: {exc}"
    try:
        client = boto3.client(
            "s3",
            endpoint_url=_s3_endpoint(),
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "auto"),
        )
        client.upload_file(
            str(path),
            os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET"),
            storage_key,
            ExtraArgs={"ContentType": mime_type, "CacheControl": "public, max-age=31536000, immutable"},
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)


def save_public_file(file_storage, folder="media"):
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise ValueError("No media file provided.")
    safe_name = safe_media_name(file_storage.filename)
    folder = secure_filename(folder or "media") or "media"
    dated_folder = f"{folder}/{datetime.utcnow().strftime('%Y/%m/%d')}"
    relative = f"{dated_folder}/{safe_name}"
    target_dir = PUBLIC_UPLOAD_ROOT / dated_folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    file_storage.save(target)
    mime_type = getattr(file_storage, "mimetype", "") or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    current_provider = provider()
    uploaded = False
    upload_error = ""
    if current_provider in {"r2", "s3"}:
        uploaded, upload_error = _upload_to_object_storage(target, relative, mime_type)
        if not uploaded and os.getenv("MEDIA_REQUIRE_DURABLE_UPLOAD", "").lower() in {"1", "true", "yes"}:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError(f"Durable media upload failed: {upload_error}")
    effective_provider = current_provider if uploaded else "local"
    local_url = f"/static/uploads/{relative}"
    media_url = public_media_url(relative) if uploaded or current_provider == "local" else local_url
    return {
        "provider": effective_provider,
        "media_url": media_url,
        "local_url": local_url,
        "storage_key": relative,
        "local_path": str(target),
        "mime_type": mime_type,
        "file_size": target.stat().st_size if target.exists() else 0,
        "durable_uploaded": uploaded,
        "upload_error": upload_error,
    }


def private_document_path(filename, folder="documents"):
    folder = secure_filename(folder or "documents") or "documents"
    target_dir = PRIVATE_UPLOAD_ROOT / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / safe_media_name(filename)
