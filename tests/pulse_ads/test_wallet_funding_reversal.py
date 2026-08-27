"""Reversal of advertiser wallet top-ups (refunds and disputes).

These tests exist because the failure they guard against is invisible: before
`reverse_wallet_funding`, a refunded top-up left the wallet balance untouched, so
the advertiser kept spending money Stripe had already clawed back and every
surface in the product agreed the books were fine.

The cumulative-amount case is the one that has bitten this repository before in
the marketplace refund path. Stripe's `amount_refunded` is a running total for
the charge, not the newest refund, so debiting it directly makes a second $10
refund take $20.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import pulse_ad_payments  # noqa: E402


SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    business_name TEXT,
    business_type TEXT,
    status TEXT DEFAULT 'active',
    verification_status TEXT DEFAULT 'unverified',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    currency TEXT DEFAULT 'usd',
    available_balance_cents INTEGER DEFAULT 0,
    pending_balance_cents INTEGER DEFAULT 0,
    promotional_credits_cents INTEGER DEFAULT 0,
    bonus_credits_cents INTEGER DEFAULT 0,
    refund_credits_cents INTEGER DEFAULT 0,
    lifetime_funded_cents INTEGER DEFAULT 0,
    lifetime_spent_cents INTEGER DEFAULT 0,
    reserved_budget_cents INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    campaign_id INTEGER,
    creative_id INTEGER,
    transaction_type TEXT,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    idempotency_key TEXT UNIQUE,
    description TEXT,
    metadata_json TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_wallet_funding_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    user_id INTEGER,
    amount_cents INTEGER,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'pending',
    provider TEXT DEFAULT 'stripe',
    provider_session_id TEXT,
    provider_payment_intent_id TEXT,
    provider_charge_id TEXT,
    reversed_cents INTEGER DEFAULT 0,
    checkout_url TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    funding_session_id INTEGER,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    reason TEXT,
    provider_reference_hash TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    invoice_number TEXT,
    receipt_number TEXT,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_account_id INTEGER,
    campaign_name TEXT,
    status TEXT DEFAULT 'draft',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT,
    entity_type TEXT,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    ip_hash TEXT,
    user_agent_hash TEXT,
    created_at TEXT
);
"""

OWNER_ID = 7001
FUNDED_CENTS = 50_000  # $500.00


class WalletReversalTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, business_type, status) VALUES (?, ?, ?, 'active')",
            (OWNER_ID, "Test Advertiser", "business"),
        )
        self.account_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO pulse_ad_wallet_funding_sessions
            (account_id, user_id, amount_cents, currency, status, provider, provider_session_id,
             provider_payment_intent_id, provider_charge_id, reversed_cents)
            VALUES (?, ?, ?, 'usd', 'credited', 'stripe', 'cs_test_1', 'pi_test_1', 'ch_test_1', 0)
            """,
            (self.account_id, OWNER_ID, FUNDED_CENTS),
        )
        self.funding_id = cur.lastrowid
        wallet = pulse_ad_payments.ensure_wallet(self.conn, self.account_id)
        cur.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=?, lifetime_funded_cents=? WHERE id=?",
            (FUNDED_CENTS, FUNDED_CENTS, wallet["id"]),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- helpers ---------------------------------------------------------

    def charge(self, amount_refunded, charge_id="ch_test_1"):
        return {
            "id": charge_id,
            "object": "charge",
            "payment_intent": "pi_test_1",
            "amount": FUNDED_CENTS,
            "amount_refunded": amount_refunded,
            "currency": "usd",
        }

    def balance(self):
        cur = self.conn.cursor()
        cur.execute("SELECT available_balance_cents FROM pulse_ad_wallets WHERE account_id=?", (self.account_id,))
        return cur.fetchone()["available_balance_cents"]

    def count(self, table):
        cur = self.conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return cur.fetchone()["n"]

    def transactions(self):
        cur = self.conn.cursor()
        cur.execute("SELECT transaction_type, amount_cents FROM pulse_ad_wallet_transactions ORDER BY id")
        return [(r["transaction_type"], r["amount_cents"]) for r in cur.fetchall()]

    # -- tests -----------------------------------------------------------

    def test_full_refund_debits_the_wallet_exactly_once(self):
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_full_1", self.charge(FUNDED_CENTS), "charge.refunded"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reversed_cents"], FUNDED_CENTS)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.transactions(), [("refund", FUNDED_CENTS)])
        self.assertEqual(self.count("pulse_ad_refunds"), 1)

    def test_cumulative_partial_refunds_debit_the_difference_not_the_total(self):
        """The marketplace double-count regression, in the ad wallet.

        Stripe reports `amount_refunded` as the running total on the charge. A
        second $10 refund arrives as 2000, not 1000. Debiting it as reported
        would take $20 for a $10 refund.
        """
        first = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_partial_1", self.charge(1000), "charge.refunded"
        )
        self.assertEqual(first["reversed_cents"], 1000)
        self.assertEqual(self.balance(), FUNDED_CENTS - 1000)

        second = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_partial_2", self.charge(2000), "charge.refunded"
        )
        self.assertEqual(second["reversed_cents"], 1000, "second $10 refund must debit $10, not the $20 running total")
        self.assertEqual(self.balance(), FUNDED_CENTS - 2000)
        self.assertEqual(self.transactions(), [("refund", 1000), ("refund", 1000)])

    def test_redelivered_event_does_not_debit_twice(self):
        pulse_ad_payments.reverse_wallet_funding(self.conn, "evt_dupe", self.charge(2500), "charge.refunded")
        after_first = self.balance()
        again = pulse_ad_payments.reverse_wallet_funding(self.conn, "evt_dupe", self.charge(2500), "charge.refunded")
        self.assertTrue(again["ok"])
        self.assertTrue(again.get("noop") or again.get("deduped"))
        self.assertEqual(self.balance(), after_first)
        self.assertEqual(self.count("pulse_ad_wallet_transactions"), 1)
        self.assertEqual(self.count("pulse_ad_refunds"), 1)

    def test_dispute_uses_its_own_amount_and_writes_a_chargeback(self):
        dispute = {
            "id": "dp_test_1",
            "object": "dispute",
            "charge": "ch_test_1",
            "payment_intent": "pi_test_1",
            "amount": 30_000,
            "currency": "usd",
            "reason": "fraudulent",
        }
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_dispute_1", dispute, "charge.dispute.created"
        )
        self.assertEqual(result["reversed_cents"], 30_000)
        self.assertEqual(self.transactions(), [("chargeback", 30_000)])
        cur = self.conn.cursor()
        cur.execute("SELECT status FROM pulse_ad_refunds")
        self.assertEqual(cur.fetchone()["status"], "disputed")

    def test_reversal_larger_than_the_top_up_is_capped(self):
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_over", self.charge(FUNDED_CENTS * 3), "charge.refunded"
        )
        self.assertEqual(result["reversed_cents"], FUNDED_CENTS)
        self.assertEqual(self.balance(), 0)

    def test_balance_may_go_negative_after_the_money_was_spent(self):
        """A clamped-to-zero wallet is a fake zero.

        If the advertiser already spent the top-up, the refund leaves a real
        debt. Showing $0.00 would tell everyone the books are square. The
        negative is the information; `spendable_balance_cents` is what stops it
        being spent.
        """
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=0, lifetime_spent_cents=? WHERE account_id=?",
            (FUNDED_CENTS, self.account_id),
        )
        self.conn.commit()

        pulse_ad_payments.reverse_wallet_funding(self.conn, "evt_neg", self.charge(FUNDED_CENTS), "charge.refunded")
        self.assertEqual(self.balance(), -FUNDED_CENTS)
        self.assertEqual(pulse_ad_payments.spendable_balance_cents(self.conn, self.account_id), 0)

        summary = pulse_ad_payments.wallet_summary(self.conn, OWNER_ID, self.account_id)
        self.assertEqual(summary["amount_owed_cents"], FUNDED_CENTS)
        self.assertEqual(summary["amount_owed"], "$500.00")
        self.assertEqual(summary["available_balance"], "-$500.00")
        self.assertEqual(summary["spendable_balance_cents"], 0)

    def test_active_campaigns_are_paused_when_the_account_can_no_longer_pay(self):
        cur = self.conn.cursor()
        for name, status in (("Live A", "active"), ("Live B", "active"), ("Old", "archived")):
            cur.execute(
                "INSERT INTO pulse_ad_campaigns (ad_account_id, campaign_name, status) VALUES (?, ?, ?)",
                (self.account_id, name, status),
            )
        self.conn.commit()

        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_pause", self.charge(FUNDED_CENTS), "charge.refunded"
        )
        self.assertEqual(result["campaigns_paused"], 2)
        cur.execute("SELECT campaign_name, status FROM pulse_ad_campaigns ORDER BY id")
        rows = {r["campaign_name"]: r["status"] for r in cur.fetchall()}
        self.assertEqual(rows, {"Live A": "paused", "Live B": "paused", "Old": "archived"})

    def test_a_charge_that_is_not_a_wallet_top_up_writes_nothing(self):
        """Every marketplace refund reaches this function. None may touch a wallet."""
        marketplace = {
            "id": "ch_marketplace_9",
            "object": "charge",
            "payment_intent": "pi_marketplace_9",
            "amount": 4200,
            "amount_refunded": 4200,
            "metadata": {"transaction_id": "412"},
        }
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_marketplace", marketplace, "charge.refunded"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_ad_wallet_funding")
        self.assertEqual(self.balance(), FUNDED_CENTS)
        self.assertEqual(self.count("pulse_ad_wallet_transactions"), 0)
        self.assertEqual(self.count("pulse_ad_refunds"), 0)
        self.assertEqual(self.count("pulse_ad_audit_logs"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
