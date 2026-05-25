#!/usr/bin/env python3
"""Audit Pulse composer media attachment, upload persistence, and desktop feed scale."""

from __future__ import annotations

import base64
import re
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
WEBM_BYTES = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"


def require(condition: bool, message: str, details: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def ensure_user() -> int:
    user_id = 986000 + int(time.time()) % 10000
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
            f"media_attach_{user_id}",
            "Media Attachment Audit",
            f"media-attachment-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def upload_media(client, filename: str, content_type: str, payload: bytes) -> dict:
    response = client.post(
        "/api/pulse/media/upload",
        data={
            "context_type": "pulse",
            "context_id": "audit",
            "file": (BytesIO(payload), filename, content_type),
        },
        content_type="multipart/form-data",
    )
    data = response.get_json() or {}
    require(response.status_code == 200 and data.get("ok"), f"{filename} uploads through media endpoint", str(data))
    media = data.get("media") or {}
    require(media.get("id"), f"{filename} returns media id", str(media))
    require(media.get("media_url") or media.get("valid_url"), f"{filename} returns canonical media URL", str(media))
    require((data.get("progress") or {}).get("percent") == 100, f"{filename} reports complete upload progress")
    return media


def main() -> None:
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    picker_js = (ROOT / "static" / "js" / "pulse_media_picker.js").read_text(encoding="utf-8")
    upload_js = (ROOT / "static" / "js" / "pulse_upload_manager.js").read_text(encoding="utf-8")
    desktop_css = (ROOT / "static" / "css" / "pulse_desktop_feed.css").read_text(encoding="utf-8")

    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    response = client.get("/pulse")
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "Pulse homepage renders")
    visible_copy = re.sub(r"<input[^>]+type=['\"]file['\"][^>]*>", "", html, flags=re.I)
    require("Choose File" not in visible_copy and "Choose Files" not in visible_copy, "raw native upload controls are not visible")
    require('id="postMedia"' in html and 'type="file"' in html and "multiple" in html, "composer has hidden multi-file input")
    require("image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime" in html, "composer accepts safe image and video MIME types")
    require("data-pulse-media-trigger" in html and "data-expand-composer=\"pulseComposer\"" in html, "Media button uses unified picker and expands composer")
    require("pulsePickerBound" in picker_js and "preventDefault" in picker_js, "media picker prevents duplicate native trigger behavior")

    require("composerMediaFiles" in source and "URL.createObjectURL" in source, "composer keeps a real selected-file queue with local previews")
    require("pulse-selected-media" in source and "data-remove-composer-media" in source, "selected media previews include remove controls")
    require("<video src=" in source and "<img src=" in source, "composer preview renders image and video thumbnails")
    require("PulseUploadManager.upload" in source and "media_ids:mediaIds" in source, "publish flow uploads attachments and persists media ids")
    require("Uploading video..." in upload_js and "Processing media..." in upload_js and "Posted successfully" in upload_js, "upload manager exposes progress, processing, and success states")

    image = upload_media(client, "pulse-audit-image.png", "image/png", PNG_BYTES)
    video = upload_media(client, "pulse-audit-video.webm", "video/webm", WEBM_BYTES)
    post_response = client.post(
        "/api/pulse/posts",
        json={
            "title": "Media Attachment Audit",
            "body": "Audit post with image and video attachments.",
            "post_type": "video",
            "media_ids": [image["id"], video["id"]],
            "visibility": "public",
        },
    )
    post_payload = post_response.get_json() or {}
    require(post_response.status_code == 200 and post_payload.get("ok"), "Pulse post publishes with attached media ids", str(post_payload))
    post = post_payload.get("post") or {}
    require(len(post.get("media") or []) >= 2, "published post response includes attached media")
    for item in post.get("media") or []:
        require(item.get("valid_url") or item.get("media_url"), "published media item has renderable URL", str(item))
        require(item.get("is_available") is not False, "published media item is available", str(item))

    feed_response = client.get("/api/pulse/feed?feed=for_you&limit=20")
    feed_payload = feed_response.get_json() or {}
    posts = feed_payload.get("posts") or []
    created = next((item for item in posts if int(item.get("id") or 0) == int(post_payload.get("post_id") or 0)), None)
    require(feed_response.status_code == 200 and created, "attached media post appears in feed after refresh", str(feed_payload)[:500])
    require(len(created.get("media") or []) >= 2, "feed payload preserves image/video attachments after refresh")

    feed_widths = [int(v) for v in re.findall(r"--pulse-feed-column:\s*clamp\((\d+)px", desktop_css)]
    text_widths = [int(v) for v in re.findall(r"--pulse-text-column:\s*(\d+)px", desktop_css)]
    require(max(feed_widths or [0]) >= 1360 and max(text_widths or [0]) >= 1280, "desktop feed column is wide and social-scale")
    require("minmax(0, var(--pulse-feed-column))" in desktop_css, "desktop grid reserves a flexible wide feed lane")
    require("min-height: 72px" in desktop_css and "padding: clamp(14px" in desktop_css, "desktop cards are compact vertically while widened")
    require("max-height: min(62vh, 620px)" in desktop_css, "desktop media stays wide without excessive height")

    print("media attachment audit ok")


if __name__ == "__main__":
    main()
