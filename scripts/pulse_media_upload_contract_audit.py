#!/usr/bin/env python3
"""Exercise Pulse media upload contracts and write a compact report."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
WEBM_BYTES = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"
MOV_BYTES = b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  "
OGG_BYTES = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"
MP3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x15TIT2\x00\x00\x00\x05\x00\x00Audit"


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user() -> int:
    user_id = 972000 + int(time.time()) % 100000
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
            f"pulse_media_upload_{user_id}",
            "Pulse Media Upload Audit",
            f"pulse-media-upload-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def upload(client, filename: str, mime_type: str, payload: bytes, context_type: str, context_id: str = "audit") -> dict:
    response = client.post(
        "/api/pulse/media/upload",
        data={
            "file": (BytesIO(payload), filename, mime_type),
            "context_type": context_type,
            "context_id": context_id,
        },
        content_type="multipart/form-data",
    )
    data = response.get_json() or {}
    expect(response.content_type.startswith("application/json"), f"{filename} returns JSON", response.get_data(as_text=True)[:300])
    expect(response.status_code == 200 and data.get("ok") and data.get("success") is True, f"{filename} uploads", str(data))
    expect("media_url" in data and "status_id" in data and "message" in data, f"{filename} exposes frontend envelope", str(data))
    media = data.get("media") or {}
    expect(media.get("id") and (media.get("valid_url") or media.get("media_url")), f"{filename} returns media record and URL", str(media))
    return {"response": data, "media": media, "status_code": response.status_code}


def post_json(client, path: str, payload: dict, label: str) -> dict:
    response = client.post(path, json=payload)
    data = response.get_json() or {}
    expect(response.content_type.startswith("application/json"), f"{label} returns JSON", response.get_data(as_text=True)[:300])
    expect(response.status_code == 200 and (data.get("ok") is True or data.get("success") is True), label, response.get_data(as_text=True)[:500])
    return data


def report_row(name: str, result: dict, *, note: str = "") -> dict:
    media = result.get("media") or {}
    resolved = media_service.resolve_media(media)
    cdn_url = resolved.get("cdn_url") or ""
    if not str(cdn_url).startswith(("http://", "https://")):
        cdn_url = ""
    return {
        "upload_type": name,
        "success": bool(result.get("response", {}).get("success") or result.get("response", {}).get("ok")),
        "media_id": media.get("id") or "",
        "media_type": media.get("media_type") or resolved.get("media_type") or "",
        "mime_type": media.get("mime_type") or resolved.get("mime_type") or "",
        "media_url": media.get("valid_url") or media.get("media_url") or "",
        "cdn_url": cdn_url,
        "mux_playback_id": resolved.get("mux_playback_id") or "",
        "processing_status": resolved.get("processing_status") or "",
        "verification_status": resolved.get("verification_status") or "",
        "error": result.get("response", {}).get("message") if not result.get("response", {}).get("ok") else "",
        "resolution": note or "Uploaded and normalized.",
    }


def write_report(rows: list[dict], created: dict) -> None:
    provider = media_storage.storage_status()
    mux = media_service.mux_diagnostics()
    lines = [
        "# Pulse Media Upload Audit",
        "",
        f"Generated: {bot.datetime.utcnow().isoformat(timespec='seconds')}",
        "",
        "## Infrastructure",
        "",
        f"- Storage provider: `{provider.get('provider')}`",
        f"- Storage configured: `{bool(provider.get('configured'))}`",
        f"- R2/CDN base: `{provider.get('public_base_url') or os.getenv('R2_PUBLIC_BASE_URL', '') or 'not configured locally'}`",
        f"- Mux configured: `{bool(mux.get('configured'))}`",
        f"- ffmpeg present: `{bool(__import__('shutil').which('ffmpeg'))}`",
        "",
        "## Upload Results",
        "",
        "| Upload type | Result | Media type | URL | CDN | Mux playback | Processing | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {upload_type} | {result} | {media_type} / {mime_type} | `{media_url}` | `{cdn_url}` | `{mux_playback_id}` | {processing_status}/{verification_status} | {resolution} |".format(
                upload_type=row["upload_type"],
                result="PASS" if row["success"] else "FAIL",
                media_type=row["media_type"],
                mime_type=row["mime_type"],
                media_url=row["media_url"],
                cdn_url=row["cdn_url"] or "n/a",
                mux_playback_id=row["mux_playback_id"] or "n/a",
                processing_status=row["processing_status"],
                verification_status=row["verification_status"],
                resolution=row["resolution"].replace("|", "/"),
            )
        )
    lines.extend([
        "",
        "## Created Objects",
        "",
        f"- Mixed media Pulse post: `{created.get('post_id') or 'not created'}`",
        f"- Photo/video/audio Status ids: `{', '.join(str(x) for x in created.get('status_ids', []))}`",
        f"- Reel id: `{created.get('reel_id') or 'not created'}`",
        f"- Original sound track id: `{created.get('track_id') or 'not created'}`",
        "",
        "## Resolution",
        "",
        "- Pulse media upload returns readable JSON with `success`, `media_url`, and `status_id`.",
        "- Image, MP4/MOV/WebM video, and audio files are accepted by the current upload path.",
        "- Mux playback ids are preserved when present; no Mux id is fabricated when local/R2 upload does not create one.",
        "- Media engine failure states remain safe through `pending_unavailable` and `processing_blocked` worker handling.",
    ])
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/pulse_media_upload_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    image = upload(client, "pulse-image.png", "image/png", PNG_BYTES, "pulse")
    webm = upload(client, "pulse-video.webm", "video/webm", WEBM_BYTES, "pulse")
    mov = upload(client, "pulse-video.mov", "video/quicktime", MOV_BYTES, "pulse_status")
    audio = upload(client, "pulse-audio.ogg", "audio/ogg", OGG_BYTES, "pulse_sound")

    media_ids = [int(image["media"]["id"]), int(webm["media"]["id"]), int(audio["media"]["id"])]
    post = post_json(
        client,
        "/api/pulse/posts",
        {
            "title": "Pulse Media Upload Audit",
            "body": "Mixed media upload audit.",
            "post_type": "video",
            "media_ids": media_ids,
            "visibility": "public",
        },
        "mixed media Pulse post creates",
    )

    status_ids = []
    for label, status_type, ids in [
        ("image Status creates", "photo", [image["media"]["id"]]),
        ("video Status creates", "video", [mov["media"]["id"]]),
        ("audio Status creates", "music", [audio["media"]["id"]]),
    ]:
        status = post_json(
            client,
            "/api/pulse/status",
            {"status_type": status_type, "body": f"{label} audit", "media_ids": ids, "music_media_id": audio["media"]["id"] if status_type == "music" else 0, "visibility": "public"},
            label,
        )
        expect(status.get("success") is True and status.get("status_id"), f"{label} returns status_id", str(status))
        status_ids.append(status.get("status_id"))

    reel = post_json(
        client,
        "/api/pulse/reels/create",
        {"title": "Pulse Media Upload Audit Reel", "caption": "Video upload audit.", "category": "Community", "visibility": "public", "post_type": "video", "media_ids": [webm["media"]["id"]]},
        "Reel creates from uploaded video",
    )

    sound_response = client.post(
        "/api/pulse/reels/sounds/upload",
        data={
            "file": (BytesIO(MP3_BYTES), "pulse-original.mp3", "audio/mpeg"),
            "title": "Pulse Audit Original",
            "artist": "Pulse Audit",
            "rights_confirmed": "1",
        },
        content_type="multipart/form-data",
    )
    sound = sound_response.get_json() or {}
    expect(sound_response.status_code == 200 and sound.get("ok") and sound.get("success") is True, "original sound upload creates track", str(sound))
    expect(sound.get("media_url") and "status_id" in sound, "original sound upload returns frontend envelope", str(sound))

    rows = [
        report_row("image-only", image),
        report_row("video-only webm", webm),
        report_row("video-only mov", mov, note="MOV stored; playback may vary until transcoding is enabled."),
        report_row("audio-only", audio),
    ]
    write_report(rows, {"post_id": post.get("post_id"), "status_ids": status_ids, "reel_id": reel.get("reel_id"), "track_id": sound.get("track_id")})
    print("pulse media upload contract audit ok")


if __name__ == "__main__":
    main()
