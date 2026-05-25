#!/usr/bin/env python3
"""Audit the Pulse Create Status picker, preview editor, and publish path."""

from __future__ import annotations

import base64
import os
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
WEBM_BYTES = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"


def expect(ok: bool, label: str, details: str = ""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user() -> int:
    user_id = 960000 + int(time.time()) % 100000
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
            f"create_status_{user_id}",
            "Create Status Audit",
            f"create-status-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def media_url_is_safe(url: str) -> bool:
    if not url:
        return False
    if url.startswith("file:") or url.startswith("/Users/") or url.startswith("/tmp/"):
        return False
    if os.getenv("MEDIA_STORAGE_PROVIDER") == "r2":
        return url.startswith(os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/") + "/")
    return url.startswith(("http://", "https://", "/uploads/", "/static/", "data:"))


def upload_media(client, name: str, content_type: str, data: bytes) -> dict:
    response = client.post(
        "/api/pulse/media/upload",
        data={
            "file": (BytesIO(data), name),
            "context_type": "pulse_status",
            "context_id": "audit",
        },
        content_type="multipart/form-data",
    )
    payload = response.get_json() or {}
    expect(response.status_code == 200 and payload.get("ok"), f"{name} uploads for status", response.get_data(as_text=True)[:400])
    media = payload.get("media") or {}
    expect(media.get("id"), f"{name} returns media id", str(media))
    expect(media_url_is_safe(media.get("valid_url") or media.get("media_url") or ""), f"{name} returns safe media URL", str(media))
    expect(media.get("media_type") in {"image", "video", "gif"}, f"{name} stores media type", str(media))
    return media


def create_status(client, media_id: int, media_type: str) -> dict:
    response = client.post(
        "/api/pulse/status",
        json={
            "status_type": media_type,
            "body": f"Create Status audit {media_type}",
            "media_ids": [media_id],
            "visibility": "public",
            "duration_hours": 24,
            "effect_name": "cinematic",
            "sticker": "✨",
            "link_url": "https://coinpilotx.app/pulse",
        },
    )
    payload = response.get_json() or {}
    expect(response.status_code == 200 and payload.get("ok") and payload.get("status_id"), f"{media_type} status publishes", response.get_data(as_text=True)[:400])
    status = payload.get("status") or {}
    expect(status.get("media"), f"{media_type} status returns hydrated media", str(status))
    first_media = (status.get("media") or [{}])[0]
    expect(first_media.get("is_available") is not False, f"{media_type} status media is available", str(first_media))
    expect(media_url_is_safe(first_media.get("valid_url") or first_media.get("media_url") or ""), f"{media_type} status media has safe render URL", str(first_media))
    tools = status.get("status_tools") or {}
    expect(tools.get("effect_name") == "cinematic", f"{media_type} status persists editor effect", str(tools))
    return payload


def main():
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    html = client.get("/pulse").get_data(as_text=True)
    required_tokens = [
        "data-status-card",
        "data-status-mode='create'",
        "pulseStatusMedia",
        "type='file'",
        "multiple",
        "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime",
        "data-status-editor",
        "data-status-preview-stage",
        "data-status-tool='stickers'",
        "data-status-tool='text'",
        "data-status-tool='music'",
        "data-status-tool='filters'",
        "data-status-tool='mention'",
        "data-status-tool='links'",
        "data-status-tool='choose'",
        "pulseStatusPrivacy",
        "data-status-effects-tray",
        "data-status-effect='cinematic'",
        "data-upload-progress",
        "PulseUploadManager.upload",
        "openStatusGalleryCreator",
        "Choose an image or video from your gallery.",
        "/api/pulse/status",
    ]
    for token in required_tokens:
        expect(token in html, f"Create Status UI contains {token}")
    expect("capture" not in html[html.find("pulseStatusMedia") : html.find("pulseStatusMedia") + 260], "Create Status picker does not force camera capture")
    expect("location.href='/pulse/status'" not in html, "Create Status publish does not redirect away from editor flow")

    image_media = upload_media(client, "status-audit.png", "image/png", PNG_BYTES)
    video_media = upload_media(client, "status-audit.webm", "video/webm", WEBM_BYTES)
    image_status = create_status(client, int(image_media["id"]), "image")
    video_status = create_status(client, int(video_media["id"]), "video")

    rail = client.get("/api/pulse/status/rail")
    rail_payload = rail.get_json() or {}
    expect(rail.status_code == 200 and rail_payload.get("ok"), "status rail loads after publish", rail.get_data(as_text=True)[:400])
    rail_ids = {int(item.get("id") or 0) for item in rail_payload.get("items") or []}
    expect(int(image_status["status_id"]) in rail_ids, "image status appears in rail immediately")
    expect(int(video_status["status_id"]) in rail_ids, "video status appears in rail immediately")

    css = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
    for token in ["100dvh", ".pulse-status-tool-rail", ".pulse-status-bottom-bar", ".pulse-status-upload-progress", "object-fit: contain"]:
        expect(token in css, f"Status preview CSS contains {token}")
    print("create status flow audit ok")


if __name__ == "__main__":
    main()
