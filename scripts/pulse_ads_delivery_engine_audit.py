#!/usr/bin/env python3
"""Audit the PulseSoc Ads Delivery Engine Phase 2.

Runs against an isolated SQLite database. The audit verifies context-aware
delivery, signed tracking, dedupe, placement metadata, advertiser analytics,
and privacy-safe payloads without printing secrets or user tokens.
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

tmpdir = tempfile.TemporaryDirectory(prefix="pulse_ads_delivery_audit_")
db_path = Path(tmpdir.name) / "audit.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["FORCE_INIT_DB"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "pulse-ads-delivery-audit-secret")
os.environ.setdefault("SESSION_SECRET", "pulse-ads-delivery-audit-session")
os.environ.setdefault("PULSE_ADS_DELIVERY_SECRET", "pulse-ads-delivery-token-secret")

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
    for user_id, username in ((2101, "ads_owner"), (2102, "ads_viewer")):
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
        VALUES (2102, 1, ?)
        """,
        (now,),
    )
    conn.commit()


def build_approved_ad(conn, *, placements, creative_type="text", campaign_name="Delivery Audit", account_active=True):
    account = pulse_ads_service.create_ad_account(
        conn,
        2101,
        {
            "business_name": f"{campaign_name} Studio",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        },
    )
    campaign = pulse_ads_service.create_campaign(
        conn,
        2101,
        {
            "ad_account_id": account["id"],
            "campaign_name": campaign_name,
            "objective": "awareness",
            "daily_budget_cents": 1000,
            "placements": placements,
        },
    )
    creative = pulse_ads_service.create_creative(
        conn,
        2101,
        {
            "campaign_id": campaign["id"],
            "creative_type": creative_type,
            "title": campaign_name,
            "body": "Privacy-safe sponsored signal for PulseSoc creators.",
            "destination_url": "https://example.com/creator",
            "call_to_action": "Explore",
        },
    )
    pulse_ads_service.submit_creative_for_review(conn, 2101, creative["id"])
    pulse_ads_service.approve_creative(conn, 9001, creative["id"], "Delivery audit approval")
    cur = conn.cursor()
    if account_active:
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
    conn.commit()
    return {"account": account, "campaign": campaign, "creative": creative}


def main():
    bot.INIT_DB_COMPLETED = False
    bot.init_db()
    conn = connect()
    try:
        create_users(conn)

        inactive = build_approved_ad(conn, placements=["feed_inline"], campaign_name="Inactive Advertiser", account_active=False)
        assert_true(
            pulse_ads_service.select_ads(conn, user_id=2102, session_id="viewer-inactive", context="feed", device_type="desktop") == [],
            "Inactive advertiser account served ads",
        )
        cur = conn.cursor()
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused' WHERE id=?", (inactive["campaign"]["id"],))
        conn.commit()

        feed = build_approved_ad(conn, placements=["feed_inline", "marketplace_sponsor"], campaign_name="Feed Signal")
        radio = build_approved_ad(conn, placements=["pulse_radio_sponsor"], creative_type="audio", campaign_name="Radio Signal")
        search = build_approved_ad(conn, placements=["search_sponsored_result"], campaign_name="Search Signal")

        metadata = pulse_ads_service.placement_metadata("marketplace", "mobile")
        assert_true(metadata and metadata[0]["placement_key"] == "marketplace_sponsor", "Marketplace metadata missing")
        assert_true("internal" not in str(metadata).lower(), "Placement metadata exposed internal data")

        desktop_feed = pulse_ads_service.select_ads(
            conn,
            user_id=2102,
            session_id="viewer-feed",
            context="feed",
            device_type="desktop",
            contextual_category="creator_tools",
            limit=2,
        )
        assert_true(desktop_feed, "Feed delivery did not return an approved active ad")
        ad = desktop_feed[0]
        assert_true(ad["placement_key"] == "feed_inline", "Desktop feed selected an incompatible placement")
        assert_true(ad.get("delivery_token") and ad.get("tracking_nonce"), "Delivery token or nonce missing")
        forbidden = {"owner_user_id", "business_email", "keywords_json", "interests_json", "min_age", "max_age"}
        assert_true(not (forbidden & set(ad.keys())), "Client delivery payload leaks targeting or owner data")

        impression = pulse_ads_service.record_impression(conn, ad, viewer_user_id=2102, session_id="viewer-feed", device_type="desktop", viewport="1440x900")
        assert_true(impression.get("impression_id"), "Signed impression was not recorded")
        deduped = pulse_ads_service.record_impression(conn, ad, viewer_user_id=2102, session_id="viewer-feed", device_type="desktop", viewport="1440x900")
        assert_true(deduped.get("deduped") is True, "Duplicate impression was not deduped")
        click = pulse_ads_service.record_click(conn, ad, viewer_user_id=2102, session_id="viewer-feed")
        assert_true(click.get("destination_url") == "https://example.com/creator", "Click destination failed server validation")
        report = pulse_ads_service.record_event(conn, {**ad, "event_type": "report", "reason": "audit"}, viewer_user_id=2102, session_id="viewer-feed")
        assert_true(report.get("event_id"), "Report event was not recorded")

        forged = dict(ad)
        forged["campaign_id"] = search["campaign"]["id"]
        try:
            pulse_ads_service.record_click(conn, forged, viewer_user_id=2102, session_id="viewer-feed")
            raise AssertionError("Tampered tracking payload was accepted")
        except pulse_ads_service.PulseAdsError:
            pass

        marketplace_ads = pulse_ads_service.select_ads(conn, user_id=2102, session_id="viewer-market", context="marketplace", device_type="mobile", limit=1)
        assert_true(marketplace_ads and marketplace_ads[0]["placement_key"] == "marketplace_sponsor", "Marketplace placement did not serve")
        radio_ads = pulse_ads_service.select_ads(conn, user_id=2102, session_id="viewer-radio", context="radio", device_type="mobile", limit=1)
        assert_true(radio_ads and radio_ads[0]["placement_key"] == "pulse_radio_sponsor", "Radio placement did not serve")
        search_ads = pulse_ads_service.select_ads(conn, user_id=2102, session_id="viewer-search", context="search", device_type="desktop", search_query="creator tools", limit=1)
        assert_true(search_ads and search_ads[0]["placement_key"] == "search_sponsored_result", "Search sponsored result did not serve")

        analytics = pulse_ads_service.advertiser_analytics(conn, 2101, feed["account"]["id"])
        assert_true(analytics["totals"]["impressions"] >= 1, "Advertiser analytics missed impression")
        assert_true(analytics["totals"]["clicks"] >= 1, "Advertiser analytics missed click")
        assert_true("delivery_token" not in str(analytics), "Advertiser analytics exposed delivery token")
        assert_true("viewer_user_id" not in str(analytics), "Advertiser analytics exposed viewer identity")

        routes = {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}
        for route in {"/api/pulse/ads/placement-metadata", "/api/pulse/ads/analytics", "/api/pulse/ads/placements"}:
            assert_true(route in routes, f"Missing route: {route}")

        with bot.webhook_app.test_client() as client:
            response = client.get("/api/pulse/ads/placement-metadata?context=radio&device_type=mobile")
            assert_true(response.status_code == 200, "Placement metadata should be public-safe for ad rendering")
            metadata = response.get_json() or {}
            assert_true(metadata.get("ok") is True, "Placement metadata response should be successful")
            assert_true(isinstance(metadata.get("placements"), list), "Placement metadata should return placement summaries")
            assert_true("delivery_token" not in str(metadata), "Placement metadata exposed delivery tokens")
            assert_true("advertiser" not in str(metadata).lower(), "Placement metadata exposed advertiser data")
    finally:
        conn.close()

    print("Pulse Ads delivery engine audit passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        tmpdir.cleanup()
