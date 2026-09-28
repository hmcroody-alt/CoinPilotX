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
CREATE TABLE IF NOT EXISTS marketplace_product_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER,
    seller_user_id INTEGER,
    business_id TEXT,
    store_id TEXT,
    supplier_connection_id TEXT,
    external_product_id TEXT
);
CREATE TABLE IF NOT EXISTS business_os_store_import_policy (
    business_id TEXT,
    store_id TEXT,
    auto_publish INTEGER NOT NULL DEFAULT 1
);
"""

STORE = "mkt-seller:90402"


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
        conn.execute("DELETE FROM marketplace_product_sources")
        conn.execute("DELETE FROM business_os_store_import_policy")
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

    def imported_draft(self, *, auto_publish=1, approval=lifecycle.PENDING_REVIEW):
        """A product the merchant imported and the publish gate declined.

        Both halves of the evidence, because either one alone is a different
        listing: the ``marketplace_product_sources`` row is what makes it an
        import rather than something the seller typed, and ``auto_publish`` is
        what makes ``draft`` the gate's word rather than the merchant's.
        """
        listing_id = self.insert(lifecycle.DRAFT, approval)
        conn = db_service.connect()
        conn.execute(
            "INSERT INTO marketplace_product_sources "
            "(listing_id, seller_user_id, business_id, store_id, supplier_connection_id, "
            " external_product_id) VALUES (?,?,?,?,?,?)",
            (listing_id, SELLER, STORE, STORE, "conn-1", f"ext-{listing_id}"))
        conn.execute(
            "INSERT INTO business_os_store_import_policy (business_id, store_id, auto_publish) "
            "SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM business_os_store_import_policy "
            "WHERE business_id=? AND store_id=?)",
            (STORE, STORE, auto_publish, STORE, STORE))
        conn.commit()
        conn.close()
        return listing_id

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

    # -- the imports that were never released ----------------------------------
    #
    # The mirror of everything above, on the other axis. `drafts.autopublish`
    # used to write `draft` when the publish gate declined an import, so
    # `awaiting_moderation` was false for the same reason an unfinished
    # product's is -- except this merchant pressed "Import & publish". One
    # production seller held 67 of these on 2026-09-27, each reading "pending
    # review" and sitting in no queue. The code fix moves none of them.

    def test_an_imported_draft_is_stranded_only_when_the_release_is_evidenced(self):
        """The evidence is not on the listing, and must not be guessed from it.

        This is the property that keeps the narrow rule narrow: the very same row
        is a zombie or an ordinary draft depending on a join the classifier does
        not get to do, so a caller holding only a listing row gets the safe
        answer.
        """
        row = {"status": lifecycle.DRAFT, "approval_status": lifecycle.PENDING_REVIEW}
        self.assertIsNone(rv.zombie_reason(row))
        self.assertIsNone(rv.zombie_repair(row))

        evidenced = dict(row, imported_under_autopublish=True)
        self.assertEqual(rv.zombie_reason(evidenced), rv.IMPORTED_BUT_NEVER_RELEASED)

    def test_repairing_an_unreleased_import_releases_it_without_publishing_it(self):
        """The safety half. `review_ready` is not a status any buyer query reads,
        so the sweep hands the product to a reviewer and to nobody else."""
        repair = rv.zombie_repair({
            "status": lifecycle.DRAFT,
            "approval_status": lifecycle.PENDING_REVIEW,
            "imported_under_autopublish": True,
        })
        self.assertEqual(repair["status"], lifecycle.REVIEW_READY)
        self.assertNotIn(repair["status"], lifecycle.PUBLIC_STATUSES)
        # And it decides nothing, which is the rule the other class obeys too.
        self.assertNotIn("approval_status", repair)

    def test_a_decided_import_is_not_reopened(self):
        """A rejected import is a finished conversation. Re-releasing it would put
        a product a reviewer already turned down back at the top of their queue."""
        for approval in (lifecycle.APPROVED, lifecycle.REJECTED,
                         lifecycle.CHANGES_REQUESTED):
            self.assertIsNone(rv.zombie_reason({
                "status": lifecycle.DRAFT,
                "approval_status": approval,
                "imported_under_autopublish": True,
            }), approval)

    def test_the_sweep_finds_an_unreleased_import_and_makes_it_reviewable(self):
        """End to end, and ending at the queue rather than at the column."""
        stranded = self.imported_draft()
        self.assertEqual(self.in_queue(), set(),
                         "fixture is not stranded, so this proves nothing")

        result = sweep.audit(apply_changes=True)

        self.assertEqual(result["requeued_count"], 1)
        self.assertEqual(result["requeued"][0]["reason"], rv.IMPORTED_BUT_NEVER_RELEASED)
        self.assertEqual(self.in_queue(), {stranded})
        row = self.rows()[stranded]
        self.assertEqual(row["status"], lifecycle.REVIEW_READY)
        self.assertNotIn(row["status"], lifecycle.PUBLIC_STATUSES)
        self.assertTrue(lifecycle.awaiting_moderation(row))
        self.assertIsNone(rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER))

    def test_a_store_that_turned_auto_publish_off_keeps_its_drafts(self):
        """It asked to look before anything moved. These drafts are drafts."""
        held = self.imported_draft(auto_publish=0)
        result = sweep.audit(apply_changes=True)
        self.assertEqual(result["requeued_count"], 0)
        self.assertEqual(self.rows()[held]["status"], lifecycle.DRAFT)
        self.assertEqual(self.in_queue(), set())

    def test_a_hand_written_draft_is_never_swept(self):
        """No source row, so no import, so no release to restore. A seller's own
        unfinished product must not appear in a reviewer's backlog.

        A *real* import sits beside it deliberately. With the sources table empty
        this test passes no matter what the predicate does -- a cross join over
        nothing returns nothing -- so it would have been a guard that could not
        fail. The imported row is what makes a dropped join observable: it gives
        the broken predicate a source row to pair the typed draft with.
        """
        typed = self.insert(lifecycle.DRAFT, lifecycle.PENDING_REVIEW)
        imported = self.imported_draft()

        result = sweep.audit(apply_changes=True)

        self.assertEqual(result["requeued_count"], 1)
        self.assertEqual(result["requeued"][0]["listing_id"], imported)
        self.assertEqual(self.rows()[typed]["status"], lifecycle.DRAFT)
        self.assertEqual(self.in_queue(), {imported})

    def test_repairing_the_imports_is_idempotent(self):
        self.imported_draft()
        self.imported_draft()
        first = sweep.audit(apply_changes=True)
        second = sweep.audit(apply_changes=True)
        self.assertEqual(first["requeued_count"], 2)
        self.assertEqual(second["requeued_count"], 0)

    def test_a_listing_with_two_source_rows_is_counted_once(self):
        """The report is what an operator reads before running --apply, so a
        double-counted row is a number they cannot act on."""
        listing_id = self.imported_draft()
        conn = db_service.connect()
        conn.execute(
            "INSERT INTO marketplace_product_sources "
            "(listing_id, seller_user_id, business_id, store_id, supplier_connection_id) "
            "VALUES (?,?,?,?,?)",
            (listing_id, SELLER, STORE, STORE, "conn-2"))
        conn.commit()
        conn.close()
        result = sweep.audit(apply_changes=False)
        self.assertEqual(result["requeued_count"], 1)

    def test_the_two_classes_are_repaired_on_their_own_axes_in_one_run(self):
        """The reason the UPDATE is built from the repair dict. One hardcoded
        column would have written `review_ready` into `approval_status`, or a
        blank moderation state into `status`."""
        blank = self.insert(lifecycle.PUBLISHED, "")
        imported = self.imported_draft()

        sweep.audit(apply_changes=True)

        rows = self.rows()
        self.assertEqual(rows[blank]["approval_status"], lifecycle.PENDING_REVIEW)
        self.assertEqual(rows[blank]["status"], lifecycle.PUBLISHED)
        self.assertEqual(rows[imported]["status"], lifecycle.REVIEW_READY)
        self.assertEqual(rows[imported]["approval_status"], lifecycle.PENDING_REVIEW)
        self.assertEqual(self.in_queue(), {blank, imported})

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
