"""Domain B — ad wallet hardening: incident wiring, events, atomicity.

Runs the REAL modules (`services.pulse_ad_payments`, the incident engine, the
reconciler) against one shared on-disk SQLite database, because the incident
engine writes on its own `services.db` connection: an in-memory database per
connection would give the wallet and the incidents two different universes.

What is proved here, and why each matters for money:

  * a replayed Stripe credit with a DIFFERENT amount opens a critical
    DUPLICATE_CREDIT_ATTEMPT — and an exact replay stays silent (webhook
    redelivery is normal, disagreement about money is not)
  * an over-refund is clamped to the funded amount AND reported as
    REFUND_MISMATCH — Stripe's figure is recorded, the wallet stays honest
  * a reversal that lands the wallet negative records NEGATIVE_BALANCE_DETECTED
    and an `auto_pause` wallet event; the debt is visible, never hidden
  * reversals only ever touch cash (`available_balance_cents`) — promotional /
    bonus / refund credit buckets are never refunded to a card
  * auto-pauses, limit hits, and top-up prompts leave `pulse_ad_wallet_events`
    rows an owner can read back (keyset paginated, cross-account denied)
  * the spend/reserve read-decide-write sequence takes the write lock first
    (BEGIN IMMEDIATE) and sequential spends can never overdraw
  * a webhook naming an unknown funding session raises (existing behaviour)
    and now also opens ORPHAN_STRIPE_OBJECT
  * a broken incident engine can never break a wallet operation
  * reconcile_funding_sessions catches paid-but-never-credited sessions and
    day-old abandoned checkouts; reconcile_ad_wallets reports negative
    reserved/spendable states

    python3 -m unittest tests.business_os_finance.test_ad_wallet_hardening -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

_TMP_DB = os.path.join(tempfile.gettempdir(), "ad_wallet_hardening_test.db")
# Must be set BEFORE services.db is imported anywhere in this process so every
# connection — the wallet's and the incident engine's — opens the same file.
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

from services import db  # noqa: E402
from services import pulse_ad_payments  # noqa: E402
from services.pulse_ads_service import PulseAdsError  # noqa: E402
from services.business_os.payments import incidents, reconciliation  # noqa: E402

OWNER = 101
STRANGER = 202

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    business_name TEXT NOT NULL,
    business_email TEXT,
    business_type TEXT,
    status TEXT DEFAULT 'active',
    verification_status TEXT DEFAULT 'verified',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pulse_ad_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_account_id INTEGER NOT NULL,
    campaign_name TEXT NOT NULL,
    objective TEXT DEFAULT 'awareness',
    status TEXT DEFAULT 'draft',
    budget_type TEXT DEFAULT 'daily',
    daily_budget_cents INTEGER DEFAULT 0,
    lifetime_budget_cents INTEGER DEFAULT 0,
    spent_cents INTEGER DEFAULT 0,
    start_at TEXT,
    end_at TEXT,
    archived_at TEXT,
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
    updated_at TEXT,
    UNIQUE(account_id, currency)
);
CREATE TABLE IF NOT EXISTS pulse_ad_wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    campaign_id INTEGER,
    creative_id INTEGER,
    transaction_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'posted',
    idempotency_key TEXT UNIQUE,
    description TEXT,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS pulse_ad_wallet_funding_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    user_id INTEGER,
    amount_cents INTEGER,
    currency TEXT DEFAULT 'usd',
    provider TEXT DEFAULT 'stripe',
    provider_session_id TEXT,
    provider_payment_intent_id TEXT,
    provider_charge_id TEXT,
    reversed_cents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'created',
    idempotency_key TEXT,
    checkout_url TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pulse_ad_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    funding_session_id INTEGER,
    invoice_number TEXT,
    receipt_number TEXT,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    provider TEXT,
    provider_reference_hash TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS pulse_ad_refunds (
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
CREATE TABLE IF NOT EXISTS pulse_ad_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    before_json TEXT DEFAULT '{}',
    after_json TEXT DEFAULT '{}',
    ip_hash TEXT,
    user_agent_hash TEXT,
    created_at TEXT
);
"""

_CLEAR_TABLES = (
    "pulse_ad_accounts",
    "pulse_ad_campaigns",
    "pulse_ad_wallets",
    "pulse_ad_wallet_transactions",
    "pulse_ad_wallet_funding_sessions",
    "pulse_ad_receipts",
    "pulse_ad_refunds",
    "pulse_ad_audit_logs",
    "pulse_ad_invoices",
    "pulse_ad_notifications",
    "pulse_ad_wallet_events",
    "financial_incidents",
    "reconciliation_runs",
)


def _iso(days=0, hours=0):
    return (
        datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    ).replace(microsecond=0).isoformat()


class BaseCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        incidents.ensure_schema()
        reconciliation.ensure_schema()
        self.conn = db.connect()
        self.conn.executescript(_SCHEMA)
        pulse_ad_payments.ensure_schema(self.conn)
        for table in _CLEAR_TABLES:
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def tearDown(self):
        try:
            self.conn.rollback()
        except Exception:
            pass
        self.conn.close()

    # -- seed helpers ------------------------------------------------------

    def _account(self, owner=OWNER, business_type="business"):
        cur = self.conn.cursor()
        now = _iso()
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, "
            "business_type, status, created_at, updated_at) "
            "VALUES (?, 'Biz', ?, 'active', ?, ?)",
            (owner, business_type, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _campaign(self, account_id, status="active", daily=0, lifetime=0):
        cur = self.conn.cursor()
        now = _iso()
        cur.execute(
            "INSERT INTO pulse_ad_campaigns (ad_account_id, campaign_name, "
            "status, daily_budget_cents, lifetime_budget_cents, created_at, "
            "updated_at) VALUES (?, 'Camp', ?, ?, ?, ?, ?)",
            (account_id, status, daily, lifetime, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _funding_session(self, account_id, amount_cents, status="created",
                         created_at=None, pi="", sess=""):
        cur = self.conn.cursor()
        now = created_at or _iso()
        cur.execute(
            "INSERT INTO pulse_ad_wallet_funding_sessions (account_id, user_id, "
            "amount_cents, currency, status, provider_session_id, "
            "provider_payment_intent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'usd', ?, ?, ?, ?, ?)",
            (account_id, OWNER, amount_cents, status, sess, pi, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _fund(self, account_id, amount_cents, event_id="evt_1", pi="pi_1",
              sess="cs_1"):
        """Insert a funding session and credit it via the real webhook path."""
        session_id = self._funding_session(account_id, amount_cents)
        result = pulse_ad_payments.credit_wallet_from_stripe_session(
            self.conn,
            event_id,
            {
                "id": sess,
                "payment_intent": pi,
                "amount_total": amount_cents,
                "currency": "usd",
                "metadata": {
                    "purpose": "pulse_ad_wallet_funding",
                    "funding_session_id": session_id,
                    "ad_account_id": account_id,
                },
            },
        )
        return session_id, result

    def _spend(self, campaign_id, cents, idem):
        return pulse_ad_payments.record_spend_event(
            self.conn, campaign_id, None, "feed_inline",
            amount_cents=cents, idempotency_key=idem,
        )

    def _wallet(self, account_id):
        row = self.conn.execute(
            "SELECT * FROM pulse_ad_wallets WHERE account_id=?", (account_id,)
        ).fetchone()
        return dict(row) if row else {}

    def _incidents(self, incident_type=None):
        sql = "SELECT * FROM financial_incidents"
        params = ()
        if incident_type:
            sql += " WHERE incident_type=?"
            params = (incident_type,)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id", params)]

    def _events(self, account_id, event_type=None):
        sql = "SELECT * FROM pulse_ad_wallet_events WHERE account_id=?"
        params = [account_id]
        if event_type:
            sql += " AND event_type=?"
            params.append(event_type)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id", params)]


# ---------------------------------------------------------------------------
# Duplicate credits
# ---------------------------------------------------------------------------

class DuplicateCreditTests(BaseCase):
    def test_replay_with_different_amount_opens_critical_incident(self):
        account_id, _ = self._fund_and_capture()
        # Same event id, same funding session, DIFFERENT amount.
        result = pulse_ad_payments.credit_wallet_from_stripe_session(
            self.conn,
            "evt_1",
            {
                "id": "cs_1",
                "payment_intent": "pi_1",
                "amount_total": 5100,
                "currency": "usd",
                "metadata": {
                    "purpose": "pulse_ad_wallet_funding",
                    "funding_session_id": self.session_id,
                    "ad_account_id": account_id,
                },
            },
        )
        # Still deduped — the first verified delivery is the truth of record.
        self.assertTrue(result.get("deduped"))
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 5000)
        rows = self._incidents(incidents.DUPLICATE_CREDIT_ATTEMPT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "critical")
        self.assertEqual(rows[0]["domain"], "ad_wallet")
        self.assertIn("5100", rows[0]["summary"])

    def test_exact_replay_stays_silent(self):
        account_id, _ = self._fund_and_capture()
        result = pulse_ad_payments.credit_wallet_from_stripe_session(
            self.conn,
            "evt_1",
            {
                "id": "cs_1",
                "payment_intent": "pi_1",
                "amount_total": 5000,
                "currency": "usd",
                "metadata": {
                    "purpose": "pulse_ad_wallet_funding",
                    "funding_session_id": self.session_id,
                    "ad_account_id": account_id,
                },
            },
        )
        self.assertTrue(result.get("deduped"))
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 5000)
        self.assertEqual(self._incidents(), [])

    def _fund_and_capture(self):
        account_id = self._account()
        self.session_id, result = self._fund(account_id, 5000)
        self.assertTrue(result["ok"])
        return account_id, result


# ---------------------------------------------------------------------------
# Reversals: over-refunds, negative balances, cash-only
# ---------------------------------------------------------------------------

class ReversalTests(BaseCase):
    def test_over_refund_is_clamped_and_reported(self):
        account_id = self._account()
        self._fund(account_id, 5000)
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn,
            "evt_rf_1",
            {"object": "charge", "id": "ch_1", "payment_intent": "pi_1",
             "amount_refunded": 8000},
            "charge.refunded",
        )
        self.assertTrue(result["ok"])
        # Clamped: only what was funded came back out — no silent negative.
        self.assertEqual(result["reversed_cents"], 5000)
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 0)
        rows = self._incidents(incidents.REFUND_MISMATCH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "critical")
        self.assertIn("8000", rows[0]["summary"])

    def test_reversal_after_spend_reports_debt_and_pauses(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        self._fund(account_id, 5000)
        self.assertTrue(self._spend(campaign_id, 3000, "sp-1")["ok"])
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn,
            "evt_rf_2",
            {"object": "charge", "id": "ch_2", "payment_intent": "pi_1",
             "amount_refunded": 5000},
            "charge.refunded",
        )
        self.assertTrue(result["ok"])
        # The negative IS the information: the advertiser owes 3000.
        self.assertEqual(result["available_balance_cents"], -3000)
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], -3000)
        rows = self._incidents(incidents.NEGATIVE_BALANCE_DETECTED)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "warning")
        self.assertIn("3000", rows[0]["summary"])
        # The campaign was paused and the pause is auditable.
        self.assertEqual(result["campaigns_paused"], 1)
        events = self._events(account_id, "auto_pause")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "wallet_funding_reversed")

    def test_credits_are_never_refunded_to_a_card(self):
        account_id = self._account()
        self._fund(account_id, 1000)
        # Grant credits the advertiser did not pay Stripe for.
        self.conn.execute(
            "UPDATE pulse_ad_wallets SET promotional_credits_cents=2000, "
            "bonus_credits_cents=1500, refund_credits_cents=500 "
            "WHERE account_id=?",
            (account_id,),
        )
        self.conn.commit()
        result = pulse_ad_payments.reverse_wallet_funding(
            self.conn,
            "evt_rf_3",
            {"object": "charge", "id": "ch_3", "payment_intent": "pi_1",
             "amount_refunded": 6000},
            "charge.refunded",
        )
        self.assertTrue(result["ok"])
        # Only the 1000 of CASH ever funded is reversed; every credit bucket
        # is untouched. Reversals never touch money the card never paid.
        self.assertEqual(result["reversed_cents"], 1000)
        wallet = self._wallet(account_id)
        self.assertEqual(wallet["available_balance_cents"], 0)
        self.assertEqual(wallet["promotional_credits_cents"], 2000)
        self.assertEqual(wallet["bonus_credits_cents"], 1500)
        self.assertEqual(wallet["refund_credits_cents"], 500)


# ---------------------------------------------------------------------------
# Wallet lifecycle events
# ---------------------------------------------------------------------------

class WalletEventTests(BaseCase):
    def test_insufficient_funds_pause_writes_auto_pause_event(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        self._fund(account_id, 100)
        result = self._spend(campaign_id, 500, "sp-poor-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "wallet_insufficient")
        events = self._events(account_id, "auto_pause")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "insufficient_funds")
        self.assertEqual(events[0]["campaign_id"], campaign_id)
        self.assertIn('"attempted_amount_cents":500', events[0]["details_json"])

    def test_daily_limit_hit_writes_limit_hit_event(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        self._fund(account_id, 5000)
        self.conn.execute(
            "UPDATE pulse_ad_wallets SET daily_limit_cents=100 WHERE account_id=?",
            (account_id,),
        )
        self.conn.commit()
        self.assertTrue(self._spend(campaign_id, 60, "sp-lim-1")["ok"])
        result = self._spend(campaign_id, 60, "sp-lim-2")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "daily_limit_reached")
        events = self._events(account_id, "limit_hit")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "daily_limit_reached")

    def test_low_balance_writes_topup_prompt_event(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        self._fund(account_id, 1000)
        pulse_ad_payments.set_auto_topup(
            self.conn, OWNER, account_id,
            {"enabled": True, "threshold_cents": 5000, "amount_cents": 1000},
        )
        self.assertTrue(self._spend(campaign_id, 100, "sp-low-1")["ok"])
        events = self._events(account_id, "topup_prompt")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "low_balance")
        self.assertIn('"threshold_cents":5000', events[0]["details_json"])

    def test_list_wallet_events_paginates_and_denies_strangers(self):
        account_id = self._account()
        for index in range(3):
            pulse_ad_payments._wallet_event(
                self.conn, account_id, "auto_pause", f"r{index}",
                details={"n": index},
            )
        self.conn.commit()
        page1 = pulse_ad_payments.list_wallet_events(
            self.conn, OWNER, account_id, limit=2
        )
        self.assertEqual(len(page1["events"]), 2)
        self.assertIsNotNone(page1["next_before_id"])
        self.assertEqual(page1["events"][0]["details"], {"n": 2})
        page2 = pulse_ad_payments.list_wallet_events(
            self.conn, OWNER, account_id, limit=2,
            before_id=page1["next_before_id"],
        )
        self.assertEqual(len(page2["events"]), 1)
        self.assertEqual(page2["events"][0]["details"], {"n": 0})
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.list_wallet_events(self.conn, STRANGER, account_id)


# ---------------------------------------------------------------------------
# Atomic budget enforcement
# ---------------------------------------------------------------------------

class SpendAtomicityTests(BaseCase):
    def test_begin_immediate_serializes_two_connections(self):
        conn1 = db.connect()
        conn2 = db.connect()
        try:
            conn2.execute("PRAGMA busy_timeout=100")
            self.assertTrue(pulse_ad_payments._begin_immediate(conn1))
            # Same connection, already in the transaction: not started again.
            self.assertFalse(pulse_ad_payments._begin_immediate(conn1))
            # Second writer cannot enter the read-decide-write window.
            self.assertFalse(pulse_ad_payments._begin_immediate(conn2))
            conn1.rollback()
            # Lock released — the second writer proceeds.
            self.assertTrue(pulse_ad_payments._begin_immediate(conn2))
            conn2.rollback()
        finally:
            conn1.close()
            conn2.close()

    def test_sequential_spends_never_overdraw(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        self._fund(account_id, 200)
        results = [self._spend(campaign_id, 100, f"sp-race-{i}") for i in range(5)]
        succeeded = [r for r in results if r.get("ok") and not r.get("skipped")]
        refused = [r for r in results if not r.get("ok")]
        self.assertEqual(len(succeeded), 2)
        self.assertEqual(len(refused), 3)
        wallet = self._wallet(account_id)
        self.assertEqual(wallet["available_balance_cents"], 0)
        self.assertEqual(wallet["lifetime_spent_cents"], 200)

    def test_replayed_spend_dedupes_and_releases_the_lock(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        self._fund(account_id, 1000)
        self.assertTrue(self._spend(campaign_id, 100, "sp-dup")["ok"])
        replay = self._spend(campaign_id, 100, "sp-dup")
        self.assertTrue(replay.get("deduped"))
        # Lock was released on the dedup path: another writer can enter.
        other = db.connect()
        try:
            other.execute("PRAGMA busy_timeout=100")
            self.assertTrue(pulse_ad_payments._begin_immediate(other))
            other.rollback()
        finally:
            other.close()
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 900)


# ---------------------------------------------------------------------------
# Orphans and resilience
# ---------------------------------------------------------------------------

class OrphanAndResilienceTests(BaseCase):
    def test_unknown_funding_session_raises_and_opens_incident(self):
        account_id = self._account()
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.credit_wallet_from_stripe_session(
                self.conn,
                "evt_orphan",
                {
                    "id": "cs_x",
                    "amount_total": 5000,
                    "currency": "usd",
                    "metadata": {
                        "purpose": "pulse_ad_wallet_funding",
                        "funding_session_id": 999,
                        "ad_account_id": account_id,
                    },
                },
            )
        rows = self._incidents(incidents.ORPHAN_STRIPE_OBJECT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "warning")
        self.assertIn("999", rows[0]["summary"])

    def test_wallet_operations_survive_a_broken_incident_engine(self):
        account_id = self._account()
        self._fund(account_id, 5000)
        with mock.patch(
            "services.business_os.payments.incidents.open_incident",
            side_effect=RuntimeError("incident engine down"),
        ):
            # Over-refund: the money path must still clamp, debit, and commit.
            result = pulse_ad_payments.reverse_wallet_funding(
                self.conn,
                "evt_broken",
                {"object": "charge", "id": "ch_b", "payment_intent": "pi_1",
                 "amount_refunded": 9000},
                "charge.refunded",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["reversed_cents"], 5000)
            # Orphan webhook: still the same PulseAdsError, not a RuntimeError.
            with self.assertRaises(PulseAdsError):
                pulse_ad_payments.credit_wallet_from_stripe_session(
                    self.conn,
                    "evt_orphan_b",
                    {
                        "id": "cs_b",
                        "amount_total": 100,
                        "currency": "usd",
                        "metadata": {
                            "purpose": "pulse_ad_wallet_funding",
                            "funding_session_id": 998,
                            "ad_account_id": account_id,
                        },
                    },
                )
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 0)
        self.assertEqual(self._incidents(), [])


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class FundingSessionReconcileTests(BaseCase):
    def test_credited_session_without_funding_tx_is_a_missing_credit(self):
        account_id = self._account()
        self._funding_session(account_id, 4200, status="credited")
        result = reconciliation.reconcile_funding_sessions()
        self.assertEqual(result["missing_credits"], 1)
        rows = self._incidents(incidents.BALANCE_MISMATCH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "critical")
        self.assertEqual(rows[0]["domain"], "ad_wallet")
        self.assertIn("4200", rows[0]["summary"])

    def test_properly_credited_session_is_clean(self):
        account_id = self._account()
        self._fund(account_id, 5000)
        result = reconciliation.reconcile_funding_sessions()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["missing_credits"], 0)
        self.assertEqual(result["stuck_pending"], 0)
        self.assertEqual(self._incidents(), [])

    def test_day_old_pending_checkout_is_flagged_as_info(self):
        account_id = self._account()
        self._funding_session(
            account_id, 1500, status="created", created_at=_iso(days=2)
        )
        # A fresh pending session is NOT flagged.
        self._funding_session(account_id, 1500, status="created")
        result = reconciliation.reconcile_funding_sessions()
        self.assertEqual(result["stuck_pending"], 1)
        rows = self._incidents(incidents.ORPHAN_LOCAL_RECORD)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "info")

    def test_run_all_includes_the_funding_session_check(self):
        summary = reconciliation.run_all()
        self.assertIn("funding_sessions", summary["checks"])
        self.assertNotIn("error", summary["checks"]["funding_sessions"])


class NegativeStateReconcileTests(BaseCase):
    def _seed_wallet(self, account_id, *, available, spent, reserved, funded, txs):
        self.conn.execute(
            "INSERT INTO pulse_ad_wallets (account_id, available_balance_cents, "
            "lifetime_spent_cents, reserved_budget_cents, lifetime_funded_cents, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, available, spent, reserved, funded, _iso()),
        )
        for index, (tx_type, cents) in enumerate(txs):
            self.conn.execute(
                "INSERT INTO pulse_ad_wallet_transactions (account_id, "
                "transaction_type, amount_cents, status, idempotency_key, "
                "created_at) VALUES (?, ?, ?, 'posted', ?, ?)",
                (account_id, tx_type, cents, f"nw-{account_id}-{index}", _iso()),
            )
        self.conn.commit()

    def test_negative_spendable_debt_state_is_recorded_not_hidden(self):
        # Invariant-consistent debt: fund 5000, spend 3000, full 5000 reversal.
        self._seed_wallet(
            7, available=-3000, spent=3000, reserved=0, funded=0,
            txs=[("funding", 5000), ("spend", 3000), ("refund", 5000)],
        )
        result = reconciliation.reconcile_ad_wallets()
        # The invariant HOLDS — this is designed debt, not drift.
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(result["negative_balances"], 1)
        rows = self._incidents(incidents.NEGATIVE_BALANCE_DETECTED)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "warning")
        self.assertIn("3000", rows[0]["summary"])

    def test_negative_reserved_budget_is_critical(self):
        self._seed_wallet(8, available=0, spent=0, reserved=-50, funded=0, txs=[])
        result = reconciliation.reconcile_ad_wallets()
        self.assertEqual(result["negative_balances"], 1)
        rows = [
            r for r in self._incidents(incidents.NEGATIVE_BALANCE_DETECTED)
            if "reserved" in r["summary"]
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "critical")


# ---------------------------------------------------------------------------
# Route wiring (structural — bot.py is unimportable here)
# ---------------------------------------------------------------------------

class WalletEventsRouteTests(unittest.TestCase):
    def test_events_route_is_owner_gated_and_calls_list_wallet_events(self):
        bot_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "bot.py")
        )
        with open(bot_path, encoding="utf-8") as handle:
            src = handle.read()
        marker = "def api_pulse_ads_wallet_events_flat("
        self.assertIn(marker, src)
        block = src[src.index(marker):]
        block = block[: block.index("\n@webhook_app.route")]
        decorator_zone = src[: src.index(marker)].rsplit("\n@webhook_app.route", 1)[-1]
        self.assertIn('"/api/pulse/ads/wallet/events"', decorator_zone)
        self.assertIn('methods=["GET"]', decorator_zone)
        self.assertIn("pulse_ads_api_user_required()", block)
        self.assertIn("if denied", block)
        self.assertIn("pulse_ad_payments.list_wallet_events", block)
        self.assertIn("pulse_ads_error_response", block)


if __name__ == "__main__":
    unittest.main()
