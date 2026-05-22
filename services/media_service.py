"""Secure local/S3-ready media upload scaffold for chat and Arena comments."""

import mimetypes
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.utils import secure_filename

from . import user_context


ALLOWED_TYPES = {x.strip().lower() for x in os.getenv("MEDIA_UPLOAD_ALLOWED_TYPES", "jpg,jpeg,png,webp,gif,mp4,webm,mov,mp3,m4a,wav,ogg,pdf,txt,doc,docx").split(",")}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
GIF_EXTS = {"gif"}
VIDEO_EXTS = {"mp4", "webm", "mov"}
AUDIO_EXTS = {"mp3", "m4a", "wav", "ogg"}
FILE_EXTS = {"pdf", "txt", "doc", "docx"}
UPLOAD_ROOT = Path(os.getenv("MEDIA_UPLOAD_DIR", "static/uploads/chat_media"))


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _limit_bytes(ext):
    if ext in IMAGE_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_IMAGE_MB", "5")) * 1024 * 1024)
    if ext in GIF_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_GIF_MB", "8")) * 1024 * 1024)
    if ext in AUDIO_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_AUDIO_MB", "15")) * 1024 * 1024)
    if ext in FILE_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_FILE_MB", "12")) * 1024 * 1024)
    return int(float(os.getenv("MEDIA_UPLOAD_MAX_VIDEO_MB", "25")) * 1024 * 1024)


def _media_type(ext):
    if ext in IMAGE_EXTS:
        return "image"
    if ext in GIF_EXTS:
        return "gif"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in FILE_EXTS:
        return "file"
    return ""


def _image_header_ok(ext, header):
    if ext in {"jpg", "jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return True


def _public(row):
    if not row:
        return None
    item = dict(row)
    return {
        "id": item.get("id"),
        "media_url": item.get("media_url"),
        "thumbnail_url": item.get("thumbnail_url") or item.get("media_url"),
        "media_type": item.get("media_type"),
        "mime_type": item.get("mime_type"),
        "file_size_bytes": item.get("file_size_bytes"),
        "width": item.get("width"),
        "height": item.get("height"),
        "moderation_status": item.get("moderation_status") or "pending",
    }


def rate_limited(user_id, media_type):
    conn = user_context.connect()
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat(timespec="seconds")
    cur.execute("SELECT COUNT(*) AS c FROM chat_media_uploads WHERE uploader_user_id=? AND created_at>=?", (int(user_id), cutoff))
    recent = int((cur.fetchone() or {"c": 0})["c"] or 0)
    if recent >= 10:
        conn.close()
        return True
    if media_type == "video":
        video_cutoff = (datetime.utcnow() - timedelta(minutes=30)).isoformat(timespec="seconds")
        cur.execute("SELECT COUNT(*) AS c FROM chat_media_uploads WHERE uploader_user_id=? AND media_type='video' AND created_at>=?", (int(user_id), video_cutoff))
        videos = int((cur.fetchone() or {"c": 0})["c"] or 0)
        conn.close()
        return videos >= 3
    conn.close()
    return False


def save_upload(user_id, file_storage, context_type="private_chat", context_id=""):
    if not file_storage or not file_storage.filename:
        return {"ok": False, "message": "Choose a photo, GIF, video, voice note, audio clip, or safe file."}, 400
    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_TYPES:
        return {"ok": False, "message": "That file type is not supported."}, 400
    media_type = _media_type(ext)
    if not media_type:
        return {"ok": False, "message": "That file type is not supported."}, 400
    if rate_limited(user_id, media_type):
        return {"ok": False, "message": "You’re sending media quickly. Try again in a few minutes."}, 429
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > _limit_bytes(ext):
        return {"ok": False, "message": "File too large. Please upload a smaller media file."}, 400
    if media_type in {"image", "gif"}:
        header = file_storage.stream.read(512)
        file_storage.stream.seek(0)
        if not _image_header_ok(ext, header):
            return {"ok": False, "message": "This image or GIF could not be verified safely."}, 400
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    stored = f"{datetime.utcnow().strftime('%Y%m%d')}_{secrets.token_urlsafe(16)}.{ext}"
    path = UPLOAD_ROOT / stored
    file_storage.save(path)
    mime = file_storage.mimetype or mimetypes.guess_type(original)[0] or "application/octet-stream"
    url = "/" + str(path).replace(os.sep, "/")
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, context_id, original_filename, stored_filename, media_url, thumbnail_url,
         media_type, mime_type, file_size_bytes, moderation_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)
        """,
        (int(user_id), context_type, str(context_id or ""), original, stored, url, url if media_type != "video" else "", media_type, mime, int(size), _now()),
    )
    media_id = int(cur.lastrowid)
    conn.commit()
    cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,))
    row = cur.fetchone()
    conn.close()
    return {"ok": True, "media": _public(row)}, 200


def attach_media_to_message(user_id, message_id, media_ids, context_type="private_chat", context_id=""):
    ids = [int(x) for x in (media_ids or []) if str(x).isdigit()]
    if not ids:
        return []
    conn = user_context.connect()
    cur = conn.cursor()
    attached = []
    for media_id in ids[:4]:
        cur.execute("SELECT * FROM chat_media_uploads WHERE id=? AND uploader_user_id=? AND message_id IS NULL LIMIT 1", (media_id, int(user_id)))
        row = cur.fetchone()
        if not row:
            continue
        cur.execute(
            "UPDATE chat_media_uploads SET message_id=?, context_type=?, context_id=? WHERE id=?",
            (int(message_id), context_type, str(context_id or ""), media_id),
        )
        attached.append(_public(row))
    conn.commit()
    conn.close()
    return attached


def media_for_messages(message_ids):
    ids = [int(x) for x in (message_ids or []) if int(x or 0)]
    if not ids:
        return {}
    conn = user_context.connect()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(f"SELECT * FROM chat_media_uploads WHERE message_id IN ({placeholders}) AND moderation_status!='blocked' ORDER BY id ASC", ids)
    out = {}
    for row in cur.fetchall():
        item = _public(row)
        out.setdefault(int(row["message_id"]), []).append(item)
    conn.close()
    return out


def report_media(user_id, media_id, reason=""):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE chat_media_uploads SET moderation_status='pending', moderation_reason=? WHERE id=?", (str(reason or "reported")[:500], int(media_id)))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Media reported for review."}
