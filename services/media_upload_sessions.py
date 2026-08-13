"""Authorized direct-to-object-storage upload sessions for PulseSoc media."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from werkzeug.utils import secure_filename

from . import media_service, media_storage, user_context


SESSION_TTL_SECONDS = int(os.getenv("MEDIA_UPLOAD_SESSION_TTL_SECONDS", "3600"))
SIGNED_URL_TTL_SECONDS = min(900, int(os.getenv("MEDIA_UPLOAD_SIGNED_URL_TTL_SECONDS", "600")))
MULTIPART_THRESHOLD = int(float(os.getenv("MEDIA_UPLOAD_MULTIPART_THRESHOLD_MB", "16")) * 1024 * 1024)
PART_SIZE = max(5 * 1024 * 1024, int(float(os.getenv("MEDIA_UPLOAD_PART_SIZE_MB", "8")) * 1024 * 1024))
MAX_PARTS_PER_SIGN = 12


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _future(seconds):
    return (datetime.utcnow() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _bucket():
    return os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET") or ""


def ensure_schema(conn=None):
    owned = conn is None
    conn = conn or user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_media_upload_sessions (
            upload_id TEXT PRIMARY KEY,
            uploader_user_id INTEGER NOT NULL,
            context_type TEXT NOT NULL,
            context_id TEXT,
            original_filename TEXT NOT NULL,
            object_key TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            media_type TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            provider_upload_id TEXT,
            part_size_bytes INTEGER,
            completed_parts_json TEXT,
            status TEXT NOT NULL,
            media_id INTEGER,
            retry_count INTEGER DEFAULT 0,
            trace_id TEXT,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            aborted_at TEXT
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_media_upload_object_key ON pulse_media_upload_sessions(object_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_media_upload_owner_status ON pulse_media_upload_sessions(uploader_user_id, status)")
    conn.commit()
    if owned:
        conn.close()


def _extension(filename):
    safe = secure_filename(filename or "")
    return safe.rsplit(".", 1)[-1].lower() if "." in safe else ""


def _validate(filename, mime_type, file_size, context_type):
    safe = secure_filename(filename or "")[:220]
    ext = _extension(safe)
    declared = str(mime_type or "").split(";", 1)[0].strip().lower()
    media_type = media_service._media_type(ext)
    if not safe or ext not in media_service.ALLOWED_TYPES or not media_type:
        return None, "unsupported_type"
    expected = mimetypes.guess_type(safe)[0] or ""
    if declared and expected and declared.split("/", 1)[0] != expected.split("/", 1)[0]:
        return None, "mime_mismatch"
    if int(file_size or 0) <= 0:
        return None, "invalid_size"
    if int(file_size) > media_service._limit_bytes(ext, context_type):
        return None, "file_too_large"
    return {"filename": safe, "ext": ext, "mime_type": declared or expected or "application/octet-stream", "media_type": media_type}, ""


def _row(upload_id, user_id=None):
    ensure_schema()
    conn = user_context.connect(); cur = conn.cursor()
    if user_id is None:
        cur.execute("SELECT * FROM pulse_media_upload_sessions WHERE upload_id=? LIMIT 1", (upload_id,))
    else:
        cur.execute("SELECT * FROM pulse_media_upload_sessions WHERE upload_id=? AND uploader_user_id=? LIMIT 1", (upload_id, int(user_id)))
    row = cur.fetchone(); conn.close()
    return dict(row) if row else None


def _public(row):
    completed = json.loads(row.get("completed_parts_json") or "[]")
    return {
        "upload_id": row.get("upload_id"), "resource_type": row.get("media_type"),
        "object_key": row.get("object_key"), "mime_type": row.get("mime_type"),
        "file_size_bytes": int(row.get("file_size_bytes") or 0), "strategy": row.get("strategy"),
        "part_size_bytes": int(row.get("part_size_bytes") or 0), "completed_parts": completed,
        "status": row.get("status"), "media_id": int(row.get("media_id") or 0),
        "retry_count": int(row.get("retry_count") or 0), "expires_at": row.get("expires_at"),
        "trace_id": row.get("trace_id"),
    }


def create_session(user_id, payload):
    if media_storage.provider() not in {"r2", "s3"} or not media_storage.storage_status().get("configured"):
        return {"ok": False, "error": "storage_not_configured", "message": "Direct media storage is unavailable."}, 503
    active, error = _validate(payload.get("filename"), payload.get("mime_type"), payload.get("file_size_bytes"), payload.get("context_type") or "pulse_post")
    if error:
        messages = {"file_too_large": "Media is too large.", "mime_mismatch": "Media type does not match the file.", "invalid_size": "Media size is required."}
        return {"ok": False, "error": error, "message": messages.get(error, "That media type is not supported.")}, 413 if error == "file_too_large" else 400
    if media_service.rate_limited(user_id, active["media_type"]):
        return {"ok": False, "error": "rate_limited", "message": "Uploads are temporarily limited. Please wait a moment."}, 429
    upload_id = secrets.token_urlsafe(24)
    trace_id = secrets.token_hex(6)
    prefix = "pulse_media/%s/%s/%s" % (int(user_id), datetime.utcnow().strftime("%Y/%m/%d"), secrets.token_hex(12))
    object_key = str(PurePosixPath(prefix) / active["filename"])
    size = int(payload.get("file_size_bytes"))
    strategy = "multipart" if size >= MULTIPART_THRESHOLD else "single"
    client = media_storage.object_client()
    provider_upload_id = ""
    try:
        if strategy == "multipart":
            created = client.create_multipart_upload(Bucket=_bucket(), Key=object_key, ContentType=active["mime_type"], CacheControl="public, max-age=31536000, immutable")
            provider_upload_id = created["UploadId"]
        now = _now(); expires_at = _future(SESSION_TTL_SECONDS)
        conn = user_context.connect(); cur = conn.cursor(); ensure_schema(conn)
        cur.execute(
            """INSERT INTO pulse_media_upload_sessions
            (upload_id,uploader_user_id,context_type,context_id,original_filename,object_key,mime_type,media_type,file_size_bytes,strategy,provider_upload_id,part_size_bytes,completed_parts_json,status,media_id,retry_count,trace_id,expires_at,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'created',NULL,0,?,?,?,?)""",
            (upload_id, int(user_id), str(payload.get("context_type") or "pulse_post")[:80], str(payload.get("context_id") or "native-draft")[:160], active["filename"], object_key, active["mime_type"], active["media_type"], size, strategy, provider_upload_id, PART_SIZE if strategy == "multipart" else size, "[]", trace_id, expires_at, now, now),
        )
        conn.commit(); conn.close()
        result = _public(_row(upload_id, user_id))
        if strategy == "single":
            result["upload_url"] = client.generate_presigned_url("put_object", Params={"Bucket": _bucket(), "Key": object_key, "ContentType": active["mime_type"]}, ExpiresIn=SIGNED_URL_TTL_SECONDS)
        result.update({"ok": True, "signed_url_expires_in": SIGNED_URL_TTL_SECONDS})
        logging.info("MEDIA_UPLOAD_EVENT event=upload_session_created upload_id=%s trace_id=%s user_id=%s media_type=%s size=%s strategy=%s", upload_id, trace_id, user_id, active["media_type"], size, strategy)
        return result, 201
    except Exception as exc:
        logging.exception("MEDIA_UPLOAD_SESSION_CREATE_FAILED trace_id=%s user_id=%s category=storage error=%s", trace_id, user_id, str(exc)[:300])
        return {"ok": False, "error": "storage_session_failed", "message": "Upload could not be prepared.", "trace_id": trace_id}, 502


def sign_parts(user_id, upload_id, part_numbers):
    row = _row(upload_id, user_id)
    if not row:
        return {"ok": False, "error": "not_found"}, 404
    if row["strategy"] != "multipart" or row["status"] in {"completed", "finalized", "aborted"}:
        return {"ok": False, "error": "invalid_state"}, 409
    if row["expires_at"] < _now():
        return {"ok": False, "error": "session_expired", "refreshable": True}, 410
    numbers = sorted({int(n) for n in (part_numbers or []) if str(n).isdigit() and 1 <= int(n) <= 10000})[:MAX_PARTS_PER_SIGN]
    if not numbers:
        return {"ok": False, "error": "invalid_parts"}, 400
    client = media_storage.object_client()
    parts = [{"part_number": n, "upload_url": client.generate_presigned_url("upload_part", Params={"Bucket": _bucket(), "Key": row["object_key"], "UploadId": row["provider_upload_id"], "PartNumber": n}, ExpiresIn=SIGNED_URL_TTL_SECONDS)} for n in numbers]
    return {"ok": True, "upload_id": upload_id, "parts": parts, "signed_url_expires_in": SIGNED_URL_TTL_SECONDS}, 200


def refresh_authorization(user_id, upload_id):
    row = _row(upload_id, user_id)
    if not row:
        return {"ok": False, "error": "not_found"}, 404
    if row["status"] in {"completed", "finalized", "aborted"}:
        return {"ok": False, "error": "invalid_state"}, 409
    # Refreshing authorization extends only the PulseSoc session. It never
    # changes the object key or discards already-completed multipart parts.
    expires_at = _future(SESSION_TTL_SECONDS)
    conn = user_context.connect(); cur = conn.cursor()
    cur.execute("UPDATE pulse_media_upload_sessions SET expires_at=?,updated_at=? WHERE upload_id=? AND uploader_user_id=?", (expires_at, _now(), upload_id, int(user_id)))
    conn.commit(); conn.close()
    result = {"ok": True, **_public(_row(upload_id, user_id)), "signed_url_expires_in": SIGNED_URL_TTL_SECONDS}
    if row["strategy"] == "single":
        result["upload_url"] = media_storage.object_client().generate_presigned_url("put_object", Params={"Bucket": _bucket(), "Key": row["object_key"], "ContentType": row["mime_type"]}, ExpiresIn=SIGNED_URL_TTL_SECONDS)
    return result, 200


def complete_upload(user_id, upload_id, parts=None):
    row = _row(upload_id, user_id)
    if not row:
        return {"ok": False, "error": "not_found"}, 404
    if row["status"] in {"completed", "finalized"}:
        return {"ok": True, **_public(row)}, 200
    if row["status"] == "aborted":
        return {"ok": False, "error": "aborted"}, 409
    client = media_storage.object_client()
    normalized = []
    if row["strategy"] == "multipart":
        seen = set()
        for item in parts or []:
            number = int(item.get("part_number") or 0); etag = str(item.get("etag") or "").strip()
            if number < 1 or number > 10000 or not etag or number in seen:
                return {"ok": False, "error": "invalid_parts"}, 400
            seen.add(number); normalized.append({"PartNumber": number, "ETag": etag})
        normalized.sort(key=lambda item: item["PartNumber"])
        if not normalized:
            return {"ok": False, "error": "missing_parts"}, 400
        client.complete_multipart_upload(Bucket=_bucket(), Key=row["object_key"], UploadId=row["provider_upload_id"], MultipartUpload={"Parts": normalized})
    head = client.head_object(Bucket=_bucket(), Key=row["object_key"])
    if int(head.get("ContentLength") or 0) != int(row["file_size_bytes"]):
        return {"ok": False, "error": "size_mismatch", "message": "Stored media size did not match."}, 409
    stored_type = str(head.get("ContentType") or "").split(";", 1)[0].lower()
    if stored_type and stored_type != str(row["mime_type"] or "").lower():
        return {"ok": False, "error": "mime_mismatch", "message": "Stored media type did not match."}, 409
    try:
        body = media_storage.get_object(row["object_key"], "bytes=0-511")["Body"].read(512)
        if not media_service._media_header_ok(_extension(row["original_filename"]), body):
            return {"ok": False, "error": "content_verification_failed", "message": "Stored media could not be verified safely."}, 400
    except Exception as exc:
        logging.warning("MEDIA_UPLOAD_HEADER_CHECK_UNAVAILABLE upload_id=%s trace_id=%s error=%s", upload_id, row["trace_id"], str(exc)[:200])
    now = _now(); conn = user_context.connect(); cur = conn.cursor()
    cur.execute("UPDATE pulse_media_upload_sessions SET status='completed',completed_parts_json=?,completed_at=?,updated_at=? WHERE upload_id=? AND uploader_user_id=?", (json.dumps([{"part_number": x["PartNumber"], "etag": x["ETag"]} for x in normalized]), now, now, upload_id, int(user_id)))
    conn.commit(); conn.close()
    logging.info("MEDIA_UPLOAD_EVENT event=upload_completed upload_id=%s trace_id=%s size=%s parts=%s", upload_id, row["trace_id"], row["file_size_bytes"], len(normalized) or 1)
    return {"ok": True, **_public(_row(upload_id, user_id))}, 200


def finalize_upload(user_id, upload_id):
    row = _row(upload_id, user_id)
    if not row:
        return {"ok": False, "error": "not_found"}, 404
    if row.get("media_id"):
        conn = user_context.connect(); cur = conn.cursor(); cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (int(row["media_id"]),)); media = media_service._public(cur.fetchone()); conn.close()
        return {"ok": True, "media_id": int(row["media_id"]), "media": media, "idempotent": True}, 200
    if row["status"] != "completed":
        return {"ok": False, "error": "upload_not_complete"}, 409
    head = media_storage.head_object(row["object_key"])
    if int(head.get("ContentLength") or 0) != int(row["file_size_bytes"]):
        return {"ok": False, "error": "size_mismatch"}, 409
    now = _now(); url = media_storage.public_media_url(row["object_key"])
    conn = user_context.connect(); cur = conn.cursor()
    try:
        cur.execute("SELECT media_id FROM pulse_media_upload_sessions WHERE upload_id=? AND uploader_user_id=?", (upload_id, int(user_id)))
        locked = cur.fetchone()
        if locked and locked["media_id"]:
            media_id = int(locked["media_id"])
        else:
            cur.execute(
                """INSERT INTO chat_media_uploads
                (uploader_user_id,context_type,context_id,original_filename,stored_filename,media_url,thumbnail_url,media_type,mime_type,file_size_bytes,moderation_status,storage_provider,storage_key,bucket,object_key,cdn_url,public_url,is_available,verification_status,processing_status,upload_complete_at,trace_id,updated_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,'approved',?,?,?,?,?,?,1,'verified',?,?,?, ?,?)""",
                (int(user_id), row["context_type"], row["context_id"], row["original_filename"], row["object_key"], url, url if row["media_type"] != "video" else "", row["media_type"], row["mime_type"], int(row["file_size_bytes"]), media_storage.provider(), row["object_key"], _bucket(), row["object_key"], url, url, "uploaded" if row["media_type"] == "video" else "ready", now, row["trace_id"], now, now),
            )
            media_id = int(cur.lastrowid)
            cur.execute("UPDATE pulse_media_upload_sessions SET media_id=?,status='finalized',updated_at=? WHERE upload_id=? AND uploader_user_id=? AND media_id IS NULL", (media_id, now, upload_id, int(user_id)))
        conn.commit(); cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,)); media = media_service._public(cur.fetchone()); conn.close()
        logging.info("MEDIA_UPLOAD_EVENT event=finalize_completed upload_id=%s trace_id=%s media_id=%s processing_status=%s", upload_id, row["trace_id"], media_id, media.get("processing_status"))
        if row["media_type"] == "video":
            threading.Thread(target=_process_video, args=(media_id, row["object_key"], row["trace_id"]), name=f"media-process-{media_id}", daemon=True).start()
        return {"ok": True, "media_id": media_id, "media": media, "processing_status": media.get("processing_status"), "trace_id": row["trace_id"]}, 200
    except Exception as exc:
        conn.rollback(); conn.close(); logging.exception("MEDIA_UPLOAD_FINALIZE_FAILED upload_id=%s trace_id=%s category=database error=%s", upload_id, row["trace_id"], str(exc)[:300])
        return {"ok": False, "error": "finalize_failed", "message": "Upload was stored but could not be finalized.", "trace_id": row["trace_id"]}, 500


def _process_video(media_id, object_key, trace_id):
    """Start delivery processing off the upload/finalize request path."""
    if not media_service.mux_diagnostics().get("configured"):
        return
    source_url = media_service.mux_source_url_for_key(object_key, media_storage.public_media_url(object_key))
    result = media_service.create_mux_asset_from_url(source_url, trace_id=trace_id, media_id=media_id)
    now = _now(); conn = user_context.connect(); cur = conn.cursor()
    try:
        if result.get("ok"):
            playback_id = result.get("playback_id") or ""
            playback_url = media_service.mux_playback_urls(playback_id).get("hls_url") if playback_id else ""
            status = result.get("status") or "created"
            cur.execute("""UPDATE chat_media_uploads SET mux_asset_id=?,mux_playback_id=?,mux_status=?,playback_url=?,playback_mime_type='application/vnd.apple.mpegurl',processing_status=?,updated_at=? WHERE id=? AND processing_status!='ready'""", (result.get("asset_id") or "", playback_id, status, playback_url, "ready" if playback_id and status in {"ready", "asset_ready", "available"} else "mux_processing", now, int(media_id)))
            logging.info("MEDIA_UPLOAD_EVENT event=processing_started media_id=%s trace_id=%s provider=mux", media_id, trace_id)
        else:
            cur.execute("UPDATE chat_media_uploads SET processing_status='mux_failed',mux_status=?,error_message=?,updated_at=? WHERE id=? AND processing_status!='ready'", (result.get("status") or "failed", (result.get("message") or "Mux processing failed.")[:1000], now, int(media_id)))
            logging.error("MEDIA_UPLOAD_EVENT event=upload_failed media_id=%s trace_id=%s category=processing provider=mux status=%s", media_id, trace_id, result.get("status") or "failed")
        conn.commit()
    finally:
        conn.close()


def abort_upload(user_id, upload_id):
    row = _row(upload_id, user_id)
    if not row:
        return {"ok": False, "error": "not_found"}, 404
    if row["status"] == "finalized":
        return {"ok": False, "error": "already_finalized"}, 409
    if row["strategy"] == "multipart" and row["provider_upload_id"] and row["status"] != "aborted":
        media_storage.object_client().abort_multipart_upload(Bucket=_bucket(), Key=row["object_key"], UploadId=row["provider_upload_id"])
    now = _now(); conn = user_context.connect(); cur = conn.cursor(); cur.execute("UPDATE pulse_media_upload_sessions SET status='aborted',aborted_at=?,updated_at=? WHERE upload_id=? AND uploader_user_id=?", (now, now, upload_id, int(user_id))); conn.commit(); conn.close()
    return {"ok": True, "upload_id": upload_id, "status": "aborted"}, 200
