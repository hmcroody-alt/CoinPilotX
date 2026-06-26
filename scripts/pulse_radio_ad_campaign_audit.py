#!/usr/bin/env python3
"""Audit the real Pulse Radio sponsored ad activation flow in isolation."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmpdir = tempfile.TemporaryDirectory(prefix="pulse_radio_ad_audit_")
db_path = Path(tmpdir.name) / "audit.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["FORCE_INIT_DB"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "pulse-radio-ad-audit-secret")
os.environ.setdefault("SESSION_SECRET", "pulse-radio-ad-audit-session")
os.environ["PULSE_RADIO_AD_IMAGE_SOURCE"] = str(ROOT / "static" / "uploads" / "pulse_ads" / "pulse-radio-sponsored-ad.png")

import bot  # noqa: E402
from scripts.activate_pulse_radio_ad import DESTINATION_URL, PUBLIC_MEDIA_URL, activate_pulse_radio_ad  # noqa: E402
from services import pulse_ads_service  # noqa: E402


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def connect():
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    return conn


def main():
    result = activate_pulse_radio_ad()
    assert_true(result["ok"], "Pulse Radio ad activation failed")
    assert_true(result["media_url"] == PUBLIC_MEDIA_URL, "Media URL was not normalized to static ads storage")
    assert_true(result["destination_url"] == DESTINATION_URL, "Pulse Radio destination URL is incorrect")
    assert_true(result["creative_status"] == "approved", "Creative was not approved")
    assert_true(result["moderation_status"] == "approved", "Creative did not pass moderation approval")
    assert_true(result["campaign_status"] == "active", "Campaign is not active")
    assert_true(result["desktop_served"], "Desktop placement did not serve the Pulse Radio ad")
    assert_true(result["mobile_served"], "Mobile placement did not serve the Pulse Radio ad")
    assert_true(result["radio_served"], "Pulse Radio sponsor placement did not serve the ad")
    assert_true(result["dashboard_served"], "Dashboard sponsor placement did not serve the ad")
    assert_true(result["tracking_verified"], "Impression/click/hide tracking was not verified")
    required = {"feed_inline", "pulse_radio_sponsor", "dashboard_sponsor", "feed_side_ufo_desktop", "feed_inline_ufo_mobile"}
    assert_true(required.issubset(set(result["placements"])), "Required placements are missing")

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, reviewer_id FROM pulse_ad_moderation_queue WHERE creative_id=?", (result["creative_id"],))
        review = cur.fetchone()
        assert_true(review and review["status"] == "approved" and review["reviewer_id"], "Creative did not go through moderation approval")
        cur.execute("SELECT COUNT(*) AS count FROM pulse_ad_policy_flags WHERE creative_id=?", (result["creative_id"],))
        assert_true(cur.fetchone()["count"] == 0, "Pulse Radio ad unexpectedly generated policy flags")
        ads = pulse_ads_service.select_ads(conn, user_id=1010, session_id="audit-payload", context="home", device_type="mobile", limit=5)
        ad = next((item for item in ads if int(item.get("creative_id") or 0) == result["creative_id"]), None)
        assert_true(ad, "Pulse Radio ad missing from mobile payload")
        assert_true(ad.get("media_url") == PUBLIC_MEDIA_URL, "Payload media URL is incorrect")
        assert_true(ad.get("destination_url") == DESTINATION_URL, "Payload destination URL is incorrect")
        forbidden = {"owner_user_id", "business_email", "business_phone", "interests_json", "keywords_json"}
        assert_true(not (forbidden & set(ad.keys())), "Ad payload leaks private advertiser data")
        assert_true(ad.get("delivery_token") and ad.get("tracking_nonce"), "Ad payload is missing signed tracking fields")
    finally:
        conn.close()
        tmpdir.cleanup()
    print("Pulse Radio ad campaign audit passed.")


if __name__ == "__main__":
    main()
