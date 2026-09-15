"""Private Messenger media upload foundation.

This module keeps Messenger media uploads backend-controlled and private. It
does not wire any composer UI; callers can create upload records, stream files
into private storage, complete metadata, and later attach uploaded media to a
message.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename

from services import db as db_service
from services import media_storage
from services import media_upload_sessions
from services import stored_video_policy

MESSENGER_VIDEO_SURFACE = "messenger"

# Resumable-upload shape is shared with the posts/reels sessions rather than
# re-chosen here. A Messenger-only part size would mean two answers to "how big
# is a part", and the one that is wrong is only discovered on a phone network.
RESUMABLE_THRESHOLD_BYTES = media_upload_sessions.MULTIPART_THRESHOLD
RESUMABLE_PART_SIZE_BYTES = media_upload_sessions.PART_SIZE
RESUMABLE_URL_TTL_SECONDS = media_upload_sessions.SIGNED_URL_TTL_SECONDS
RESUMABLE_SESSION_TTL_SECONDS = media_upload_sessions.SESSION_TTL_SECONDS
MAX_PARTS_PER_SIGN = media_upload_sessions.MAX_PARTS_PER_SIGN
MAX_PARTS = 10000


UPLOAD_STATUSES = {"pending", "uploaded", "attached", "failed", "deleted"}
PROCESSING_STATUSES = {"not_required", "queued", "processing", "ready", "failed"}
MEDIA_TYPES = {"photo", "video", "voice", "file"}

# The single server-side allowlist for everything Messenger will accept as an
# attachment. Nothing outside this table can be uploaded, and the entry decides
# three things: which media_type the file belongs to, which filename extensions
# may accompany that type, and -- crucially -- whether the download route is
# allowed to serve the bytes INLINE.
#
# On ``disposition``. Attachments are served from the product's own origin
# (/api/messages/media/<id>/download), so an inline response is same-origin
# content. That is safe for the media formats a browser renders in a sandboxed
# decoder: images, video and audio cannot execute script. It is NOT safe as a
# blanket rule for documents, and the two entries a reviewer will look for --
# text/html and image/svg+xml -- are deliberately absent from this table rather
# than present-and-forced-to-download, because a stored-XSS vector should not
# depend on one header being correct forever.
#
# Documents therefore enter as ``"disposition": "attachment"``. The download
# route reads it (as_attachment) and the presigner reads it
# (ResponseContentDisposition), so both the local-bytes path and the object-
# storage redirect path force a download instead of rendering. A future entry
# that omits ``disposition`` gets "attachment" from ``disposition_for`` -- the
# default is the safe one, so forgetting the key cannot open a rendering path.
ALLOWED_MIME_TYPES: dict[str, dict[str, Any]] = {
    "image/jpeg": {"media_type": "photo", "extensions": {"jpg", "jpeg"}, "disposition": "inline"},
    "image/png": {"media_type": "photo", "extensions": {"png"}, "disposition": "inline"},
    "image/webp": {"media_type": "photo", "extensions": {"webp"}, "disposition": "inline"},
    "image/heic": {"media_type": "photo", "extensions": {"heic"}, "disposition": "inline"},
    "image/heif": {"media_type": "photo", "extensions": {"heif"}, "disposition": "inline"},
    "video/mp4": {"media_type": "video", "extensions": {"mp4", "m4v"}, "disposition": "inline"},
    # An iPhone's photo library hands the picker a QuickTime movie, and the
    # picker reports it honestly as video/quicktime. Its absence here is what
    # answered "That file type is not supported for Messenger media." to the most
    # ordinary video an iOS user can possibly choose.
    "video/quicktime": {"media_type": "video", "extensions": {"mov", "qt"}, "disposition": "inline"},
    "video/webm": {"media_type": "video", "extensions": {"webm"}, "disposition": "inline"},
    "audio/webm": {"media_type": "voice", "extensions": {"webm"}, "disposition": "inline"},
    "audio/mpeg": {"media_type": "voice", "extensions": {"mp3", "mpeg"}, "disposition": "inline"},
    "audio/mp4": {"media_type": "voice", "extensions": {"mp4", "m4a"}, "disposition": "inline"},
    "audio/wav": {"media_type": "voice", "extensions": {"wav"}, "disposition": "inline"},
    "audio/x-wav": {"media_type": "voice", "extensions": {"wav"}, "disposition": "inline"},
    "audio/ogg": {"media_type": "voice", "extensions": {"ogg", "oga"}, "disposition": "inline"},
    # Documents. media_type "file" already existed here -- MEDIA_TYPES has
    # carried "file" and SIZE_LIMIT_ENV has carried MESSENGER_FILE_MAX_MB since
    # the foundation was written -- but no document MIME type was ever
    # allowlisted, so the branch was unreachable. These entries make the
    # existing "file" path usable rather than introducing a new one, which is
    # why Private Office document sharing needs no separate upload surface.
    "application/pdf": {"media_type": "file", "extensions": {"pdf"}, "disposition": "attachment"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
        "media_type": "file", "extensions": {"docx"}, "disposition": "attachment"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
        "media_type": "file", "extensions": {"xlsx"}, "disposition": "attachment"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {
        "media_type": "file", "extensions": {"pptx"}, "disposition": "attachment"},
    "application/msword": {"media_type": "file", "extensions": {"doc"}, "disposition": "attachment"},
    "application/vnd.ms-excel": {"media_type": "file", "extensions": {"xls"}, "disposition": "attachment"},
    "application/vnd.ms-powerpoint": {"media_type": "file", "extensions": {"ppt"}, "disposition": "attachment"},
    "text/plain": {"media_type": "file", "extensions": {"txt", "log", "md"}, "disposition": "attachment"},
    "text/csv": {"media_type": "file", "extensions": {"csv"}, "disposition": "attachment"},
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
    "video/quicktime": "mov",
    "video/webm": "webm",
    "audio/webm": "webm",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "text/plain": "txt",
    "text/csv": "csv",
}

# Sent by iOS/Android pickers and by Windows clients for the same bytes. An
# alias is a spelling of an ALREADY allowlisted type -- it never widens what is
# accepted, because ``_normalize_mime`` resolves it before the allowlist check.
MIME_ALIASES.update({
    "application/x-pdf": "application/pdf",
    "text/comma-separated-values": "text/csv",
    "application/csv": "text/csv",
    "text/markdown": "text/plain",
    # Spellings of a QuickTime movie seen from Android pickers, older iOS
    # versions and desktop browsers. Each resolves to the entry above.
    "video/x-quicktime": "video/quicktime",
    "video/mov": "video/quicktime",
    "video/x-m4v": "video/mp4",
    "video/x-mp4": "video/mp4",
})

SIZE_LIMIT_ENV = {
    "photo": ("MESSENGER_PHOTO_MAX_MB", 15),
    # 2 GB is what the 90-minute duration ceiling costs at a deliverable bitrate:
    # 2048 MB over 5400 s is ~3.1 Mbps, which is 720p H.264 territory. A 200 MB
    # cap made the duration policy unreachable -- 90 minutes inside it would be
    # ~300 kbps. Note this is the *accepted* size, not the size a phone should
    # send: native 1080p30 is ~17 Mbps, so a full-length capture has to be
    # transcoded before upload rather than squeezed through this limit.
    "video": ("MESSENGER_VIDEO_MAX_MB", 2048),
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
            deleted_at TEXT,
            upload_provider_id TEXT,
            upload_part_size_bytes INTEGER,
            upload_expires_at TEXT
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
        # Resumable-upload session state. Deliberately *not* a completed-parts
        # list: the provider's own `list_parts` is the only account of what was
        # actually stored, and a client-supplied list lets a caller claim a part
        # landed when it did not.
        ("upload_provider_id", "TEXT"),
        ("upload_part_size_bytes", "INTEGER"),
        ("upload_expires_at", "TEXT"),
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


def max_request_mb() -> float:
    """The per-request ceiling for the upload route, in megabytes.

    The request guard runs before the route, so if it caps lower than the
    largest media limit the foundation will accept, the guard wins and the
    caller gets a generic 413 instead of the specific error the client knows
    how to present. Deriving both from this one function is what keeps the two
    from drifting; the override exists for an operator who wants to cap the
    whole route below the per-type limits, and is only honoured downward.
    """
    largest = max(media_limits().values()) / (1024.0 * 1024.0)
    # A multipart envelope carries field boundaries and the filename alongside
    # the bytes, so the request is always slightly larger than the payload.
    ceiling = largest + 8.0
    raw = os.getenv("MESSENGER_MEDIA_MAX_REQUEST_MB", "")
    if raw:
        try:
            override = float(raw)
        except (TypeError, ValueError):
            override = 0.0
        if 0 < override < ceiling:
            return override
    return ceiling


def _normalize_mime(mime_type: str) -> str:
    cleaned = str(mime_type or "").split(";", 1)[0].strip().lower()
    return MIME_ALIASES.get(cleaned, cleaned)


def disposition_for(mime_type: str) -> str:
    """Return "inline" or "attachment" for an allowlisted MIME type.

    Unknown types answer "attachment". A caller reaching here with a type the
    allowlist has never heard of is already in a state that should not render,
    and an entry whose author forgot the key should not silently inherit the
    permissive answer.
    """
    entry = ALLOWED_MIME_TYPES.get(_normalize_mime(mime_type)) or {}
    return "inline" if entry.get("disposition") == "inline" else "attachment"


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


# Reverse index of the allowlist. Insertion order decides the winner for the two
# extensions that two entries share ("mp4" and "webm" are both a video and an
# audio container), which resolves each to its video entry -- the right guess for
# a file chosen out of a photo library.
EXTENSION_TO_MIME: dict[str, str] = {}
for _mime, _entry in ALLOWED_MIME_TYPES.items():
    for _ext in _entry["extensions"]:
        EXTENSION_TO_MIME.setdefault(_ext, _mime)
del _mime, _entry, _ext


def media_class_for_mime(mime_type: str) -> str:
    """Return the media class ("photo"/"video"/"voice"/"file") or "" if unknown."""
    entry = ALLOWED_MIME_TYPES.get(_normalize_mime(mime_type))
    return str(entry["media_type"]) if entry else ""


def resolve_media_class(filename: str, mime_type: str) -> dict[str, str]:
    """The one authority that turns (filename, declared MIME) into a media class.

    Type detection is shared; *policy* (size caps, duration caps, which surface
    accepts which class) stays with the caller. Callers must not re-derive a type
    from a file extension themselves -- that is how Messenger, posts and Reels
    each ended up guessing differently about the same bytes.

    Three inputs are consulted rather than one, because no single one is reliable:
    a picker can report a container spelling the allowlist does not carry, and it
    can equally report ``application/octet-stream`` for a file whose name says
    exactly what it is. The extension may correct the MIME only *within the same
    media class*, so this can never promote a document into a video -- and when it
    rescues an unrecognised MIME it can only ever land on an already allowlisted
    type. The declared type is not evidence about the bytes either way; that is
    what ``sniff_media_class`` checks once the bytes are in hand.
    """
    declared = _normalize_mime(mime_type)
    suffix = Path(sanitize_filename(filename)).suffix.lower().lstrip(".")
    from_extension = EXTENSION_TO_MIME.get(suffix, "")

    resolved = ""
    if declared in ALLOWED_MIME_TYPES:
        declared_class = ALLOWED_MIME_TYPES[declared]["media_type"]
        if not suffix or suffix in ALLOWED_MIME_TYPES[declared]["extensions"]:
            resolved = declared
        elif from_extension and ALLOWED_MIME_TYPES[from_extension]["media_type"] == declared_class:
            resolved = from_extension
        else:
            resolved = declared
    elif from_extension:
        resolved = from_extension

    if not resolved:
        raise MessengerMediaError("unsupported_mime_type", "That file type is not supported for Messenger media.", 415)
    return {
        "mime_type": resolved,
        "media_type": str(ALLOWED_MIME_TYPES[resolved]["media_type"]),
        "extension": _extension_for(filename, resolved),
    }


# Container signatures, checked against the bytes actually received. This exists
# so the declared type is not the only thing standing between the allowlist and
# an executable with a renamed extension. It answers a media *class*, not an
# exact type: distinguishing video/mp4 from video/quicktime from their headers is
# not something the pipeline needs, but distinguishing "a movie" from "a
# Mach-O binary" very much is.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "photo"),          # JPEG
    (b"\x89PNG\r\n\x1a\n", "photo"),     # PNG
    (b"GIF87a", "photo"),
    (b"GIF89a", "photo"),
    (b"%PDF-", "file"),                  # PDF
    (b"PK\x03\x04", "file"),             # zip container: docx/xlsx/pptx
    (b"\xd0\xcf\x11\xe0", "file"),       # legacy OLE: doc/xls/ppt
    (b"ID3", "voice"),                   # MP3 with a tag
    (b"OggS", "voice"),
    (b"RIFF", "voice"),                  # wav (also avi; not allowlisted)
)

# Executable and script containers. None of these can be an allowlisted media
# class, so seeing one means the declared type was a lie.
_FORBIDDEN_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",                  # DOS/PE executable
    b"\x7fELF",             # ELF
    b"\xcf\xfa\xed\xfe",    # Mach-O 64-bit little endian
    b"\xce\xfa\xed\xfe",    # Mach-O 32-bit
    b"\xca\xfe\xba\xbe",    # Mach-O universal / Java class
    b"#!",                  # shebang script
)


def sniff_media_class(header: bytes) -> str:
    """Return the media class the leading bytes look like, or "" if undecided.

    "" is not a pass -- it means this check had nothing to say, which is the
    honest answer for the ISO base-media containers (mp4/m4v/mov/m4a) whose
    ``ftyp`` box sits at offset 4 and whose brand does not reliably separate
    audio-only from video. Callers treat "" as "no evidence" and fall back to the
    allowlist, and treat a *mismatch* as a rejection.
    """
    if not header:
        return ""
    for signature in _FORBIDDEN_SIGNATURES:
        if header.startswith(signature):
            return "forbidden"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return ""
    if len(header) >= 4 and header[:4] == b"\x1a\x45\xdf\xa3":
        return ""  # Matroska/WebM: video/webm and audio/webm share it
    for signature, media_class in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            return media_class
    return ""


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
    filename = sanitize_filename(str(data.get("filename") or data.get("original_filename") or "upload"))
    resolved = resolve_media_class(filename, data.get("mime_type") or data.get("content_type") or "")
    mime_type = resolved["mime_type"]
    mime_media_type = resolved["media_type"]
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
    _reject_overlong_video(mime_media_type, _declared_duration_ms(data))
    extension = resolved["extension"]
    return {
        "conversation_id": conversation_id,
        "media_type": media_type,
        "mime_type": mime_type,
        "filename": filename,
        "extension": extension,
        "size_bytes": size_bytes,
        "max_size_bytes": limit,
    }


def _declared_duration_ms(data: dict[str, Any]) -> float:
    """Read a duration out of a request in either unit the clients send."""
    for key, scale in (("duration_ms", 1.0), ("duration_seconds", 1000.0), ("duration", 1000.0)):
        raw = data.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw) * scale
        except (TypeError, ValueError):
            continue
    return 0.0


def _reject_overlong_video(media_type: str, duration_ms: Any) -> None:
    """Enforce the canonical stored-video duration ceiling.

    Called at /init with the client's declared duration so an over-long file is
    refused before its bytes are uploaded, and again whenever real metadata
    arrives. The client's number is a courtesy, not proof -- the authoritative
    measurement is the one the processing worker probes off the stored file.
    """
    if media_type != "video":
        return
    if stored_video_policy.exceeds_limit_ms(MESSENGER_VIDEO_SURFACE, duration_ms):
        raise MessengerMediaError(
            "video_too_long",
            stored_video_policy.limit_message(MESSENGER_VIDEO_SURFACE),
            413,
        )


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
    session = _open_resumable_session(cur, attachment_id, key, data["mime_type"], data["size_bytes"])
    conn.commit()
    log_event(
        "upload_init", trace_id, user_id, data["conversation_id"], attachment_id,
        media_type=data["media_type"], size_bytes=data["size_bytes"], mime_type=data["mime_type"],
        upload_method=session["upload_method"],
    )
    return ok_response(
        {
            "attachment_id": attachment_id,
            "max_size_bytes": data["max_size_bytes"],
            "media_type": data["media_type"],
            "mime_type": data["mime_type"],
            "trace_id": trace_id,
            **session,
        },
        201,
    )


def _resumable_available() -> bool:
    """Whether bytes can go straight to private object storage.

    Local-disk development has no multipart API, so the single POST stays the
    answer there. This is the only reason the direct path survives.
    """
    return bool(media_storage.object_client() and (os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")))


def _storage_bucket() -> str:
    return os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET") or ""


def _part_count(size_bytes: int, part_size: int) -> int:
    return max(1, -(-int(size_bytes) // int(part_size)))


def _open_resumable_session(cur: Any, attachment_id: int, storage_key: str, mime_type: str, size_bytes: int) -> dict[str, Any]:
    """Open a multipart upload when the file is too big for one request.

    A 90-minute video is the case this exists for. One POST cannot carry it: the
    request has to survive a cell handoff, a backgrounded app and a tunnel that
    drops, and a single stream restarts from zero every time any of those
    happen. Below the threshold the extra round trips cost more than they save,
    so a small photo still goes direct.
    """
    if int(size_bytes) < RESUMABLE_THRESHOLD_BYTES or not _resumable_available():
        return {"upload_method": "direct", "upload_url": "/api/messages/media/upload"}
    part_size = RESUMABLE_PART_SIZE_BYTES
    if _part_count(size_bytes, part_size) > MAX_PARTS:
        # S3/R2 cap a multipart upload at 10,000 parts. Growing the part instead
        # of refusing keeps the ceiling a size decision rather than an accident
        # of arithmetic.
        part_size = -(-int(size_bytes) // MAX_PARTS)
    try:
        created = media_storage.object_client().create_multipart_upload(
            Bucket=_storage_bucket(),
            Key=storage_key,
            ContentType=mime_type,
            CacheControl="private, max-age=0, no-store",
        )
        provider_upload_id = str(created["UploadId"])
    except Exception as exc:
        # A failure here is not fatal: the direct path still works for anything
        # the request ceiling can carry, and saying so beats a 502 on init.
        logging.warning("MESSENGER_MEDIA_RESUMABLE_INIT_FAILED key=%s error=%s", storage_key, str(exc)[:300])
        return {"upload_method": "direct", "upload_url": "/api/messages/media/upload"}
    cur.execute(
        "UPDATE message_attachments SET upload_provider_id=?, upload_part_size_bytes=?, upload_expires_at=?, updated_at=? WHERE id=?",
        (provider_upload_id, int(part_size), _future_iso(RESUMABLE_SESSION_TTL_SECONDS), now_iso(), attachment_id),
    )
    return {
        "upload_method": "resumable",
        "upload_url": "/api/messages/media/upload/parts",
        "part_size_bytes": int(part_size),
        "part_count": _part_count(size_bytes, part_size),
        "max_parts_per_request": MAX_PARTS_PER_SIGN,
        "session_expires_at": _future_iso(RESUMABLE_SESSION_TTL_SECONDS),
    }


def _future_iso(seconds: int) -> str:
    """Same spelling as `now_iso`, because expiry is compared as a string.

    A timestamp without the trailing Z sorts *before* the identical timestamp
    with one, so a mismatched format here would read as expired on creation.
    """
    return datetime.utcfromtimestamp(time.time() + int(seconds)).replace(microsecond=0).isoformat() + "Z"


def _require_resumable_session(cur: Any, user: dict[str, Any], attachment_id: int, allow_expired: bool = False) -> tuple[Any, int, str]:
    """The shared gate for every resumable operation.

    Sender-only, because a conversation member who can *read* an attachment must
    not be able to sign writes into another member's upload.
    """
    if not attachment_id:
        raise MessengerMediaError("attachment_required", "Attachment is required.", 400)
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=True)
    provider_upload_id = str(_row_get(row, "upload_provider_id", "") or "")
    if not provider_upload_id:
        raise MessengerMediaError("upload_not_resumable", "This upload did not open a resumable session.", 409)
    status = str(_row_get(row, "upload_status", "") or "").lower()
    if status in {"uploaded", "attached"}:
        raise MessengerMediaError("upload_already_complete", "This upload has already finished.", 409)
    expires_at = str(_row_get(row, "upload_expires_at", "") or "")
    if not allow_expired and expires_at and expires_at < now_iso():
        # Refreshable rather than fatal: the stored parts are still there, so the
        # client re-authorizes and resumes instead of restarting the transfer.
        raise MessengerMediaError("upload_session_expired", "This upload needs to be re-authorized before it can continue.", 410)
    return row, user_id, provider_upload_id


def _provider_parts(row: Any, provider_upload_id: str) -> list[dict[str, Any]]:
    """What the provider says is stored. The only account of that worth trusting.

    A client-reported parts list is a claim about someone else's storage; taking
    it would let a caller complete an upload out of parts that were never sent.
    """
    client = media_storage.object_client()
    storage_key = str(_row_get(row, "storage_key", "") or "")
    collected: list[dict[str, Any]] = []
    marker = 0
    while True:
        response = client.list_parts(
            Bucket=_storage_bucket(), Key=storage_key, UploadId=provider_upload_id,
            MaxParts=1000, PartNumberMarker=marker,
        )
        for part in response.get("Parts") or []:
            collected.append({
                "part_number": int(part.get("PartNumber") or 0),
                "etag": str(part.get("ETag") or ""),
                "size_bytes": int(part.get("Size") or 0),
            })
        if not response.get("IsTruncated"):
            break
        marker = int(response.get("NextPartNumberMarker") or 0)
        if not marker:
            break
    collected.sort(key=lambda item: item["part_number"])
    return collected


def sign_upload_parts(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Hand the client short-lived URLs for a batch of parts.

    Batched rather than all-at-once because the URLs expire: signing 250 parts up
    front means the tail of a long upload carries dead authorization.
    """
    ensure_schema(cur)
    attachment_id = int(payload.get("attachment_id") or 0)
    row, user_id, provider_upload_id = _require_resumable_session(cur, user, attachment_id)
    part_size = int(_row_get(row, "upload_part_size_bytes", 0) or 0) or RESUMABLE_PART_SIZE_BYTES
    part_count = _part_count(int(_row_get(row, "size_bytes", 0) or 0), part_size)
    requested = payload.get("part_numbers") or payload.get("parts") or []
    numbers = sorted({int(n) for n in requested if str(n).strip().lstrip("-").isdigit() and 1 <= int(n) <= part_count})
    if not numbers:
        raise MessengerMediaError("invalid_part_numbers", "Valid part numbers are required.", 400)
    numbers = numbers[:MAX_PARTS_PER_SIGN]
    client = media_storage.object_client()
    storage_key = str(_row_get(row, "storage_key", "") or "")
    parts = [
        {
            "part_number": number,
            "upload_url": client.generate_presigned_url(
                "upload_part",
                Params={"Bucket": _storage_bucket(), "Key": storage_key, "UploadId": provider_upload_id, "PartNumber": number},
                ExpiresIn=RESUMABLE_URL_TTL_SECONDS,
            ),
        }
        for number in numbers
    ]
    return ok_response({
        "attachment_id": attachment_id,
        "parts": parts,
        "part_size_bytes": part_size,
        "part_count": part_count,
        "signed_url_expires_in": RESUMABLE_URL_TTL_SECONDS,
    })


def resumable_upload_state(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Where to pick the transfer back up, and extend the session while asking.

    This is the whole point of the feature: after a dropped tunnel the client
    asks what landed instead of starting the file again.
    """
    ensure_schema(cur)
    attachment_id = int(payload.get("attachment_id") or 0)
    row, user_id, provider_upload_id = _require_resumable_session(cur, user, attachment_id, allow_expired=True)
    stored = _provider_parts(row, provider_upload_id)
    part_size = int(_row_get(row, "upload_part_size_bytes", 0) or 0) or RESUMABLE_PART_SIZE_BYTES
    declared = int(_row_get(row, "size_bytes", 0) or 0)
    part_count = _part_count(declared, part_size)
    completed = [item["part_number"] for item in stored]
    expires_at = _future_iso(RESUMABLE_SESSION_TTL_SECONDS)
    cur.execute(
        "UPDATE message_attachments SET upload_expires_at=?, updated_at=? WHERE id=?",
        (expires_at, now_iso(), attachment_id),
    )
    conn.commit()
    return ok_response({
        "attachment_id": attachment_id,
        "completed_parts": completed,
        "missing_parts": [n for n in range(1, part_count + 1) if n not in set(completed)],
        "bytes_stored": sum(item["size_bytes"] for item in stored),
        "size_bytes": declared,
        "part_size_bytes": part_size,
        "part_count": part_count,
        "session_expires_at": expires_at,
    })


def finish_resumable_upload(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Stitch the parts, then verify the object before calling it uploaded.

    The byte total is checked *before* completing, so a client that finishes
    early never produces a truncated object someone would later have to explain.
    """
    ensure_schema(cur)
    trace_id = _trace_id()
    attachment_id = int(payload.get("attachment_id") or 0)
    row, user_id, provider_upload_id = _require_resumable_session(cur, user, attachment_id, allow_expired=True)
    media_type = str(_row_get(row, "media_type", "file") or "file")
    storage_key = str(_row_get(row, "storage_key", "") or "")
    declared = int(_row_get(row, "size_bytes", 0) or 0)
    stored = _provider_parts(row, provider_upload_id)
    if not stored:
        raise MessengerMediaError("no_parts_uploaded", "No part of this upload has been received yet.", 409)
    total = sum(item["size_bytes"] for item in stored)
    if declared and total != declared:
        raise MessengerMediaError(
            "upload_incomplete",
            "The upload is not finished yet. Please let it complete and try again.",
            409,
        )
    client = media_storage.object_client()
    client.complete_multipart_upload(
        Bucket=_storage_bucket(), Key=storage_key, UploadId=provider_upload_id,
        MultipartUpload={"Parts": [{"PartNumber": item["part_number"], "ETag": item["etag"]} for item in stored]},
    )
    head = media_storage.head_object(storage_key) or {}
    stored_length = int(head.get("ContentLength") or 0)
    if declared and stored_length and stored_length != declared:
        raise MessengerMediaError("size_mismatch", "The stored file size did not match the upload.", 409)
    _reject_mismatched_object_bytes(storage_key, str(_row_get(row, "mime_type", "") or ""))
    meta = _normalized_metadata(payload)
    _reject_overlong_video(media_type, meta.get("duration_ms"))
    processing_status = _initial_processing_status(media_type, meta.get("waveform") if isinstance(meta.get("waveform"), list) else None)
    cur.execute(
        """
        UPDATE message_attachments
        SET upload_status='uploaded',
            signed_url_strategy=?,
            size_bytes=?,
            duration_ms=COALESCE(?, duration_ms),
            width=COALESCE(?, width),
            height=COALESCE(?, height),
            waveform_json=COALESCE(?, waveform_json),
            processing_status=?,
            error_code='',
            error_message='',
            upload_provider_id=NULL,
            upload_expires_at=NULL,
            updated_at=?
        WHERE id=?
        """,
        (
            media_storage.provider() if media_storage.provider() in {"r2", "s3"} else "private",
            stored_length or total,
            meta.get("duration_ms"),
            meta.get("width"),
            meta.get("height"),
            json.dumps(meta.get("waveform"), separators=(",", ":")) if meta.get("waveform") is not None else None,
            processing_status,
            now_iso(),
            attachment_id,
        ),
    )
    _enqueue_processing_jobs(cur, attachment_id, int(_row_get(row, "conversation_id", 0) or 0), media_type, processing_status)
    conn.commit()
    log_event(
        "upload_resumable_finished", trace_id, user_id, int(_row_get(row, "conversation_id", 0) or 0), attachment_id,
        media_type=media_type, size_bytes=stored_length or total, parts=len(stored),
    )
    return ok_response(_attachment_payload(cur, attachment_id, user_id, include_url=False))


def _reject_mismatched_object_bytes(storage_key: str, expected_mime: str) -> None:
    """Sniff the stored object's header, the same rule the direct path applies.

    Bytes that never passed through Flask still have to be what they claimed to
    be. An unreadable range is not a refusal -- see the direct path's reasoning:
    a storage hiccup must not look like a malicious file.
    """
    try:
        header = media_storage.get_object(storage_key, "bytes=0-511")["Body"].read(16)
    except Exception as exc:
        logging.warning("MESSENGER_MEDIA_OBJECT_HEADER_CHECK_UNAVAILABLE key=%s error=%s", storage_key, str(exc)[:200])
        return
    observed = sniff_media_class(header or b"")
    if not observed:
        return
    if observed == "forbidden":
        raise MessengerMediaError("unsafe_file_contents", "That file cannot be sent as a Messenger attachment.", 415)
    expected_class = media_class_for_mime(expected_mime)
    if expected_class and observed != expected_class:
        raise MessengerMediaError("file_contents_mismatch", "The file contents do not match its type.", 415)


def abort_resumable_upload(cur: Any, conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Discard the parts so an abandoned upload stops being billed storage."""
    ensure_schema(cur)
    attachment_id = int(payload.get("attachment_id") or 0)
    row, user_id, provider_upload_id = _require_resumable_session(cur, user, attachment_id, allow_expired=True)
    try:
        media_storage.object_client().abort_multipart_upload(
            Bucket=_storage_bucket(), Key=str(_row_get(row, "storage_key", "") or ""), UploadId=provider_upload_id,
        )
    except Exception as exc:
        logging.warning("MESSENGER_MEDIA_RESUMABLE_ABORT_FAILED attachment_id=%s error=%s", attachment_id, str(exc)[:200])
    cur.execute(
        "UPDATE message_attachments SET upload_status='failed', error_code='upload_aborted', error_message='Upload was cancelled.', upload_provider_id=NULL, upload_expires_at=NULL, updated_at=? WHERE id=?",
        (now_iso(), attachment_id),
    )
    conn.commit()
    return ok_response({"attachment_id": attachment_id, "upload_status": "failed", "aborted": True})


def _initial_processing_status(media_type: str, waveform: list[float] | None) -> str:
    if media_type == "video":
        return "queued"
    if media_type == "photo":
        return "queued"
    if media_type == "voice":
        return "ready" if waveform else "queued"
    return "not_required"


PROCESSING_JOB_TYPE_BY_MEDIA_TYPE = {
    "photo": "messenger_photo_thumbnail",
    "video": "messenger_video_metadata_thumbnail",
    "voice": "messenger_voice_waveform",
}

# How many job rows one attachment may ever accumulate before its derived assets
# are declared unreachable. The sweep below re-queues stranded work, and without
# a ceiling an attachment whose bytes are genuinely gone would be re-queued on
# every cycle for the life of the product.
MAX_PROCESSING_ROUNDS = max(1, int(os.getenv("MESSENGER_MEDIA_MAX_PROCESSING_ROUNDS", "4")))


def _enqueue_processing_jobs(cur: Any, attachment_id: int, conversation_id: int, media_type: str, status: str) -> None:
    if not attachment_id or status != "queued":
        return
    job_type = PROCESSING_JOB_TYPE_BY_MEDIA_TYPE.get(media_type)
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


def reconcile_processing_backlog(cur: Any, limit: int = 50) -> dict[str, Any]:
    """Re-queue attachments whose processing job died without producing anything.

    ``_enqueue_processing_jobs`` fires exactly once, at attach time, and nothing
    has ever reconciled it. For the whole period the media engine did not
    recognise the three messenger job types, its dispatcher retired each one as
    *done* on first sight: the job row reads ``done``/``attempts=1`` with no
    error, while the attachment it was supposed to serve still sits at
    ``queued`` with no ``thumbnail_key``, no duration and no waveform. Those rows
    are terminal in both directions -- the job will never run again and the
    attachment will never re-enqueue -- so every photo, video and voice note sent
    before the dispatcher was fixed is permanently without a preview.

    The same shape happens for ordinary reasons too: a worker killed mid-job, a
    deploy between claim and completion, a transient byte-store outage that
    exhausted ``max_attempts``.

    So this looks at the attachment rather than the job. An attachment that is
    uploaded, undeleted, unblocked, still in a non-terminal processing state and
    has no live job gets one, which is the same insert the attach path does --
    the worker, ``process_attachment`` and ``_write_processing_result`` are
    untouched, and a re-run of an already-ready attachment is a no-op because
    nothing outside this state selects. Re-uploading is never required.

    Bounded twice over: ``limit`` per sweep, and ``MAX_PROCESSING_ROUNDS`` job
    rows per attachment for all time, after which the attachment is marked
    ``failed`` so the renderer can stop promising a preview that is not coming.
    """
    job_types = sorted(PROCESSING_JOB_TYPE_BY_MEDIA_TYPE.values())
    media_types = sorted(PROCESSING_JOB_TYPE_BY_MEDIA_TYPE)
    cur.execute(
        f"""
        SELECT a.id, a.media_type
        FROM message_attachments a
        WHERE a.media_type IN ({",".join(["?"] * len(media_types))})
          AND LOWER(COALESCE(a.processing_status,'')) IN ('queued','processing')
          AND LOWER(COALESCE(a.upload_status,'')) IN ('uploaded','attached')
          AND a.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM pulse_jobs j
            WHERE j.target_type='message_attachment'
              AND j.target_id=a.id
              AND j.job_type IN ({",".join(["?"] * len(job_types))})
              AND LOWER(COALESCE(j.status,'')) IN ('pending','processing')
          )
        ORDER BY a.id DESC
        LIMIT ?
        """,
        [*media_types, *job_types, max(1, int(limit or 50))],
    )
    candidates = [(int(_row_get(row, "id", 0) or 0), str(_row_get(row, "media_type", "") or "").lower()) for row in cur.fetchall()]
    requeued = 0
    exhausted = 0
    for attachment_id, media_type in candidates:
        job_type = PROCESSING_JOB_TYPE_BY_MEDIA_TYPE.get(media_type)
        if not attachment_id or not job_type:
            continue
        cur.execute(
            "SELECT COUNT(*) FROM pulse_jobs WHERE target_type='message_attachment' AND target_id=? AND job_type=?",
            (attachment_id, job_type),
        )
        rounds = int((cur.fetchone() or [0])[0] or 0)
        if rounds >= MAX_PROCESSING_ROUNDS:
            cur.execute(
                "UPDATE message_attachments SET processing_status='failed', error_code=?, error_message=?, updated_at=? WHERE id=?",
                (
                    "processing_unrecoverable",
                    "Preview could not be generated for this attachment.",
                    now_iso(),
                    attachment_id,
                ),
            )
            exhausted += 1
            logging.warning(
                "MESSENGER_MEDIA_PROCESSING_EXHAUSTED attachment_id=%s media_type=%s rounds=%s",
                attachment_id, media_type, rounds,
            )
            continue
        # Reuses the attach-time insert so there is one definition of what a
        # messenger processing job is.
        _enqueue_processing_jobs(cur, attachment_id, 0, media_type, "queued")
        requeued += 1
        logging.info(
            "MESSENGER_MEDIA_PROCESSING_REQUEUED attachment_id=%s media_type=%s job_type=%s round=%s",
            attachment_id, media_type, job_type, rounds + 1,
        )
    return {"candidates": len(candidates), "requeued": requeued, "exhausted": exhausted}


def _fetch_attachment(cur: Any, attachment_id: int) -> Any:
    cur.execute("SELECT * FROM message_attachments WHERE id=? LIMIT 1", (attachment_id,))
    row = cur.fetchone()
    if not row:
        raise MessengerMediaError("attachment_not_found", "Attachment not found.", 404)
    return row


def _require_attachment_access(cur: Any, row: Any, user_id: int, require_sender: bool = False) -> str:
    if _row_get(row, "deleted_at") or str(_row_get(row, "upload_status", "")).lower() == "deleted":
        raise MessengerMediaError("attachment_deleted", "Attachment has been deleted.", 410)
    # Every read of an attachment comes through here, which is the only reason a
    # measured-too-long video can be taken back after it was already attached to a
    # message. Gating the download route alone would leave the thumbnail and the
    # metadata still serving it.
    if str(_row_get(row, "upload_status", "")).lower() == "blocked":
        raise MessengerMediaError(
            "attachment_blocked",
            str(_row_get(row, "error_message", "") or "This attachment is not available."),
            410,
        )
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
    # A multipart part may spell the container differently from the /init call
    # that reserved this row -- an iOS picker reporting video/quicktime at init
    # and the upload body arriving as video/mp4 is the same movie, and rejecting
    # it here would reproduce the original failure one step later. Disagreement
    # *across* media classes is still a refusal.
    if actual_mime and actual_mime != expected_mime:
        expected_class = media_class_for_mime(expected_mime)
        actual_class = media_class_for_mime(actual_mime)
        if not expected_class or actual_class != expected_class:
            raise MessengerMediaError("mime_type_mismatch", "Uploaded file type does not match the initialized attachment.", 415)
    media_type = str(_row_get(row, "media_type", "file") or "file")
    limit = max_size_for(media_type)
    storage_key = str(_row_get(row, "storage_key", "") or "")
    temp_path, size_bytes, checksum = _spool_upload(file_storage, limit)
    _reject_mismatched_bytes(temp_path, expected_mime)
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
        _reject_overlong_video(media_type, meta.get("duration_ms"))
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


def _reject_mismatched_bytes(temp_path: str, expected_mime: str) -> None:
    """Refuse bytes that contradict the type the attachment was reserved for.

    This runs on the spooled file rather than on the declared header, so it is
    the first point in the pipeline that has seen actual evidence. It deletes the
    spool before raising -- the caller's error path never gets to run for a
    validation refusal, so leaving it behind would leak a temp file per attempt.
    """
    try:
        with open(temp_path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return
    observed = sniff_media_class(header)
    if not observed:
        return
    expected_class = media_class_for_mime(expected_mime)
    if observed == "forbidden":
        _delete_temp(temp_path)
        raise MessengerMediaError("unsafe_file_contents", "That file cannot be sent as a Messenger attachment.", 415)
    if expected_class and observed != expected_class:
        _delete_temp(temp_path)
        raise MessengerMediaError("file_contents_mismatch", "The file contents do not match its type.", 415)


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
    _reject_overlong_video(str(_row_get(row, "media_type", "file") or "file"), meta.get("duration_ms"))
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


PROCESSING_JOB_TYPES = {
    "messenger_photo_thumbnail",
    "messenger_video_metadata_thumbnail",
    "messenger_voice_waveform",
}

THUMBNAIL_MAX_EDGE = 480
THUMBNAIL_MIME = "image/jpeg"

# Strategy values that mean "this attachment's bytes are in object storage".
# Three spellings for one fact, because the multipart finish path records the
# provider name while the direct-upload path records how the URL will be built.
# Anything outside this set is on local disk and is served by the download route.
REMOTE_OBJECT_STRATEGIES = {"r2", "s3", "signed"}


def process_attachment(cur: Any, attachment_id: int, job_type: str) -> dict[str, Any]:
    """Produce the derived assets an attachment's bubble needs, then mark it ready.

    The three job types this answers to were enqueued from the day the
    foundation was written and consumed by nothing: the media engine's
    ``MEDIA_JOB_TYPES`` never listed them, and its dispatcher retires an
    unrecognised type as *done*. So every photo, video and voice note has been
    draining its own processing job without producing a thumbnail, a duration or
    a waveform, leaving ``processing_status`` at ``queued`` forever and the
    thread with nothing to show but the full asset. That is the black card and
    most of the slowness.

    Raising is how a genuine failure reaches the worker's retry budget; a
    *recoverable* absence (no ffmpeg, no bytes yet) returns instead, because
    retrying it three times and retiring the job would strand the attachment.
    """
    ensure_schema(cur)
    if job_type not in PROCESSING_JOB_TYPES:
        return {"status": "skipped", "reason": "unknown_job_type"}
    row = _fetch_attachment(cur, attachment_id)
    if _row_get(row, "deleted_at"):
        return {"status": "skipped", "reason": "deleted"}
    # Settled, not deferred. A blocked attachment is never going to become
    # uploaded again, and deferral reschedules rather than spending the error
    # budget -- so calling this "incomplete" would retry it every two minutes for
    # as long as the row exists.
    if str(_row_get(row, "upload_status", "")).lower() == "blocked":
        return {"status": "skipped", "reason": "blocked"}
    if str(_row_get(row, "upload_status", "")).lower() not in {"uploaded", "attached"}:
        return {"status": "deferred", "reason": "upload_incomplete"}

    media_type = str(_row_get(row, "media_type", "") or "")
    source = _local_source_for(row)
    if not source:
        return {"status": "deferred", "reason": "bytes_unavailable"}

    temporary = source["temporary"]
    path = source["path"]
    try:
        if media_type == "voice":
            result = _derive_voice_assets(path)
        elif media_type == "video":
            result = _derive_video_assets(cur, row, path)
        elif media_type == "photo":
            result = _derive_photo_assets(cur, row, path)
        else:
            result = {"status": "skipped", "reason": "no_processing_for_type"}
    finally:
        if temporary:
            _delete_temp(str(path))

    if result.get("status") == "deferred":
        return result

    updates = result.get("updates") or {}
    # ffprobe has just read the real length off the container. Until now that
    # number was only ever written down; the ceiling was enforced against the
    # client's declared `duration_ms` at /init and /finish, which a caller talking
    # to the API directly simply gets to choose. This is the same rule applied to
    # the one duration nobody outside the server picked.
    reason = stored_video_policy.measured_violation_ms(MESSENGER_VIDEO_SURFACE, updates.get("duration_ms")) if media_type == "video" else ""
    if reason:
        _block_overlong_attachment(cur, attachment_id, updates, reason)
        return {"status": "rejected", "reason": stored_video_policy.MEASURED_REJECTION_CODE, "measured_duration_ms": updates.get("duration_ms")}
    _write_processing_result(cur, attachment_id, updates)
    return {"status": "processed", "updates": sorted(updates)}


def _block_overlong_attachment(cur: Any, attachment_id: int, updates: dict[str, Any], reason: str) -> None:
    """Take an attachment away once its measured length is known to break the rule.

    `blocked` rather than `failed`: the upload did not fail, and a sender told
    "upload failed" would reasonably retry the same file forever. It is also the
    state `attach_attachments` refuses, so an attachment measured before the
    sender attaches it never reaches a message at all -- and `_require_attachment_access`
    turns the already-attached case into the same 410 a deleted attachment gets.

    The measurement is stored alongside the refusal. Without it the row says only
    that something was wrong, and the next person to look has to re-download 2 GB
    to find out what.
    """
    cur.execute(
        """
        UPDATE message_attachments
        SET upload_status='blocked', processing_status='rejected_too_long',
            duration_ms=COALESCE(?, duration_ms), error_code=?, error_message=?, updated_at=?
        WHERE id=?
        """,
        (updates.get("duration_ms"), stored_video_policy.MEASURED_REJECTION_CODE, reason[:1000], now_iso(), attachment_id),
    )
    logging.warning(
        "MESSENGER_MEDIA_DURATION_ENFORCED attachment_id=%s measured_duration_ms=%s limit_seconds=%s",
        attachment_id, updates.get("duration_ms"), stored_video_policy.max_duration_seconds(MESSENGER_VIDEO_SURFACE),
    )


def _local_source_for(row: Any) -> dict[str, Any] | None:
    """Local bytes for an attachment, fetched from object storage if need be.

    Returns ``temporary`` so the caller knows whether deleting the path would
    destroy the only copy of the upload.
    """
    storage_key = str(_row_get(row, "storage_key", "") or "")
    if not storage_key:
        return None
    local_path = _local_path(storage_key)
    if local_path.exists():
        return {"path": local_path, "temporary": False}
    client = media_storage.object_client()
    bucket = os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")
    if not client or not bucket:
        return None
    handle = tempfile.NamedTemporaryFile(delete=False, prefix="messenger-process-", suffix=".bin")
    handle.close()
    try:
        client.download_file(bucket, storage_key, handle.name)
    except Exception as exc:
        _delete_temp(handle.name)
        logging.warning("MESSENGER_MEDIA_PROCESS_FETCH_FAILED key=%s error=%s", storage_key, exc)
        return None
    return {"path": Path(handle.name), "temporary": True}


def _write_processing_result(cur: Any, attachment_id: int, updates: dict[str, Any]) -> None:
    assignments = ["processing_status='ready'", "error_code=''", "error_message=''", "updated_at=?"]
    params: list[Any] = [now_iso()]
    for column in ("thumbnail_key", "duration_ms", "width", "height", "waveform_json"):
        if column not in updates:
            continue
        # COALESCE so a re-run that produced nothing new cannot erase a value a
        # previous pass, or the client's own metadata, already established.
        assignments.insert(-1, f"{column}=COALESCE(?, {column})")
        params.insert(-1, updates[column])
    cur.execute(
        f"UPDATE message_attachments SET {', '.join(assignments)} WHERE id=?",
        (*params, attachment_id),
    )


def _derive_voice_assets(path: Path) -> dict[str, Any]:
    duration_ms = _probe_duration_ms(path)
    if duration_ms is None and not shutil.which("ffprobe"):
        return {"status": "deferred", "reason": "ffprobe_missing"}
    updates: dict[str, Any] = {}
    if duration_ms:
        updates["duration_ms"] = duration_ms
    return {"status": "processed", "updates": updates}


def _derive_video_assets(cur: Any, row: Any, path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        return {"status": "deferred", "reason": "ffmpeg_missing"}
    updates: dict[str, Any] = {}
    duration_ms = _probe_duration_ms(path)
    if duration_ms:
        updates["duration_ms"] = duration_ms
    dimensions = _probe_dimensions(path)
    if dimensions:
        updates["width"], updates["height"] = dimensions
    poster = _extract_video_poster(path, duration_ms)
    if poster:
        key = _store_derived_thumbnail(row, poster)
        if key:
            updates["thumbnail_key"] = key
    return {"status": "processed", "updates": updates}


def _derive_photo_assets(cur: Any, row: Any, path: Path) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        return {"status": "deferred", "reason": "ffmpeg_missing"}
    updates: dict[str, Any] = {}
    dimensions = _probe_dimensions(path)
    if dimensions:
        updates["width"], updates["height"] = dimensions
    thumbnail = _scale_image(path)
    if thumbnail:
        key = _store_derived_thumbnail(row, thumbnail)
        if key:
            updates["thumbnail_key"] = key
    return {"status": "processed", "updates": updates}


def _store_derived_thumbnail(row: Any, temp_path: str) -> str:
    """Put a generated thumbnail beside its source, under the same storage authority.

    "The same storage authority" is the whole job, and getting it wrong is silent.
    The two finish paths record the identical fact -- *this object is in R2* -- in
    two different vocabularies: the multipart path writes the provider name
    (``r2``/``s3``), the direct-upload path writes ``signed``, and both fall back
    to ``private_local_endpoint`` when the object really is on local disk. The
    read path already knows this and presigns anything that is not the local
    strategy, which is why sources resolve fine either way.

    This function used to accept only ``{r2, s3}``. Every ``signed`` row -- the
    common case, since that is what the direct upload writes on success -- took
    the local branch, wrote the thumbnail to a container filesystem that does not
    survive a deploy, and then returned the key as though it had been stored. The
    row was marked ``ready`` with a ``thumbnail_key`` naming an object that had
    never existed in R2 for even a moment. Nothing downstream can detect that: the
    grant is valid, the presigned URL is well-formed, and the 404 only arrives in
    the renderer. On 2026-09-14 that was 38 of the 39 attachments carrying a
    thumbnail key, and the correlation with the strategy value was exact -- the
    single ``r2`` row was the single row whose thumbnail was present.

    So the remote set has to match what the read path will do with the key, and a
    failed upload must not be reported as a stored one. Returning empty leaves the
    attachment ready with no thumbnail, which every caller already handles by
    showing the full asset; inventing a key it cannot serve is the one outcome
    that has no recovery.
    """
    storage_key = str(_row_get(row, "storage_key", "") or "")
    if not storage_key:
        _delete_temp(temp_path)
        return ""
    key = f"{storage_key.rsplit('.', 1)[0]}-thumb.jpg"
    if str(_row_get(row, "signed_url_strategy", "") or "").lower() in REMOTE_OBJECT_STRATEGIES:
        uploaded, error = _upload_private_object(temp_path, key, THUMBNAIL_MIME)
        if uploaded:
            _delete_temp(temp_path)
            return key
        # No local fallback here. The source for this row lives in R2, so the read
        # path will presign this key against R2 and get a 404 no matter what is
        # sitting on this container's disk.
        logging.warning("MESSENGER_MEDIA_THUMBNAIL_UPLOAD_FAILED key=%s error=%s", key, error)
        _delete_temp(temp_path)
        return ""
    _store_local_private(temp_path, key)
    return key


def _ffprobe_value(path: Path, entry: str, *select: str) -> str:
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", *select, "-show_entries", entry, "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=45, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _probe_duration_ms(path: Path) -> int | None:
    raw = _ffprobe_value(path, "format=duration")
    if not raw:
        return None
    try:
        seconds = float(raw.splitlines()[0])
    except (TypeError, ValueError, IndexError):
        return None
    return int(seconds * 1000) if seconds > 0 else None


def _probe_dimensions(path: Path) -> tuple[int, int] | None:
    """The largest video stream, which for a tiled image is not the first one.

    An iPhone HEIC is routinely a grid: ffprobe reports one stream per tile
    ahead of the full image. Production attachment 80 has 95 streams -- sixty
    512x512 tiles before the 2016x1512 photo -- so reading the first pair stored
    a square size for a 4:3 picture, and every bubble laid out from those values
    reserved a square box. Attachment 79 had the same shape.

    Single-stream media is unaffected: one pair in, the same pair out, which is
    what keeps the generated-thumbnail assertions honest.
    """
    # -select_streams v so a subtitle or data stream cannot contribute an N/A
    # into the positional pairing below.
    raw = _ffprobe_value(path, "stream=width,height", "-select_streams", "v")
    values = [int(line.strip()) for line in raw.splitlines() if line.strip().isdigit()]
    pairs = [(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]
    candidates = [pair for pair in pairs if pair[0] > 0 and pair[1] > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0] * pair[1])


def _extract_video_poster(path: Path, duration_ms: int | None) -> str:
    # One second in, not zero: the first frame of a phone recording is very often
    # the black frame the sensor emits before exposure settles, which is the
    # "black rectangle" this poster exists to prevent.
    offset = 1.0
    if duration_ms and duration_ms < 2000:
        offset = max(0.0, (duration_ms / 1000.0) / 2)
    return _run_ffmpeg_thumbnail(["-ss", f"{offset:.2f}", "-i", str(path), "-frames:v", "1"])


def _scale_image(path: Path) -> str:
    return _run_ffmpeg_thumbnail(["-i", str(path), "-frames:v", "1"])


def _run_ffmpeg_thumbnail(source_args: list[str]) -> str:
    handle = tempfile.NamedTemporaryFile(delete=False, prefix="messenger-thumb-", suffix=".jpg")
    handle.close()
    command = [
        "ffmpeg", "-y", "-loglevel", "error", *source_args,
        "-vf", f"scale='min({THUMBNAIL_MAX_EDGE},iw)':-2",
        "-f", "image2", handle.name,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _delete_temp(handle.name)
        logging.warning("MESSENGER_MEDIA_THUMBNAIL_FFMPEG_FAILED error=%s", exc)
        return ""
    if completed.returncode != 0 or not Path(handle.name).exists() or Path(handle.name).stat().st_size == 0:
        # Say why. An empty return here is indistinguishable from "this media has
        # no frame to take", and the caller turns both into a `processed` result
        # with no thumbnail -- so a silent failure leaves the attachment looking
        # deliberately preview-less and leaves no trace anywhere. Recovering the
        # five rows this stranded on 2026-09-14 meant pulling the originals out
        # of R2 and guessing, because ffmpeg's own account of the failure had
        # been captured and then dropped on the floor.
        logging.warning(
            "MESSENGER_MEDIA_THUMBNAIL_EMPTY returncode=%s args=%s stderr=%s",
            completed.returncode, " ".join(source_args), (completed.stderr or "").strip()[:500],
        )
        _delete_temp(handle.name)
        return ""
    return handle.name


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
    # Advertised only once the preview actually exists. A URL offered before the
    # pipeline has run would make every bubble in a fresh thread fetch, 404, and
    # then fall back to the full asset -- the client must be able to tell "no
    # preview yet" from "preview here" without paying a request to find out.
    if _row_get(row, "thumbnail_key"):
        payload["thumbnail_url"] = f"/api/messages/media/{attachment_id}/thumbnail"
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
        params: dict[str, Any] = {
            "Bucket": os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET"),
            "Key": storage_key,
        }
        # A presigned URL bypasses the download route entirely, so the
        # disposition decision has to be baked into the signature. Without
        # this, a document served straight from object storage would render
        # inline on the storage origin no matter what the route did.
        mime_type = _normalize_mime(_row_get(row, "mime_type", ""))
        if disposition_for(mime_type) == "attachment":
            filename = sanitize_filename(str(_row_get(row, "original_filename", "") or "attachment"))
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=SIGNED_URL_TTL_SECONDS,
        )
    except Exception as exc:
        logging.warning("MESSENGER_MEDIA_SIGNED_URL_FAILED attachment_id=%s error=%s", _row_get(row, "id", ""), exc)
        return ""


# ---------------------------------------------------------------------------
# Media access credentials
#
# A bounded credential that proves "this viewer may render this one attachment,
# until this moment". It is deliberately NOT a session credential: it carries no
# refresh material, it cannot be exchanged for one, and presenting an invalid one
# must never cost the holder their login. The download route still re-checks
# conversation membership in the database on every request -- this is transport
# authorization, not an authorization bypass.
# ---------------------------------------------------------------------------

ACCESS_TOKEN_TTL_SECONDS = max(60, int(os.getenv("PULSESOC_MESSENGER_MEDIA_TOKEN_TTL_SECONDS", "900")))
ACCESS_TOKEN_ARG = "mt"
ACCESS_TOKEN_HEADER = "X-PulseSoc-Media-Token"


def _access_token_signature(secret: str, body: str) -> str:
    return hmac.new(
        str(secret or "").encode("utf-8"),
        ("messenger_media:v1:" + body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mint_access_token(secret: str, attachment_id: int, user_id: int, ttl_seconds: int | None = None) -> tuple[str, int]:
    """Return (token, expires_at) bound to one attachment, one viewer, one window."""
    if not secret:
        raise ValueError("messenger media access secret is required")
    ttl = max(60, int(ttl_seconds or ACCESS_TOKEN_TTL_SECONDS))
    expires_at = int(time.time()) + ttl
    body = "%d.%d.%d" % (int(attachment_id), int(user_id), expires_at)
    return body + "." + _access_token_signature(secret, body), expires_at


def access_token_state(secret: str, attachment_id: int, token: str) -> tuple[str, int]:
    """Classify a media token: ("absent"|"invalid"|"expired"|"valid", user_id).

    Expiry is reported separately from forgery because the two mean different
    things to the caller: an expired grant is a routine, renewable media
    condition, while a bad signature is a denial. Neither is ever an
    authentication event -- this function reads nothing and writes nothing, and
    in particular never touches session state.
    """
    parts = str(token or "").strip().split(".")
    if not str(token or "").strip():
        return "absent", 0
    if len(parts) != 4:
        return "invalid", 0
    body = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _access_token_signature(secret, body)):
        return "invalid", 0
    try:
        token_attachment = int(parts[0])
        token_user = int(parts[1])
        token_expiry = int(parts[2])
    except (TypeError, ValueError):
        return "invalid", 0
    if token_attachment != int(attachment_id):
        return "invalid", 0
    if token_expiry <= int(time.time()):
        logging.info("MESSENGER_MEDIA_SIGNATURE_EXPIRED attachment_id=%s", attachment_id)
        return "expired", 0
    return "valid", max(0, token_user)


def access_token_user_id(secret: str, attachment_id: int, token: str) -> int:
    """Return the viewer a token authorizes for this attachment, else 0."""
    return access_token_state(secret, attachment_id, token)[1]


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
    # The route must not have to re-derive this from the MIME type: one place
    # decides whether these bytes may render, and it is the allowlist.
    disposition = disposition_for(mime_type)
    if storage_key:
        local_path = _local_path(storage_key)
        if local_path.exists():
            return {
                "kind": "local", "path": local_path, "mime_type": mime_type,
                "filename": filename, "disposition": disposition,
            }
    signed_url = signed_or_private_url(row)
    if signed_url:
        return {
            "kind": "signed_redirect", "url": signed_url, "mime_type": mime_type,
            "filename": filename, "disposition": disposition,
        }
    raise MessengerMediaError("file_not_available", "Attachment file is temporarily unavailable.", 404)


def attachment_thumbnail_target(cur: Any, user: dict[str, Any], attachment_id: int) -> dict[str, Any]:
    """Resolve an authorized attachment to its derived preview image.

    This never falls back to the original asset. A thread that renders fifty
    bubbles asks for fifty thumbnails, and answering one of them with the full
    video would download gigabytes to paint a card a few hundred pixels
    wide -- which is the performance defect this route exists to remove, so
    serving the original "just this once" would reintroduce it silently. When
    there is no thumbnail yet the honest answer is 404, and the client shows a
    placeholder and keeps the ``processing_status`` it already has.

    The bytes are always the JPEG we produced ourselves, so the MIME type is
    fixed rather than read from the row: the thumbnail's type is a property of
    the pipeline, not of whatever the sender uploaded, and deriving it from the
    row would let a document's type ride out on an image response.
    """
    user_id = int(user.get("user_id") or user.get("id") or 0)
    row = _fetch_attachment(cur, attachment_id)
    _require_attachment_access(cur, row, user_id, require_sender=False)
    thumbnail_key = str(_row_get(row, "thumbnail_key", "") or "")
    if not thumbnail_key:
        raise MessengerMediaError("thumbnail_not_available", "No preview has been generated for this attachment.", 404)
    local_path = _local_path(thumbnail_key)
    if local_path.exists():
        return {"kind": "local", "path": local_path, "mime_type": THUMBNAIL_MIME}
    signed_url = _signed_thumbnail_url(row, thumbnail_key)
    if signed_url:
        return {"kind": "signed_redirect", "url": signed_url, "mime_type": THUMBNAIL_MIME}
    raise MessengerMediaError("thumbnail_not_available", "Preview is temporarily unavailable.", 404)


def _signed_thumbnail_url(row: Any, thumbnail_key: str) -> str:
    if str(_row_get(row, "signed_url_strategy", "") or "") == "private_local_endpoint":
        return ""
    try:
        client = media_storage.object_client()
        if not client:
            return ""
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET"),
                "Key": thumbnail_key,
                "ResponseContentType": THUMBNAIL_MIME,
            },
            ExpiresIn=SIGNED_URL_TTL_SECONDS,
        )
    except Exception as exc:
        logging.warning(
            "MESSENGER_MEDIA_THUMBNAIL_URL_FAILED attachment_id=%s error=%s", _row_get(row, "id", ""), exc
        )
        return ""
