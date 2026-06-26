#!/usr/bin/env python3
"""Audit PulseSoc advertiser portal wiring and permissions."""

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
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(prefix='pulse_ads_portal_', suffix='.db', delete=False).name}"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["FLASK_SECRET_KEY"] = "portal-audit-secret"
os.environ["SESSION_SECRET"] = "portal-audit-session"

import bot  # noqa: E402
from services import pulse_advertiser_portal  # noqa: E402


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def db_path() -> str:
    return os.environ["DATABASE_URL"].replace("sqlite:///", "", 1)


def create_user(email: str) -> int:
    conn = sqlite3.connect(db_path())
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (email, password_hash, username, display_name, account_status, access_enabled, login_enabled, created_at)
            VALUES (?, 'x', ?, ?, 'active', 1, 1, datetime('now'))
            """,
            (email, email.split("@")[0], email.split("@")[0]),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def login(client, user_id: int):
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["csrf_token"] = "audit-csrf"


def api(client, method: str, path: str, payload=None, csrf=True):
    headers = {"X-CSRF-Token": "audit-csrf"} if csrf else {}
    if method == "GET":
        return client.get(path, headers=headers)
    return client.open(path, method=method, json=payload or {}, headers=headers)


def main():
    bot.init_db()
    owner_id = create_user("ads-owner@example.com")
    intruder_id = create_user("ads-intruder@example.com")
    client = bot.webhook_app.test_client()
    login(client, owner_id)

    page = client.get("/pulse/advertise")
    assert_true(page.status_code == 200, "advertiser portal page should load")

    denied = api(client, "POST", "/api/pulse/ads/accounts", {"business_name": "No CSRF"}, csrf=False)
    assert_true(denied.status_code == 403, "write APIs must require CSRF")

    account_resp = api(
        client,
        "POST",
        "/api/pulse/ads/accounts",
        {
            "business_name": "Pulse Launch Studio",
            "business_email": "team@example.com",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        },
    )
    assert_true(account_resp.status_code == 200, account_resp.get_data(as_text=True))
    account_id = account_resp.get_json()["account"]["id"]

    profile_resp = api(
        client,
        "POST",
        f"/api/pulse/ads/accounts/{account_id}/profile",
        {
            "legal_name": "Pulse Launch Studio LLC",
            "tax_identifier": "12-3456789",
            "contact_email": "ops@example.com",
            "website": "https://example.com",
        },
    )
    assert_true(profile_resp.status_code == 200, profile_resp.get_data(as_text=True))
    profile = profile_resp.get_json()["profile"]
    assert_true("3456789" not in json.dumps(profile), "full tax id must not be returned")
    assert_true(profile["tax_identifier_masked"].startswith("***"), "tax id should be masked")

    campaign_resp = api(
        client,
        "POST",
        "/api/pulse/ads/campaigns",
        {
            "ad_account_id": account_id,
            "campaign_name": "Creator Launch",
            "objective": "awareness",
            "budget_type": "daily",
            "daily_budget_cents": 2500,
            "placements": ["feed_inline", "pulse_network_hologram"],
        },
    )
    assert_true(campaign_resp.status_code == 200, campaign_resp.get_data(as_text=True))
    campaign_id = campaign_resp.get_json()["campaign"]["id"]

    patch_resp = api(
        client,
        "PATCH",
        f"/api/pulse/ads/campaigns/{campaign_id}",
        {
            "campaign_name": "Creator Launch V2",
            "objective": "traffic",
            "budget_type": "lifetime",
            "lifetime_budget_cents": 10000,
            "placements": ["feed_inline_ufo_mobile"],
        },
    )
    assert_true(patch_resp.status_code == 200, patch_resp.get_data(as_text=True))
    assert_true("feed_inline_ufo_mobile" in patch_resp.get_json()["campaign"]["placements"], "campaign placements should update")

    creative_resp = api(
        client,
        "POST",
        "/api/pulse/ads/creatives",
        {
            "campaign_id": campaign_id,
            "creative_type": "image",
            "title": "Creator Intelligence Stack",
            "body": "Premium tools for safer creators.",
            "media_url": "https://example.com/ad.png",
            "destination_url": "https://example.com/creator",
            "call_to_action": "Explore",
        },
    )
    assert_true(creative_resp.status_code == 200, creative_resp.get_data(as_text=True))
    creative_id = creative_resp.get_json()["creative"]["id"]

    submit_resp = api(client, "POST", f"/api/pulse/ads/creatives/{creative_id}/action", {"action": "submit"})
    assert_true(submit_resp.status_code == 200, submit_resp.get_data(as_text=True))

    duplicate_resp = api(client, "POST", f"/api/pulse/ads/campaigns/{campaign_id}/action", {"action": "duplicate"})
    assert_true(duplicate_resp.status_code == 200, duplicate_resp.get_data(as_text=True))
    assert_true(duplicate_resp.get_json()["status"] == "draft", "duplicated campaign should be draft")

    billing_resp = api(client, "GET", f"/api/pulse/ads/accounts/{account_id}/billing-summary")
    assert_true(billing_resp.status_code == 200, billing_resp.get_data(as_text=True))
    assert_true("stripe_customer_id" not in json.dumps(billing_resp.get_json()), "billing summary must not expose Stripe ids")

    portal_resp = api(client, "GET", "/api/pulse/ads/portal")
    assert_true(portal_resp.status_code == 200, portal_resp.get_data(as_text=True))
    portal = portal_resp.get_json()["portal"]
    assert_true(portal["metrics"]["campaign_count"] >= 2, "portal should include campaigns")
    assert_true(portal["metrics"]["creative_count"] >= 1, "portal should include creatives")
    assert_true(portal["review_board"], "portal should include advertiser review state")

    login(client, intruder_id)
    intruder_resp = api(client, "GET", f"/api/pulse/ads/accounts/{account_id}/profile")
    assert_true(intruder_resp.status_code == 404, "other users must not see advertiser account profile")
    intruder_campaign = api(client, "POST", f"/api/pulse/ads/campaigns/{campaign_id}/action", {"action": "pause"})
    assert_true(intruder_campaign.status_code == 404, "other users must not mutate campaigns")

    expected_files = [
        ROOT / "templates/pulse_advertiser_portal.html",
        ROOT / "static/css/pulse_advertiser_portal.css",
        ROOT / "static/js/pulse_advertiser_portal.js",
    ]
    for path in expected_files:
        assert_true(path.exists(), f"missing portal asset {path}")

    print(json.dumps({
        "ok": True,
        "account_id": account_id,
        "campaign_id": campaign_id,
        "creative_id": creative_id,
        "checks": [
            "page_loads",
            "csrf_required",
            "account_profile_masking",
            "campaign_wizard",
            "creative_submission",
            "billing_no_stripe_id",
            "owner_only_permissions",
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
