#!/usr/bin/env python3
"""Verify Communications V2 can send each supported attachment kind."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pulse_communications_v2_audit import client_for, ensure_users  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)

WEBM_STUB = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"
OGG_STUB = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
PDF_STUB = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def post_json(client, path: str, payload: dict) -> tuple[int, dict]:
    response = client.post(path, data=json.dumps(payload), content_type="application/json")
    return response.status_code, response.get_json(silent=True) or {}


def upload_attachment(client, conversation_id: int, name: str, mime_type: str, data: bytes, *, voice: bool = False) -> int:
    form = {
        "conversation_id": str(conversation_id),
        "file": (io.BytesIO(data), name, mime_type),
    }
    if voice:
        form["attachment_kind"] = "voice"
        form["duration_seconds"] = "1.2"
        form["waveform_json"] = "[0.15,0.6,0.35,0.8]"
    response = client.post(
        "/api/pulse/communications/v2/attachments/upload",
        data=form,
        content_type="multipart/form-data",
    )
    payload = response.get_json(silent=True) or {}
    expect(response.status_code == 200 and payload.get("ok"), f"{name} upload reaches uploaded", f"status={response.status_code} body={payload}")
    media_id = int((payload.get("media") or {}).get("id") or 0)
    expect(media_id > 0, f"{name} upload returns media id", str(payload))
    return media_id


def send_with_attachment(client, conversation_id: int, media_id: int, message_type: str, label: str) -> None:
    status, payload = post_json(
        client,
        f"/api/pulse/communications/v2/conversations/{conversation_id}/messages",
        {"body": "" if label == "image" else f"attachment audit: {label}", "message_type": message_type, "media_ids": [media_id]},
    )
    expect(status == 200 and payload.get("ok"), f"{label} message send succeeds", f"status={status} body={payload}")
    message = payload.get("message") or {}
    attachments = message.get("attachments") or []
    expect(bool(attachments), f"{label} message has linked attachment", str(payload))
    expect(int((attachments[0] or {}).get("media_upload_id") or 0) == int(media_id), f"{label} attachment links uploaded media", str(attachments))


def main() -> int:
    user_id, other_id, *_ = ensure_users()
    client = client_for(user_id)
    status, payload = post_json(client, "/api/pulse/communications/v2/direct/open", {"target_user_id": other_id})
    expect(status == 200 and payload.get("ok"), "open audit DM", f"status={status} body={payload}")
    conversation_id = int(payload["conversation_id"])

    cases = [
        ("image", "audit-image.png", "image/png", PNG_1X1, "image", False),
        ("video", "audit-video.webm", "video/webm", WEBM_STUB, "video", False),
        ("voice", "audit-voice.ogg", "audio/ogg", OGG_STUB, "voice", True),
        ("file", "audit-file.pdf", "application/pdf", PDF_STUB, "file", False),
    ]
    for label, filename, mime_type, data, message_type, voice in cases:
        media_id = upload_attachment(client, conversation_id, filename, mime_type, data, voice=voice)
        send_with_attachment(client, conversation_id, media_id, message_type, label)

    print("PASS pulse_comm_v2_attachment_send_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
