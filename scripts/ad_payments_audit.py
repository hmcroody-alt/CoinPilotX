#!/usr/bin/env python3
"""Audit PulseSoc advertiser payment route guardrails."""

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
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(prefix='pulse_ad_payments_', suffix='.db', delete=False).name}"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["FLASK_SECRET_KEY"] = "payments-audit-secret"
os.environ["SESSION_SECRET"] = "payments-audit-session"
os.environ.pop("PULSE_ADS_BILLING_ENABLED", None)

import bot  # noqa: E402


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def db_path() -> str:
    return os.environ["DATABASE_URL"].replace("sqlite:///", "", 1)


def create_user() -> int:
    conn = sqlite3.connect(db_path())
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (email, password_hash, username, display_name, account_status, access_enabled, login_enabled, created_at)
            VALUES ('payments-owner@example.com', 'x', 'payments-owner', 'Payments Owner', 'active', 1, 1, datetime('now'))
            """
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def login(client, user_id: int):
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["csrf_token"] = "payments-csrf"


def main():
    bot.init_db()
    user_id = create_user()
    client = bot.webhook_app.test_client()
    login(client, user_id)
    account_resp = client.post(
        "/api/pulse/ads/accounts",
        json={"business_name": "Payments Audit", "business_website": "https://example.com"},
        headers={"X-CSRF-Token": "payments-csrf"},
    )
    assert_true(account_resp.status_code == 200, account_resp.get_data(as_text=True))
    account_id = account_resp.get_json()["account"]["id"]
    no_csrf = client.post(f"/api/pulse/ads/accounts/{account_id}/wallet/funding-session", json={"amount_cents": 2500})
    assert_true(no_csrf.status_code == 403, "funding route must require CSRF")
    disabled = client.post(
        f"/api/pulse/ads/accounts/{account_id}/wallet/funding-session",
        json={"amount_cents": 2500, "idempotency_key": "payments-audit"},
        headers={"X-CSRF-Token": "payments-csrf"},
    )
    assert_true(disabled.status_code == 503, "billing-disabled mode must not create live checkout")
    wallet_resp = client.get(f"/api/pulse/ads/accounts/{account_id}/wallet", headers={"X-CSRF-Token": "payments-csrf"})
    assert_true(wallet_resp.status_code == 200, wallet_resp.get_data(as_text=True))
    assert_true("stripe_customer_id" not in json.dumps(wallet_resp.get_json()), "wallet route must not expose Stripe ids")
    print(json.dumps({"ok": True, "checks": ["csrf_required", "billing_disabled_blocks_checkout", "wallet_no_stripe_id"]}, indent=2))


if __name__ == "__main__":
    main()
