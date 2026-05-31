#!/usr/bin/env python3
"""Focused audit for Pulse Status posting contracts and empty state UI."""

from __future__ import annotations

import base64
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def expect(ok: bool, label: str, details: str = ""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user() -> int:
    user_id = 970000 + int(time.time()) % 100000
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            f"pulse_status_posting_{user_id}",
            "Pulse Status Posting Audit",
            f"pulse-status-posting-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = ensure_user()

    html = client.get("/pulse/status").get_data(as_text=True)
    for token in [
        "data-status2-form",
        "/api/pulse/status",
        "body:JSON.stringify(payload)",
        "Pulse Intelligence Field",
        "UNDX is monitoring the Pulse network.",
        "AI intelligence feed standing by.",
        "Space Signal: <b>Searching</b>",
        "data-pulse-intelligence-field",
    ]:
        expect(token in html, f"Status page contains {token}")

    empty = client.post("/api/pulse/status", json={"status_type": "text", "body": "   "})
    empty_payload = empty.get_json() or {}
    expect(empty.status_code == 400, "empty Status is rejected")
    expect(empty_payload.get("ok") is False and empty_payload.get("error") and empty_payload.get("message"), "empty Status returns specific JSON error", str(empty_payload))
    expect(empty_payload.get("message") != "Pulse Status could not be created.", "empty Status is not generic-only", str(empty_payload))

    wrong_type = client.post("/api/pulse/status", data="not-json", content_type="text/plain")
    wrong_payload = wrong_type.get_json() or {}
    expect(wrong_type.status_code == 415 and wrong_payload.get("error_code") == "invalid_content_type", "non-JSON Status request reports exact reason", str(wrong_payload))

    text = client.post("/api/pulse/status", json={"status_type": "text", "body": "Focused text status", "visibility": "public"})
    text_payload = text.get_json() or {}
    expect(text.status_code == 200 and text_payload.get("ok") and text_payload.get("status_id"), "backend accepts text-only Status", text.get_data(as_text=True)[:400])

    upload = client.post(
        "/api/pulse/media/upload",
        data={"file": (BytesIO(PNG_BYTES), "focused-status.png"), "context_type": "pulse_status", "context_id": "posting-audit"},
        content_type="multipart/form-data",
    )
    upload_payload = upload.get_json() or {}
    expect(upload.status_code == 200 and upload_payload.get("ok") and (upload_payload.get("media") or {}).get("id"), "media upload works for Status", upload.get_data(as_text=True)[:400])
    media_id = int((upload_payload.get("media") or {}).get("id"))

    photo = client.post("/api/pulse/status", json={"status_type": "photo", "media_ids": [media_id], "visibility": "public"})
    photo_payload = photo.get_json() or {}
    expect(photo.status_code == 200 and photo_payload.get("ok") and photo_payload.get("status_id"), "backend accepts media-only Status", photo.get_data(as_text=True)[:400])

    mixed = client.post("/api/pulse/status", json={"status_type": "photo", "body": "Focused caption", "media_ids": [media_id], "visibility": "public"})
    mixed_payload = mixed.get_json() or {}
    expect(mixed.status_code == 200 and mixed_payload.get("ok") and mixed_payload.get("status_id"), "backend accepts text plus media Status", mixed.get_data(as_text=True)[:400])

    missing_media = client.post("/api/pulse/status", json={"status_type": "photo", "media_ids": [999999999], "visibility": "public"})
    missing_payload = missing_media.get_json() or {}
    expect(missing_media.status_code == 400 and missing_payload.get("error_code") == "media_not_found", "missing media reports exact reason", str(missing_payload))
    expect(missing_payload.get("message") != "Pulse Status could not be created.", "missing media is not generic-only", str(missing_payload))

    print("pulse status posting audit ok")


if __name__ == "__main__":
    main()
