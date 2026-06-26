#!/usr/bin/env python3
"""Audit PulseSoc ads review-board moderation flow."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(prefix='pulse_ad_review_', suffix='.db', delete=False).name}"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["FLASK_SECRET_KEY"] = "review-audit-secret"
os.environ["SESSION_SECRET"] = "review-audit-session"

import bot  # noqa: E402
from services import pulse_ads_service  # noqa: E402


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def db_path() -> str:
    return os.environ["DATABASE_URL"].replace("sqlite:///", "", 1)


def create_user(conn) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (email, password_hash, username, display_name, account_status, access_enabled, login_enabled, created_at)
        VALUES ('review-owner@example.com', 'x', 'review-owner', 'Review Owner', 'active', 1, 1, datetime('now'))
        """
    )
    return cur.lastrowid


def main():
    bot.init_db()
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        owner_id = create_user(conn)
        conn.commit()
        account = pulse_ads_service.create_ad_account(conn, owner_id, {
            "business_name": "Review Audit",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        })
        campaign = pulse_ads_service.create_campaign(conn, owner_id, {
            "ad_account_id": account["id"],
            "campaign_name": "Review Campaign",
            "objective": "brand_awareness",
            "budget_type": "daily",
            "daily_budget_cents": 1000,
            "placements": ["feed_inline"],
        })
        creative = pulse_ads_service.create_creative(conn, owner_id, {
            "campaign_id": campaign["id"],
            "creative_type": "image",
            "title": "Review Creative",
            "body": "Safe creative for moderation.",
            "media_url": "https://example.com/ad.png",
            "destination_url": "https://example.com",
            "call_to_action": "Open",
        })
        submitted = pulse_ads_service.submit_creative_for_review(conn, owner_id, creative["id"])
        assert_true(submitted["moderation_status"] == "pending", "creative should enter pending review")
        rows = pulse_ads_service.review_board(conn)
        assert_true(rows and rows[0]["creative_id"] == creative["id"], "review board should include submitted creative")
        assert_true("destination_url" not in rows[0], "review board API must not expose landing URL in broad list")
        rejected = pulse_ads_service.reject_creative(conn, 999, creative["id"], "Needs changes")
        assert_true(rejected["moderation_status"] == "rejected", "admin rejection should update creative")
        approved = pulse_ads_service.approve_creative(conn, 999, creative["id"], "Approved after update")
        assert_true(approved["moderation_status"] == "approved", "admin approval should update creative")
        print(json.dumps({"ok": True, "checks": ["submit_review", "review_queue_visible", "broad_url_hidden", "reject", "approve"]}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
