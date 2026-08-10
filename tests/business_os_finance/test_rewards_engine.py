"""Rewards engine (Wave D) — Pulse Credits, cash rewards, redemption, reconcile.

Runs hermetically against a temporary SQLite DB (set via DATABASE_URL before
importing services.db), mirroring tests/business_os_finance/test_seller_payouts.py.

    python3 -m unittest tests.business_os_finance.test_rewards_engine -v
"""

import os
import tempfile
import unittest

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fin_rewards_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import (  # noqa: E402
    connect_accounts,
    incidents,
    reconciliation,
    seller_payouts,
)
from services.business_os.rewards import engine as rewards  # noqa: E402

ACCOUNT_SNAPSHOT_OK = {
    "ok": True,
    "provider_account_id": "acct_test_1",
    "payouts_enabled": True,
    "charges_enabled": True,
    "account": {"details_submitted": True},
}

_AD_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    business_name TEXT,
    business_type TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pulse_ad_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
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
CREATE TABLE IF NOT EXISTS pulse_ad_wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    campaign_id INTEGER,
    creative_id INTEGER,
    transaction_type TEXT,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'posted',
    idempotency_key TEXT UNIQUE,
    description TEXT,
    metadata_json TEXT,
    created_at TEXT
);
"""


class BaseCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        ledger.ensure_schema()
        incidents.ensure_schema()
        seller_payouts.ensure_schema()
        connect_accounts.ensure_schema()
        reconciliation.ensure_schema()
        rewards.ensure_schema()
        conn = db.connect()
        conn.executescript(_AD_TABLES_DDL)
        for table in (
            "reward_events", "pulse_credit_ledger",
            "seller_payout_events", "seller_payout_requests",
            "connect_account_state",
            "pulse_ad_accounts", "pulse_ad_wallets",
            "pulse_ad_wallet_transactions",
            "financial_incidents", "ledger_entries", "ledger_transactions",
            "ledger_balances", "reconciliation_runs",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
        conn.close()

    # -- helpers ----------------------------------------------------------
    def _grant_credits(self, key="evt-c-1", user_id="7", amount=100, **kw):
        return rewards.grant_reward(
            key, user_id, "engagement_bonus", "pulse_credits", amount, "test", **kw)

    def _grant_cash(self, key="evt-cash-1", user_id="7", amount=500, **kw):
        return rewards.grant_reward(
            key, user_id, "creator_bonus", "cash", amount, "test", **kw)

    def _connect_ok(self, user_id="7"):
        result = connect_accounts.record_account_snapshot(
            user_id, ACCOUNT_SNAPSHOT_OK)
        self.assertTrue(result["ok"])

    def _make_ad_account(self, owner=7):
        conn = db.connect()
        cur = conn.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, "
            "created_at, updated_at) VALUES (?, 'Test Biz', '', '')",
            (owner,),
        )
        account_id = cur.lastrowid
        conn.commit()
        conn.close()
        return int(account_id)

    def _wallet(self, account_id):
        conn = db.connect()
        row = conn.execute(
            "SELECT * FROM pulse_ad_wallets WHERE account_id=?",
            (account_id,)).fetchone()
        conn.close()
        return dict(row) if row is not None else None

    def _wallet_tx_count(self, account_id):
        conn = db.connect()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_wallet_transactions "
            "WHERE account_id=?", (account_id,)).fetchone()
        conn.close()
        return int(row["n"])

    def _credit_rows(self, user_id="7"):
        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM pulse_credit_ledger WHERE user_id=? ORDER BY id",
            (str(user_id),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _incident_types(self):
        conn = db.connect()
        rows = conn.execute(
            "SELECT incident_type, severity FROM financial_incidents").fetchall()
        conn.close()
        return [(str(r["incident_type"]), str(r["severity"])) for r in rows]

    def _balances(self, user_id="7"):
        return (
            ledger.get_balance(seller_payouts.seller_payable_account(user_id)),
            ledger.get_balance(seller_payouts.payout_pending_account(user_id)),
            ledger.get_balance(rewards.REWARDS_EXPENSE_ACCOUNT),
        )


class GrantRewardTests(BaseCase):
    def test_pulse_credits_clear_grants_immediately(self):
        result = self._grant_credits(amount=150)
        self.assertFalse(result["duplicate"])
        reward = result["reward"]
        self.assertEqual(reward["status"], "granted")
        self.assertEqual(reward["fraud_state"], "clear")
        self.assertEqual(rewards.get_credit_balance("7"), 150)
        rows = self._credit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta"], 150)
        self.assertEqual(rows[0]["balance_after"], 150)

    def test_grant_is_idempotent_on_event_key(self):
        first = self._grant_credits(key="evt-dup", amount=100)
        second = self._grant_credits(key="evt-dup", amount=100)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertFalse(second.get("conflict"))
        self.assertEqual(second["reward"]["id"], first["reward"]["id"])
        self.assertEqual(rewards.get_credit_balance("7"), 100)
        self.assertEqual(len(self._credit_rows()), 1)
        self.assertEqual(self._incident_types(), [])

    def test_same_key_different_amount_opens_critical_incident(self):
        self._grant_credits(key="evt-conflict", amount=100)
        replay = rewards.grant_reward(
            "evt-conflict", "7", "engagement_bonus", "pulse_credits", 999, "test")
        self.assertTrue(replay["duplicate"])
        self.assertTrue(replay["conflict"])
        self.assertEqual(int(replay["reward"]["amount"]), 100)
        self.assertEqual(rewards.get_credit_balance("7"), 100)
        self.assertIn((incidents.REWARD_DUPLICATE_ATTEMPT, "critical"),
                      self._incident_types())

    def test_cash_grant_lands_pending_and_moves_no_money(self):
        result = self._grant_cash()
        self.assertEqual(result["reward"]["status"], "pending")
        self.assertEqual(self._balances(), (0, 0, 0))
        self.assertEqual(self._credit_rows(), [])

    def test_invalid_inputs_rejected(self):
        for bad_amount in (0, -5, "100", 4.2, True, None):
            with self.assertRaises(rewards.RewardError):
                rewards.grant_reward(
                    f"evt-bad-{bad_amount}", "7", "x", "pulse_credits",
                    bad_amount, "test")
        with self.assertRaises(rewards.RewardError):
            rewards.grant_reward("", "7", "x", "pulse_credits", 10, "test")
        with self.assertRaises(rewards.RewardError):
            rewards.grant_reward("evt-k", "7", "x", "lottery", 10, "test")


class FraudGateTests(BaseCase):
    def test_review_holds_then_clear_grants(self):
        result = self._grant_credits(key="evt-held", amount=80,
                                     fraud_state="review")
        reward = result["reward"]
        self.assertEqual(reward["status"], "pending")
        self.assertEqual(reward["fraud_state"], "review")
        self.assertEqual(rewards.get_credit_balance("7"), 0)

        cleared = rewards.set_fraud_state(reward["id"], "clear", "admin:1")
        self.assertEqual(cleared["status"], "granted")
        self.assertEqual(cleared["fraud_state"], "clear")
        self.assertEqual(rewards.get_credit_balance("7"), 80)
        # replayed clear must not grant twice
        again = rewards.set_fraud_state(reward["id"], "clear", "admin:1")
        self.assertEqual(again["status"], "granted")
        self.assertEqual(rewards.get_credit_balance("7"), 80)
        self.assertEqual(len(self._credit_rows()), 1)

    def test_blocked_denies_pending_cash(self):
        result = self._grant_cash(key="evt-fraud-cash", fraud_state="review")
        reward = result["reward"]
        blocked = rewards.set_fraud_state(reward["id"], "blocked", "admin:1",
                                          note="fraud ring")
        self.assertEqual(blocked["status"], "denied")
        self.assertEqual(blocked["fraud_state"], "blocked")
        # denied is terminal: cannot approve
        with self.assertRaises(rewards.RewardError):
            rewards.approve_cash_reward(reward["id"], "admin:1")
        self.assertEqual(self._balances(), (0, 0, 0))

    def test_fraud_history_is_appended(self):
        reward = self._grant_cash(key="evt-hist", fraud_state="review")["reward"]
        rewards.set_fraud_state(reward["id"], "clear", "admin:1", note="looks fine")
        row = rewards.get_reward(reward_id=reward["id"])
        history = row["details"]["fraud_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["fraud_state"], "clear")


class CashLifecycleTests(BaseCase):
    def _approved_cash(self, key="evt-cash-1", amount=500):
        reward = self._grant_cash(key=key, amount=amount)["reward"]
        return rewards.approve_cash_reward(reward["id"], "admin:1")

    def test_approve_requires_clear_fraud(self):
        reward = self._grant_cash(key="evt-rev", fraud_state="review")["reward"]
        with self.assertRaises(rewards.RewardError) as ctx:
            rewards.approve_cash_reward(reward["id"], "admin:1")
        self.assertEqual(ctx.exception.reason, "fraud_gate")

    def test_disburse_without_account_needs_onboarding_and_stays_approved(self):
        reward = self._approved_cash()
        result = rewards.disburse_cash_reward(reward["id"], "admin:1")
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_onboarding"])
        self.assertEqual(result["reason"], "no_connected_account")
        fresh = rewards.get_reward(reward_id=reward["id"])
        self.assertEqual(fresh["status"], "approved")
        self.assertEqual(self._balances(), (0, 0, 0))

    def test_disburse_with_enabled_account_funds_and_fences(self):
        reward = self._approved_cash()
        self._connect_ok()
        result = rewards.disburse_cash_reward(reward["id"], "admin:1")
        self.assertTrue(result["ok"])
        self.assertFalse(result["needs_onboarding"])
        self.assertEqual(result["reward"]["status"], "disbursing")
        payout = result["payout"]
        self.assertEqual(payout["payout_key"], "reward_payout:evt-cash-1")
        self.assertEqual(payout["amount_cents"], 500)
        self.assertEqual(int(result["reward"]["payout_request_id"]),
                         int(payout["id"]))
        payable, pending, expense = self._balances()
        self.assertEqual((payable, pending, expense), (0, 500, -500))

    def test_disburse_replay_moves_money_once(self):
        reward = self._approved_cash()
        self._connect_ok()
        first = rewards.disburse_cash_reward(reward["id"], "admin:1")
        second = rewards.disburse_cash_reward(reward["id"], "admin:1")
        self.assertTrue(second["ok"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(int(second["payout"]["id"]), int(first["payout"]["id"]))
        self.assertEqual(self._balances(), (0, 500, -500))

    def test_payout_paid_marks_reward_disbursed(self):
        reward = self._approved_cash()
        self._connect_ok()
        result = rewards.disburse_cash_reward(reward["id"], "admin:1")
        payout = result["payout"]
        seller_payouts.mark_payout_submitted(
            payout["id"], stripe_payout_id="po_reward_1")
        applied = seller_payouts.apply_stripe_payout_event({
            "id": "evt_paid_1", "type": "payout.paid",
            "data": {"object": {"id": "po_reward_1", "object": "payout",
                                 "status": "paid"}},
        })
        self.assertTrue(applied["applied"])
        fresh = rewards.get_reward(reward_id=reward["id"])
        self.assertEqual(fresh["status"], "disbursed")

    def test_payout_failed_returns_reward_to_approved_without_double_money(self):
        reward = self._approved_cash(key="evt-cash-fail")
        self._connect_ok()
        result = rewards.disburse_cash_reward(reward["id"], "admin:1")
        payout = result["payout"]
        seller_payouts.mark_payout_submitted(
            payout["id"], stripe_payout_id="po_reward_2")
        seller_payouts.apply_stripe_payout_event({
            "id": "evt_fail_1", "type": "payout.failed",
            "data": {"object": {"id": "po_reward_2", "object": "payout",
                                 "status": "failed",
                                 "failure_code": "account_closed"}},
        })
        fresh = rewards.get_reward(reward_id=reward["id"])
        self.assertEqual(fresh["status"], "approved")
        # Wave B's reversal returned the fence; the engine moved nothing extra.
        payable, pending, expense = self._balances()
        self.assertEqual((payable, pending, expense), (500, 0, -500))

    def test_sync_ignores_non_reward_payouts_and_never_moves_money(self):
        result = rewards.sync_from_payout(
            {"payout_key": "user-payout-9", "status": "paid", "id": 1})
        self.assertTrue(result["ignored"])
        result = rewards.sync_from_payout(
            {"payout_key": "reward_payout:no-such-event", "status": "paid",
             "id": 1})
        self.assertTrue(result["ignored"])
        self.assertEqual(self._balances(), (0, 0, 0))


class RedeemTests(BaseCase):
    def test_redeem_burns_credits_and_grants_promo(self):
        self._grant_credits(amount=100)
        account_id = self._make_ad_account(owner=7)
        result = rewards.redeem_credits_to_ad_promo("7", 40, account_id, "rk-1")
        self.assertTrue(result["ok"])
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["credits_burned"], 40)
        self.assertEqual(result["promo_credit_cents"], 40 * rewards.CREDIT_TO_CENT)
        self.assertEqual(result["credit_balance"], 60)
        wallet = self._wallet(account_id)
        self.assertEqual(wallet["promotional_credits_cents"], 40)
        self.assertEqual(wallet["available_balance_cents"], 0)  # never cash
        self.assertEqual(self._wallet_tx_count(account_id), 1)

    def test_redeem_replay_is_idempotent(self):
        self._grant_credits(amount=100)
        account_id = self._make_ad_account(owner=7)
        rewards.redeem_credits_to_ad_promo("7", 40, account_id, "rk-dup")
        replay = rewards.redeem_credits_to_ad_promo("7", 40, account_id, "rk-dup")
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(rewards.get_credit_balance("7"), 60)
        self.assertEqual(self._wallet(account_id)["promotional_credits_cents"], 40)
        self.assertEqual(self._wallet_tx_count(account_id), 1)

    def test_insufficient_credits_rejected_and_wallet_untouched(self):
        self._grant_credits(amount=30)
        account_id = self._make_ad_account(owner=7)
        with self.assertRaises(rewards.RewardError) as ctx:
            rewards.redeem_credits_to_ad_promo("7", 100, account_id, "rk-over")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.reason, "insufficient_credits")
        self.assertEqual(rewards.get_credit_balance("7"), 30)
        # the promo credit written first in the transaction rolled back too
        wallet = self._wallet(account_id)
        self.assertTrue(wallet is None or wallet["promotional_credits_cents"] == 0)
        self.assertEqual(self._wallet_tx_count(account_id), 0)

    def test_redeem_requires_account_ownership(self):
        self._grant_credits(amount=100)
        account_id = self._make_ad_account(owner=8)
        with self.assertRaises(rewards.RewardError) as ctx:
            rewards.redeem_credits_to_ad_promo("7", 10, account_id, "rk-own")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(rewards.get_credit_balance("7"), 100)

    def test_balance_never_negative(self):
        self._grant_credits(amount=10)
        account_id = self._make_ad_account(owner=7)
        rewards.redeem_credits_to_ad_promo("7", 10, account_id, "rk-a")
        self.assertEqual(rewards.get_credit_balance("7"), 0)
        with self.assertRaises(rewards.RewardError):
            rewards.redeem_credits_to_ad_promo("7", 1, account_id, "rk-b")
        for row in self._credit_rows():
            self.assertGreaterEqual(row["balance_after"], 0)


class ReadTests(BaseCase):
    def test_list_rewards_and_ledger_paginate(self):
        for i in range(5):
            self._grant_credits(key=f"evt-p-{i}", amount=10 + i)
        page = rewards.list_rewards("7", limit=3)
        self.assertEqual(len(page["rewards"]), 3)
        self.assertTrue(page["has_more"])
        rest = rewards.list_rewards("7", limit=3,
                                    before_id=page["next_before_id"])
        self.assertEqual(len(rest["rewards"]), 2)
        self.assertFalse(rest["has_more"])
        ledger_page = rewards.list_credit_ledger("7", limit=2)
        self.assertEqual(len(ledger_page["entries"]), 2)
        self.assertTrue(ledger_page["has_more"])

    def test_list_rewards_filters(self):
        self._grant_credits(key="evt-f-1")
        self._grant_cash(key="evt-f-2", fraud_state="review")
        held = rewards.list_rewards(None, status="pending",
                                    fraud_state="review")
        self.assertEqual(len(held["rewards"]), 1)
        self.assertEqual(held["rewards"][0]["event_key"], "evt-f-2")


class ReconcileRewardsTests(BaseCase):
    def test_balance_mismatch_detected(self):
        self._grant_credits(amount=100)
        conn = db.connect()
        conn.execute(
            "UPDATE pulse_credit_ledger SET balance_after = balance_after + 5")
        conn.commit()
        conn.close()
        result = reconciliation.reconcile_rewards()
        self.assertEqual(result["balance_mismatches"], 1)
        self.assertIn((incidents.BALANCE_MISMATCH, "critical"),
                      self._incident_types())

    def test_stuck_disbursing_after_missed_bounce_detected(self):
        reward = self._grant_cash()["reward"]
        rewards.approve_cash_reward(reward["id"], "admin:1")
        self._connect_ok()
        result = rewards.disburse_cash_reward(reward["id"], "admin:1")
        # Simulate a missed notification: payout dies without the callback.
        conn = db.connect()
        conn.execute(
            "UPDATE seller_payout_requests SET status='failed' WHERE id=?",
            (int(result["payout"]["id"]),))
        conn.commit()
        conn.close()
        summary = reconciliation.reconcile_rewards()
        self.assertEqual(summary["stuck_disbursing"], 1)
        self.assertIn((incidents.RECONCILIATION_FAILURE, "warning"),
                      self._incident_types())

    def test_negative_balance_detected(self):
        conn = db.connect()
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO pulse_credit_ledger "
            "(user_id, delta, balance_after, reason, idempotency_key, created_at) "
            "VALUES ('9', -5, -5, 'bug', 'neg-1', '2026-01-01T00:00:00Z')")
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
        conn.close()
        result = reconciliation.reconcile_rewards()
        self.assertGreaterEqual(result["negative_balances"], 1)
        self.assertIn((incidents.NEGATIVE_BALANCE_DETECTED, "critical"),
                      self._incident_types())

    def test_clean_state_reports_nothing(self):
        self._grant_credits(amount=50)
        result = reconciliation.reconcile_rewards()
        self.assertEqual(result["balance_mismatches"], 0)
        self.assertEqual(result["stuck_disbursing"], 0)
        self.assertEqual(result["negative_balances"], 0)
        self.assertEqual(result["incidents"], [])

    def test_run_all_includes_rewards_check(self):
        summary = reconciliation.run_all()
        self.assertIn("rewards", summary["checks"])
        self.assertNotIn("error", summary["checks"]["rewards"])


if __name__ == "__main__":
    unittest.main()
