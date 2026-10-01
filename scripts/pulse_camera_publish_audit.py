#!/usr/bin/env python3
"""Audit real Pulse Camera publish actions for Status, Reels, and Pulse Feed."""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name, email, created_at) VALUES (?, ?, ?, ?, ?)",
        (950031, "pulse_camera_publish_audit", "Pulse Camera Publish Audit", "pulse-camera-publish-audit@example.test", now),
    )
    conn.commit()
    conn.close()


def authed_client():
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 950031
    return client


def upload_media(client, name):
    response = client.post(
        "/api/pulse/media/upload",
        data={
            "context_type": "pulse_camera",
            "target": "audit",
            "mode": "photo",
            "filter_name": "natural",
            "effect_key": "pulse_glow",
            "file": (BytesIO(TINY_PNG), name),
        },
        content_type="multipart/form-data",
    )
    data = response.get_json() or {}
    expect(response.status_code == 200 and data.get("ok"), "camera upload succeeds", str(data))
    media = data.get("media") or {}
    expect(media.get("id"), "upload returns media id")
    expect(media.get("media_url"), "upload returns media URL")
    if os.getenv("MEDIA_STORAGE_PROVIDER") == "r2" and os.getenv("R2_PUBLIC_BASE_URL"):
        expect(str(media.get("media_url")).startswith(os.getenv("R2_PUBLIC_BASE_URL").rstrip("/")), "R2/CDN URL returned")
    return media


def main():
    bot.init_db()
    ensure_user()
    client = authed_client()
    media = upload_media(client, "pulse-camera-publish-audit.png")

    preview = client.post(
        "/api/pulse/camera/preview",
        json={"destination": "status", "media": media, "caption": "Camera publish audit", "privacy": "public"},
    )
    preview_data = preview.get_json() or {}
    expect(preview.status_code == 200 and preview_data.get("ok"), "preview record is created before publish", str(preview_data))

    status = client.post(
        "/api/pulse/status",
        json={"status_type": "image", "body": "Camera publish audit status", "media_ids": [media["id"]], "visibility": "public", "duration_hours": 24},
    )
    status_data = status.get_json() or {}
    expect(status.status_code == 200 and status_data.get("ok"), "Publish Status creates real status", str(status_data))
    expect((status_data.get("status") or {}).get("id"), "status id returned")

    reel_media = upload_media(client, "pulse-camera-reel-audit.png")
    reel = client.post(
        "/api/pulse/reels/create",
        json={"title": "Camera Reel Audit", "caption": "Camera publish audit reel", "category": "Community", "visibility": "public", "post_type": "image", "media_ids": [reel_media["id"]]},
    )
    reel_data = reel.get_json() or {}
    expect(reel.status_code == 200 and reel_data.get("ok"), "Publish Reels creates real reel", str(reel_data))
    expect(reel_data.get("reel_id") or (reel_data.get("reel") or {}).get("id"), "reel id returned")

    post_media = upload_media(client, "pulse-camera-feed-audit.png")
    post = client.post(
        "/api/pulse/posts/create-from-camera",
        json={"media_id": post_media["id"], "media_url": post_media["media_url"], "title": "Pulse Camera Audit", "body": "Camera publish audit post", "post_type": "image"},
    )
    post_data = post.get_json() or {}
    expect(post.status_code == 200 and post_data.get("ok"), "Publish Pulse creates real feed post", str(post_data))
    expect(post_data.get("post_id"), "feed post id returned")

    source = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")
    for token in ["openPreview", "Publishing...", "Published.", "Upload interrupted. Retrying", "data-preview-destination"]:
        expect(token in source, f"publish flow token exists: {token}")
    print("pulse camera publish audit ok")


if __name__ == "__main__":
    main()
