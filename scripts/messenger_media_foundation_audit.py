#!/usr/bin/env python3
"""Audit the private Messenger media foundation."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read_text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def static_checks():
    bot_text = read_text("bot.py")
    service_text = read_text("services/messenger_media_foundation.py")
    gitignore = read_text(".gitignore")
    required_routes = [
        "/api/messages/media/init",
        "/api/messages/media/upload",
        "/api/messages/media/complete",
        "/api/messages/media/attach",
        "/api/messages/media/<int:attachment_id>",
        "/api/messages/media/<int:attachment_id>/retry",
        "/api/messages/media/<int:attachment_id>/download",
    ]
    for route in required_routes:
        assert_true(route in bot_text, f"missing route {route}")
    for mime in [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "video/mp4",
        "video/webm",
        "audio/webm",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/ogg",
    ]:
        assert_true(mime in service_text, f"missing allowed MIME {mime}")
    for env_name in [
        "MESSENGER_PHOTO_MAX_MB",
        "MESSENGER_VIDEO_MAX_MB",
        "MESSENGER_VOICE_MAX_MB",
        "MESSENGER_FILE_MAX_MB",
    ]:
        assert_true(env_name in service_text, f"missing size limit {env_name}")
    for token in [
        "sanitize_filename",
        "storage_key_for",
        "require_conversation_access",
        "signed_url_strategy",
        "waveform_json",
        "thumbnail_key",
        "processing_status",
        "upload_status",
        "pulse_jobs",
        "local_download_path",
    ]:
        assert_true(token in service_text, f"missing foundation token {token}")
    assert_true("BLOB" not in service_text.upper(), "raw media blob storage must not be used")
    assert_true("storage/messenger_uploads/" in gitignore, "private local uploads must be ignored")


def set_session(client, user_id):
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id


def json_body(response):
    data = response.get_json(silent=True)
    assert_true(isinstance(data, dict), f"expected JSON response, got {response.status_code}: {response.get_data(as_text=True)[:200]}")
    return data


def create_seed_data(bot):
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-02T00:00:00Z"
    users = [
        (91001, "media_sender", "Media Sender", "media.sender@example.test"),
        (91002, "media_member", "Media Member", "media.member@example.test"),
        (91003, "media_outsider", "Media Outsider", "media.outsider@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute(
            """
            INSERT OR REPLACE INTO users (
                user_id, username, display_name, email, signup_time,
                onboarding_complete, alerts_enabled, account_status
            )
            VALUES (?, ?, ?, ?, ?, 1, 0, 'active')
            """,
            (user_id, username, display_name, email, now),
        )
    cur.execute(
        """
        INSERT OR REPLACE INTO pulse_conversations (
            id, conversation_type, created_by_user_id, owner_user_id,
            member_count, status, created_at, updated_at
        )
        VALUES (92001, 'direct', 91001, 91001, 2, 'active', ?, ?)
        """,
        (now, now),
    )
    for user_id in (91001, 91002):
        cur.execute(
            """
            INSERT OR REPLACE INTO pulse_conversation_participants (
                conversation_id, user_id, role, joined_at, created_at
            )
            VALUES (92001, ?, 'member', ?, ?)
            """,
            (user_id, now, now),
        )
    conn.commit()
    conn.close()


def init_attachment(client, media_type, filename, mime_type, size_bytes, expected_status=201):
    response = client.post(
        "/api/messages/media/init",
        json={
            "conversation_id": 92001,
            "media_type": media_type,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
        },
    )
    data = json_body(response)
    assert_true(response.status_code == expected_status, f"init {media_type} expected {expected_status}, got {response.status_code}: {data}")
    return data


def upload_attachment(client, attachment_id, filename, mime_type, body, **metadata):
    form = {"attachment_id": str(attachment_id)}
    form.update({key: str(value) for key, value in metadata.items() if value is not None})
    form["file"] = (io.BytesIO(body), filename, mime_type)
    response = client.post("/api/messages/media/upload", data=form, content_type="multipart/form-data")
    data = json_body(response)
    assert_true(response.status_code == 200, f"upload expected 200, got {response.status_code}: {data}")
    return data


def runtime_checks(tmp_path):
    os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'messenger_media_audit.sqlite3'}"
    os.environ["FORCE_INIT_DB"] = "1"
    os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(tmp_path / "messenger_uploads")
    os.environ["MEDIA_STORAGE_PROVIDER"] = "local"

    import bot  # noqa: WPS433 - audit intentionally imports app after env setup.

    bot.init_db()
    os.environ["FORCE_INIT_DB"] = "0"
    create_seed_data(bot)

    sender = bot.webhook_app.test_client()
    member = bot.webhook_app.test_client()
    outsider = bot.webhook_app.test_client()
    set_session(sender, 91001)
    set_session(member, 91002)
    set_session(outsider, 91003)

    photo = init_attachment(sender, "photo", "photo.jpg", "image/jpeg", 16)
    photo_id = int(photo["attachment_id"])
    photo_upload = upload_attachment(sender, photo_id, "photo.jpg", "image/jpeg", b"\xff\xd8jpeg-audit\xff\xd9", width=320, height=240)
    assert_true(photo_upload["upload_status"] == "uploaded", "photo upload did not reach uploaded state")
    assert_true("storage_key" not in photo_upload and "public_url" not in photo_upload, "upload response exposed private storage fields")

    member_get = member.get(f"/api/messages/media/{photo_id}")
    member_data = json_body(member_get)
    assert_true(member_get.status_code == 200, f"member fetch failed: {member_data}")
    assert_true(member_data["download_url"].endswith(f"/{photo_id}/download"), "private download URL missing")
    assert_true("storage_key" not in member_data and "public_url" not in member_data, "metadata response exposed private storage fields")

    download = member.get(f"/api/messages/media/{photo_id}/download")
    assert_true(download.status_code == 200 and download.data.startswith(b"\xff\xd8"), "member private download failed")

    outsider_get = outsider.get(f"/api/messages/media/{photo_id}")
    outsider_data = json_body(outsider_get)
    assert_true(outsider_get.status_code == 403 and outsider_data["error"] == "not_conversation_member", "non-member fetch was not blocked")

    video = init_attachment(sender, "video", "clip.mp4", "video/mp4", 24)
    video_id = int(video["attachment_id"])
    video_upload = upload_attachment(sender, video_id, "clip.mp4", "video/mp4", b"\x00\x00\x00\x18ftypmp42audit-video")
    assert_true(video_upload["processing_status"] == "queued", "video processing was not queued")

    voice = init_attachment(sender, "voice", "voice.webm", "audio/webm", 20)
    voice_id = int(voice["attachment_id"])
    waveform = json.dumps([0.0, 0.35, 0.8, 0.25])
    voice_upload = upload_attachment(sender, voice_id, "voice.webm", "audio/webm", b"WEBM-audit-voice-note", duration_ms=1800, waveform_json=waveform)
    assert_true(voice_upload["processing_status"] == "ready", "voice waveform metadata was not accepted")
    assert_true(voice_upload.get("waveform") == [0.0, 0.35, 0.8, 0.25], "voice waveform did not round-trip")

    oversized = sender.post(
        "/api/messages/media/init",
        json={
            "conversation_id": 92001,
            "media_type": "photo",
            "filename": "huge.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 16 * 1024 * 1024,
        },
    )
    oversized_data = json_body(oversized)
    assert_true(oversized.status_code == 413 and oversized_data["error"] == "file_too_large", "oversized photo was not rejected")

    unsupported = sender.post(
        "/api/messages/media/init",
        json={
            "conversation_id": 92001,
            "media_type": "file",
            "filename": "bad.exe",
            "mime_type": "application/x-msdownload",
            "size_bytes": 12,
        },
    )
    unsupported_data = json_body(unsupported)
    assert_true(unsupported.status_code == 415 and unsupported_data["error"] == "unsupported_mime_type", "unsupported MIME was not rejected")

    outsider_init = outsider.post(
        "/api/messages/media/init",
        json={
            "conversation_id": 92001,
            "media_type": "photo",
            "filename": "outsider.png",
            "mime_type": "image/png",
            "size_bytes": 10,
        },
    )
    outsider_init_data = json_body(outsider_init)
    assert_true(outsider_init.status_code == 403 and outsider_init_data["error"] == "not_conversation_member", "non-member upload init was not blocked")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT storage_key, checksum FROM message_attachments WHERE id=?", (photo_id,))
    row = dict(cur.fetchone() or {})
    local_path = Path(os.environ["MESSENGER_MEDIA_LOCAL_DIR"]) / row["storage_key"]
    assert_true(local_path.exists(), "local private fallback file was not created")
    assert_true(row.get("checksum"), "checksum was not stored")

    cur.execute(
        "INSERT INTO pulse_messages (conversation_id, sender_user_id, body, message_type, created_at) VALUES (92001, 91001, 'voice caption', 'voice', ?)",
        ("2026-07-02T00:00:01Z",),
    )
    message_id = cur.lastrowid
    conn.commit()
    conn.close()
    attach = sender.post("/api/messages/media/attach", json={"message_id": message_id, "attachments": [voice_id]})
    attach_data = json_body(attach)
    assert_true(attach.status_code == 200 and attach_data["attached"] == [voice_id], f"attach failed: {attach_data}")

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("UPDATE message_attachments SET upload_status='failed', error_code='audit', error_message='audit retry' WHERE id=?", (video_id,))
    conn.commit()
    conn.close()
    retry = sender.post(f"/api/messages/media/{video_id}/retry")
    retry_data = json_body(retry)
    assert_true(retry.status_code == 200 and retry_data["retry_available"] is True and retry_data["upload_status"] == "pending", "retry did not reset failed upload")

    delete_response = sender.delete(f"/api/messages/media/{photo_id}")
    delete_data = json_body(delete_response)
    assert_true(delete_response.status_code == 200 and delete_data["deleted"] is True, "delete did not mark attachment deleted")
    deleted_get = member.get(f"/api/messages/media/{photo_id}")
    deleted_data = json_body(deleted_get)
    assert_true(deleted_get.status_code == 410 and deleted_data["error"] == "attachment_deleted", "deleted attachment remained fetchable")


def main():
    static_checks()
    with tempfile.TemporaryDirectory(prefix="messenger-media-audit-") as tmp:
        runtime_checks(Path(tmp))
    print("Messenger media foundation audit passed.")


if __name__ == "__main__":
    main()
