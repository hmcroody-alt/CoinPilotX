"""Media storage abstraction for local dev and R2/S3 production URLs."""

from __future__ import annotations

import mimetypes
import os
import secrets
import logging
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
        endpoint = _s3_endpoint()
        required = {
            "R2_ACCESS_KEY_ID": bool(os.getenv("R2_ACCESS_KEY_ID")),
            "R2_SECRET_ACCESS_KEY": bool(os.getenv("R2_SECRET_ACCESS_KEY")),
            "R2_BUCKET": bool(os.getenv("R2_BUCKET")),
            "R2_PUBLIC_BASE_URL": bool(os.getenv("R2_PUBLIC_BASE_URL")),
        }
        if current == "r2":
            required["R2_ENDPOINT_OR_ACCOUNT_ID"] = bool(endpoint)
        return {
            "provider": current,
            "configured": all(required.values()),
            "required": required,
            "public_base_url": os.getenv("R2_PUBLIC_BASE_URL", "").strip(),
            "bucket": os.getenv("R2_BUCKET", "").strip(),
            "endpoint_configured": bool(endpoint),
            "endpoint_host": endpoint.split("//", 1)[-1].split("/", 1)[0] if endpoint else "",
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
        endpoint = os.getenv("R2_ENDPOINT_URL", "").strip() or os.getenv("R2_ENDPOINT", "").strip()
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = f"https://{endpoint}"
        return endpoint.rstrip("/") or (f"https://{account_id}.r2.cloudflarestorage.com" if account_id else "")
    return os.getenv("S3_ENDPOINT_URL", "").strip() or None


def _content_type_for_upload(path, fallback=""):
    guessed = mimetypes.guess_type(str(path))[0] or ""
    provided = str(fallback or "").split(";", 1)[0].strip().lower()
    generic = {"", "application/octet-stream", "binary/octet-stream"}
    if guessed and provided in generic:
        return guessed
    if guessed and provided in {"video/quicktime", "application/quicktime"} and str(path).lower().endswith(".mp4"):
        return "video/mp4"
    return provided or guessed or "application/octet-stream"


def _upload_to_object_storage(path, storage_key, mime_type):
    status = storage_status()
    if status.get("provider") not in {"r2", "s3"} or not status.get("configured"):
        logging.error(
            "MEDIA_R2_UPLOAD_SKIPPED reason=config_incomplete provider=%s bucket=%s endpoint_configured=%s required=%s key=%s",
            status.get("provider"),
            status.get("bucket"),
            status.get("endpoint_configured"),
            status.get("required"),
            storage_key,
        )
        return False, "object storage env is not fully configured"
    try:
        import boto3
    except Exception as exc:
        logging.exception("MEDIA_R2_CLIENT_UNAVAILABLE key=%s error=%s", storage_key, exc)
        return False, f"boto3 unavailable: {exc}"
    try:
        bucket = os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")
        endpoint = _s3_endpoint()
        size = Path(path).stat().st_size if Path(path).exists() else 0
        mime_type = _content_type_for_upload(path, mime_type)
        logging.info(
            "MEDIA_R2_UPLOAD_START provider=%s bucket=%s endpoint_host=%s key=%s mime_type=%s size=%s",
            provider(),
            bucket,
            status.get("endpoint_host"),
            storage_key,
            mime_type,
            size,
        )
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "auto"),
        )
        client.upload_file(
            str(path),
            bucket,
            storage_key,
            ExtraArgs={"ContentType": mime_type, "CacheControl": "public, max-age=31536000, immutable"},
        )
        head = client.head_object(Bucket=bucket, Key=storage_key)
        logging.info(
            "MEDIA_R2_UPLOAD_COMPLETE provider=%s bucket=%s key=%s public_url=%s content_type=%s content_length=%s etag_present=%s",
            provider(),
            bucket,
            storage_key,
            public_media_url(storage_key),
            head.get("ContentType") or "",
            head.get("ContentLength") or 0,
            bool(head.get("ETag")),
        )
        return True, ""
    except Exception as exc:
        logging.exception(
            "MEDIA_R2_UPLOAD_FAILED provider=%s bucket=%s endpoint_host=%s key=%s mime_type=%s error=%s",
            provider(),
            os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET"),
            status.get("endpoint_host"),
            storage_key,
            mime_type,
            exc,
        )
        return False, str(exc)


def object_client():
    """Return an S3-compatible client for R2/S3 without exposing credentials."""
    status = storage_status()
    if status.get("provider") not in {"r2", "s3"} or not status.get("configured"):
        return None
    try:
        import boto3
    except Exception as exc:
        logging.exception("MEDIA_R2_CLIENT_UNAVAILABLE error=%s", exc)
        return None
    return boto3.client(
        "s3",
        endpoint_url=_s3_endpoint(),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "auto"),
    )


def get_object(storage_key, byte_range=""):
    """Fetch an object from durable storage. Caller controls response streaming."""
    key = str(storage_key or "").strip().replace("\\", "/").lstrip("/")
    if not key or ".." in key.split("/"):
        raise ValueError("Invalid media object key.")
    client = object_client()
    if client is None:
        raise RuntimeError("Durable media storage is not configured.")
    params = {"Bucket": os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET"), "Key": key}
    if byte_range:
        params["Range"] = byte_range
    return client.get_object(**params)


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
    mime_type = _content_type_for_upload(target, getattr(file_storage, "mimetype", ""))
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
