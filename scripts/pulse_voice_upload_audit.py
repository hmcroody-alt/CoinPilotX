#!/usr/bin/env python3
"""Exercise Communications V2 voice upload and send flow."""

from __future__ import annotations

import io
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")
os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

import bot  # noqa: E402


def client_for(user_id: int):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def ok(condition, label, detail=""):
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"PASS: {label}")


bot.init_db()
conn = bot.db()
cur = conn.cursor()
now = datetime.now(UTC).isoformat(timespec="seconds")
for uid, name in [(987201, "voice_owner"), (987202, "voice_peer")]:
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
            (uid, name, name.replace("_", " ").title(), f"{name}@example.test", now),
        )
conn.commit()
conn.close()

owner = client_for(987201)
direct = owner.post("/api/pulse/communications/v2/direct/open", json={"target_user_id": 987202})
ok(direct.status_code == 200 and direct.get_json().get("conversation_id"), "direct conversation opens")
conversation_id = direct.get_json()["conversation_id"]

upload = owner.post(
    "/api/pulse/communications/v2/attachments/upload",
    data={
        "conversation_id": str(conversation_id),
        "attachment_kind": "voice_note",
        "duration_seconds": "7",
        "waveform_json": "[12,36,62,48,24]",
        "file": (io.BytesIO(b"OggS" + b"\x00" * 2048), "voice-note.ogg"),
    },
    content_type="multipart/form-data",
)
payload = upload.get_json() or {}
ok(upload.status_code == 200 and payload.get("media", {}).get("id"), "voice upload succeeds", str(payload))
ok(payload.get("media", {}).get("voice_note") is True, "voice upload marks voice note")

media_id = payload["media"]["id"]
sent = owner.post(
    f"/api/pulse/communications/v2/conversations/{conversation_id}/messages",
    json={"message_type": "voice", "media_ids": [media_id]},
)
sent_payload = sent.get_json() or {}
ok(sent.status_code == 200 and sent_payload.get("message", {}).get("message_type") == "voice", "voice message sends", str(sent_payload))
attachment = (sent_payload.get("message", {}).get("attachments") or [{}])[0]
ok(attachment.get("voice_note") is True, "voice attachment persists")
ok(int(attachment.get("duration_seconds") or 0) == 7, "voice duration persists")
ok(attachment.get("waveform"), "voice waveform persists")

webm_upload = owner.post(
    "/api/pulse/communications/v2/attachments/upload",
    data={
        "conversation_id": str(conversation_id),
        "attachment_kind": "voice_note",
        "duration_seconds": "5",
        "waveform_json": "[14,28,42]",
        "file": (io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 2048), "safari-compatible-voice.webm", "audio/webm"),
    },
    content_type="multipart/form-data",
)
webm_payload = webm_upload.get_json() or {}
ok(webm_upload.status_code == 200 and webm_payload.get("media", {}).get("media_type") == "audio", "audio/webm voice upload stays audio", str(webm_payload))

m4a_upload = owner.post(
    "/api/pulse/communications/v2/attachments/upload",
    data={
        "conversation_id": str(conversation_id),
        "attachment_kind": "voice_note",
        "duration_seconds": "6",
        "waveform_json": "[16,32,48]",
        "file": (io.BytesIO(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 2048), "iphone-voice.m4a", "audio/mp4"),
    },
    content_type="multipart/form-data",
)
m4a_payload = m4a_upload.get_json() or {}
ok(m4a_upload.status_code == 200 and m4a_payload.get("media", {}).get("voice_note") is True, "audio/mp4 m4a voice upload succeeds", str(m4a_payload))

print("pulse voice upload audit ok")
