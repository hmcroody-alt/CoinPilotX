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


def create_status_payload(client, payload: dict, label: str) -> dict:
    response = client.post("/api/pulse/status", json=payload)
    data = response.get_json() or {}
    expect(response.status_code == 200 and data.get("ok") and data.get("status_id"), label, response.get_data(as_text=True)[:400])
    return data


def main():
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    home_html = client.get("/pulse").get_data(as_text=True)
    expect("data-status2-form" not in home_html and "pulseStatus2Media" not in home_html, "homepage does not render Create Status composer")
    expect("data-status-home-create" in home_html and "/pulse?create_status=1" in home_html, "homepage Create Status entry opens the Home command center")
    html = client.get("/pulse/status").get_data(as_text=True)
    required_tokens = [
        "data-status2-form",
        "data-status-create-form='dedicated'",
        "Create Pulse Status",
        "data-status2-type='text'",
        "data-status2-type='photo'",
        "data-status2-type='video'",
        "data-status2-type='music'",
        "data-status2-type='camera'",
        "data-status2-type='ai'",
        "data-status2-type='live'",
        "pulseStatus2Media",
        "type='file'",
        "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime",
        "data-status2-preview",
        "data-status2-body",
        "data-status2-style-panel",
        "Customize Status Style",
        "data-status2-style='background_color'",
        "data-status2-style='font_family'",
        "data-status2-style='text_align'",
        "data-status2-privacy",
        "data-status2-duration",
        "data-status2-cancel",
        "data-status2-post",
        "renderStatusMediaPreview",
        "URL.createObjectURL",
        "PulseUploadManager.upload",
        "/api/pulse/status",
        "/api/pulse/media/upload",
    ]
    for token in required_tokens:
        expect(token in html, f"dedicated Create Status page contains {token}")
    expect("Post Status" in html, "Create Status footer has clear post action")
    expect("capture" not in html[html.find("pulseStatus2Media") : html.find("pulseStatus2Media") + 260], "Create Status picker does not force camera capture")
    expect("location.href" not in html[html.find("data-status2-form") : html.find("data-status2-form") + 8000], "Create Status publish does not redirect away from editor flow")

    image_media = upload_media(client, "status-audit.png", "image/png", PNG_BYTES)
    video_media = upload_media(client, "status-audit.webm", "video/webm", WEBM_BYTES)
    text_only_status = create_status_payload(client, {"status_type": "text", "body": "Text-only Status audit", "visibility": "public"}, "text-only status publishes")
    styled_status = create_status_payload(
        client,
        {
            "status_type": "text",
            "body": "Styled Status audit",
            "visibility": "public",
            "status_style": {
                "background_color": "#123456",
                "gradient": "midnight",
                "text_color": "#ffffff",
                "font_family": "Serif",
                "font_size": 32,
                "text_align": "center",
                "card_style": "glass",
                "bold": True,
                "italic": True,
            },
        },
        "styled text status publishes",
    )
    styled_payload = styled_status.get("status") or {}
    expect((styled_payload.get("status_style") or {}).get("gradient") == "midnight", "styled status returns style metadata", str(styled_payload))
    photo_only_status = create_status_payload(client, {"status_type": "photo", "media_ids": [int(image_media["id"])], "visibility": "public"}, "photo-only status publishes")
    text_photo_status = create_status_payload(client, {"status_type": "photo", "body": "Caption plus photo", "media_ids": [int(image_media["id"])], "visibility": "public"}, "text plus photo status publishes")
    image_status = create_status(client, int(image_media["id"]), "image")
    video_status = create_status(client, int(video_media["id"]), "video")

    rail = client.get("/api/pulse/status/rail")
    rail_payload = rail.get_json() or {}
    expect(rail.status_code == 200 and rail_payload.get("ok"), "status rail loads after publish", rail.get_data(as_text=True)[:400])
    rail_ids = {int(item.get("id") or 0) for item in rail_payload.get("items") or []}
    expect(int(text_only_status["status_id"]) in rail_ids, "text-only status appears in rail immediately")
    expect(int(styled_status["status_id"]) in rail_ids, "styled status appears in rail immediately")
    styled_rail_item = next((item for item in rail_payload.get("items") or [] if int(item.get("id") or 0) == int(styled_status["status_id"])), {})
    expect((styled_rail_item.get("status_style") or {}).get("card_style") == "glass", "styled status style appears in rail", str(styled_rail_item))
    expect(int(photo_only_status["status_id"]) in rail_ids, "photo-only status appears in rail immediately")
    expect(int(text_photo_status["status_id"]) in rail_ids, "text plus photo status appears in rail immediately")
    expect(int(image_status["status_id"]) in rail_ids, "image status appears in rail immediately")
    expect(int(video_status["status_id"]) in rail_ids, "video status appears in rail immediately")

    css = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
    for token in ["100dvh", ".pulse-status2-type-grid", ".pulse-status2-preview", ".pulse-status2-media-preview", ".pulse-status2-style-panel", ".pulse-intelligence-field", "pulseIntelFeed", ".pulse-status2-state", "object-fit: contain"]:
        expect(token in css, f"Status preview CSS contains {token}")
    print("create status flow audit ok")


if __name__ == "__main__":
    main()
