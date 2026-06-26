#!/usr/bin/env python3
"""Audit the PulseSoc Ads foundation.

This script uses an isolated SQLite database and validates schema, moderation,
eligibility, privacy-safe payloads, tracking, CSRF enforcement, and route
registration. It does not print secrets or tokens.
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

tmpdir = tempfile.TemporaryDirectory(prefix="pulse_ads_audit_")
db_path = Path(tmpdir.name) / "audit.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["FORCE_INIT_DB"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "pulse-ads-audit-secret")
os.environ.setdefault("SESSION_SECRET", "pulse-ads-audit-session")

import bot  # noqa: E402
from services import pulse_ad_payments, pulse_ads_service  # noqa: E402


REQUIRED_TABLES = {
    "pulse_ad_accounts",
    "pulse_ad_campaigns",
    "pulse_ad_creatives",
    "pulse_ad_placements",
    "pulse_ad_campaign_placements",
    "pulse_ad_targeting",
    "pulse_ad_impressions",
    "pulse_ad_clicks",
    "pulse_ad_events",
    "pulse_ad_frequency_caps",
    "pulse_ad_moderation_queue",
    "pulse_ad_policy_flags",
    "pulse_ad_audit_logs",
    "pulse_ad_review_board",
    "pulse_ad_platform_settings",
}

REQUIRED_ROUTES = {
    "/api/pulse/ads/placements",
    "/api/pulse/ads/placement-metadata",
    "/api/pulse/ads/impression",
    "/api/pulse/ads/viewability",
    "/api/pulse/ads/click",
    "/api/pulse/ads/event",
    "/api/pulse/ads/hide",
    "/api/pulse/ads/accounts",
    "/api/pulse/ads/campaigns",
    "/api/pulse/ads/creatives",
    "/api/pulse/ads/creatives/submit",
    "/api/pulse/ads/analytics",
    "/api/admin/pulse/ads/review-board",
    "/api/admin/pulse/ads/creatives/approve",
    "/api/admin/pulse/ads/creatives/reject",
    "/api/admin/pulse/ads/campaigns/suspend",
    "/api/admin/pulse/ads/kill-switch",
}


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def connect():
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row["name"] for row in cur.fetchall()}


def create_users(conn):
    cur = conn.cursor()
    now = pulse_ads_service.now_iso()
    cur.execute(
        """
        INSERT INTO users (user_id, username, email, account_status, created_at, signup_time)
        VALUES (1001, 'ads_owner', 'ads-owner@example.com', 'active', ?, ?)
        """,
        (now, now),
    )
    cur.execute(
        """
        INSERT INTO users (user_id, username, email, account_status, created_at, signup_time)
        VALUES (1002, 'ads_viewer', 'ads-viewer@example.com', 'active', ?, ?)
        """,
        (now, now),
    )
    conn.commit()


def main():
    bot.INIT_DB_COMPLETED = False
    bot.init_db()
    conn = connect()
    try:
        names = table_names(conn)
        missing = sorted(REQUIRED_TABLES - names)
        assert_true(not missing, f"Missing ads tables: {missing}")

        cur = conn.cursor()
        cur.execute("SELECT placement_key FROM pulse_ad_placements")
        placements = {row["placement_key"] for row in cur.fetchall()}
        expected_placements = {key for key, *_ in pulse_ads_service.PLACEMENTS}
        assert_true(expected_placements.issubset(placements), "Required PulseSoc ad placements were not seeded")

        create_users(conn)
        account = pulse_ads_service.create_ad_account(
            conn,
            1001,
            {
                "business_name": "Pulse QA Studio",
                "business_website": "https://example.com",
                "business_type": "creator_tools",
            },
        )
        campaign = pulse_ads_service.create_campaign(
            conn,
            1001,
            {
                "ad_account_id": account["id"],
                "campaign_name": "Creator Signal",
                "objective": "awareness",
                "placements": ["feed_inline", "pulse_network_hologram"],
            },
        )
        creative = pulse_ads_service.create_creative(
            conn,
            1001,
            {
                "campaign_id": campaign["id"],
                "creative_type": "hologram",
                "title": "Creator intelligence stack",
                "body": "Approved tools that help creators publish safer and faster.",
                "destination_url": "https://example.com/creator",
                "call_to_action": "Explore",
            },
        )
        pulse_ads_service.submit_creative_for_review(conn, 1001, creative["id"])
        blocked_before_approval = pulse_ads_service.select_ads(conn, user_id=1002, session_id="viewer-a", context="home", device_type="desktop")
        assert_true(blocked_before_approval == [], "Unapproved creative was served")

        pulse_ads_service.approve_creative(conn, 9001, creative["id"], "Audit approval")
        cur.execute("UPDATE pulse_ad_accounts SET status='active', verification_status='verified' WHERE id=?", (account["id"],))
        cur.execute("UPDATE pulse_ad_campaigns SET status='active', start_at='', end_at='' WHERE id=?", (campaign["id"],))
        wallet = pulse_ad_payments.ensure_wallet(conn, account["id"])
        cur.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=1000, lifetime_funded_cents=1000 WHERE id=?",
            (wallet["id"],),
        )
        conn.commit()
        ads = pulse_ads_service.select_ads(conn, user_id=1002, session_id="viewer-a", context="home", device_type="desktop", limit=1)
        assert_true(len(ads) == 1, "Approved active creative did not serve")
        ad = ads[0]
        forbidden_payload_keys = {"owner_user_id", "business_email", "business_phone", "interests_json", "keywords_json", "min_age", "max_age"}
        assert_true(not (forbidden_payload_keys & set(ad.keys())), "Client ad payload leaks private/internal targeting data")

        impression = pulse_ads_service.record_impression(conn, ad, viewer_user_id=1002, session_id="viewer-a", device_type="desktop", viewport="1440x900")
        assert_true(impression.get("impression_id"), "Impression was not recorded")
        viewability = pulse_ads_service.record_viewability(conn, {"impression_id": impression["impression_id"], "visible_ms": 1500}, viewer_user_id=1002)
        assert_true(viewability.get("viewable") is True, "Viewability did not mark as true")
        click = pulse_ads_service.record_click(conn, ad, viewer_user_id=1002, session_id="viewer-a")
        assert_true(click.get("destination_url") == "https://example.com/creator", "Click destination was not preserved")
        hidden = pulse_ads_service.record_event(conn, {**ad, "event_type": "hide", "reason": "not relevant"}, viewer_user_id=1002, session_id="viewer-a")
        assert_true(hidden.get("event_id"), "Hide event was not recorded")
        try:
            forged = dict(ad)
            forged.pop("delivery_token", None)
            pulse_ads_service.record_click(conn, forged, viewer_user_id=1002, session_id="viewer-a")
            raise AssertionError("Tracking accepted a missing delivery token")
        except pulse_ads_service.PulseAdsError:
            pass

        analytics = pulse_ads_service.advertiser_analytics(conn, 1001)
        assert_true(analytics["totals"]["impressions"] >= 1, "Advertiser analytics did not count impressions")
        assert_true("viewer_user_id" not in str(analytics), "Advertiser analytics exposed viewer identifiers")

        try:
            pulse_ads_service.create_creative(
                conn,
                1001,
                {
                    "campaign_id": campaign["id"],
                    "creative_type": "text",
                    "title": "Bad URL",
                    "body": "Should fail",
                    "destination_url": "javascript:alert(1)",
                },
            )
            raise AssertionError("Unsafe destination URL was accepted")
        except pulse_ads_service.PulseAdsError:
            pass

        pulse_ads_service.set_kill_switch(conn, False, 9001)
        assert_true(
            pulse_ads_service.select_ads(conn, user_id=1002, session_id="viewer-b", context="home", device_type="desktop") == [],
            "Kill switch did not stop ad serving",
        )
        pulse_ads_service.set_kill_switch(conn, True, 9001)

        cur.execute("UPDATE pulse_ad_campaigns SET status='paused' WHERE id=?", (campaign["id"],))
        conn.commit()
        assert_true(
            pulse_ads_service.select_ads(conn, user_id=1002, session_id="viewer-c", context="home", device_type="desktop") == [],
            "Paused campaign was served",
        )
    finally:
        conn.close()

    routes = {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}
    missing_routes = sorted(REQUIRED_ROUTES - routes)
    assert_true(not missing_routes, f"Missing ads API routes: {missing_routes}")

    with bot.webhook_app.test_client() as client:
        response = client.get("/api/admin/pulse/ads/review-board")
        assert_true(response.status_code in {401, 403}, "Admin review board did not reject anonymous access")
        with client.session_transaction() as sess:
            sess["account_user_id"] = 1002
            sess["csrf_token"] = "audit-csrf"
        response = client.post("/api/pulse/ads/impression", json={"creative_id": 1, "campaign_id": 1, "placement_key": "feed_inline"})
        assert_true(response.status_code == 403, "Tracking write did not require CSRF")

    print("Pulse Ads foundation audit passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        tmpdir.cleanup()
