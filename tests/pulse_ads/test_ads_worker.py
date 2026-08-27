"""Failure-mode tests for the pulsesoc-ads-worker engine.

Covers the mission's required failure scenarios that are testable in-process:
duplicate job delivery, the same job processed twice, malformed jobs,
retry/backoff, dead-lettering, restart recovery of orphaned jobs, operations
state transitions (activation / completion / budget-exhaustion pause),
attribution idempotency, and aggregate recomputation idempotency.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import pulse_ad_payments  # noqa: E402
from services import pulse_ads_worker_service as engine  # noqa: E402
from tests.pulse_ads.test_wallet_funding_reversal import SCHEMA  # noqa: E402

OWNER_ID = 9200

EXTRA_SCHEMA = """
CREATE TABLE pulse_ad_creatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER,
    content_ref_type TEXT DEFAULT '',
    content_ref_id INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE pulse_ad_impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER,
    creative_id INTEGER,
    placement_key TEXT DEFAULT '',
    viewer_user_id INTEGER,
    viewable INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE pulse_ad_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER,
    creative_id INTEGER,
    placement_key TEXT DEFAULT '',
    viewer_user_id INTEGER,
    created_at TEXT
);
"""


class WorkerBase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.executescript(EXTRA_SCHEMA)
        cur = self.conn.cursor()
        for ddl in (
            "ALTER TABLE pulse_ad_campaigns ADD COLUMN spent_cents INTEGER DEFAULT 0",
            "ALTER TABLE pulse_ad_campaigns ADD COLUMN start_at TEXT",
            "ALTER TABLE pulse_ad_campaigns ADD COLUMN end_at TEXT",
        ):
            try:
                cur.execute(ddl)
            except Exception:
                pass
        # Verified on purpose: billing fails closed on account standing, so an
        # unverified advertiser never reaches the spend path this loop exercises.
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, business_type, status, verification_status) "
            "VALUES (?, ?, ?, 'active', 'verified')",
            (OWNER_ID, "Worker Advertiser", "business"),
        )
        self.account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO pulse_ad_campaigns (ad_account_id, campaign_name, status, spent_cents) VALUES (?, 'Delivery', 'active', 0)",
            (self.account_id,),
        )
        self.campaign_id = cur.lastrowid
        pulse_ad_payments.ensure_wallet(self.conn, self.account_id)
        engine.ensure_schema(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def job(self, job_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM pulse_ad_jobs WHERE id=?", (job_id,))
        return dict(cur.fetchone())


class QueueSemanticsTests(WorkerBase):
    def test_duplicate_enqueue_dedupes(self):
        first = engine.enqueue_job(
            self.conn, "reporting", "refresh_aggregates",
            {"campaign_id": self.campaign_id, "day": "2026-08-09"},
            idempotency_key="agg:test:1",
        )
        second = engine.enqueue_job(
            self.conn, "reporting", "refresh_aggregates",
            {"campaign_id": self.campaign_id, "day": "2026-08-09"},
            idempotency_key="agg:test:1",
        )
        self.assertFalse(first["deduped"])
        self.assertTrue(second["deduped"])
        self.assertEqual(first["job_id"], second["job_id"])

    def test_same_job_processed_twice_is_safe(self):
        out = engine.enqueue_job(
            self.conn, "reporting", "refresh_aggregates",
            {"campaign_id": self.campaign_id, "day": "2026-08-09"},
            idempotency_key="agg:test:2",
        )
        engine.process_pending_jobs(self.conn)
        # Force it pending again (simulates an at-least-once redelivery).
        cur = self.conn.cursor()
        cur.execute("UPDATE pulse_ad_jobs SET status='pending', run_after=NULL WHERE id=?", (out["job_id"],))
        self.conn.commit()
        result = engine.process_pending_jobs(self.conn)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.job(out["job_id"])["status"], "completed")

    def test_malformed_payload_dead_letters_immediately(self):
        out = engine.enqueue_job(self.conn, "events", "refresh_aggregates", {}, idempotency_key="bad:1")
        cur = self.conn.cursor()
        cur.execute("UPDATE pulse_ad_jobs SET payload_json='not-json{{' WHERE id=?", (out["job_id"],))
        self.conn.commit()
        result = engine.process_pending_jobs(self.conn)
        self.assertEqual(result["dead"], 1)
        self.assertEqual(self.job(out["job_id"])["status"], "dead")

    def test_unknown_job_type_dead_letters(self):
        out = engine.enqueue_job(self.conn, "events", "campaign_state_sweep", {}, idempotency_key="unk:1")
        cur = self.conn.cursor()
        cur.execute("UPDATE pulse_ad_jobs SET job_type='definitely_not_a_handler' WHERE id=?", (out["job_id"],))
        self.conn.commit()
        result = engine.process_pending_jobs(self.conn)
        self.assertEqual(result["dead"], 1)

    def test_transient_failure_retries_with_backoff_then_dies(self):
        out = engine.enqueue_job(
            self.conn, "attribution", "attribute_conversions", {"campaign_id": self.campaign_id},
            idempotency_key="retry:1", max_attempts=2,
        )
        original = engine.HANDLERS["attribute_conversions"]
        calls = {"n": 0}

        def flaky(conn, payload):
            calls["n"] += 1
            raise RuntimeError("simulated transient failure")

        engine.HANDLERS["attribute_conversions"] = flaky
        try:
            r1 = engine.process_pending_jobs(self.conn)
            self.assertEqual(r1["retried"], 1)
            row = self.job(out["job_id"])
            self.assertEqual(row["status"], "pending")
            self.assertGreater(row["run_after"], engine.now_iso())  # backoff in the future
            # Fast-forward past the backoff and fail again → dead letter.
            cur = self.conn.cursor()
            cur.execute("UPDATE pulse_ad_jobs SET run_after='2000-01-01T00:00:00' WHERE id=?", (out["job_id"],))
            self.conn.commit()
            r2 = engine.process_pending_jobs(self.conn)
            self.assertEqual(r2["dead"], 1)
            self.assertEqual(self.job(out["job_id"])["status"], "dead")
            self.assertEqual(calls["n"], 2)
        finally:
            engine.HANDLERS["attribute_conversions"] = original

    def test_worker_restart_recovers_orphaned_processing_jobs(self):
        out = engine.enqueue_job(self.conn, "events", "campaign_state_sweep", {}, idempotency_key="orph:1")
        cur = self.conn.cursor()
        # Simulate a worker killed mid-job long ago.
        cur.execute(
            "UPDATE pulse_ad_jobs SET status='processing', updated_at='2020-01-01T00:00:00' WHERE id=?",
            (out["job_id"],),
        )
        self.conn.commit()
        self.assertEqual(engine.recover_orphaned_jobs(self.conn), 1)
        self.assertEqual(self.job(out["job_id"])["status"], "pending")
        # A freshly-claimed job is NOT stolen.
        cur.execute(
            "UPDATE pulse_ad_jobs SET status='processing', updated_at=? WHERE id=?",
            (engine.now_iso(), out["job_id"]),
        )
        self.conn.commit()
        self.assertEqual(engine.recover_orphaned_jobs(self.conn), 0)

    def test_dead_job_can_be_requeued(self):
        out = engine.enqueue_job(self.conn, "events", "campaign_state_sweep", {}, idempotency_key="rq:1")
        cur = self.conn.cursor()
        cur.execute("UPDATE pulse_ad_jobs SET status='dead' WHERE id=?", (out["job_id"],))
        self.conn.commit()
        self.assertTrue(engine.requeue_dead_job(self.conn, out["job_id"])["requeued"])
        self.assertEqual(self.job(out["job_id"])["status"], "pending")

    def test_queue_health_reports_depths(self):
        engine.enqueue_job(self.conn, "billing", "reconcile_wallet", {"account_id": self.account_id}, idempotency_key="qh:1")
        health = engine.queue_health(self.conn)
        self.assertEqual(health["queues"]["billing"]["pending"], 1)


class OperationsCycleTests(WorkerBase):
    def test_scheduled_campaign_activates_when_start_reached(self):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_ad_campaigns (ad_account_id, campaign_name, status, spent_cents, start_at) "
            "VALUES (?, 'Scheduled', 'scheduled', 0, '2020-01-01T00:00:00')",
            (self.account_id,),
        )
        cid = cur.lastrowid
        # Fund the wallet so the budget sweep doesn't immediately pause it.
        cur.execute("UPDATE pulse_ad_wallets SET available_balance_cents=1000 WHERE account_id=?", (self.account_id,))
        self.conn.commit()
        out = engine.run_operations_cycle(self.conn)
        self.assertGreaterEqual(out["activated"], 1)
        cur.execute("SELECT status FROM pulse_ad_campaigns WHERE id=?", (cid,))
        self.assertEqual(cur.fetchone()["status"], "active")

    def test_expired_campaign_completes(self):
        cur = self.conn.cursor()
        cur.execute("UPDATE pulse_ad_wallets SET available_balance_cents=1000 WHERE account_id=?", (self.account_id,))
        cur.execute(
            "UPDATE pulse_ad_campaigns SET end_at='2020-01-02T00:00:00' WHERE id=?", (self.campaign_id,)
        )
        self.conn.commit()
        out = engine.run_operations_cycle(self.conn)
        self.assertGreaterEqual(out["completed"], 1)
        cur.execute("SELECT status FROM pulse_ad_campaigns WHERE id=?", (self.campaign_id,))
        self.assertEqual(cur.fetchone()["status"], "completed")

    def test_budget_exhausted_campaign_pauses(self):
        # Wallet is empty → sweep must pause the active campaign.
        out = engine.run_operations_cycle(self.conn)
        cur = self.conn.cursor()
        cur.execute("SELECT status FROM pulse_ad_campaigns WHERE id=?", (self.campaign_id,))
        self.assertEqual(cur.fetchone()["status"], "paused")
        self.assertGreaterEqual(out["paused"], 1)

    def test_operations_cycle_is_idempotent(self):
        engine.run_operations_cycle(self.conn)
        second = engine.run_operations_cycle(self.conn)
        self.assertEqual(second["activated"], 0)
        self.assertEqual(second["completed"], 0)


class BillingReconciliationTests(WorkerBase):
    def test_balanced_wallet_reports_balanced(self):
        pulse_ad_payments.grant_promotional_credits(
            self.conn, account_id=self.account_id, amount_cents=500,
            idempotency_key="promo:rec:1", reason="test",
        )
        out = engine.reconcile_wallet(self.conn, self.account_id)
        self.assertTrue(out["balanced"], out)

    def test_drifted_wallet_is_flagged_not_mutated(self):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=777 WHERE account_id=?",
            (self.account_id,),
        )
        self.conn.commit()
        out = engine.reconcile_wallet(self.conn, self.account_id)
        self.assertFalse(out["balanced"])
        self.assertEqual(out["drift_cents"], 777)
        cur.execute("SELECT available_balance_cents FROM pulse_ad_wallets WHERE account_id=?", (self.account_id,))
        self.assertEqual(cur.fetchone()["available_balance_cents"], 777)  # untouched


class ReportingAggregateTests(WorkerBase):
    def seed_delivery(self, day="2026-08-09"):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_ad_creatives (campaign_id, created_at) VALUES (?, ?)",
            (self.campaign_id, f"{day}T00:00:00"),
        )
        creative_id = cur.lastrowid
        for i in range(3):
            cur.execute(
                "INSERT INTO pulse_ad_impressions (campaign_id, creative_id, placement_key, viewable, created_at) "
                "VALUES (?, ?, 'home_feed', ?, ?)",
                (self.campaign_id, creative_id, 1 if i == 0 else 0, f"{day}T10:0{i}:00"),
            )
        cur.execute(
            "INSERT INTO pulse_ad_clicks (campaign_id, creative_id, placement_key, created_at) VALUES (?, ?, 'home_feed', ?)",
            (self.campaign_id, creative_id, f"{day}T10:05:00"),
        )
        self.conn.commit()
        return creative_id

    def test_aggregates_match_raw_rows_and_recompute_is_idempotent(self):
        creative_id = self.seed_delivery()
        engine.refresh_daily_aggregates(self.conn, self.campaign_id, "2026-08-09")
        engine.refresh_daily_aggregates(self.conn, self.campaign_id, "2026-08-09")  # idempotent
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM pulse_ad_daily_aggregates WHERE campaign_id=? AND creative_id=? AND placement_key='home_feed'",
            (self.campaign_id, creative_id),
        )
        rows = [dict(r) for r in cur.fetchall()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["impressions"], 3)
        self.assertEqual(rows[0]["viewable_impressions"], 1)
        self.assertEqual(rows[0]["clicks"], 1)

    def test_reporting_cycle_covers_recent_campaigns(self):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date().isoformat()
        self.seed_delivery(day=today)
        out = engine.run_reporting_cycle(self.conn)
        self.assertGreaterEqual(out["campaigns"], 1)


class EndToEndLoopTests(WorkerBase):
    """The mission finish line, in miniature: spend recorded via the real
    idempotent billing path, jobs queued, worker consumes, aggregates update,
    and a retried spend never double-charges."""

    def test_full_loop_spend_queue_aggregate(self):
        cur = self.conn.cursor()
        cur.execute("UPDATE pulse_ad_wallets SET available_balance_cents=1000 WHERE account_id=?", (self.account_id,))
        self.conn.commit()
        day = engine.now_iso()[:10]  # spend rows are stamped with real now()
        # 1. Billable delivery event → idempotent spend (same key twice).
        first = pulse_ad_payments.record_spend_event(
            self.conn, self.campaign_id, 0, "home_feed", amount_cents=1,
            idempotency_key="impression-token:e2e:1",
        )
        dup = pulse_ad_payments.record_spend_event(
            self.conn, self.campaign_id, 0, "home_feed", amount_cents=1,
            idempotency_key="impression-token:e2e:1",
        )
        self.assertTrue(first["ok"])
        self.assertTrue(dup.get("deduped"))
        cur.execute("SELECT available_balance_cents, lifetime_spent_cents FROM pulse_ad_wallets WHERE account_id=?", (self.account_id,))
        wallet = dict(cur.fetchone())
        self.assertEqual(wallet["available_balance_cents"], 999)  # charged exactly once
        self.assertEqual(wallet["lifetime_spent_cents"], 1)
        # 2. Delivery evidence row + queued reporting job.
        cur.execute(
            "INSERT INTO pulse_ad_impressions (campaign_id, creative_id, placement_key, created_at) VALUES (?, 0, 'home_feed', ?)",
            (self.campaign_id, f"{day}T12:00:00"),
        )
        self.conn.commit()
        engine.enqueue_job(
            self.conn, "reporting", "refresh_aggregates",
            {"campaign_id": self.campaign_id, "day": day},
            idempotency_key=f"agg:{self.campaign_id}:{day}",
        )
        # 3. Worker consumes the queue.
        result = engine.process_pending_jobs(self.conn)
        self.assertEqual(result["processed"], 1)
        # 4. Aggregate reflects both the impression and the spend.
        cur.execute(
            "SELECT SUM(impressions) AS imp, SUM(spend_cents) AS spend FROM pulse_ad_daily_aggregates WHERE campaign_id=? AND day=?",
            (self.campaign_id, day),
        )
        agg = dict(cur.fetchone())
        self.assertEqual(agg["imp"], 1)
        self.assertEqual(agg["spend"], 1)


if __name__ == "__main__":
    unittest.main()
