import sqlite3
from io import BytesIO

import pytest

from services import media_upload_sessions as uploads


class FakeStorageClient:
    def __init__(self):
        self.objects = {}
        self.multipart = {}
        self.aborted = []

    def create_multipart_upload(self, **kwargs):
        upload_id = f"provider-{len(self.multipart) + 1}"
        self.multipart[upload_id] = kwargs
        return {"UploadId": upload_id}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://storage.test/{operation}/{Params['Key']}?expires={ExpiresIn}"

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        assert UploadId in self.multipart
        self.objects[Key] = {"ContentLength": 20 * 1024 * 1024, "ContentType": "video/mp4"}

    def head_object(self, Bucket, Key):
        return self.objects[Key]

    def abort_multipart_upload(self, **kwargs):
        self.aborted.append(kwargs)


@pytest.fixture()
def upload_env(tmp_path, monkeypatch):
    database = tmp_path / "uploads.db"

    def connect():
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect(); conn.execute(
        """CREATE TABLE chat_media_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uploader_user_id INTEGER, context_type TEXT, context_id TEXT,
        original_filename TEXT, stored_filename TEXT, media_url TEXT, thumbnail_url TEXT, media_type TEXT,
        mime_type TEXT, file_size_bytes INTEGER, moderation_status TEXT, storage_provider TEXT, storage_key TEXT,
        bucket TEXT, object_key TEXT, cdn_url TEXT, public_url TEXT, is_available INTEGER,
        verification_status TEXT, processing_status TEXT, upload_complete_at TEXT, trace_id TEXT,
        updated_at TEXT, created_at TEXT, width INTEGER, height INTEGER, duration_seconds REAL,
        mux_playback_id TEXT, mux_asset_id TEXT, mux_status TEXT, playback_url TEXT, playback_mime_type TEXT,
        thumbnail_url_unused TEXT, poster_url TEXT, error_message TEXT, has_audio INTEGER
        )"""
    ); conn.commit(); conn.close()
    client = FakeStorageClient()
    monkeypatch.setattr(uploads.user_context, "connect", connect)
    monkeypatch.setattr(uploads.media_storage, "provider", lambda: "r2")
    monkeypatch.setattr(uploads.media_storage, "storage_status", lambda: {"configured": True})
    monkeypatch.setattr(uploads.media_storage, "object_client", lambda: client)
    monkeypatch.setattr(uploads.media_storage, "head_object", lambda key: client.objects[key])
    monkeypatch.setattr(uploads.media_storage, "get_object", lambda key, byte_range="": {"Body": BytesIO(b"\x00\x00\x00\x18ftypmp42" + (b"\x00" * 500))})
    monkeypatch.setattr(uploads.media_storage, "public_media_url", lambda key: f"https://cdn.test/{key}")
    monkeypatch.setattr(uploads.media_service, "rate_limited", lambda *_args: False)
    monkeypatch.setattr(uploads.media_service, "mux_diagnostics", lambda: {"configured": False})
    monkeypatch.setattr(uploads, "_bucket", lambda: "media")
    return client


def test_rejects_mime_mismatch_and_oversize(upload_env):
    result, status = uploads.create_session(7, {"filename": "clip.mp4", "mime_type": "image/jpeg", "file_size_bytes": 100, "context_type": "pulse_post"})
    assert status == 400 and result["error"] == "mime_mismatch"
    result, status = uploads.create_session(7, {"filename": "clip.mp4", "mime_type": "video/mp4", "file_size_bytes": 151 * 1024 * 1024, "context_type": "pulse_post"})
    assert status == 413 and result["error"] == "file_too_large"


def test_multipart_owner_complete_and_finalize_are_idempotent(upload_env):
    client = upload_env
    result, status = uploads.create_session(7, {"filename": "clip.mp4", "mime_type": "video/mp4", "file_size_bytes": 20 * 1024 * 1024, "context_type": "pulse_post", "context_id": "draft"})
    assert status == 201 and result["strategy"] == "multipart"
    upload_id = result["upload_id"]

    denied, denied_status = uploads.sign_parts(8, upload_id, [1])
    assert denied_status == 404 and denied["error"] == "not_found"
    signed, signed_status = uploads.sign_parts(7, upload_id, [1, 2, 3])
    assert signed_status == 200 and len(signed["parts"]) == 3

    parts = [{"part_number": 1, "etag": '"a"'}, {"part_number": 2, "etag": '"b"'}, {"part_number": 3, "etag": '"c"'}]
    completed, completed_status = uploads.complete_upload(7, upload_id, parts)
    assert completed_status == 200 and completed["status"] == "completed"
    again, again_status = uploads.complete_upload(7, upload_id, parts)
    assert again_status == 200 and again["status"] == "completed"

    finalized, finalized_status = uploads.finalize_upload(7, upload_id)
    assert finalized_status == 200 and finalized["media_id"] > 0
    duplicate, duplicate_status = uploads.finalize_upload(7, upload_id)
    assert duplicate_status == 200 and duplicate["media_id"] == finalized["media_id"] and duplicate["idempotent"] is True


def test_abort_is_owner_scoped(upload_env):
    result, _ = uploads.create_session(7, {"filename": "clip.mp4", "mime_type": "video/mp4", "file_size_bytes": 20 * 1024 * 1024, "context_type": "pulse_post"})
    denied, status = uploads.abort_upload(8, result["upload_id"])
    assert status == 404 and denied["error"] == "not_found"
    aborted, status = uploads.abort_upload(7, result["upload_id"])
    assert status == 200 and aborted["status"] == "aborted"
    assert len(upload_env.aborted) == 1
