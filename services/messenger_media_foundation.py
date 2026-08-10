"""Private Messenger media upload foundation.

This module keeps Messenger media uploads backend-controlled and private. It
does not wire any composer UI; callers can create upload records, stream files
into private storage, complete metadata, and later attach uploaded media to a
message.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename

from services import db as db_service
from services import media_storage


UPLOAD_STATUSES = {"pending", "uploaded", "attached", "failed", "deleted"}
PROCESSING_STATUSES = {"not_required", "queued", "processing", "ready", "failed"}
MEDIA_TYPES = {"photo", "video", "voice", "file"}

ALLOWED_MIME_TYPES: dict[str, dict[str, Any]] = {
    "image/jpeg": {"media_type": "photo", "extensions": {"jpg", "jpeg"}},
    "image/png": {"media_type": "photo", "extensions": {"png"}},
    "image/webp": {"media_type": "photo", "extensions": {"webp"}},
    "image/heic": {"media_type": "photo", "extensions": {"heic"}},
    "image/heif": {"media_type": "photo", "extensions": {"heif"}},
    "video/mp4": {"media_type": "video", "extensions": {"mp4", "m4v"}},
    "video/webm": {"media_type": "video", "extensions": {"webm"}},
    "audio/webm": {"media_type": "voice", "extensions": {"webm"}},
    "audio/mpeg": {"media_type": "voice", "extensions": {"mp3", "mpeg"}},
    "audio/mp4": {"media_type": "voice", "extensions": {"mp4", "m4a"}},
    "audio/wav": {"media_type": "voice", "extensions": {"wav"}},
    "audio/x-wav": {"media_type": "voice", "extensions": {"wav"}},
    "audio/ogg": {"media_type": "voice", "extensions": {"ogg", "oga"}},
}

MIME_ALIASES = {
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/mp4a-latm": "audio/mp4",
    "application/x-m4a": "audio/mp4",
}

DEFAULT_EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "audio/webm": "webm",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
}

SIZE_LIMIT_ENV = {
    "photo": ("MESSENGER_PHOTO_MAX_MB", 15),
    "video": ("MESSENGER_VIDEO_MAX_MB", 200),
    "voice": ("MESSENGER_VOICE_MAX_MB", 25),
    "file": ("MESSENGER_FILE_MAX_MB", 50),
}

LOCAL_PRIVATE_UPLOAD_DIR = "storage/messenger_uploads"
SIGNED_URL_TTL_SECONDS = 900
MAX_WAVEFORM_POINTS = 512
MAX_CHUNK_SIZE = 1024 * 1024
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MessengerMediaError(Exception):
    def __init__(self, error: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.error = error
        self.message = message
        self.status_code = status_code


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def error_response(exc: MessengerMediaError, trace_id: str = "") -> tuple[dict[str, Any], int]:
    payload = {"ok": False, "error": exc.error, "message": exc.message}
    if trace_id:
        payload["trace_id"] = trace_id
    return payload, exc.status_code


def ok_response(payload: dict[str, Any], status_code: int = 200) -> tuple[dict[str, Any], int]:
    payload.setdefault("ok", True)
    return payload, status_code


def _safe_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.match(identifier or ""):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return identifier


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _table_columns(cur: Any, table: str) -> set[str]:
    table = _safe_identifier(table)
    if db_service.IS_POSTGRES:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        )
        return {str(_row_get(row, "column_name", row[0] if row else "")).lower() for row in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table})")
    return {str(_row_get(row, "name", "")).lower() for row in cur.fetchall()}


def _add_column_if_missing(cur: Any, table: str, column: str, definition: str) -> None:
    table = _safe_identifier(table)
    column = _safe_identifier(column)
    if column.lower() in _table_columns(cur, table):
        return
    if db_service.IS_POSTGRES:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    else:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_schema(cur: Any, conn: Any | None = None) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS message_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            attachment_type TEXT,
            storage_key TEXT,
            metadata TEXT,
            created_at TEXT,
            conversation_id INTEGER,
            conversation_model TEXT DEFAULT 'pulse',
            sender_id INTEGER,
            media_type TEXT,
            mime_type TEXT,
            original_filename TEXT,
            public_url TEXT,
            signed_url_strategy TEXT DEFAULT 'private',
            thumbnail_key TEXT,
            waveform_json TEXT,
            duration_ms INTEGER,
            width INTEGER,
            height INTEGER,
            size_bytes INTEGER,
            checksum TEXT,
            upload_status TEXT DEFAULT 'pending',
            processing_status TEXT DEFAULT 'not_required',
            error_code TEXT,
            error_message TEXT,
            metadata_json TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
        """
    )
    for column, definition in [
        ("conversation_id", "INTEGER"),
        ("conversation_model", "TEXT DEFAULT 'pulse'"),
        ("sender_id", "INTEGER"),
        ("media_type", "TEXT"),
        ("mime_type", "TEXT"),
        ("original_filename", "TEXT"),
        ("public_url", "TEXT"),
        ("signed_url_strategy", "TEXT DEFAULT 'private'"),
        ("thumbnail_key", "TEXT"),
        ("waveform_json", "TEXT"),
        ("duration_ms", "INTEGER"),
        ("width", "INTEGER"),
        ("height", "INTEGER"),
        ("size_bytes", "INTEGER"),
        ("checksum", "TEXT"),
        ("upload_status", "TEXT DEFAULT 'pending'"),
        ("processing_status", "TEXT DEFAULT 'not_required'"),
        ("error_code", "TEXT"),
        ("error_message", "TEXT"),
        ("metadata_json", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ]:
        _add_column_if_missing(cur, "message_attachments", column, definition)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_message_attachments_conversation ON message_attachments(conversation_id, conversation_model)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_message_attachments_sender ON message_attachments(sender_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_message_attachments_message ON message_attachments(message_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_message_attachments_status ON message_attachments(upload_status, processing_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_message_attachments_storage_key ON message_attachments(storage_key)")
    if conn is not None:
        try:
            conn.commit()
        except Exception:
            pass


def media_limits() -> dict[str, int]:
    limits: dict[str, int] = {}
    for media_type, (env_name, default_mb) in SIZE_LIMIT_ENV.items():
        try:
            mb = float(os.getenv(env_name, str(default_mb)))
        except (TypeError, ValueError):
            mb = float(default_mb)
        limits[media_type] = int(max(1.0, mb) * 1024 * 1024)
    return limits


def max_size_for(media_type: str) -> int:
    return media_limits().get(media_type, media_limits()["file"])


def _normalize_mime(mime_type: str) -> str:
    cleaned = str(mime_type or "").split(";", 1)[0].strip().lower()
    return MIME_ALIASES.get(cleaned, cleaned)


def sanitize_filename(filename: str) -> str:
    cleaned = secure_filename(filename or "upload")
    cleaned = cleaned.strip("._-")[:140]
    return cleaned or "upload"


def _extension_for(filename: str, mime_type: str) -> str:
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower().lstrip(".")
    allowed = ALLOWED_MIME_TYPES[mime_type]["extensions"]
    if suffix:
        if suffix not in allowed:
            raise MessengerMediaError("unsupported_extension", "That file extension is not allowed for this media type.", 415)
        return suffix
    return DEFAULT_EXTENSION_BY_MIME[mime_type]


def validate_media_request(data: dict[str, Any]) -> dict[str, Any]:
    try:
        conversation_id = int(data.get("conversation_id") or 0)
    except (TypeError, ValueError):
        conversation_id = 0
    if conversation_id <= 0:
        raise MessengerMediaError("invalid_conversation", "Conversation is required.", 400)
    media_type = str(data.get("media_type") or "").strip().lower()
    if media_type not in MEDIA_TYPES:
        raise MessengerMediaError("invalid_media_type", "Media type must be photo, video, voice, or file.", 400)
    mime_type = _normalize_mime(data.get("mime_type") or data.get("content_type") or "")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise MessengerMediaError("unsupported_mime_type", "That file type is not supported for Messenger media.", 415)
    mime_media_type = ALLOWED_MIME_TYPES[mime_type]["media_type"]
    if media_type != "file" and media_type != mime_media_type:
        raise MessengerMediaError("media_type_mismatch", "The selected file does not match the requested media type.", 415)
    try:
        size_bytes = int(data.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    if size_bytes <= 0:
        raise MessengerMediaError("invalid_size", "File size is required.", 400)
    limit = max_size_for(media_type)
    if size_bytes > limit:
        raise MessengerMediaError("file_too_large", _size_message(media_type, limit), 413)
    filename = sanitize_filename(str(data.get("filename") or data.get("original_filename") or "upload"))
    extension = _extension_for(filename, mime_type)
    return {
        "conversation_id": conversation_id,
        "media_type": media_type,
        "mime_type": mime_type,
        "filename": filename,
        "extension": extension,
        "size_bytes": size_bytes,
        "max_size_bytes": limit,
    }


def _size_message(media_type: str, limit: int) -> str:
    mb = int(limit / (1024 * 1024))
    label = {"photo": "Photos", "video": "Videos", "voice": "Voice messages", "file": "Files"}.get(media_type, "Files")
    return f"{label} must be {mb} MB or smaller."


def _members_for_conversation(cur: Any, conversation_id: int, model: str) -> list[int]:
    if model == "comm_v2":
        cur.execute(
            """
            SELECT user_id
            FROM comm_v2_participants
            WHERE conversation_id=? AND membership_state='active' AND (left_at IS NULL OR left_at='')
            """,
            (conversation_id,),
        )
    elif model == "pulse":
        cur.execute(
            """
            SELECT user_id
            FROM pulse_conversation_participants
            WHERE conversation_id=? AND (left_at IS NULL OR left_at='')
            """,
            (conversation_id,),
        )
    else:
        cur.execute("SELECT user_id FROM conversation_members WHERE conversation_id=?", (conversation_id,))
    members = []
    for row in cur.fetchall():
        try:
            members.append(int(_row_get(row, "user_id", 0)))
        except (TypeError, ValueError):
            continue
    return members


def _conversation_blocked(cur: Any, user_id: int, conversation_id: int, model: str) -> bool:
    members = [member_id for member_id in _members_for_conversation(cur, conversation_id, model) if member_id and member_id != user_id]
    if not members:
        return False
    placeholders = ",".join("?" for _ in members)
    params = [user_id, *members, user_id, *members]
    cur.execute(
        f"""
        SELECT 1
        FROM blocked_users
        WHERE (blocker_user_id=? AND blocked_user_id IN ({placeholders}))
           OR (blocked_user_id=? AND blocker_user_id IN ({placeholders}))
        LIMIT 1
        """,
        params,
    )
    return bool(cur.fetchone())


def require_conversation_access(cur: Any, user_id: int, conversation_id: int) -> str:
    try:
        cur.execute(
            """
            SELECT c.id, c.status, c.deleted_at
            FROM comm_v2_participants p
            JOIN comm_v2_conversations c ON c.id=p.conversation_id
            WHERE p.conversation_id=? AND p.user_id=?
              AND p.membership_state='active'
              AND (p.left_at IS NULL OR p.left_at='')
            LIMIT 1
            """,
            (conversation_id, user_id),
        )
        comm_v2_row = cur.fetchone()
    except Exception as exc:
        logging.info("MESSENGER_MEDIA_COMM_V2_ACCESS_CHECK_SKIPPED conversation_id=%s error=%s", conversation_id, exc)
        comm_v2_row = None
    if comm_v2_row:
        status = str(_row_get(comm_v2_row, "status", "active") or "active").lower()
        if status not in {"active", "open", ""} or _row_get(comm_v2_row, "deleted_at"):
            raise MessengerMediaError("conversation_inactive", "This conversation is not active.", 403)
        if _conversation_blocked(cur, user_id, conversation_id, "comm_v2"):
            raise MessengerMediaError("messaging_blocked", "Messaging is blocked for this conversation.", 403)
        return "comm_v2"
    cur.execute(
        """
        SELECT c.id, c.status, c.deleted_at
        FROM pulse_conversation_participants p
        JOIN pulse_conversations c ON c.id=p.conversation_id
        WHERE p.conversation_id=? AND p.user_id=? AND (p.left_at IS NULL OR p.left_at='')
        LIMIT 1
        """,
        (conversation_id, user_id),
    )
    pulse_row = cur.fetchone()
    if pulse_row:
        status = str(_row_get(pulse_row, "status", "active") or "active").lower()
        if status not in {"active", "open", ""} or _row_get(pulse_row, "deleted_at"):
            raise MessengerMediaError("conversation_inactive", "This conversation is not active.", 403)
        if _conversation_blocked(cur, user_id, conversation_id, "pulse"):
            raise MessengerMediaError("messaging_blocked", "Messaging is blocked for this conversation.", 403)
        return "pulse"
    cur.execute(
        """
        SELECT c.id
        FROM conversation_members m
        JOIN conversations c ON c.id=m.conversation_id
        WHERE m.conversation_id=? AND m.user_id=?
        LIMIT 1
        """,
        (conversation_id, user_id),
    )
    if cur.fetchone():
        if _conversation_blocked(cur, user_id, conversation_id, "legacy"):
            raise MessengerMediaError("messaging_blocked", "Messaging is blocked for this conversation.", 403)
        return "legacy"
    raise MessengerMediaError("not_conversation_member", "You do not have access to this conversation.", 403)


def storage_key_for(conversation_id: int, sender_id: int, extension: str, stamp: datetime | None = None) -> str:
    stamp = stamp or datetime.utcnow()
    ext = re.sub(r"[^a-z0-9]+", "", extension.lower())[:8] or "bin"
    return f"messenger/{conversation_id}/{sender_id}/{stamp:%Y}/{stamp:%m}/{uuid.uuid4().hex}.{ext}"


def local_private_root() -> Path:
    return Path(os.getenv("MESSENGER_MEDIA_LOCAL_DIR", LOCAL_PRIVATE_UPLOAD_DIR)).resolve()


def _local_path(storage_key: str) -> Path:
    key = str(storage_key or "").replace("\\", "/").lstrip("/")
    if not key or ".." in key.split("/"):
        raise MessengerMediaError("invalid_storage_key", "Invalid attachment storage key.", 400)
    root = local_private_root()
    target = (root / key).resolve()
    if target != root and root not in target.parents:
        raise MessengerMediaError("invalid_storage_key", "Invalid attachment storage key.", 400)
    return target


def _trace_id() -> str:
    return uuid.uuid4().hex[:12]


def log_event(event: str, trace_id: str, user_id: int, conversation_id: int, attachment_id: int | None = None, **details: Any) -> None:
    safe_details = {key: value for key, value in details.items() if key not in {"signed_url", "upload_url"}}
    logging.info(
        "MESSENGER_MEDIA_EVENT event=%s trace_id=%s user_id=%s conversation_id=%s attachment_id=%s details=%s",
        event,
        trace_id,
        user_id,
        conversation_id,
        attachment_id or "",
        json.dumps(safe_details, sort_keys=True, default=str)[:1000],
    )


def init_upload(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    ensure_schema(cur)
    trace_id = _trace_id()
    user_id = int(user.get("user_id") or user.get("id") or 0)
    data = validate_media_request(payload)
    model = require_conversation_access(cur, user_id, data["conversation_id"])
    key = storage_key_for(data["conversation_id"], user_id, data["extension"])
    created_at = now_iso()
    processing_status = _initial_processing_status(data["media_type"], None)
    cur.execute(
        """
        INSERT INTO message_attachments (
            message_id, conversation_id, conversation_model, sender_id, attachment_type,
            media_type, mime_type, original_filename, storage_key, public_url,
            signed_url_strategy, size_bytes, upload_status, processing_status,
            metadata, metadata_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            None,
            data["conversation_id"],
            model,
            user_id,
            data["media_type"],
            data["media_type"],
            data["mime_type"],
            data["filename"],
            key,
            "",
            "private",
            data["size_bytes"],
            "pending",
            processing_status,
            "{}",
            json.dumps({"declared_size_bytes": data["size_bytes"], "storage_scope": "private"}),
            created_at,
            created_at,
        ),
    )
    attachment_id = int(getattr(cur, "lastrowid", None) or 0)
    if not attachment_id:
        cur.execute("SELECT id FROM message_attachments WHERE storage_key=? LIMIT 1", (key,))
        row = cur.fetchone()
        attachment_id = int(_row_get(row, "id", 0))
    _enqueue_processing_jobs(cur, attachment_id, data["conversation_id"], data["media_type"], processing_status)
    conn.commit()
    log_event("upload_init", trace_id, user_id, data["conversation_id"], attachment_id, media_type=data["media_type"], size_bytes=data["size_bytes"], mime_type=data["mime_type"])
    return ok_response(
        {
            "attachment_id": attachment_id,
            "upload_url": "/api/messages/media/upload",
            "upload_method": "direct",
            "max_size_bytes": data["max_size_bytes"],
            "media_type": data["media_type"],
            "mime_type": data["mime_type"],
            "trace_id": trace_id,
        },
        201,
    )


def _initial_processing_status(media_type: str, waveform: list[float] | None) -> str:
    if media_type == "video":
        return "queued"
    if media_type == "photo":
        return "queued"
    if media_type == "voice":
        return "ready" if waveform else "queued"
    return "not_required"


def _enqueue_processing_jobs(cur: Any, attachment_id: int, conversation_id: int, media_type: str, status: str) -> None:
    if not attachment_id or status != "queued":
        return
    job_type = {
        "photo": "messenger_photo_thumbnail",
        "video": "messenger_video_metadata_thumbnail",
        "voice": "messenger_voice_waveform",
    }.get(media_type)
    if not job_type:
        return
    try:
        stamp = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_jobs (job_type, target_type, target_id, status, attempts, max_attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_type, "message_attachment", attachment_id, "pending", 0, 3, stamp, stamp),
        )
    except Exception as exc:
        logging.warning("MESSENGER_MEDIA_PROCESSING_QUEUE_SKIPPED attachment_id=%s conversation_id=%s error=%s", attachment_id, conversation_id, exc)


def _fetch_attachment(cur: Any, attachment_id: int) -> Any:
    cur.execute("SELECT * FROM message_attachments WHERE id=? LIMIT 1", (attachment_id,))
    row = cur.fetchone()
    if not row:
        raise MessengerMediaError("attachment_not_found", "Attachment not found.", 404)
    return row


def _require_attachment_access(cur: Any, row: Any, user_id: int, require_sender: bool = False) -> str:
    if _row_get(row, "deleted_at") or str(_row_get(row, "upload_status", "")).lower() == "deleted":
        raise MessengerMediaError("attachment_deleted", "Attachment has been deleted.", 410)
    conversation_id = int(_row_get(row, "conversation_id", 0) or 0)
    model = require_conversation_access(cur, user_id, conversation_id)
    if require_sender and int(_row_get(row, "sender_id", 0) or 0) != user_id:
        raise MessengerMediaError("not_attachment_owner", "Only the sender can modify this attachment.", 403)
    return model


def upload_file(cur: Any, conn: Any, user: dict[str, Any], attachment_id: int, file_storage: Any, metadata: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    ensure_schema(cur)
    trace_id = _trace_id()
    user_id = int(user.get("user_id") or user.get("id") or 0)
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise MessengerMediaError("file_required", "Upload file is required.", 400)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=True)
    status = str(_row_get(row, "upload_status", "pending") or "pending").lower()
    if status not in {"pending", "failed", "uploaded"}:
        raise MessengerMediaError("invalid_upload_state", "This attachment cannot be uploaded in its current state.", 409)
    expected_mime = _normalize_mime(_row_get(row, "mime_type", ""))
    actual_mime = _normalize_mime(getattr(file_storage, "mimetype", "") or expected_mime)
    if actual_mime == "application/octet-stream" and expected_mime in ALLOWED_MIME_TYPES:
        actual_mime = expected_mime
    if actual_mime and actual_mime != expected_mime:
        raise MessengerMediaError("mime_type_mismatch", "Uploaded file type does not match the initialized attachment.", 415)
    media_type = str(_row_get(row, "media_type", "file") or "file")
    limit = max_size_for(media_type)
    storage_key = str(_row_get(row, "storage_key", "") or "")
    temp_path, size_bytes, checksum = _spool_upload(file_storage, limit)
    provider = "local_private"
    upload_error = ""
    try:
        if media_storage.provider() in {"r2", "s3"} and media_storage.storage_status().get("configured"):
            uploaded, upload_error = _upload_private_object(temp_path, storage_key, expected_mime)
            provider = media_storage.provider() if uploaded else "local_private"
            if not uploaded:
                logging.warning("MESSENGER_MEDIA_DURABLE_UPLOAD_FALLBACK attachment_id=%s error=%s", attachment_id, upload_error)
                _store_local_private(temp_path, storage_key)
            else:
                _delete_temp(temp_path)
        else:
            _store_local_private(temp_path, storage_key)
        meta = _normalized_metadata(metadata or {})
        waveform = meta.get("waveform")
        processing_status = _initial_processing_status(media_type, waveform if isinstance(waveform, list) else None)
        if waveform is not None:
            meta["waveform_points"] = len(waveform)
        stamp = now_iso()
        cur.execute(
            """
            UPDATE message_attachments
            SET size_bytes=?, checksum=?, upload_status='uploaded', processing_status=?,
                waveform_json=?, duration_ms=?, width=?, height=?, public_url='',
                signed_url_strategy=?, metadata_json=?, error_code='', error_message='',
                updated_at=?
            WHERE id=?
            """,
            (
                size_bytes,
                checksum,
                processing_status,
                json.dumps(waveform, separators=(",", ":")) if waveform is not None else _row_get(row, "waveform_json"),
                meta.get("duration_ms"),
                meta.get("width"),
                meta.get("height"),
                "signed" if provider in {"r2", "s3"} else "private_local_endpoint",
                json.dumps({"storage_provider": provider, "upload_error": upload_error, **{k: v for k, v in meta.items() if k != "waveform"}}, sort_keys=True, default=str),
                stamp,
                attachment_id,
            ),
        )
        _enqueue_processing_jobs(cur, attachment_id, int(_row_get(row, "conversation_id", 0) or 0), media_type, processing_status)
        conn.commit()
        log_event(
            "upload_completed",
            trace_id,
            user_id,
            int(_row_get(row, "conversation_id", 0) or 0),
            attachment_id,
            media_type=media_type,
            size_bytes=size_bytes,
            mime_type=expected_mime,
            storage_provider=provider,
        )
        return ok_response(_attachment_payload(cur, attachment_id, user_id, include_url=False) | {"trace_id": trace_id})
    except MessengerMediaError:
        raise
    except Exception as exc:
        _delete_temp(temp_path)
        _mark_failed(cur, conn, attachment_id, "upload_failed", "Upload failed. Please retry.")
        log_event("upload_failed", trace_id, user_id, int(_row_get(row, "conversation_id", 0) or 0), attachment_id, error=str(exc)[:200])
        raise MessengerMediaError("upload_failed", "Upload failed. Please retry.", 500) from exc


def _spool_upload(file_storage: Any, limit: int) -> tuple[str, int, str]:
    digest = hashlib.sha256()
    size = 0
    handle = tempfile.NamedTemporaryFile(delete=False, prefix="messenger-media-", suffix=".upload")
    try:
        while True:
            chunk = file_storage.stream.read(MAX_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise MessengerMediaError("file_too_large", "Upload exceeds the configured Messenger media limit.", 413)
            digest.update(chunk)
            handle.write(chunk)
        handle.flush()
    finally:
        handle.close()
    if size <= 0:
        _delete_temp(handle.name)
        raise MessengerMediaError("empty_file", "Upload file is empty.", 400)
    return handle.name, size, digest.hexdigest()


def _store_local_private(temp_path: str, storage_key: str) -> None:
    target = _local_path(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(temp_path, target)


def _upload_private_object(temp_path: str, storage_key: str, mime_type: str) -> tuple[bool, str]:
    client = media_storage.object_client()
    bucket = os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")
    if not client or not bucket:
        return False, "object storage is not configured"
    try:
        client.upload_file(
            str(temp_path),
            bucket,
            storage_key,
            ExtraArgs={"ContentType": mime_type, "CacheControl": "private, max-age=0, no-store"},
        )
        return True, ""
    except Exception as exc:
        logging.exception("MESSENGER_MEDIA_PRIVATE_OBJECT_UPLOAD_FAILED key=%s error=%s", storage_key, exc)
        return False, str(exc)


def _delete_temp(temp_path: str) -> None:
    try:
        Path(temp_path).unlink(missing_ok=True)
    except Exception:
        pass


def _normalized_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("duration_ms", "width", "height"):
        value = metadata.get(key)
        if value in (None, ""):
            normalized[key] = None
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            parsed = 0
        normalized[key] = parsed if parsed > 0 else None
    waveform_value = metadata.get("waveform_json") or metadata.get("waveform")
    waveform = _parse_waveform(waveform_value)
    if waveform is not None:
        normalized["waveform"] = waveform
    return normalized


def _parse_waveform(value: Any) -> list[float] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MessengerMediaError("invalid_waveform", "Voice waveform data is not valid JSON.", 400) from exc
    if not isinstance(value, list):
        raise MessengerMediaError("invalid_waveform", "Voice waveform data must be an array.", 400)
    if len(value) > MAX_WAVEFORM_POINTS:
        raise MessengerMediaError("waveform_too_large", "Voice waveform data is too large.", 400)
    waveform: list[float] = []
    for item in value:
        try:
            point = float(item)
        except (TypeError, ValueError):
            raise MessengerMediaError("invalid_waveform", "Voice waveform points must be numeric.", 400)
        waveform.append(max(0.0, min(1.0, point)))
    return waveform


def _mark_failed(cur: Any, conn: Any, attachment_id: int, error_code: str, message: str) -> None:
    try:
        cur.execute(
            "UPDATE message_attachments SET upload_status='failed', error_code=?, error_message=?, updated_at=? WHERE id=?",
            (error_code, message, now_iso(), attachment_id),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def complete_upload(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    attachment_id = int(payload.get("attachment_id") or 0)
    if not attachment_id:
        raise MessengerMediaError("attachment_required", "Attachment is required.", 400)
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=True)
    meta = _normalized_metadata(payload)
    processing_status = _initial_processing_status(str(_row_get(row, "media_type", "file") or "file"), meta.get("waveform") if isinstance(meta.get("waveform"), list) else None)
    cur.execute(
        """
        UPDATE message_attachments
        SET duration_ms=COALESCE(?, duration_ms),
            width=COALESCE(?, width),
            height=COALESCE(?, height),
            waveform_json=COALESCE(?, waveform_json),
            processing_status=?,
            updated_at=?
        WHERE id=?
        """,
        (
            meta.get("duration_ms"),
            meta.get("width"),
            meta.get("height"),
            json.dumps(meta.get("waveform"), separators=(",", ":")) if meta.get("waveform") is not None else None,
            processing_status,
            now_iso(),
            attachment_id,
        ),
    )
    _enqueue_processing_jobs(cur, attachment_id, int(_row_get(row, "conversation_id", 0) or 0), str(_row_get(row, "media_type", "file") or "file"), processing_status)
    conn.commit()
    return ok_response(_attachment_payload(cur, attachment_id, user_id, include_url=False))


def attach_to_message(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    attachment_ids = payload.get("attachments") or payload.get("attachment_ids") or payload.get("attachment_id")
    if isinstance(attachment_ids, (str, int)):
        attachment_ids = [attachment_ids]
    if not isinstance(attachment_ids, list) or not attachment_ids:
        raise MessengerMediaError("attachment_required", "At least one attachment is required.", 400)
    try:
        message_id = int(payload.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    if message_id <= 0:
        raise MessengerMediaError("message_required", "Message id is required before attaching media.", 400)
    user_id = int(user.get("user_id") or user.get("id") or 0)
    attached = []
    for raw_id in attachment_ids:
        attachment_id = int(raw_id or 0)
        row = _fetch_attachment(cur, attachment_id)
        _require_attachment_access(cur, row, user_id, require_sender=True)
        upload_status = str(_row_get(row, "upload_status", "")).lower()
        existing_message_id = int(_row_get(row, "message_id", 0) or 0)
        if upload_status == "attached" and existing_message_id == message_id:
            attached.append(attachment_id)
            continue
        if upload_status != "uploaded":
            raise MessengerMediaError("attachment_not_uploaded", "Attachment must finish uploading before it can be attached.", 409)
        _validate_message_for_attachment(cur, message_id, row, user_id)
        cur.execute(
            "UPDATE message_attachments SET message_id=?, upload_status='attached', updated_at=? WHERE id=?",
            (message_id, now_iso(), attachment_id),
        )
        attached.append(attachment_id)
    conn.commit()
    return ok_response({"attached": attached, "message_id": message_id})


def _validate_message_for_attachment(cur: Any, message_id: int, attachment_row: Any, user_id: int) -> None:
    conversation_id = int(_row_get(attachment_row, "conversation_id", 0) or 0)
    model = str(_row_get(attachment_row, "conversation_model", "pulse") or "pulse")
    if model == "pulse":
        cur.execute(
            "SELECT id FROM pulse_messages WHERE id=? AND conversation_id=? AND sender_user_id=? LIMIT 1",
            (message_id, conversation_id, user_id),
        )
    elif model == "comm_v2":
        cur.execute(
            "SELECT id FROM comm_v2_messages WHERE id=? AND conversation_id=? AND sender_user_id=? LIMIT 1",
            (message_id, conversation_id, user_id),
        )
    else:
        cur.execute(
            "SELECT id FROM private_messages WHERE id=? AND conversation_id=? AND sender_user_id=? LIMIT 1",
            (message_id, conversation_id, user_id),
        )
    if not cur.fetchone():
        raise MessengerMediaError("message_not_found", "Message was not found for this attachment.", 404)


def retry_attachment(cur: Any, conn: Any, user: dict[str, Any], attachment_id: int) -> tuple[dict[str, Any], int]:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=True)
    status = str(_row_get(row, "upload_status", "pending") or "pending").lower()
    if status not in {"failed", "pending"}:
        return ok_response(_attachment_payload(cur, attachment_id, user_id, include_url=False) | {"retry_available": False})
    cur.execute(
        "UPDATE message_attachments SET upload_status='pending', error_code='', error_message='', updated_at=? WHERE id=?",
        (now_iso(), attachment_id),
    )
    conn.commit()
    return ok_response(_attachment_payload(cur, attachment_id, user_id, include_url=False) | {"retry_available": True})


def delete_attachment(cur: Any, conn: Any, user: dict[str, Any], attachment_id: int) -> tuple[dict[str, Any], int]:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=True)
    cur.execute(
        "UPDATE message_attachments SET upload_status='deleted', deleted_at=?, updated_at=? WHERE id=?",
        (now_iso(), now_iso(), attachment_id),
    )
    conn.commit()
    return ok_response({"deleted": True, "attachment_id": attachment_id})


def get_attachment(cur: Any, user: dict[str, Any], attachment_id: int, include_url: bool = True) -> tuple[dict[str, Any], int]:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    _fetch_attachment(cur, attachment_id)
    return ok_response(_attachment_payload(cur, attachment_id, user_id, include_url=include_url))


def _attachment_payload(cur: Any, attachment_id: int, user_id: int, include_url: bool = True) -> dict[str, Any]:
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=False)
    payload = {
        "attachment_id": int(_row_get(row, "id", 0) or 0),
        "message_id": _row_get(row, "message_id"),
        "conversation_id": int(_row_get(row, "conversation_id", 0) or 0),
        "media_type": _row_get(row, "media_type"),
        "mime_type": _row_get(row, "mime_type"),
        "filename": _row_get(row, "original_filename"),
        "size_bytes": int(_row_get(row, "size_bytes", 0) or 0),
        "duration_ms": _row_get(row, "duration_ms"),
        "width": _row_get(row, "width"),
        "height": _row_get(row, "height"),
        "upload_status": _row_get(row, "upload_status"),
        "processing_status": _row_get(row, "processing_status"),
        "checksum": _row_get(row, "checksum"),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
        "signed_url_strategy": _row_get(row, "signed_url_strategy") or "private",
        "download_url": f"/api/messages/media/{attachment_id}/download",
    }
    waveform = _row_get(row, "waveform_json")
    if waveform:
        try:
            payload["waveform"] = json.loads(waveform)
        except Exception:
            payload["waveform"] = []
    if include_url:
        signed_url = signed_or_private_url(row)
        if signed_url:
            payload["signed_url"] = signed_url
            payload["signed_url_expires_in"] = SIGNED_URL_TTL_SECONDS
    return payload


def signed_or_private_url(row: Any) -> str:
    storage_key = str(_row_get(row, "storage_key", "") or "")
    strategy = str(_row_get(row, "signed_url_strategy", "") or "")
    if not storage_key or strategy == "private_local_endpoint":
        return ""
    try:
        client = media_storage.object_client()
        if not client:
            return ""
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET"), "Key": storage_key},
            ExpiresIn=SIGNED_URL_TTL_SECONDS,
        )
    except Exception as exc:
        logging.warning("MESSENGER_MEDIA_SIGNED_URL_FAILED attachment_id=%s error=%s", _row_get(row, "id", ""), exc)
        return ""


def local_download_path(cur: Any, user: dict[str, Any], attachment_id: int) -> tuple[Path, str, str]:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=False)
    storage_key = str(_row_get(row, "storage_key", "") or "")
    path = _local_path(storage_key)
    if not path.exists():
        raise MessengerMediaError("file_not_available", "Attachment file is not available from local storage.", 404)
    return path, str(_row_get(row, "mime_type", "") or "application/octet-stream"), str(_row_get(row, "original_filename", "") or path.name)


def attachment_download_target(cur: Any, user: dict[str, Any], attachment_id: int) -> dict[str, Any]:
    """Resolve an authorized attachment to local bytes or an expiring object URL."""
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=False)
    storage_key = str(_row_get(row, "storage_key", "") or "")
    mime_type = str(_row_get(row, "mime_type", "") or "application/octet-stream")
    filename = str(_row_get(row, "original_filename", "") or f"attachment-{attachment_id}")
    if storage_key:
        local_path = _local_path(storage_key)
        if local_path.exists():
            return {"kind": "local", "path": local_path, "mime_type": mime_type, "filename": filename}
    signed_url = signed_or_private_url(row)
    if signed_url:
        return {"kind": "signed_redirect", "url": signed_url, "mime_type": mime_type, "filename": filename}
    raise MessengerMediaError("file_not_available", "Attachment file is temporarily unavailable.", 404)
