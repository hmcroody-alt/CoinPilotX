#!/usr/bin/env python3
"""Audit Communications V2 voice-note security controls."""

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


def client_for(user_id: int = 0):
    client = bot.app.test_client()
    if user_id:
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
for uid, name in [(987301, "voice_secure_owner"), (987302, "voice_secure_peer")]:
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
            (uid, name, name.replace("_", " ").title(), f"{name}@example.test", now),
        )
conn.commit()
conn.close()

anon = client_for()
denied = anon.post("/api/pulse/communications/v2/attachments/upload")
ok(denied.status_code == 401, "anonymous voice upload denied")

owner = client_for(987301)
direct = owner.post("/api/pulse/communications/v2/direct/open", json={"target_user_id": 987302})
conversation_id = direct.get_json()["conversation_id"]

bad_mime = owner.post(
    "/api/pulse/communications/v2/attachments/upload",
    data={
        "conversation_id": str(conversation_id),
        "attachment_kind": "voice_note",
        "duration_seconds": "4",
        "file": (io.BytesIO(b"<html>not audio</html>"), "voice-note.html"),
    },
    content_type="multipart/form-data",
)
ok(bad_mime.status_code == 400, "bad voice MIME/extension rejected", str(bad_mime.get_json()))

too_long = owner.post(
    "/api/pulse/communications/v2/attachments/upload",
    data={
        "conversation_id": str(conversation_id),
        "attachment_kind": "voice_note",
        "duration_seconds": "99999",
        "file": (io.BytesIO(b"OggS" + b"\x00" * 128), "voice-note.ogg"),
    },
    content_type="multipart/form-data",
)
ok(too_long.status_code == 400 and "minutes" in (too_long.get_json() or {}).get("message", ""), "voice duration limit enforced", str(too_long.get_json()))

service = (ROOT / "pulse_communications_v2" / "service.py").read_text(encoding="utf-8")
for needle in ["COMM_V2_VOICE_MAX_SECONDS", "COMM_V2_VOICE_MAX_MB", "_validate_voice_upload", "conversation, access = _conversation_access"]:
    ok(needle in service, f"security code includes {needle}")

print("pulse voice security audit ok")
