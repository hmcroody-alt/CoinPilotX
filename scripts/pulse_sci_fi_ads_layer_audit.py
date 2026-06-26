#!/usr/bin/env python3
"""Audit PulseSoc sci-fi ad layer integration.

This audit runs against an isolated SQLite database. It verifies that the visual
ad layer is fed by the Ads Delivery Engine, approved creatives serve, unapproved
creatives do not serve, signed media events are accepted, and the Home page
includes the auto-booting client hook without exposing unsafe rendering paths.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmpdir = tempfile.TemporaryDirectory(prefix="pulse_sci_fi_ads_audit_")
db_path = Path(tmpdir.name) / "audit.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["FORCE_INIT_DB"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "pulse-sci-fi-ads-audit-secret")
os.environ.setdefault("SESSION_SECRET", "pulse-sci-fi-ads-audit-session")
os.environ.setdefault("PULSE_ADS_DELIVERY_SECRET", "pulse-sci-fi-delivery-token-secret")

import bot  # noqa: E402
from services import pulse_ad_payments, pulse_ads_service  # noqa: E402


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def connect():
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    return conn


def create_users(conn):
    now = pulse_ads_service.now_iso()
    cur = conn.cursor()
    for user_id, username in ((4101, "sci_fi_ad_owner"), (4102, "sci_fi_viewer")):
        cur.execute(
            """
            INSERT INTO users (user_id, username, email, account_status, created_at, signup_time)
            VALUES (?, ?, ?, 'active', ?, ?)
            """,
            (user_id, username, f"{username}@example.com", now, now),
        )
    cur.execute(
        """
        INSERT INTO privacy_preferences (user_id, personalized_ads_opt_out, updated_at)
        VALUES (4102, 1, ?)
        """,
        (now,),
    )
    conn.commit()


def create_ad_asset(conn, owner_id: int, account_id: int, creative_type: str, name: str) -> dict:
    now = pulse_ads_service.now_iso()
    media_type = "video" if creative_type == "video" else "audio" if creative_type == "audio" else "image"
    ext = "mp4" if media_type == "video" else "mp3" if media_type == "audio" else "jpg"
    mime = "video/mp4" if media_type == "video" else "audio/mpeg" if media_type == "audio" else "image/jpeg"
    media_url = f"/static/uploads/pulse_ads/{name.lower().replace(' ', '-')}.{ext}"
    thumb_url = f"/static/uploads/pulse_ads/{name.lower().replace(' ', '-')}.jpg"
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, original_filename, media_url, thumbnail_url, media_type, mime_type, file_size_bytes, width, height, duration_seconds, context_type, context_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pulse_ad_creative', ?, ?)
        """,
        (owner_id, f"{name}.{ext}", media_url, thumb_url, media_type, mime, 2048, 1200 if media_type != "audio" else 0, 628 if media_type != "audio" else 0, 15 if media_type in {"video", "audio"} else 0, f"account:{account_id}:creative_media", now),
    )
    return pulse_ads_service.create_ad_media_asset(
        conn,
        owner_id,
        account_id,
        {
            "id": cur.lastrowid,
            "media_type": media_type,
            "mime_type": mime,
            "media_url": media_url,
            "thumbnail_url": thumb_url,
            "width": 1200 if media_type != "audio" else 0,
            "height": 628 if media_type != "audio" else 0,
            "duration_seconds": 15 if media_type in {"video", "audio"} else 0,
            "file_size_bytes": 2048,
        },
    )


def build_ad(conn, *, placements, creative_type="video", campaign_name="Sci Fi Delivery", approved=True):
    account = pulse_ads_service.create_ad_account(
        conn,
        4101,
        {
            "business_name": f"{campaign_name} Studio",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        },
    )
    campaign = pulse_ads_service.create_campaign(
        conn,
        4101,
        {
            "ad_account_id": account["id"],
            "campaign_name": campaign_name,
            "objective": "awareness",
            "daily_budget_cents": 1000,
            "placements": placements,
        },
    )
    asset = create_ad_asset(conn, 4101, account["id"], creative_type, campaign_name)
    creative = pulse_ads_service.create_creative(
        conn,
        4101,
        {
            "campaign_id": campaign["id"],
            "creative_type": creative_type,
            "title": campaign_name,
            "body": "Approved sponsor signal rendered as a PulseSoc sci-fi projection.",
            "media_asset_id": asset["id"],
            "destination_url": "https://example.com/creator",
            "call_to_action": "Explore",
        },
    )
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_accounts SET status='active', verification_status='verified' WHERE id=?",
        (account["id"],),
    )
    wallet = pulse_ad_payments.ensure_wallet(conn, account["id"])
    cur.execute(
        "UPDATE pulse_ad_wallets SET available_balance_cents=2000, lifetime_funded_cents=2000 WHERE id=?",
        (wallet["id"],),
    )
    cur.execute("UPDATE pulse_ad_campaigns SET status='active', start_at='', end_at='' WHERE id=?", (campaign["id"],))
    if approved:
        pulse_ads_service.submit_creative_for_review(conn, 4101, creative["id"])
        pulse_ads_service.approve_creative(conn, 9001, creative["id"], "Sci-fi layer audit approval")
    conn.commit()
    return {"account": account, "campaign": campaign, "creative": creative}


def main():
    bot.INIT_DB_COMPLETED = False
    bot.init_db()
    conn = connect()
    try:
        create_users(conn)
        pulse_ads_service.set_kill_switch(conn, True, actor_user_id=9001)
        build_ad(conn, placements=["feed_side_ufo_desktop", "pulse_network_hologram"], creative_type="video")
        build_ad(conn, placements=["feed_inline_ufo_mobile"], creative_type="image", campaign_name="Mobile UFO")
        build_ad(conn, placements=["feed_inline"], campaign_name="Unapproved Signal", approved=False)

        desktop_ads = pulse_ads_service.select_ads(conn, user_id=4102, session_id="viewer-desktop", context="home", device_type="desktop", limit=4)
        keys = {ad["placement_key"] for ad in desktop_ads}
        assert_true("feed_side_ufo_desktop" in keys, "Desktop UFO placement did not serve")
        assert_true("pulse_network_hologram" in keys, "Pulse Network hologram placement did not serve")
        assert_true(all("Unapproved Signal" not in ad.get("title", "") for ad in desktop_ads), "Unapproved creative served")
        ufo_ad = next(ad for ad in desktop_ads if ad["placement_key"] == "feed_side_ufo_desktop")
        assert_true(ufo_ad.get("delivery_token") and ufo_ad.get("tracking_nonce"), "Sci-fi ad payload missing signed tracking")
        assert_true(ufo_ad.get("card_style") == "ufo-side", "Desktop UFO card style missing")

        impression = pulse_ads_service.record_impression(conn, ufo_ad, viewer_user_id=4102, session_id="viewer-desktop", device_type="desktop", viewport="1440x900")
        assert_true(impression.get("impression_id"), "Sci-fi impression was not tracked")
        video = pulse_ads_service.record_event(conn, {**ufo_ad, "event_type": "video_25"}, viewer_user_id=4102, session_id="viewer-desktop")
        assert_true(video.get("event_id"), "Video quartile event was rejected")
        hidden = pulse_ads_service.record_event(conn, {**ufo_ad, "event_type": "hide"}, viewer_user_id=4102, session_id="viewer-desktop")
        assert_true(hidden.get("event_id"), "Hide event was rejected")

        mobile_ads = pulse_ads_service.select_ads(conn, user_id=4102, session_id="viewer-mobile", context="home", device_type="mobile", limit=3)
        assert_true(any(ad["placement_key"] == "feed_inline_ufo_mobile" for ad in mobile_ads), "Mobile inline UFO placement did not serve")
        assert_true(all("desktop" not in ad["placement_key"] for ad in mobile_ads), "Desktop ad leaked into mobile placement")

        home_source = (ROOT / "bot.py").read_text()
        hook_source = (ROOT / "static/js/pulse_ads_hooks.js").read_text()
        css_source = (ROOT / "static/css/pulse_home_os.css").read_text()
        assert_true("/static/js/pulse_ads_hooks.js" in home_source, "Home page does not include Pulse Ads hook")
        assert_true("data-pulse-ad-zone" in home_source, "Home rails do not expose ad zones")
        assert_true("innerHTML" not in hook_source, "Ad hook uses unsafe innerHTML rendering")
        assert_true("IntersectionObserver" in hook_source, "Ad hook does not use visibility-based tracking")
        assert_true("visibilitychange" in hook_source, "Ad hook does not pause media on tab visibility changes")
        assert_true("prefers-reduced-motion" in hook_source and "prefers-reduced-motion" in css_source, "Reduced motion fallback missing")
        assert_true("pulse-sponsored-signal__ship" in css_source, "UFO visual style missing")
        assert_true("pointer-events: none" in css_source, "Decorative ad chrome is not pointer-safe")

        routes = {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}
        assert_true("/api/pulse/ads/placements" in routes, "Placement API route missing")
        assert_true("/api/pulse/ads/event" in routes, "Event API route missing")
    finally:
        conn.close()

    print("Pulse sci-fi ads layer audit passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        tmpdir.cleanup()
