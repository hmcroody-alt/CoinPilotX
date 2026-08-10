"""Reconciliation engine — detect-and-report, never repair.

Runs hermetically against a temporary SQLite DB (set via DATABASE_URL before
importing services.db), mirroring tests/business_os/test_ledger_and_webhook_inbox.py.

    python3 -m unittest tests.business_os_finance.test_reconciliation -v
"""

import os
import tempfile
import unittest

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fin_rec_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ledger  # noqa: E402
from services.business_os.payments import incidents  # noqa: E402
from services.business_os.payments import reconciliation  # noqa: E402
from services.business_os.payments import webhook_inbox  # noqa: E402

# Mirrors the wallet tables in tests/pulse_ads/test_reports_insights_wallet.py.
_WALLET_SCHEMA = """
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
"""

_CLEAR_TABLES = (
    "financial_incidents",
    "reconciliation_runs",
    "provider_webhook_events",
    "ledger_entries",
    "ledger_transactions",
    "ledger_balances",
    "pulse_ad_wallet_transactions",
    "pulse_ad_wallets",
)


class BaseCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        ledger.ensure_schema()
        webhook_inbox.ensure_schema()
        incidents.ensure_schema()
        reconciliation.ensure_schema()
        conn = db.connect()
        conn.executescript(_WALLET_SCHEMA)
        for table in _CLEAR_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()

    # -- helpers ---------------------------------------------------------

    def _incident_rows(self, incident_type=None):
        conn = db.connect()
        if incident_type:
            cur = conn.execute(
                "SELECT * FROM financial_incidents WHERE incident_type = ?",
                (incident_type,),
            )
        else:
            cur = conn.execute("SELECT * FROM financial_incidents")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _post(self, key, amount, source="platform:stripe", destination="user:1"):
        return ledger.post_entry(
            idempotency_key=key, actor="test", amount_cents=amount,
            currency="usd", entry_type="test_credit",
            source=source, destination=destination,
        )

    def _seed_wallet(self, account_id, *, available, spent, reserved, txs,
                     promo=0, bonus=0, refund_credits=0, funded=None):
        # lifetime_funded mirrors the writer: sum of funding, debited (and
        # clamped at zero) by reversals. Callers may override for tamper tests.
        if funded is None:
            funding_total = sum(c for t, c in txs if t == "funding")
            reversal_total = sum(c for t, c in txs if t in ("refund", "chargeback"))
            funded = max(0, funding_total - reversal_total)
        conn = db.connect()
        conn.execute(
            "INSERT INTO pulse_ad_wallets (account_id, available_balance_cents, "
            "lifetime_spent_cents, reserved_budget_cents, promotional_credits_cents, "
            "bonus_credits_cents, refund_credits_cents, lifetime_funded_cents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
            (account_id, available, spent, reserved, promo, bonus, refund_credits, funded),
        )
        for index, (tx_type, cents) in enumerate(txs):
            conn.execute(
                "INSERT INTO pulse_ad_wallet_transactions "
                "(account_id, transaction_type, amount_cents, status, "
                "idempotency_key, created_at) "
                "VALUES (?, ?, ?, 'posted', ?, '2026-01-01T00:00:00Z')",
                (account_id, tx_type, cents, f"w{account_id}-{index}"),
            )
        conn.commit()
        conn.close()


class LedgerReconcileTests(BaseCase):
    def test_clean_ledger_yields_no_incidents(self):
        self._post("clean-1", 500)
        result = reconciliation.reconcile_ledger_balances()
        self.assertEqual(result["mismatches"], 0)
        self.assertGreaterEqual(result["checked"], 2)
        self.assertEqual(self._incident_rows(), [])

    def test_tampered_cache_reported_and_not_mutated(self):
        self._post("tamper-1", 500)
        conn = db.connect()
        conn.execute(
            "UPDATE ledger_balances SET balance_cents = 999 WHERE account = 'user:1'"
        )
        conn.commit()
        conn.close()

        result = reconciliation.reconcile_ledger_balances()
        self.assertEqual(result["mismatches"], 1)
        rows = self._incident_rows(incidents.BALANCE_MISMATCH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domain"], "ledger")
        self.assertEqual(rows[0]["severity"], "critical")  # drift 499 >= 100

        # The tampered cache is untouched — detect, never repair.
        conn = db.connect()
        cached = conn.execute(
            "SELECT balance_cents FROM ledger_balances WHERE account = 'user:1'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(int(cached), 999)

        # Re-running the sweep does not spam: same key, one row.
        again = reconciliation.reconcile_ledger_balances()
        self.assertEqual(again["mismatches"], 1)
        self.assertEqual(len(self._incident_rows(incidents.BALANCE_MISMATCH)), 1)


class AdWalletReconcileTests(BaseCase):
    def test_clean_wallet_passes_invariant(self):
        # available = 1000 - 200 - 100 = 700; spent = 200; reserved = 300-200 = 100
        self._seed_wallet(
            1, available=700, spent=200, reserved=100,
            txs=[("funding", 1000), ("reserve", 300), ("spend", 200), ("refund", 100)],
        )
        result = reconciliation.reconcile_ad_wallets()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(self._incident_rows(), [])

    def test_tampered_wallet_flagged_and_not_mutated(self):
        self._seed_wallet(
            2, available=9999, spent=200, reserved=100,  # available should be 700
            txs=[("funding", 1000), ("reserve", 300), ("spend", 200), ("refund", 100)],
        )
        result = reconciliation.reconcile_ad_wallets()
        self.assertEqual(result["mismatches"], 1)
        rows = self._incident_rows(incidents.BALANCE_MISMATCH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domain"], "ad_wallet")
        self.assertEqual(rows[0]["severity"], "critical")  # drift 9299

        conn = db.connect()
        available = conn.execute(
            "SELECT available_balance_cents FROM pulse_ad_wallets WHERE account_id = 2"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(int(available), 9999)  # never silently fixed

    def test_credit_bucket_wallets_are_skipped_not_guessed(self):
        # A 'credit' transaction puts this wallet outside the proven invariant.
        self._seed_wallet(
            3, available=123, spent=0, reserved=0,
            txs=[("credit", 500)],
        )
        result = reconciliation.reconcile_ad_wallets()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(self._incident_rows(), [])


class WebhookReconcileTests(BaseCase):
    def _exhaust_event(self, event_id="evt_dead_1"):
        webhook_inbox.enqueue_event(
            provider="stripe", provider_event_id=event_id,
            payload={"id": event_id}, event_type="charge.succeeded",
        )

        def boom(_payload):
            raise RuntimeError("handler exploded")

        for _ in range(webhook_inbox.DEFAULT_MAX_RETRIES):
            webhook_inbox.process_event("stripe", event_id, boom)
        return event_id

    def test_dlq_exhaustion_escalates_once_and_sweep_dedupes(self):
        event_id = self._exhaust_event()
        # The inbox itself escalated on the final retry.
        rows = self._incident_rows(incidents.WEBHOOK_DLQ_EXHAUSTED)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "critical")
        self.assertEqual(rows[0]["stripe_ref"], event_id)

        # The sweep sees the same dead letter and lands on the same row.
        result = reconciliation.reconcile_webhook_inbox()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(len(self._incident_rows(incidents.WEBHOOK_DLQ_EXHAUSTED)), 1)

    def test_stuck_processing_row_reported(self):
        conn = db.connect()
        conn.execute(
            "INSERT INTO provider_webhook_events "
            "(provider, provider_event_id, payload_json, status, received_at) "
            "VALUES ('stripe', 'evt_stuck_1', '{}', 'processing', "
            "'2000-01-01T00:00:00.000000Z')",
        )
        conn.commit()
        conn.close()
        result = reconciliation.reconcile_webhook_inbox()
        self.assertEqual(result["stuck_processing"], 1)
        rows = self._incident_rows(incidents.RECONCILIATION_FAILURE)
        self.assertEqual(len(rows), 1)
        self.assertIn("evt_stuck_1", rows[0]["summary"])

    def test_snapshot_flags_only_events_missing_from_inbox(self):
        webhook_inbox.enqueue_event(
            provider="stripe", provider_event_id="evt_known",
            payload={"id": "evt_known"},
        )
        result = reconciliation.reconcile_stripe_snapshot(
            [{"id": "evt_known"}, {"id": "evt_missing", "type": "charge.refunded"}]
        )
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["missing"], 1)
        rows = self._incident_rows(incidents.MISSING_WEBHOOK_EVENT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stripe_ref"], "evt_missing")
        self.assertEqual(rows[0]["severity"], "critical")


class SuspenseTests(BaseCase):
    def test_nonzero_suspense_balance_opens_incident(self):
        self._post("susp-1", 250, source="external:stripe",
                   destination=reconciliation.SUSPENSE_ACCOUNT)
        result = reconciliation.reconcile_suspense()
        self.assertEqual(result["accounts_with_held_funds"], 1)
        rows = self._incident_rows(incidents.SUSPENSE_FUNDS_HELD)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "critical")  # 250 >= 100

    def test_zero_suspense_is_quiet(self):
        result = reconciliation.reconcile_suspense()
        self.assertEqual(result["accounts_with_held_funds"], 0)
        self.assertEqual(self._incident_rows(), [])


class RunAllTests(BaseCase):
    def test_run_all_persists_summary_and_last_run_reads_it(self):
        self._post("runall-1", 500)
        summary = reconciliation.run_all()
        self.assertEqual(
            sorted(summary["checks"]),
            ["ad_wallets", "funding_sessions", "ledger_balances", "rewards",
             "seller_payouts", "suspense", "webhook_inbox"],
        )
        self.assertEqual(summary["check_errors"], 0)
        self.assertEqual(summary["incidents_opened_or_refreshed"], 0)
        self.assertIsNotNone(summary["started_at"])
        self.assertIsNotNone(summary["finished_at"])

        last = reconciliation.last_run()
        self.assertIsNotNone(last)
        self.assertEqual(last["started_at"], summary["started_at"])
        self.assertEqual(
            last["summary"]["incidents_opened_or_refreshed"], 0
        )

    def test_run_all_counts_incidents_from_failing_checks(self):
        self._post("runall-2", 500)
        conn = db.connect()
        conn.execute(
            "UPDATE ledger_balances SET balance_cents = 1 WHERE account = 'user:1'"
        )
        conn.commit()
        conn.close()
        summary = reconciliation.run_all()
        self.assertEqual(summary["checks"]["ledger_balances"]["mismatches"], 1)
        self.assertEqual(summary["incidents_opened_or_refreshed"], 1)

    def test_last_run_none_before_any_run(self):
        self.assertIsNone(reconciliation.last_run())


if __name__ == "__main__":
    unittest.main()
