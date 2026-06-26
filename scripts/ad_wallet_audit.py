#!/usr/bin/env python3
"""Audit PulseSoc ad wallet ledger safety."""

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
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(prefix='pulse_ad_wallet_', suffix='.db', delete=False).name}"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["FLASK_SECRET_KEY"] = "wallet-audit-secret"
os.environ["SESSION_SECRET"] = "wallet-audit-session"
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
        VALUES ('wallet-owner@example.com', 'x', 'wallet-owner', 'Wallet Owner', 'active', 1, 1, datetime('now'))
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
            "business_name": "Wallet Audit Studio",
            "business_email": "wallet@example.com",
            "business_website": "https://example.com",
            "business_type": "creator_tools",
        })
        account_id = account["id"]
        session_a = pulse_ad_payments.create_funding_session(conn, owner_id, account_id, {
            "amount_cents": 2500,
            "currency": "usd",
            "idempotency_key": "wallet-audit-funding",
        })
        session_b = pulse_ad_payments.create_funding_session(conn, owner_id, account_id, {
            "amount_cents": 2500,
            "currency": "usd",
            "idempotency_key": "wallet-audit-funding",
        })
        assert_true(session_b.get("deduped"), "funding sessions must be idempotent")
        pulse_ad_payments.credit_wallet_from_stripe_session(conn, "evt_wallet_audit", {
            "id": "cs_test_wallet_audit",
            "amount_total": 2500,
            "currency": "usd",
            "payment_status": "paid",
            "metadata": {
                "purpose": "pulse_ad_wallet_funding",
                "funding_session_id": str(session_a["id"]),
                "ad_account_id": str(account_id),
                "user_id": str(owner_id),
                "amount_cents": "2500",
                "currency": "usd",
            },
        })
        pulse_ad_payments.credit_wallet_from_stripe_session(conn, "evt_wallet_audit", {
            "id": "cs_test_wallet_audit",
            "amount_total": 2500,
            "currency": "usd",
            "metadata": {
                "purpose": "pulse_ad_wallet_funding",
                "funding_session_id": str(session_a["id"]),
                "ad_account_id": str(account_id),
            },
        })
        wallet = pulse_ad_payments.wallet_summary(conn, owner_id, account_id)
        assert_true(wallet["available_balance_cents"] == 2500, "wallet crediting must not duplicate")
        campaign = pulse_ads_service.create_campaign(conn, owner_id, {
            "ad_account_id": account_id,
            "campaign_name": "Budget Audit",
            "objective": "brand_awareness",
            "budget_type": "daily",
            "daily_budget_cents": 1000,
            "placements": ["feed_inline"],
        })
        reserve = pulse_ad_payments.reserve_campaign_budget(conn, owner_id, campaign["id"])
        assert_true(reserve["reserved_cents"] == 1000, "budget reserve should match available budget when under reserve cap")
        spend = pulse_ad_payments.record_spend_event(conn, campaign["id"], 1, "feed_inline", amount_cents=1, idempotency_key="wallet-spend-1")
        assert_true(spend["ok"], "spend event should post")
        spend_dup = pulse_ad_payments.record_spend_event(conn, campaign["id"], 1, "feed_inline", amount_cents=1, idempotency_key="wallet-spend-1")
        assert_true(spend_dup.get("deduped"), "spend events must be idempotent")
        wallet_after = pulse_ad_payments.wallet_summary(conn, owner_id, account_id)
        assert_true(wallet_after["available_balance_cents"] >= 0, "wallet cannot go negative")
        assert_true("stripe_customer_id" not in json.dumps(wallet_after), "wallet summary must not expose provider ids")
        print(json.dumps({"ok": True, "checks": ["idempotent_funding", "idempotent_spend", "no_negative_balance", "no_provider_id_leak"]}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
