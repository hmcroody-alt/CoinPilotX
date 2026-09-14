"""§45. Break each guard on purpose and prove something goes red.

A green suite is evidence of nothing until you know it can go red. Every
assertion in ``test_admin_review_batch_route.py`` and
``test_admin_review_queue_page.py`` could be vacuous — asserting on a field that
is always present, a count that is always zero, a block that the fixture never
actually triggers — and the whole file would still pass. That is the failure
mode this exists to rule out: not "is the code correct" but "would these tests
notice if it were not".

Each test below does the same three things:

  1. run a **detector** against the real code and require it to *pass*. A
     detector that is already failing proves nothing about the mutation;
  2. apply one **mutation** — a small, plausible change a reasonable engineer
     might make — and require the same detector to *fail*;
  3. restore.

Step 1 is the half that is easy to leave out and the half that matters. Without
it, a detector that raises unconditionally (a typo'd table name, a fixture that
never seeds) reads as "the mutation was caught" for every mutation in the file.

The mutations are applied to *module attributes at runtime*, never to files on
disk. Nothing here writes to the repository, so a mutation left applied by a
crashed test dies with the process instead of being committed.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_review_mutation_guards.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_mutations_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 79301
REVIEWER = 79302
NOW = "2026-09-01T00:00:00"

ENDPOINT = "/api/admin/marketplace/review/batch"
PAGE = "/admin/marketplace-command"

TICK = re.compile(r"class='review-tick' value='(\d+)' data-block=\"([^\"]*)\"")


class Restore:
    """Set a module attribute and put the old one back, whatever happens.

    Not ``unittest.mock.patch``: several of these replace a *constant*
    (``REASON_REQUIRED``) rather than a callable, and the point of the file is
    that a mutation is never left applied — a leaked mutation would make every
    later test in the process report a false catch.
    """

    def __init__(self, target, name, value):
        self.target, self.name, self.value = target, name, value

    def __enter__(self):
        self.previous = getattr(self.target, self.name)
        setattr(self.target, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.target, self.name, self.previous)
        return False


class ReviewMutationGuardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_require_admin_api = bot.require_admin_api
        cls._real_require_admin_page = bot.require_admin_page
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

        def _require_admin_api(permission="users.view"):
            return {"id": REVIEWER, "username": "reviewer",
                    "email": "reviewer@example.com"}, None

        def _require_admin_page(permission="users.view"):
            return {"id": REVIEWER, "username": "reviewer",
                    "email": "reviewer@example.com", "role": "owner"}, None

        bot.require_admin_api = _require_admin_api
        bot.require_admin_page = _require_admin_page

    @classmethod
    def tearDownClass(cls):
        bot.require_admin_api = cls._real_require_admin_api
        bot.require_admin_page = cls._real_require_admin_page

    def setUp(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM marketplace_listings")
            cur.execute("DELETE FROM marketplace_review_batches")
            cur.execute("DELETE FROM admin_audit_logs")
            cur.execute("DELETE FROM pulse_notifications WHERE user_id IN (?,?)",
                        (SELLER, REVIEWER))
            cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                        (SELLER, REVIEWER))
            for user_id, store in ((SELLER, "Lamp Co"), (REVIEWER, "Reviewer's Own Store")):
                cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                            " VALUES (?,?,?)", (user_id, f"user{user_id}", store))
                cur.execute("INSERT INTO marketplace_sellers"
                            " (user_id, display_name, status, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (user_id, store, "approved", NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    # -- harness ---------------------------------------------------------------

    def insert_listing(self, seller_user_id=SELLER, **overrides):
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": lifecycle.PENDING_REVIEW,
            "approval_status": lifecycle.PENDING_REVIEW,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 12,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({cols}) VALUES ({marks})",
                    tuple(row.values()))
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def review(self, action, listing_ids, key=None, **extra):
        body = {"action": action, "listing_ids": listing_ids,
                "idempotency_key": key or f"key-{uuid.uuid4()}"}
        body.update(extra)
        return self.client.post(ENDPOINT, data=json.dumps(body),
                                content_type="application/json")

    def stored(self, listing_id):
        return self.query("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows

    def outcomes(self, response):
        body = response.get_json()
        return body, {int(e["listing_id"]): e for e in body.get("results", [])}

    def assert_mutation_is_caught(self, name, mutation, detector):
        """The whole shape of this file, in one method.

        ``detector`` must pass now and fail under ``mutation``. Both halves are
        assertions: a detector that cannot pass is broken, and a detector that
        cannot fail is decoration.
        """
        self.setUp()
        try:
            detector()
        except AssertionError as unmutated_failure:
            self.fail(f"detector for {name!r} fails against the real code, so it "
                      f"proves nothing about the mutation: {unmutated_failure}")

        self.setUp()
        with mutation:
            try:
                detector()
            except AssertionError:
                return
        self.fail(f"MUTATION SURVIVED: {name}. The code was broken on purpose and "
                  f"every assertion still passed — this guard is untested.")

    # -- 1. §18 the reviewer's own listing -------------------------------------

    def test_removing_the_self_review_gate_is_caught(self):
        def blind_to_ownership(listing, action, *, reviewer_id):
            """"Admins are trusted, and the ownership check is a nuisance in
            testing." The rewrite that drops exactly one clause."""
            if listing is None:
                return rv.NOT_FOUND
            if not lifecycle.awaiting_moderation(listing):
                return rv.NOT_AWAITING_REVIEW
            if action == rv.APPROVE and rv._goods.evaluate(
                    listing).get("decision") == "PROHIBITED":
                return rv.PROHIBITED
            return None

        def detector():
            listing_id = self.insert_listing(seller_user_id=REVIEWER)
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            self.assertEqual(results[listing_id]["outcome"], rv.BLOCKED)
            self.assertEqual(results[listing_id]["error_code"], rv.SELF_REVIEW)
            self.assertEqual(self.stored(listing_id)["approval_status"],
                             lifecycle.PENDING_REVIEW)

        self.assert_mutation_is_caught(
            "§18 self-review gate removed",
            Restore(rv, "block_reason", blind_to_ownership), detector)

    # -- 2. §34 prohibited products --------------------------------------------

    def test_letting_a_human_approve_a_prohibited_product_is_caught(self):
        def human_override(listing, action, *, reviewer_id):
            """"A reviewer looked at it, so the policy engine can stand down."
            §34 exists because this is the reasonable-sounding version."""
            if listing is None:
                return rv.NOT_FOUND
            if not lifecycle.awaiting_moderation(listing):
                return rv.NOT_AWAITING_REVIEW
            if str(listing.get("seller_user_id") or "") == str(reviewer_id or "\0"):
                return rv.SELF_REVIEW
            return None

        def detector():
            listing_id = self.insert_listing(title="Case of whisky", category="Alcohol")
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            entry = results[listing_id]
            # `outcome` first, `error_code` second. A succeeded entry carries no
            # `error_code` at all, so reading it first turns the catch into a
            # KeyError -- red, but not an *assertion*, and `assert_mutation_is_caught`
            # only counts assertions on purpose: a detector that crashes rather
            # than asserts is one whose failure message tells you nothing.
            self.assertEqual(entry["outcome"], rv.BLOCKED)
            self.assertEqual(entry.get("error_code"), rv.PROHIBITED)
            self.assertNotEqual(self.stored(listing_id)["status"], lifecycle.PUBLISHED)

        self.assert_mutation_is_caught(
            "§34 prohibited products approvable by a human",
            Restore(rv, "block_reason", human_override), detector)

    # -- 3. §9 a rejection with no reason --------------------------------------

    def test_making_the_rejection_reason_optional_is_caught(self):
        def detector():
            listing_id = self.insert_listing()
            response = self.review(rv.REJECT, [listing_id])
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "REASON_REQUIRED")
            self.assertEqual(self.stored(listing_id)["approval_status"],
                             lifecycle.PENDING_REVIEW)

        self.assert_mutation_is_caught(
            "§9 reason no longer required to reject",
            Restore(rv, "REASON_REQUIRED", frozenset()), detector)

    # -- 4. §36 an unknown reason code -----------------------------------------

    def test_filing_an_unknown_reason_code_as_other_is_caught(self):
        def coerce_to_other(action, reason_code, note=None):
            """"Be liberal in what you accept." Which turns a reviewer's typo
            into a real category in the audit trail, silently."""
            code = str(reason_code or "").strip().upper()
            if action not in rv.REASON_REQUIRED:
                return {"reason_code": "", "note": str(note or "").strip()}
            if code not in rv.REASON_CODES:
                code = rv.OTHER
            return {"reason_code": code, "note": str(note or "").strip()}

        def detector():
            listing_id = self.insert_listing()
            response = self.review(rv.REJECT, [listing_id], reason_code="BAD_VIBES")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "UNKNOWN_REASON")
            self.assertEqual(self.stored(listing_id)["moderation_category"] or "", "")

        self.assert_mutation_is_caught(
            "§36 unknown reason code coerced to OTHER",
            Restore(rv, "normalize_reason", coerce_to_other), detector)

    # -- 5. §17 the double tap --------------------------------------------------

    def test_bypassing_the_idempotency_claim_is_caught(self):
        def always_fresh(cur, reviewer_id, normalized):
            """"The unique index on the key already stops duplicates." It does
            not: the second request is a *different transaction* that reads the
            rows again and answers from whatever it finds."""
            return {"state": "claimed", "batch_id": f"mrb_{uuid.uuid4().hex[:12]}"}

        def detector():
            listing_id = self.insert_listing()
            key = "double-tap"
            first = self.review(rv.APPROVE, [listing_id], key=key)
            second = self.review(rv.APPROVE, [listing_id], key=key)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            body = second.get_json()
            self.assertTrue(body.get("replayed"),
                            "the second tap decided again instead of replaying")
            self.assertEqual(body["successful_count"], 1)
            self.assertEqual(body["batch_id"], first.get_json()["batch_id"])

        self.assert_mutation_is_caught(
            "§17 idempotency claim bypassed",
            Restore(rv, "claim", always_fresh), detector)

    # -- 6. §15 the two that were blocked --------------------------------------

    def test_dropping_the_blocked_rows_from_the_results_is_caught(self):
        real_evaluate = rv.evaluate_rows

        def quietly_shrink(rows, listing_ids, action, *, reviewer_id):
            """"Only report what we acted on." The two products the reviewer
            most needs told about are the two that then never appear."""
            verdict = real_evaluate(rows, listing_ids, action, reviewer_id=reviewer_id)
            return {"eligible": verdict["eligible"], "blocked": []}

        def detector():
            good = self.insert_listing()
            mine = self.insert_listing(seller_user_id=REVIEWER)
            body, results = self.outcomes(self.review(rv.APPROVE, [good, mine]))
            self.assertEqual(body["requested_count"], 2)
            self.assertEqual(body["blocked_count"], 1)
            self.assertIn(mine, results)

        self.assert_mutation_is_caught(
            "§15 blocked rows dropped instead of reported",
            Restore(rv, "evaluate_rows", quietly_shrink), detector)

    # -- 7. §37 "the UPDATE returned, so it is live" ----------------------------

    def test_reporting_live_without_reading_the_row_back_is_caught(self):
        def optimistic(listing):
            """"We just approved it, so it is live." Three of the five
            publication conditions live on rows the UPDATE never touched."""
            return {"review_state": lifecycle.APPROVED,
                    "publication_state": lifecycle.PUBLISHED,
                    "live": True, "blockers": [], "note": ""}

        def detector():
            listing_id = self.insert_listing(quantity=0)
            _, results = self.outcomes(self.review(rv.APPROVE, [listing_id]))
            entry = results[listing_id]
            self.assertEqual(entry["outcome"], rv.SUCCEEDED)
            self.assertFalse(entry.get("live"),
                             "an out-of-stock listing was reported live")
            self.assertTrue(entry.get("blockers"))

        self.assert_mutation_is_caught(
            "§37 publication read-back replaced with optimism",
            Restore(rv, "publication_readback", optimistic), detector)

    # -- 8. §19 the audit trail -------------------------------------------------

    def test_losing_the_audit_row_is_caught(self):
        def silent(cur, reviewer_id, action, listing_id, payload):
            """This is not hypothetical. The route shipped calling a helper that
            opened its own connection, hit SQLite's write lock, and swallowed the
            error — twenty-five decisions and zero audit rows, behind a 200."""
            return None

        def detector():
            listing_id = self.insert_listing()
            response = self.review(rv.APPROVE, [listing_id])
            self.assertEqual(response.status_code, 200)
            rows = self.query(
                "SELECT * FROM admin_audit_logs WHERE target_type='marketplace_listing'"
                " AND target_id=?", (str(listing_id),))
            self.assertEqual(len(rows), 1, "no audit row for a completed decision")
            self.assertIn(response.get_json()["batch_id"], rows[0]["metadata"])

        self.assert_mutation_is_caught(
            "§19 audit row not written",
            Restore(bot, "_marketplace_review_audit", silent), detector)

    # -- 9. §43 the reviewer's private note -------------------------------------

    def test_leaking_the_reviewers_note_to_the_seller_is_caught(self):
        secret = "seller is a repeat offender, watch this account"
        real_emit = bot.pulse_emit_marketplace_inventory_event

        def helpful_leak(cur, seller_user_id, event, **kwargs):
            """"The seller would understand the rejection better with the
            reviewer's actual words." The note is internal (§43) and the
            structured code exists precisely so there is a safe thing to send."""
            extra = dict(kwargs.pop("extra", None) or {})
            extra["seller_message"] = secret
            return real_emit(cur, seller_user_id, event, extra=extra, **kwargs)

        def detector():
            listing_id = self.insert_listing()
            self.review(rv.REJECT, [listing_id],
                        reason_code=rv.MISLEADING_DESCRIPTION, note=secret)
            notes = self.query(
                "SELECT * FROM pulse_notifications WHERE user_id=?", (SELLER,))
            self.assertTrue(notes, "the seller was not told at all")
            blob = json.dumps(notes)
            self.assertNotIn(secret, blob, "the reviewer's private note reached the seller")
            self.assertIn(rv.SELLER_MESSAGES[rv.MISLEADING_DESCRIPTION], blob)

        self.assert_mutation_is_caught(
            "§43 reviewer's note sent to the seller",
            Restore(bot, "pulse_emit_marketplace_inventory_event", helpful_leak), detector)

    # -- 10. §38 a listing the queue cannot show --------------------------------

    def test_a_queue_narrower_than_the_gate_is_caught(self):
        real_where = rv.queue_where

        def hide_the_oldest(query, alias="l"):
            """The §38 defect in miniature: a predicate that drifts from the one
            the Approve gate uses. Nothing errors. A listing is simply in review
            forever and appears on nobody's screen."""
            where, params = real_where(query, alias)
            return f"({where}) AND {alias}.id <> (SELECT MIN(id) FROM marketplace_listings)", params

        def detector():
            ids = {self.insert_listing(title=f"Lamp {index}") for index in range(3)}
            response = self.client.get(PAGE, query_string={"page_size": 50})
            self.assertEqual(response.status_code, 200)
            shown = {int(listing_id) for listing_id, _ in
                     TICK.findall(response.get_data(as_text=True))}
            self.assertEqual(shown, ids, "a pending listing is not on any page")

        self.assert_mutation_is_caught(
            "§38 queue predicate narrower than the approve gate",
            Restore(rv, "queue_where", hide_the_oldest), detector)


if __name__ == "__main__":
    unittest.main()
