#!/usr/bin/env python3
"""Audit PulseSoc advertiser Stripe webhook crediting logic."""

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
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(prefix='pulse_ad_stripe_', suffix='.db', delete=False).name}"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["FLASK_SECRET_KEY"] = "stripe-audit-secret"
os.environ["SESSION_SECRET"] = "stripe-audit-session"
os.environ["PULSE_ADS_BILLING_ENABLED"] = "true"

import bot  # noqa: E402
from services import pulse_ad_payments, pulse_ads_service  # noqa: E402


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
        VALUES ('stripe-owner@example.com', 'x', 'stripe-owner', 'Stripe Owner', 'active', 1, 1, datetime('now'))
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
            "business_name": "Stripe Audit",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        })
        funding = pulse_ad_payments.create_funding_session(conn, owner_id, account["id"], {
            "amount_cents": 5000,
            "currency": "usd",
            "idempotency_key": "stripe-webhook-audit",
        })
        session = {
            "id": "cs_test_stripe_webhook_audit",
            "amount_total": 5000,
            "currency": "usd",
            "payment_status": "paid",
            "metadata": {
                "purpose": "pulse_ad_wallet_funding",
                "funding_session_id": str(funding["id"]),
                "ad_account_id": str(account["id"]),
                "user_id": str(owner_id),
                "amount_cents": "5000",
                "currency": "usd",
            },
        }
        first = pulse_ad_payments.credit_wallet_from_stripe_session(conn, "evt_ad_stripe_audit", session)
        second = pulse_ad_payments.credit_wallet_from_stripe_session(conn, "evt_ad_stripe_audit", session)
        assert_true(first["ok"], "webhook should credit wallet")
        assert_true(second.get("deduped"), "webhook replay must be deduped")
        wallet = pulse_ad_payments.wallet_summary(conn, owner_id, account["id"])
        assert_true(wallet["available_balance_cents"] == 5000, "wallet should be credited exactly once")
        assert_true("cs_test_stripe_webhook_audit" not in json.dumps(wallet), "provider session id must not be exposed")
        print(json.dumps({"ok": True, "checks": ["stripe_metadata_credit", "webhook_idempotency", "provider_id_redaction"]}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
