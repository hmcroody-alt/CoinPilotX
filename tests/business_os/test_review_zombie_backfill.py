"""§40/§41. The listings that were already stranded when the queue was fixed.

Making the queue and the Approve gate ask one question fixes every listing from
that moment on and moves none of the ones already stuck. A stuck row is the
hard kind of defect because nothing about it looks wrong: the seller's store
says "In review — not live yet" (correct — it is released and not public), the
Review Center does not show it (correct — ``awaiting_moderation`` is false), and
buyer discovery does not return it (correct — approval never happened). Every
surface is right and the product is invisible forever.

So the properties worth pinning are not "the sweep found N rows". They are:

  * **what it repairs becomes reachable.** A backfill that writes a state the
    queue still does not accept has done nothing except make the table look
    tidier. The test therefore ends at ``queue_sql`` and ``block_reason``, not
    at the UPDATE;
  * **it decides nothing.** The states being repaired carry no verdict — that
    is what went missing — so any inference is invention. A maintenance script
    that can mark a listing approved is a route around §34 that no reviewer
    ever sees;
  * **it leaves drafts alone.** A blank approval column on a draft is the schema
    default, not damage. Sweeping those in would fill the reviewer's backlog
    with products their own authors are not finished writing;
  * **it is idempotent.** A second run must find nothing, because an operator
    who is unsure whether the first one completed will run it again.

Run standalone (this file binds its own ``DATABASE_URL``)::

    ./.venv/bin/python3 -m pytest tests/business_os/test_review_zombie_backfill.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_zombie_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from services import db as db_service  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))

import marketplace_review_zombie_audit as sweep  # noqa: E402

REVIEWER = 90401
SELLER = 90402

SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_user_id INTEGER,
    title TEXT,
    description TEXT,
    category TEXT,
    price_label TEXT,
    status TEXT,
    approval_status TEXT,
    listing_type TEXT,
    product_type TEXT,
    quantity INTEGER,
    safety_score INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS marketplace_review_batches (
    batch_id TEXT PRIMARY KEY,
    reviewer_user_id TEXT,
    idempotency_key TEXT,
    request_hash TEXT,
    action TEXT,
    response_json TEXT,
    created_at TEXT,
    completed_at TEXT
);
"""


class ReviewZombieBackfillTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = db_service.connect()
        for statement in SCHEMA.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()
        conn.close()

    def setUp(self):
        conn = db_service.connect()
        conn.execute("DELETE FROM marketplace_listings")
        conn.execute("DELETE FROM marketplace_review_batches")
        conn.commit()
        conn.close()

    # -- harness ---------------------------------------------------------------

    def insert(self, status, approval, **overrides):
        row = {
            "seller_user_id": SELLER,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp.",
            "category": "Home",
            "price_label": "$24.00",
            "status": status,
            "approval_status": approval,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 4,
            "safety_score": 0,
            "created_at": "2026-01-01T00:00:00",
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = db_service.connect()
        cur = conn.execute(f"INSERT INTO marketplace_listings ({cols}) VALUES ({marks})",
                           tuple(row.values()))
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def rows(self):
        conn = db_service.connect()
        found = {int(dict(r)["id"]): dict(r) for r in
                 conn.execute("SELECT * FROM marketplace_listings").fetchall()}
        conn.close()
        return found

    def in_queue(self):
        conn = db_service.connect()
        found = {int(dict(r)["id"]) for r in conn.execute(
            f"SELECT id FROM marketplace_listings l WHERE {rv.queue_sql('l')}").fetchall()}
        conn.close()
        return found

    # -- classification --------------------------------------------------------

    def test_a_released_listing_with_a_blank_moderation_column_is_stranded(self):
        self.assertEqual(
            rv.zombie_reason({"status": lifecycle.PENDING_REVIEW, "approval_status": ""}),
            rv.MISSING_REVIEW_STATE)

    def test_a_word_this_build_does_not_know_reads_as_in_review_to_nobody(self):
        for unknown in ("in_review", "submitted", "needs_review", "REVIEW"):
            self.assertEqual(
                rv.zombie_reason({"status": lifecycle.PUBLISHED, "approval_status": unknown}),
                rv.UNKNOWN_REVIEW_STATE, unknown)

    def test_an_unfinished_draft_is_not_a_stranded_listing(self):
        """The schema default on a product nobody submitted. Sweeping these in
        fills the reviewer's queue with other people's unfinished work."""
        self.assertIsNone(
            rv.zombie_reason({"status": lifecycle.DRAFT, "approval_status": ""}))
        self.assertIsNone(rv.zombie_repair(
            {"status": lifecycle.DRAFT, "approval_status": "in_review"}))

    def test_a_healthy_pending_listing_is_not_touched(self):
        self.assertIsNone(rv.zombie_reason(
            {"status": lifecycle.PENDING_REVIEW, "approval_status": lifecycle.PENDING_REVIEW}))
        self.assertIsNone(rv.zombie_reason(
            {"status": lifecycle.PUBLISHED, "approval_status": lifecycle.PENDING_REVIEW}))

    def test_a_decided_listing_is_not_a_zombie(self):
        for approval in (lifecycle.APPROVED, lifecycle.REJECTED,
                         lifecycle.CHANGES_REQUESTED, "restricted"):
            self.assertIsNone(rv.zombie_reason(
                {"status": lifecycle.PUBLISHED, "approval_status": approval}), approval)

    def test_approved_but_never_released_is_reported_and_never_repaired(self):
        """Its fix is on the merchant's axis. Writing ``status='published'`` from
        a maintenance script publishes a listing its merchant never released."""
        row = {"status": lifecycle.PENDING_REVIEW, "approval_status": lifecycle.APPROVED}
        self.assertEqual(rv.zombie_reason(row), rv.APPROVED_BUT_UNRELEASED)
        self.assertIsNone(rv.zombie_repair(row))

    def test_the_repair_puts_the_row_in_the_queue_and_decides_nothing(self):
        repair = rv.zombie_repair({"status": lifecycle.PUBLISHED, "approval_status": ""})
        self.assertEqual(repair["approval_status"], lifecycle.PENDING_REVIEW)
        self.assertNotIn("status", repair)
        self.assertNotEqual(repair["approval_status"], lifecycle.APPROVED)

    # -- the sweep -------------------------------------------------------------

    def test_the_sql_never_misses_a_row_the_rule_would_call_stranded(self):
        """The predicate is allowed to be a superset. It is not allowed to be a
        subset — a row the SQL skips is one the sweep can never re-check."""
        states = ["", "pending_review", "review_ready", "approved", "rejected",
                  "changes_requested", "restricted", "in_review", "submitted"]
        statuses = [lifecycle.DRAFT, lifecycle.PENDING_REVIEW, lifecycle.PUBLISHED,
                    "active", lifecycle.REJECTED, lifecycle.ARCHIVED]
        expected = set()
        for status in statuses:
            for approval in states:
                listing_id = self.insert(status, approval)
                if rv.zombie_reason({"status": status, "approval_status": approval}):
                    expected.add(listing_id)

        conn = db_service.connect()
        matched = {int(dict(r)["id"]) for r in conn.execute(
            f"SELECT id FROM marketplace_listings l WHERE {rv.zombie_sql('l')}").fetchall()}
        conn.close()
        self.assertTrue(expected, "fixture produced no stranded rows at all")
        self.assertTrue(expected <= matched,
                        f"the sweep cannot see {sorted(expected - matched)}")

    def test_a_report_run_changes_nothing(self):
        stranded = self.insert(lifecycle.PUBLISHED, "")
        before = self.rows()
        result = sweep.audit(apply_changes=False)
        self.assertEqual(result["requeued_count"], 1)
        self.assertEqual(result["requeued"][0]["listing_id"], stranded)
        self.assertFalse(result["applied"])
        self.assertEqual(self.rows(), before)

    def test_what_the_sweep_repairs_becomes_reachable_and_decidable(self):
        """The assertion the whole thing exists for. Not "the column changed" —
        the reviewer can now see it and act on it."""
        blank = self.insert(lifecycle.PENDING_REVIEW, "")
        unknown = self.insert(lifecycle.PUBLISHED, "in_review")
        self.assertEqual(self.in_queue(), set())

        result = sweep.audit(apply_changes=True)

        self.assertEqual(result["requeued_count"], 2)
        self.assertEqual(self.in_queue(), {blank, unknown})
        for listing_id, row in self.rows().items():
            self.assertIsNone(
                rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER),
                f"listing {listing_id} is in the queue and still not decidable")

    def test_the_sweep_never_publishes_and_never_approves(self):
        published = self.insert(lifecycle.PUBLISHED, "in_review")
        pending = self.insert(lifecycle.PENDING_REVIEW, "")
        sweep.audit(apply_changes=True)
        rows = self.rows()
        self.assertEqual(rows[published]["status"], lifecycle.PUBLISHED)
        self.assertEqual(rows[pending]["status"], lifecycle.PENDING_REVIEW)
        for row in rows.values():
            self.assertNotEqual(row["approval_status"], lifecycle.APPROVED)

    def test_running_it_twice_finds_nothing_the_second_time(self):
        """An operator unsure whether the first run finished will run it again."""
        self.insert(lifecycle.PUBLISHED, "in_review")
        self.insert(lifecycle.PENDING_REVIEW, "")
        first = sweep.audit(apply_changes=True)
        second = sweep.audit(apply_changes=True)
        self.assertEqual(first["requeued_count"], 2)
        self.assertEqual(second["requeued_count"], 0)

    def test_the_limit_reports_what_it_declined_to_touch(self):
        """A first --apply on a large catalogue is checked more easily when it is
        small — but the rows it skipped must still be counted somewhere."""
        for _ in range(4):
            self.insert(lifecycle.PUBLISHED, "in_review")
        result = sweep.audit(apply_changes=True, limit=2)
        self.assertEqual(result["requeued_count"], 2)
        self.assertEqual(result["needs_human_decision_count"], 2)
        self.assertEqual(len(self.in_queue()), 2)

    def test_a_listing_needing_a_human_is_counted_not_hidden(self):
        """Excluding what cannot be auto-fixed is how the original defect stayed
        invisible for so long."""
        self.insert(lifecycle.PENDING_REVIEW, lifecycle.APPROVED)
        result = sweep.audit(apply_changes=True)
        self.assertEqual(result["requeued_count"], 0)
        self.assertEqual(result["needs_human_decision_count"], 1)
        self.assertEqual(result["needs_human_decision"][0]["reason"],
                         rv.APPROVED_BUT_UNRELEASED)

    # -- §41 -------------------------------------------------------------------

    def test_two_ledger_rows_for_one_key_are_reported(self):
        """The unique index makes new ones impossible. That is exactly why the
        old ones need finding: an index added afterwards cleans up nothing."""
        conn = db_service.connect()
        for batch_id in ("mrb_one", "mrb_two"):
            conn.execute(
                "INSERT INTO marketplace_review_batches "
                "(batch_id, reviewer_user_id, idempotency_key, request_hash, action, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (batch_id, str(REVIEWER), "same-key", "hash", rv.APPROVE, "2026-01-01"))
        conn.commit()
        conn.close()

        result = sweep.audit(apply_changes=False)
        duplicates = result["duplicate_review_batches"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(int(dict(duplicates[0])["copies"]), 2)

    def test_a_clean_ledger_reports_no_duplicates(self):
        conn = db_service.connect()
        conn.execute(
            "INSERT INTO marketplace_review_batches "
            "(batch_id, reviewer_user_id, idempotency_key, request_hash, action, created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("mrb_only", str(REVIEWER), "key", "hash", rv.APPROVE, "2026-01-01"))
        conn.commit()
        conn.close()
        self.assertEqual(sweep.audit(apply_changes=False)["duplicate_review_batches"], [])


if __name__ == "__main__":
    unittest.main()
