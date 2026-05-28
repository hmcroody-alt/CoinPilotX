#!/usr/bin/env python3
"""Phase 2 media upload/CDN validation for Pulse.

Local development runs verify the code contracts that prevent raw/private media
URLs and blank renderers. Production/R2 runs additionally upload JPG, PNG, and
MP4 files through the real backend endpoint, verify the object in pulse-media2,
validate CDN access, confirm database media rows, publish a feed post, and
verify profile avatar CDN persistence.
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_storage  # noqa: E402


JPG_BYTES = bytes.fromhex("ffd8ffe000104a46494600010101006000600000ffdb004300") + (b"\x08" * 64) + bytes.fromhex("ffc00011080001000103012200021101031101ffc40014000100000000000000000000000000000000000000ffda000c03010002110311003f00d2cf20ffd9")
PNG_BYTES = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8ffff3f0005fe02fea7cd2dd40000000049454e44ae426082")
MP4_BYTES = (
    b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    b"\x00\x00\x00\x08free"
    b"\x00\x00\x00\x10mdat"
)


def require(condition: bool, label: str, details: str = ""):
    if not condition:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def endpoint() -> str:
    value = os.getenv("R2_ENDPOINT") or os.getenv("R2_ENDPOINT_URL") or ""
    if value and not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def s3_client():
    try:
        import boto3
    except Exception as exc:
        raise AssertionError(f"boto3 is required for R2 verification: {exc}") from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint() or None,
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "auto"),
    )


def cdn_probe(url: str, expected_mime: str):
    last_error = ""
    for _ in range(8):
        try:
            req = Request(url, headers={"User-Agent": "CoinPilotXAI-Phase2-Media-Audit/1.0", "Range": "bytes=0-63"})
            with urlopen(req, timeout=10) as response:
                status = int(getattr(response, "status", 200))
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                body = response.read(64)
                if status in {200, 206} and body and content_type == expected_mime:
                    return {"status": status, "content_type": content_type, "bytes": len(body)}
                last_error = f"status={status} content_type={content_type} bytes={len(body)}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AssertionError(f"CDN URL did not pass direct playback probe: {url} ({last_error})")


def ensure_user() -> int:
    user_id = 9909202
    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    cur.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cur.fetchall()}
    values = {
        "user_id": user_id,
        "telegram_id": user_id,
        "telegram_user_id": user_id,
        "username": "phase2_media_audit",
        "display_name": "Phase 2 Media Audit",
        "email": "phase2-media@example.test",
        "created_at": now,
        "signup_time": now,
    }
    insert_columns = [name for name in values if name in columns]
    cur.execute(
        f"INSERT OR IGNORE INTO users ({', '.join(insert_columns)}) VALUES ({', '.join(['?'] * len(insert_columns))})",
        [values[name] for name in insert_columns],
    )
    conn.commit()
    conn.close()
    return user_id


def upload(client, filename: str, mime_type: str, payload: bytes):
    response = client.post(
        "/api/pulse/media/upload",
        data={
            "context_type": "pulse_phase2_audit",
            "target": "phase2",
            "file": (io.BytesIO(payload), filename, mime_type),
        },
        content_type="multipart/form-data",
    )
    data = response.get_json() or {}
    require(response.status_code == 200 and data.get("ok"), f"{filename} backend upload succeeds", response.get_data(as_text=True)[:500])
    media = data.get("media") or {}
    require(media.get("id"), f"{filename} database media id returned", str(media))
    require(media.get("verified") is True, f"{filename} upload verified before success", str(media))
    require(media.get("storage_provider") == "r2", f"{filename} stored with R2 provider", str(media))
    require(str(media.get("media_url", "")).startswith("https://cdn.coinpilotx.app/"), f"{filename} returns canonical CDN URL", str(media))
    require("r2.cloudflarestorage.com" not in str(media), f"{filename} never exposes private R2 URL")
    require((media.get("mime_type") or "").split(";", 1)[0] == mime_type, f"{filename} MIME type is preserved", str(media))
    return media


def database_row(media_id: int) -> dict:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM chat_media_uploads WHERE id=? LIMIT 1", (int(media_id),))
    row = dict(cur.fetchone() or {})
    conn.close()
    return row


def local_contract_audit():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    storage = (ROOT / "services/media_storage.py").read_text(encoding="utf-8")
    upload_progress = (ROOT / "services/upload_progress_service.py").read_text(encoding="utf-8")
    for token in [
        "PulseMediaRenderer = { hydrate, retry, normalizeMedia, renderMedia, renderInto }",
        "playsinline preload=\"metadata\"",
        "Pulse video CDN HEAD",
        "r2.cloudflarestorage.com",
        "data-media-diag",
    ]:
        require(token in renderer, f"shared renderer contract contains {token}")
    for token in ["MEDIA_R2_UPLOAD_START", "MEDIA_R2_UPLOAD_COMPLETE", "ContentType", "CacheControl"]:
        require(token in storage, f"R2 storage logs/verifies {token}")
    for token in ["verify_media", "R2 verification failed", "verified"]:
        require(token in upload_progress, f"upload success waits for durable storage via {token}")
    for token in ["_profile_media_payload_url", "_profile_media_is_cdn_safe", "pulse_avatar", "pulse_cover"]:
        require(token in source, f"profile media is CDN-normalized: {token}")
    for token in ["verification_status", "object_key", "cdn_url", "trace_id", "error_message"]:
        require(token in source and token in (ROOT / "services/media_service.py").read_text(encoding="utf-8"), f"media records expose Phase 2 field {token}")
    for token in ["recording_status", "recording_error", "replay_unavailable", "replay_ready"]:
        require(token in source or token in (ROOT / "services/live_archive_service.py").read_text(encoding="utf-8"), f"live replay lifecycle exposes {token}")


def main():
    bot.init_db()
    local_contract_audit()

    if media_storage.provider() != "r2":
        print("phase2 production R2 verification skipped: MEDIA_STORAGE_PROVIDER is not r2 in this environment")
        print("phase2 media CDN audit ok")
        return

    require(os.getenv("R2_BUCKET") == "pulse-media2", "R2 bucket is pulse-media2", os.getenv("R2_BUCKET", "missing"))
    require(os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/") == "https://cdn.coinpilotx.app", "CDN base is cdn.coinpilotx.app")
    require(bool(endpoint()), "R2 endpoint is configured")
    require(bool(os.getenv("R2_ACCESS_KEY_ID")) and bool(os.getenv("R2_SECRET_ACCESS_KEY")), "R2 credentials are present")
    require(media_storage.storage_status().get("configured") is True, "R2 storage reports fully configured")

    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    s3 = s3_client()
    tests = [
        ("phase2-audit.jpg", "image/jpeg", JPG_BYTES),
        ("phase2-audit.png", "image/png", PNG_BYTES),
        ("phase2-audit.mp4", "video/mp4", MP4_BYTES),
    ]
    media_ids = []
    for filename, mime_type, payload in tests:
        media = upload(client, filename, mime_type, payload)
        media_ids.append(int(media["id"]))
        key = media.get("storage_key") or ""
        require(bool(key), f"{filename} generated object key")
        head = s3.head_object(Bucket="pulse-media2", Key=key)
        require(int(head.get("ContentLength") or 0) > 0, f"{filename} object exists in pulse-media2")
        require((head.get("ContentType") or "").split(";", 1)[0] == mime_type, f"{filename} R2 content type is valid", str(head.get("ContentType")))
        cdn_probe(media.get("media_url") or "", mime_type)
        row = database_row(int(media["id"]))
        require(row and str(row.get("media_url") or "").startswith("https://cdn.coinpilotx.app/"), f"{filename} database row stores CDN URL", str(row))
        require(row.get("verification_status") == "verified", f"{filename} database row marks storage verified", str(row))
        require(row.get("object_key") == key or row.get("storage_key") == key, f"{filename} database row keeps canonical object key", str(row))
        require(str(row.get("cdn_url") or row.get("media_url") or "").startswith("https://cdn.coinpilotx.app/"), f"{filename} database row exposes CDN URL field", str(row))

    post = client.post(
        "/api/pulse/posts",
        json={
            "body": "Phase 2 media CDN audit render check",
            "title": "Phase 2 Media Audit",
            "post_type": "video",
            "visibility": "public",
            "media_ids": media_ids,
        },
    )
    post_data = post.get_json() or {}
    require(post.status_code == 200 and post_data.get("ok"), "Pulse post publishes with audited media", post.get_data(as_text=True)[:500])
    feed = client.get("/api/pulse/feed?limit=12").get_json() or {}
    rendered = next((item for item in feed.get("posts") or [] if int(item.get("id") or 0) == int(post_data.get("post_id") or 0)), {})
    require(rendered and rendered.get("media"), "Pulse feed returns audited media post")
    require(all(str(m.get("media_url") or "").startswith("https://cdn.coinpilotx.app/") for m in rendered.get("media") or []), "desktop/mobile feed payload uses CDN URLs")

    avatar = client.post("/api/pulse/profile/avatar", json={"media_id": media_ids[0]})
    avatar_data = avatar.get_json() or {}
    require(avatar.status_code == 200 and avatar_data.get("ok"), "profile avatar accepts uploaded media id", avatar.get_data(as_text=True)[:500])
    require(str(avatar_data.get("avatar_url") or "").startswith("https://cdn.coinpilotx.app/"), "profile avatar persists CDN URL", str(avatar_data))

    print("phase2 media CDN audit ok")


if __name__ == "__main__":
    main()
