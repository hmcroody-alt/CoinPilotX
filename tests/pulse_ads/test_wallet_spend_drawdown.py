"""Delivery spend actually consumes the wallet buckets it is allowed to spend.

`spendable_balance_cents` has always counted promotional, bonus and refund
credits toward what a campaign may spend. `record_spend_event` only ever debited
`available_balance_cents`, and clamped it at zero. The two together meant an
account holding nothing but credits passed the affordability check on every
impression and was never charged for any of them: the credit buckets were
write-only, and delivery against them was free and unbounded.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import pulse_ad_payments  # noqa: E402
from tests.pulse_ads.test_wallet_funding_reversal import SCHEMA  # noqa: E402


OWNER_ID = 8100


class SpendDrawdownTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cur = self.conn.cursor()
        cur.execute("ALTER TABLE pulse_ad_campaigns ADD COLUMN spent_cents INTEGER DEFAULT 0")
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, business_type, status) VALUES (?, ?, ?, 'active')",
            (OWNER_ID, "Spend Advertiser", "business"),
        )
        self.account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO pulse_ad_campaigns (ad_account_id, campaign_name, status, spent_cents) VALUES (?, ?, 'active', 0)",
            (self.account_id, "Delivery"),
        )
        self.campaign_id = cur.lastrowid
        pulse_ad_payments.ensure_wallet(self.conn, self.account_id)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- helpers ---------------------------------------------------------

    def fund(self, **buckets):
        cur = self.conn.cursor()
        assignments = ", ".join(f"{column}=?" for column in buckets)
        cur.execute(
            f"UPDATE pulse_ad_wallets SET {assignments} WHERE account_id=?",
            (*buckets.values(), self.account_id),
        )
        self.conn.commit()

    def wallet(self):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM pulse_ad_wallets WHERE account_id=?", (self.account_id,))
        return dict(cur.fetchone())

    def spend(self, amount_cents, key):
        return pulse_ad_payments.record_spend_event(
            self.conn, self.campaign_id, None, "feed", amount_cents=amount_cents, idempotency_key=key
        )

    def campaign_status(self):
        cur = self.conn.cursor()
        cur.execute("SELECT status, spent_cents FROM pulse_ad_campaigns WHERE id=?", (self.campaign_id,))
        row = cur.fetchone()
        return row["status"], row["spent_cents"]

    # -- tests -----------------------------------------------------------

    def test_promotional_credit_is_consumed_not_just_counted(self):
        """The unbounded-free-delivery bug, directly.

        Zero cash, $1.00 of promotional credit. Before the fix this could be
        spent against forever without any bucket ever moving.
        """
        self.fund(available_balance_cents=0, promotional_credits_cents=100)

        for index in range(100):
            result = self.spend(1, f"spend-{index}")
            self.assertTrue(result["ok"], f"impression {index} should be funded")
            self.assertFalse(result.get("paused"))

        wallet = self.wallet()
        self.assertEqual(wallet["promotional_credits_cents"], 0)
        self.assertEqual(wallet["available_balance_cents"], 0)
        self.assertEqual(wallet["lifetime_spent_cents"], 100)

        exhausted = self.spend(1, "spend-101")
        self.assertFalse(exhausted["ok"])
        self.assertTrue(exhausted["paused"])
        self.assertEqual(self.campaign_status()[0], "paused")

    def test_grants_are_drawn_down_before_the_advertisers_own_cash(self):
        self.fund(
            promotional_credits_cents=300,
            bonus_credits_cents=200,
            refund_credits_cents=100,
            available_balance_cents=10_000,
        )

        first = self.spend(400, "waterfall-1")
        self.assertEqual(first["funded_from"], {"promotional_credits_cents": 300, "bonus_credits_cents": 100})

        second = self.spend(250, "waterfall-2")
        self.assertEqual(
            second["funded_from"],
            {"bonus_credits_cents": 100, "refund_credits_cents": 100, "available_balance_cents": 50},
        )

        wallet = self.wallet()
        self.assertEqual(wallet["promotional_credits_cents"], 0)
        self.assertEqual(wallet["bonus_credits_cents"], 0)
        self.assertEqual(wallet["refund_credits_cents"], 0)
        self.assertEqual(wallet["available_balance_cents"], 9_950)
        self.assertEqual(wallet["lifetime_spent_cents"], 650)

    def test_cash_spend_debits_the_full_amount(self):
        self.fund(available_balance_cents=5_000)
        result = self.spend(1_200, "cash-1")
        self.assertEqual(result["funded_from"], {"available_balance_cents": 1_200})
        self.assertEqual(self.wallet()["available_balance_cents"], 3_800)
        self.assertEqual(self.campaign_status(), ("active", 1_200))

    def test_a_negative_cash_balance_is_a_debt_not_a_funding_source(self):
        """A reversal can leave cash negative while credits remain.

        The credits are real and spendable; the negative cash is not a bucket
        that can be drawn further into.
        """
        self.fund(available_balance_cents=-2_000, promotional_credits_cents=3_000)

        result = self.spend(500, "debt-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["funded_from"], {"promotional_credits_cents": 500})
        wallet = self.wallet()
        self.assertEqual(wallet["available_balance_cents"], -2_000, "the debt must not deepen")
        self.assertEqual(wallet["promotional_credits_cents"], 2_500)

    def test_no_bucket_is_ever_driven_negative_by_spend(self):
        self.fund(available_balance_cents=700, promotional_credits_cents=300)
        self.spend(1_000, "exact-1")
        wallet = self.wallet()
        for column in pulse_ad_payments.SPEND_DRAWDOWN_ORDER:
            self.assertGreaterEqual(wallet[column], 0, column)
        self.assertEqual(wallet["available_balance_cents"], 0)
        self.assertEqual(wallet["promotional_credits_cents"], 0)

    def test_unaffordable_spend_pauses_and_writes_no_transaction(self):
        self.fund(available_balance_cents=50)
        result = self.spend(500, "short-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "wallet_insufficient")
        self.assertEqual(self.wallet()["available_balance_cents"], 50, "an unaffordable spend must not take a partial bite")
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_ad_wallet_transactions WHERE transaction_type='spend'")
        self.assertEqual(cur.fetchone()["n"], 0)
        self.assertEqual(self.campaign_status(), ("paused", 0))

    def test_reserved_budget_release_does_not_go_negative(self):
        self.fund(available_balance_cents=10_000, reserved_budget_cents=100)
        self.spend(500, "reserve-1")
        self.assertEqual(self.wallet()["reserved_budget_cents"], 0)

    def test_redelivered_spend_is_not_charged_twice(self):
        self.fund(available_balance_cents=5_000)
        self.spend(300, "dupe-key")
        again = self.spend(300, "dupe-key")
        self.assertTrue(again.get("deduped"))
        self.assertEqual(self.wallet()["available_balance_cents"], 4_700)

    def test_internal_promotion_accounts_still_bypass_billing(self):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE pulse_ad_accounts SET business_type='internal_promotion' WHERE id=?", (self.account_id,)
        )
        self.conn.commit()
        result = self.spend(900, "internal-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "internal_promotion")
        self.assertEqual(self.wallet()["available_balance_cents"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
