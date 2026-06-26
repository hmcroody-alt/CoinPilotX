#!/usr/bin/env python3
"""Audit secure PulseSoc ad creative media upload workflow."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(prefix='pulse_ad_media_', suffix='.db', delete=False).name}"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["FLASK_SECRET_KEY"] = "ad-media-audit-secret"
os.environ["SESSION_SECRET"] = "ad-media-audit-session"

import bot  # noqa: E402
from services import pulse_ad_payments, pulse_ads_service  # noqa: E402


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def db_path() -> str:
    return os.environ["DATABASE_URL"].replace("sqlite:///", "", 1)


def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def create_user(conn, user_id: int, username: str) -> int:
    now = pulse_ads_service.now_iso()
    conn.execute(
        """
        INSERT INTO users (user_id, email, password_hash, username, display_name, account_status, access_enabled, login_enabled, created_at, signup_time)
        VALUES (?, ?, 'x', ?, ?, 'active', 1, 1, ?, ?)
        """,
        (user_id, f"{username}@example.com", username, username.replace("-", " ").title(), now, now),
    )
    conn.commit()
    return user_id


def create_account_campaign(conn, owner_id: int, suffix: str):
    account = pulse_ads_service.create_ad_account(
        conn,
        owner_id,
        {
            "business_name": f"Media Audit {suffix}",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        },
    )
    campaign = pulse_ads_service.create_campaign(
        conn,
        owner_id,
        {
            "ad_account_id": account["id"],
            "campaign_name": f"Secure Upload {suffix}",
            "objective": "brand_awareness",
            "budget_type": "daily",
            "daily_budget_cents": 1000,
            "placements": ["feed_inline"],
        },
    )
    return account, campaign


def create_upload_asset(conn, owner_id: int, account_id: int, *, media_type="image", asset_kind="creative_media", name="creative") -> dict:
    now = pulse_ads_service.now_iso()
    ext = {"image": "jpg", "video": "mp4", "audio": "mp3", "gif": "gif"}.get(media_type, "bin")
    mime = {
        "image": "image/jpeg",
        "gif": "image/gif",
        "video": "video/mp4",
        "audio": "audio/mpeg",
    }.get(media_type, "application/octet-stream")
    media_url = f"/static/uploads/pulse_ads/{name}.{ext}"
    thumbnail_url = f"/static/uploads/pulse_ads/{name}.jpg"
    conn.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, original_filename, stored_filename, media_url, thumbnail_url, media_type, mime_type,
         file_size_bytes, width, height, duration_seconds, context_type, context_id, moderation_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pulse_ad_creative', ?, 'approved', ?)
        """,
        (
            owner_id,
            f"{name}.{ext}",
            f"{name}.{ext}",
            media_url,
            thumbnail_url,
            media_type,
            mime,
            2048,
            1200 if media_type in {"image", "gif", "video"} else 0,
            628 if media_type in {"image", "gif", "video"} else 0,
            18 if media_type in {"video", "audio"} else 0,
            f"account:{account_id}:{asset_kind}",
            now,
        ),
    )
    media_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return pulse_ads_service.create_ad_media_asset(
        conn,
        owner_id,
        account_id,
        {
            "id": media_id,
            "media_type": media_type,
            "mime_type": mime,
            "media_url": media_url,
            "thumbnail_url": thumbnail_url,
            "width": 1200 if media_type in {"image", "gif", "video"} else 0,
            "height": 628 if media_type in {"image", "gif", "video"} else 0,
            "duration_seconds": 18 if media_type in {"video", "audio"} else 0,
            "file_size_bytes": 2048,
        },
        asset_kind=asset_kind,
    )


def expect_pulse_ads_error(label: str, func):
    try:
        func()
    except pulse_ads_service.PulseAdsError:
        return
    raise AssertionError(f"{label} should raise PulseAdsError")


def main():
    bot.INIT_DB_COMPLETED = False
    bot.init_db()
    conn = connect()
    try:
        owner_id = create_user(conn, 7301, "ad-media-owner")
        other_id = create_user(conn, 7302, "ad-media-other")
        viewer_id = create_user(conn, 7303, "ad-media-viewer")
        account, campaign = create_account_campaign(conn, owner_id, "Primary")
        other_account, other_campaign = create_account_campaign(conn, other_id, "Other")

        expect_pulse_ads_error(
            "media URL creative",
            lambda: pulse_ads_service.create_creative(
                conn,
                owner_id,
                {
                    "campaign_id": campaign["id"],
                    "creative_type": "image",
                    "title": "Unsafe URL Creative",
                    "body": "Should be blocked.",
                    "media_url": "https://cdn.example.com/ad.jpg",
                    "destination_url": "https://example.com",
                },
            ),
        )
        expect_pulse_ads_error(
            "image creative without uploaded asset",
            lambda: pulse_ads_service.create_creative(
                conn,
                owner_id,
                {
                    "campaign_id": campaign["id"],
                    "creative_type": "image",
                    "title": "Missing Asset",
                    "body": "Should be blocked.",
                    "destination_url": "https://example.com",
                },
            ),
        )

        image_asset = create_upload_asset(conn, owner_id, account["id"], media_type="image", name="image-creative")
        thumbnail_asset = create_upload_asset(conn, owner_id, account["id"], media_type="image", asset_kind="thumbnail", name="video-thumb")
        video_asset = create_upload_asset(conn, owner_id, account["id"], media_type="video", name="video-creative")
        audio_asset = create_upload_asset(conn, owner_id, account["id"], media_type="audio", name="audio-creative")
        unused_asset = create_upload_asset(conn, owner_id, account["id"], media_type="image", name="unused-draft")

        image_creative = pulse_ads_service.create_creative(
            conn,
            owner_id,
            {
                "campaign_id": campaign["id"],
                "creative_type": "image",
                "title": "Secure Image Creative",
                "body": "Uses an internal media asset.",
                "media_asset_id": image_asset["id"],
                "destination_url": "https://example.com",
                "call_to_action": "Open",
            },
        )
        assert_true(image_creative.get("media_asset", {}).get("id") == image_asset["id"], "creative must return safe media asset metadata")
        assert_true("storage_key" not in json.dumps(image_creative), "creative response must not expose storage keys")
        assert_true("checksum" not in json.dumps(image_creative), "creative response must not expose checksums")

        video_creative = pulse_ads_service.create_creative(
            conn,
            owner_id,
            {
                "campaign_id": campaign["id"],
                "creative_type": "video",
                "title": "Secure Video Creative",
                "body": "Uses uploaded video and thumbnail assets.",
                "media_asset_id": video_asset["id"],
                "thumbnail_asset_id": thumbnail_asset["id"],
                "destination_url": "https://example.com/video",
            },
        )
        assert_true(video_creative.get("thumbnail_asset", {}).get("id") == thumbnail_asset["id"], "video custom thumbnail was not linked")
        audio_creative = pulse_ads_service.create_creative(
            conn,
            owner_id,
            {
                "campaign_id": campaign["id"],
                "creative_type": "audio",
                "title": "Secure Audio Creative",
                "body": "Uses an uploaded audio asset.",
                "media_asset_id": audio_asset["id"],
                "destination_url": "https://example.com/audio",
            },
        )
        assert_true(audio_creative.get("media_asset", {}).get("media_type") == "audio", "audio asset was not linked")

        expect_pulse_ads_error(
            "cross advertiser media reuse",
            lambda: pulse_ads_service.create_creative(
                conn,
                other_id,
                {
                    "campaign_id": other_campaign["id"],
                    "creative_type": "image",
                    "title": "Cross Account Attack",
                    "body": "Should not access another advertiser asset.",
                    "media_asset_id": image_asset["id"],
                    "destination_url": "https://example.com",
                },
            ),
        )

        deleted = pulse_ads_service.delete_ad_media_asset(conn, owner_id, account["id"], unused_asset["id"])
        assert_true(deleted.get("deleted") is True, "unused draft media should be deletable")
        expect_pulse_ads_error("delete linked media", lambda: pulse_ads_service.delete_ad_media_asset(conn, owner_id, account["id"], image_asset["id"]))

        pulse_ads_service.submit_creative_for_review(conn, owner_id, image_creative["id"])
        pulse_ads_service.approve_creative(conn, 9001, image_creative["id"], "Media workflow audit approval")
        cur = conn.cursor()
        cur.execute("UPDATE pulse_ad_accounts SET status='active', verification_status='verified' WHERE id=?", (account["id"],))
        cur.execute("UPDATE pulse_ad_campaigns SET status='active', start_at='', end_at='' WHERE id=?", (campaign["id"],))
        wallet = pulse_ad_payments.ensure_wallet(conn, account["id"])
        cur.execute("UPDATE pulse_ad_wallets SET available_balance_cents=5000, lifetime_funded_cents=5000 WHERE id=?", (wallet["id"],))
        conn.commit()
        ads = pulse_ads_service.select_ads(conn, user_id=viewer_id, session_id="media-audit", context="home", device_type="mobile", limit=1)
        assert_true(ads and ads[0].get("media_url") == image_asset["public_url"], "approved uploaded asset did not resolve for delivery")
        forbidden = {"storage_key", "checksum", "owner_user_id", "ad_account_id"}
        assert_true(not (forbidden & set(ads[0].keys())), "delivery payload leaked internal media fields")

        client = bot.webhook_app.test_client()
        with client.session_transaction() as session:
            session["account_user_id"] = owner_id
            session["csrf_token"] = "ad-media-csrf"
        missing_csrf = client.post(
            f"/api/pulse/ads/accounts/{account['id']}/media/upload",
            data={"asset_kind": "creative_media", "file": (BytesIO(b"not-image"), "missing-csrf.jpg")},
            content_type="multipart/form-data",
        )
        assert_true(missing_csrf.status_code == 403, "media upload route must require CSRF")
        unsafe_svg = client.post(
            f"/api/pulse/ads/accounts/{account['id']}/media/upload",
            data={"asset_kind": "creative_media", "file": (BytesIO(b"<svg><script>alert(1)</script></svg>"), "bad.svg")},
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": "ad-media-csrf"},
        )
        assert_true(unsafe_svg.status_code == 400, "media upload route must reject unsafe SVG/script payloads")

        print(
            json.dumps(
                {
                    "ok": True,
                    "checks": [
                        "url_inputs_rejected",
                        "media_asset_required",
                        "safe_asset_response",
                        "custom_thumbnail_linked",
                        "audio_asset_linked",
                        "cross_account_blocked",
                        "draft_delete",
                        "delivery_resolves_internal_asset",
                        "csrf_required",
                        "unsafe_svg_rejected",
                    ],
                },
                indent=2,
            )
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
