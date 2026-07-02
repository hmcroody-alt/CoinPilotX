#!/usr/bin/env python3
"""Verify Messenger composer wiring uses the private media foundation."""

from __future__ import annotations

import io
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


def json_body(response):
    data = response.get_json(silent=True)
    assert_true(isinstance(data, dict), f"expected JSON response, got {response.status_code}: {response.get_data(as_text=True)[:200]}")
    return data


def set_session(client, user_id):
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id


def static_checks():
    js = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
    service = (ROOT / "pulse_communications_v2/service.py").read_text(encoding="utf-8")
    foundation = (ROOT / "services/messenger_media_foundation.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
    assert_true('const MEDIA_API = "/api/messages/media"' in js, "composer does not target media foundation API")
    assert_true('fetch(`${API}/attachments/upload`' not in js, "composer still calls legacy comm_v2 attachment upload")
    for token in ('"/init"', '"/upload"', '"/complete"', "attachment_ids", "pendingAttachmentPreviews", "mediaFoundationMimeType", "hydrateRenderedMessages"):
        assert_true(token in js, f"missing composer wiring token {token}")
    for token in ("_validate_foundation_attachment_ids", "_attach_foundation_media", "conversation_model='comm_v2'"):
        assert_true(token in service, f"comm_v2 send missing foundation bridge token {token}")
    for token in ("comm_v2_participants", "comm_v2_conversations", "comm_v2_messages"):
        assert_true(token in foundation, f"media foundation missing comm_v2 access token {token}")
    assert_true("data-composer-shell" in template and "data-attachment-preview" in template and "data-voice-panel" in template, "composer shell/accessory structure missing")


def seed_comm_v2(bot, conversation_id=94001):
    from pulse_communications_v2.models import ensure_schema as ensure_comm_schema
    from services import messenger_media_foundation

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_comm_schema(cur)
    messenger_media_foundation.ensure_schema(cur, conn)
    now = "2026-07-02T12:00:00Z"
    for user_id, username, display_name, email in [
        (94101, "composer_sender", "Composer Sender", "composer.sender@example.test"),
        (94102, "composer_recipient", "Composer Recipient", "composer.recipient@example.test"),
        (94103, "composer_outsider", "Composer Outsider", "composer.outsider@example.test"),
    ]:
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
        INSERT OR REPLACE INTO comm_v2_conversations (
            id, public_id, conversation_type, title, owner_user_id,
            created_by_user_id, privacy, visibility, status, member_count,
            created_at, updated_at
        )
        VALUES (?, 'audit-media-chat', 'direct', 'Media Audit Chat', 94101, 94101, 'private', 'members', 'active', 2, ?, ?)
        """,
        (conversation_id, now, now),
    )
    for user_id in (94101, 94102):
        cur.execute(
            """
            INSERT OR REPLACE INTO comm_v2_participants (
                conversation_id, user_id, role, membership_state,
                joined_at, created_at, updated_at
            )
            VALUES (?, ?, 'member', 'active', ?, ?, ?)
            """,
            (conversation_id, user_id, now, now, now),
        )
    conn.commit()
    conn.close()


def init_upload(client, conversation_id, media_type, filename, mime_type, body):
    response = client.post(
        "/api/messages/media/init",
        json={
            "conversation_id": conversation_id,
            "media_type": media_type,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(body),
        },
    )
    data = json_body(response)
    assert_true(response.status_code == 201, f"init failed: {data}")
    attachment_id = int(data["attachment_id"])
    form = {"attachment_id": str(attachment_id)}
    if media_type == "voice":
        form["duration_ms"] = "1200"
        form["waveform_json"] = "[0,0.25,0.9,0.35]"
    form["file"] = (io.BytesIO(body), filename, mime_type)
    upload = client.post("/api/messages/media/upload", data=form, content_type="multipart/form-data")
    upload_data = json_body(upload)
    assert_true(upload.status_code == 200 and upload_data["upload_status"] == "uploaded", f"upload failed: {upload_data}")
    complete = client.post("/api/messages/media/complete", json={"attachment_id": attachment_id})
    complete_data = json_body(complete)
    assert_true(complete.status_code == 200, f"complete failed: {complete_data}")
    return attachment_id


def send_with_attachment(client, conversation_id, attachment_id, message_type):
    response = client.post(
        f"/api/pulse/communications/v2/conversations/{conversation_id}/messages",
        json={
            "body": f"audit {message_type}",
            "message_type": message_type,
            "attachment_ids": [attachment_id],
            "client_message_id": f"audit-{message_type}-{attachment_id}",
        },
    )
    data = json_body(response)
    assert_true(response.status_code == 200 and data.get("ok"), f"send failed: {data}")
    message = data.get("message") or {}
    attachments = message.get("attachments") or []
    assert_true(attachments, f"sent message missing attachments: {data}")
    attachment = attachments[0]
    assert_true(f"/api/messages/media/{attachment_id}/download" in (attachment.get("url") or attachment.get("playback_url") or ""), "attachment payload does not use private download endpoint")
    assert_true(not attachment.get("storage_key"), "attachment payload exposed private storage key")
    return int(message.get("id") or message.get("message_id") or 0), attachment


def runtime_checks(tmp_path):
    os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'composer_media_wiring.sqlite3'}"
    os.environ["FORCE_INIT_DB"] = "1"
    os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(tmp_path / "messenger_uploads")
    os.environ["MEDIA_STORAGE_PROVIDER"] = "local"
    os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

    import bot

    bot.init_db()
    os.environ["FORCE_INIT_DB"] = "0"
    conversation_id = 94001
    seed_comm_v2(bot, conversation_id)

    sender = bot.webhook_app.test_client()
    recipient = bot.webhook_app.test_client()
    outsider = bot.webhook_app.test_client()
    set_session(sender, 94101)
    set_session(recipient, 94102)
    set_session(outsider, 94103)

    cases = [
        ("photo", "audit.jpg", "image/jpeg", b"\xff\xd8composer-photo\xff\xd9", "image"),
        ("video", "audit.mp4", "video/mp4", b"\x00\x00\x00\x18ftypmp42composer-video", "video"),
        ("voice", "audit.webm", "audio/webm", b"WEBM-composer-voice", "voice"),
    ]
    attachment_ids = []
    for media_type, filename, mime_type, body, message_type in cases:
        attachment_id = init_upload(sender, conversation_id, media_type, filename, mime_type, body)
        attachment_ids.append(attachment_id)
        send_with_attachment(sender, conversation_id, attachment_id, message_type)
        download = recipient.get(f"/api/messages/media/{attachment_id}/download")
        assert_true(download.status_code == 200 and download.data, f"recipient download failed for {media_type}")
        outsider_download = outsider.get(f"/api/messages/media/{attachment_id}/download")
        outsider_data = json_body(outsider_download)
        assert_true(outsider_download.status_code == 403 and outsider_data["error"] == "not_conversation_member", f"outsider download was not blocked: {outsider_data}")

    fetched = recipient.get(f"/api/pulse/communications/v2/conversations/{conversation_id}/messages")
    fetched_data = json_body(fetched)
    assert_true(fetched.status_code == 200 and fetched_data.get("ok"), f"recipient fetch failed: {fetched_data}")
    messages = fetched_data.get("messages") or fetched_data.get("items") or []
    found_private_urls = [
        attachment.get("url") or attachment.get("playback_url") or ""
        for message in messages
        for attachment in (message.get("attachments") or [])
    ]
    for attachment_id in attachment_ids:
        assert_true(any(f"/api/messages/media/{attachment_id}/download" in url for url in found_private_urls), f"recipient fetch missing attachment {attachment_id}")


def main():
    static_checks()
    with tempfile.TemporaryDirectory(prefix="messenger-composer-media-") as tmp:
        runtime_checks(Path(tmp))
    print("Messenger media composer wiring audit passed.")


if __name__ == "__main__":
    main()
