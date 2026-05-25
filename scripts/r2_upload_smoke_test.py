#!/usr/bin/env python3
"""Exercise the backend upload pipeline against Cloudflare R2.

This script intentionally uses the same Flask upload endpoint that Pulse uses.
It requires real R2 credentials in the environment and fails loudly when they
are missing or when CDN/object verification does not pass.
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


def cdn_get(url: str, expected_mime: str):
    last_error = ""
    for _ in range(8):
        try:
            req = Request(url, headers={"User-Agent": "CoinPilotXAI-R2-Smoke-Test/1.0"})
            with urlopen(req, timeout=10) as response:
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                body = response.read(32)
                if response.status == 200 and body and content_type == expected_mime:
                    return content_type
                last_error = f"status={response.status} content_type={content_type} body_len={len(body)}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AssertionError(f"CDN URL did not render correctly: {url} ({last_error})")


def ensure_user() -> int:
    user_id = 9909001
    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    cur.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cur.fetchall()}
    values = {
        "user_id": user_id,
        "telegram_id": user_id,
        "telegram_user_id": user_id,
        "username": "r2_smoke_test",
        "display_name": "R2 Smoke Test",
        "email": "r2-smoke@example.test",
        "created_at": now,
        "signup_time": now,
    }
    insert_columns = [name for name in values if name in columns]
    placeholders = ", ".join(["?"] * len(insert_columns))
    cur.execute(
        f"INSERT OR IGNORE INTO users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        [values[name] for name in insert_columns],
    )
    conn.commit()
    conn.close()
    return user_id


def upload(client, filename: str, mime_type: str, payload: bytes):
    response = client.post(
        "/api/pulse/media/upload",
        data={
            "context_type": "pulse_r2_smoke",
            "target": "r2-smoke",
            "file": (io.BytesIO(payload), filename, mime_type),
        },
        content_type="multipart/form-data",
    )
    data = response.get_json() or {}
    require(response.status_code == 200 and data.get("ok"), f"{filename} backend upload succeeds", response.get_data(as_text=True)[:500])
    media = data.get("media") or {}
    require(media.get("verified") is True, f"{filename} verified before success", str(media))
    require(media.get("storage_provider") == "r2", f"{filename} stored with r2 provider", str(media))
    require(str(media.get("media_url", "")).startswith(os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/") + "/"), f"{filename} returns CDN URL", str(media))
    return media


def create_post_and_verify_feed(client, media_ids: list[int]):
    response = client.post(
        "/api/pulse/posts",
        json={
            "body": "R2 smoke test media render verification",
            "title": "R2 Smoke Test",
            "post_type": "image",
            "visibility": "public",
            "media_ids": media_ids,
        },
    )
    data = response.get_json() or {}
    require(response.status_code == 200 and data.get("ok"), "Pulse post publishes with R2 media", response.get_data(as_text=True)[:500])
    post_id = int(data.get("post_id") or 0)
    feed = client.get("/api/pulse/feed?limit=10")
    feed_data = feed.get_json() or {}
    posts = feed_data.get("posts") or []
    post = next((item for item in posts if int(item.get("id") or 0) == post_id), None)
    require(post is not None, "Pulse feed returns uploaded-media post")
    require(all((m.get("media_url") or "").startswith(os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/") + "/") for m in post.get("media") or []), "Pulse feed renders CDN media URLs")


def main():
    expected = {
        "MEDIA_STORAGE_PROVIDER": "r2",
        "R2_BUCKET": "pulse-media2",
        "R2_PUBLIC_BASE_URL": "https://cdn.coinpilotx.app",
    }
    for key, value in expected.items():
        require(os.getenv(key) == value, f"{key} is {value}", os.getenv(key, "missing"))
    require(bool(endpoint()), "R2 endpoint is configured", "set R2_ENDPOINT or R2_ENDPOINT_URL")
    require(bool(os.getenv("R2_ACCESS_KEY_ID")) and bool(os.getenv("R2_SECRET_ACCESS_KEY")), "R2 credentials are present")

    bot.init_db()
    status = media_storage.storage_status()
    require(status.get("configured") is True, "media storage reports configured", str(status))
    require(status.get("bucket") == "pulse-media2", "configured bucket is pulse-media2", str(status))

    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    tests = [
        ("r2-smoke.jpg", "image/jpeg", JPG_BYTES),
        ("r2-smoke.png", "image/png", PNG_BYTES),
        ("r2-smoke.mp4", "video/mp4", MP4_BYTES),
    ]
    s3 = s3_client()
    media_ids = []
    for filename, mime_type, payload in tests:
        media = upload(client, filename, mime_type, payload)
        media_ids.append(int(media["id"]))
        key = media.get("storage_key") or ""
        require(bool(key), f"{filename} generated object key")
        head = s3.head_object(Bucket="pulse-media2", Key=key)
        require(head.get("ContentType", "").split(";", 1)[0] == mime_type, f"{filename} R2 MIME type is correct", str(head.get("ContentType")))
        require(int(head.get("ContentLength") or 0) > 0, f"{filename} appears in R2 bucket")
        cdn_type = cdn_get(media.get("media_url") or "", mime_type)
        require(cdn_type == mime_type, f"{filename} renders from CDN")

    create_post_and_verify_feed(client, media_ids)
    print("r2 upload smoke test ok")


if __name__ == "__main__":
    main()
