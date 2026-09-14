"""The admin bulk review endpoint end to end — does the batch move the rows?

``tests/business_os/test_listing_review_authority.py`` pins what a review batch
*decides*. Every decision there is a pure function, which is what makes it
provable — and also what makes it unable to catch any of the things only the
route can get wrong:

  * the batch **writes**. A perfect verdict attached to nothing is exactly the
    state this endpoint replaced: a review page where a reviewer approves one
    listing at a time and a backlog nobody can clear;
  * **partial success is real** (§15). Twenty-three approved and two blocked
    must leave twenty-three rows moved and two untouched. The two failures worth
    telling apart are the batch that refuses all twenty-five because two were
    bad, and the batch that reports twenty-five successes because it never
    looked. Both produce a plausible summary;
  * **§18 and §34 are enforced on the server**, not in the page. A reviewer's
    own listing and a prohibited product must come back blocked *and* unchanged
    in the table — a block that reports correctly and writes anyway is the worst
    of the three outcomes;
  * **a retry does not act twice** (§17). Approving publishes, notifies the
    seller and files an audit row; a double tap that ran twice does all three
    again, and the seller sees two notifications for one decision;
  * **§37 is answered from the row, not from the UPDATE.** An approval that
    lands on a suspended seller's listing must report ``live: false`` with the
    reason, because the alternative is telling a reviewer a product is live when
    no buyer can reach it;
  * **§43 holds across the wire.** The reviewer's private note is written to the
    listing's moderation column and must not reach the seller's notification.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_admin_review_batch_route.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_batch_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 77101
RIVAL = 77102
#: The reviewer is also a seller. That is the §18 case, and it is not exotic —
#: nothing anywhere requires an admin account to have no store.
REVIEWER = 77103
NOW = "2026-09-01T00:00:00"

ENDPOINT = "/api/admin/marketplace/review/batch"


class AdminReviewBatchRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_require_admin = bot.require_admin_api
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.require_admin_api = cls._real_require_admin

    def setUp(self):
        self.sign_in(REVIEWER)
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            for table, column in (("marketplace_listings", "seller_user_id"),
                                  ("marketplace_sellers", "user_id"),
                                  ("pulse_notifications", "user_id")):
                cur.execute(f"DELETE FROM {table} WHERE {column} IN (?,?,?,?)",
                            (SELLER, RIVAL, REVIEWER, REVIEWER + 50))
            cur.execute("DELETE FROM marketplace_review_batches")
            cur.execute("DELETE FROM admin_audit_logs")
            for user_id, store in ((SELLER, "Lamp Co"), (RIVAL, "Rival Goods"),
                                   (REVIEWER, "Reviewer's Own Store")):
                cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                            " VALUES (?,?,?)", (user_id, f"user{user_id}", store))
                cur.execute("INSERT INTO marketplace_sellers"
                            " (user_id, display_name, status, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (user_id, store, "approved", NOW, NOW))
            conn.commit()
        finally:
            # Closed in `finally`, because a setUp that raises while holding a
            # write lock on the SQLite file turns one broken assumption into
            # "database is locked" on every remaining test in the class -- which
            # hides the original error behind thirty identical ones.
            conn.close()

    # -- harness ---------------------------------------------------------------

    def sign_in(self, admin_id, permitted=True):
        """Stand in for the admin session. The permission gate itself belongs to
        ``require_admin_api`` and is tested where it lives; what this file needs
        is a reviewer *identity*, because §18 turns on who is asking."""
        def _require_admin_api(permission="users.view"):
            if not permitted:
                return None, (bot.jsonify({"ok": False, "error": "Insufficient permissions."}), 403)
            return {"id": admin_id, "username": f"admin{admin_id}"}, None
        bot.require_admin_api = _require_admin_api

    def insert_listing(self, seller_user_id=SELLER, **overrides):
        """A listing sitting in review: released by its merchant, undecided."""
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

    def stored(self, listing_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM marketplace_listings WHERE id=?",
                           (listing_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows

    def review(self, action, listing_ids, key="key-1", **extra):
        body = {"action": action, "listing_ids": listing_ids, "idempotency_key": key}
        body.update(extra)
        return self.client.post(ENDPOINT, data=json.dumps(body),
                                content_type="application/json")

    def outcomes(self, body):
        return {int(entry["listing_id"]): entry for entry in body["results"]}

    # -- the batch writes ------------------------------------------------------

    def test_a_pending_listing_is_actually_approved_and_published(self):
        listing_id = self.insert_listing()
        response = self.review(rv.APPROVE, [listing_id])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()

        self.assertEqual(body["successful_count"], 1)
        self.assertEqual(body["blocked_count"], 0)
        self.assertEqual(body["failed_count"], 0)
        self.assertTrue(body["batch_id"].startswith("mrb_"))

        entry = self.outcomes(body)[listing_id]
        self.assertEqual(entry["outcome"], rv.SUCCEEDED)
        self.assertEqual(entry["old_review_state"], lifecycle.PENDING_REVIEW)
        self.assertEqual(entry["new_review_state"], lifecycle.APPROVED)
        self.assertEqual(entry["publication_state"], lifecycle.PUBLISHED)
        self.assertTrue(entry["live"])

        # The summary is a claim; the row is the fact.
        row = self.stored(listing_id)
        self.assertEqual(row["approval_status"], lifecycle.APPROVED)
        self.assertEqual(row["status"], lifecycle.PUBLISHED)
        self.assertEqual(str(row["reviewed_by"]), str(REVIEWER))
        self.assertTrue(row["approved_at"])
        self.assertTrue(row["published_at"])

    def test_the_dropship_listing_the_seller_sees_as_in_review_can_be_approved(self):
        """§39. The acceptance case, in the state the supplier path leaves.

        ``drafts.publish`` writes ``status='published'`` and leaves moderation
        untouched, so the merchant is told "In review — not live yet" and the
        row is correctly invisible. This endpoint has to be able to finish it,
        and the queue predicate has to agree that it is there to be finished.
        """
        listing_id = self.insert_listing(
            title="Cross Border Jeans Independent Station Ripped Mid Waist",
            price_label="$35.00", status=lifecycle.PUBLISHED,
            approval_status=lifecycle.PENDING_REVIEW)

        in_queue = self.query(
            f"SELECT l.id FROM marketplace_listings l WHERE {rv.queue_sql('l')} AND l.id=?",
            (listing_id,))
        self.assertEqual(len(in_queue), 1, "the listing the seller can see is not in the queue")

        body = self.review(rv.APPROVE, [listing_id]).get_json()
        self.assertEqual(body["successful_count"], 1)
        self.assertTrue(self.outcomes(body)[listing_id]["live"])
        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.APPROVED)

        self.assertEqual(
            self.query(f"SELECT l.id FROM marketplace_listings l WHERE {rv.queue_sql('l')}"),
            [], "an approved listing is still sitting in the review queue")

    def test_a_rejection_records_the_structured_reason_on_the_row(self):
        listing_id = self.insert_listing()
        body = self.review(rv.REJECT, [listing_id], reason_code=rv.INVALID_MEDIA,
                           note="Cover image is a supplier watermark.").get_json()

        self.assertEqual(body["successful_count"], 1)
        row = self.stored(listing_id)
        self.assertEqual(row["approval_status"], lifecycle.REJECTED)
        self.assertEqual(row["status"], lifecycle.REJECTED)
        self.assertEqual(row["moderation_category"], rv.INVALID_MEDIA)
        self.assertEqual(row["moderation_reason"], "Cover image is a supplier watermark.")
        self.assertFalse(self.outcomes(body)[listing_id]["live"])

    def test_request_changes_sends_it_back_to_the_seller(self):
        listing_id = self.insert_listing()
        self.review(rv.REQUEST_CHANGES, [listing_id],
                    reason_code=rv.MISSING_INFORMATION, note="Add shipping weight.")
        row = self.stored(listing_id)
        self.assertEqual(row["approval_status"], lifecycle.CHANGES_REQUESTED)
        self.assertEqual(row["status"], lifecycle.CHANGES_REQUESTED)

    def test_restrict_parks_moderation_and_leaves_the_merchants_release_alone(self):
        """A restricted listing is not a rejected one. Overwriting ``status``
        would lose the fact that the merchant had released it, and a later
        approval would then publish nothing."""
        listing_id = self.insert_listing(status=lifecycle.PUBLISHED)
        self.review(rv.RESTRICT, [listing_id], reason_code=rv.RESTRICTED_CATEGORY,
                    note="Needs authenticity paperwork.")
        row = self.stored(listing_id)
        self.assertEqual(row["approval_status"], "restricted")
        self.assertEqual(row["status"], lifecycle.PUBLISHED)

    def test_approving_clears_the_pending_media_alongside_the_listing(self):
        listing_id = self.insert_listing()
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO marketplace_product_media"
                     " (product_id, media_url, media_type, moderation_status, created_at)"
                     " VALUES (?,?,?,?,?)",
                     (listing_id, "https://cdn.example/a.jpg", "image", "pending", NOW))
        conn.execute("INSERT INTO marketplace_product_media"
                     " (product_id, media_url, media_type, moderation_status, created_at)"
                     " VALUES (?,?,?,?,?)",
                     (listing_id, "https://cdn.example/b.jpg", "image", "rejected", NOW))
        conn.commit()
        conn.close()

        self.review(rv.APPROVE, [listing_id])
        states = [row["moderation_status"] for row in self.query(
            "SELECT moderation_status FROM marketplace_product_media"
            " WHERE product_id=? ORDER BY id", (listing_id,))]
        # A previously rejected asset is not un-rejected by approving the listing.
        self.assertEqual(states, ["approved", "rejected"])

    # -- partial success -------------------------------------------------------

    def test_twenty_three_are_approved_and_two_are_blocked(self):
        """§15 at the size the mission names, with both halves read back.

        The batch that refuses all twenty-five because two were bad and the
        batch that claims all twenty-five because it never looked are the two
        failures this exists to tell apart.
        """
        good = [self.insert_listing() for _ in range(23)]
        own = self.insert_listing(seller_user_id=REVIEWER)
        banned = self.insert_listing(category="Weapons")

        body = self.review(rv.APPROVE, good + [own, banned]).get_json()

        self.assertEqual(body["requested_count"], 25)
        self.assertEqual(body["successful_count"], 23)
        self.assertEqual(body["blocked_count"], 2)
        self.assertEqual(body["failed_count"], 0)

        for listing_id in good:
            self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.APPROVED)
        for listing_id in (own, banned):
            self.assertEqual(self.stored(listing_id)["approval_status"],
                             lifecycle.PENDING_REVIEW,
                             "a blocked listing was written anyway")

    def test_the_results_read_in_the_order_the_reviewer_ticked_them(self):
        first = self.insert_listing()
        blocked = self.insert_listing(seller_user_id=REVIEWER)
        last = self.insert_listing()
        selection = [first, blocked, last]

        body = self.review(rv.APPROVE, selection).get_json()
        self.assertEqual([entry["listing_id"] for entry in body["results"]], selection)

    def test_an_id_that_does_not_exist_comes_back_blocked_not_missing(self):
        listing_id = self.insert_listing()
        body = self.review(rv.APPROVE, [listing_id, 999999]).get_json()

        self.assertEqual(body["requested_count"], 2)
        self.assertEqual(body["blocked_count"], 1)
        self.assertEqual(self.outcomes(body)[999999]["error_code"], rv.NOT_FOUND)

    def test_a_listing_already_decided_blocks_instead_of_being_decided_twice(self):
        listing_id = self.insert_listing(status=lifecycle.PUBLISHED,
                                         approval_status=lifecycle.APPROVED)
        body = self.review(rv.APPROVE, [listing_id]).get_json()
        self.assertEqual(self.outcomes(body)[listing_id]["error_code"], rv.NOT_AWAITING_REVIEW)

    # -- §18 / §34: the two gates the page did not have ------------------------

    def test_a_reviewer_cannot_approve_their_own_listing_over_the_api(self):
        """Enforced on the server. A page that hides the button is a page, and
        this endpoint is reachable without it."""
        mine = self.insert_listing(seller_user_id=REVIEWER)
        body = self.review(rv.APPROVE, [mine]).get_json()

        self.assertEqual(body["successful_count"], 0)
        self.assertEqual(self.outcomes(body)[mine]["error_code"], rv.SELF_REVIEW)
        self.assertEqual(self.stored(mine)["approval_status"], lifecycle.PENDING_REVIEW)

    def test_a_reviewer_cannot_reject_a_rivals_listing_they_also_sell_against(self):
        """Same conflict, other hat. Blocking only approvals would leave a
        moderator free to clear the competition."""
        mine = self.insert_listing(seller_user_id=REVIEWER)
        body = self.review(rv.REJECT, [mine], reason_code=rv.POLICY_VIOLATION,
                           note="n/a").get_json()
        self.assertEqual(self.outcomes(body)[mine]["error_code"], rv.SELF_REVIEW)
        self.assertEqual(self.stored(mine)["approval_status"], lifecycle.PENDING_REVIEW)

    def test_a_prohibited_product_cannot_be_approved_by_any_human(self):
        banned = self.insert_listing(category="Weapons")
        body = self.review(rv.APPROVE, [banned]).get_json()

        self.assertEqual(self.outcomes(body)[banned]["error_code"], rv.PROHIBITED)
        self.assertEqual(self.stored(banned)["approval_status"], lifecycle.PENDING_REVIEW)
        self.assertEqual(self.stored(banned)["status"], lifecycle.PENDING_REVIEW)

    def test_a_prohibited_product_can_still_be_rejected(self):
        banned = self.insert_listing(category="Weapons")
        body = self.review(rv.REJECT, [banned], reason_code=rv.PROHIBITED_PRODUCT,
                           note="Weapons are not permitted.").get_json()
        self.assertEqual(body["successful_count"], 1)
        self.assertEqual(self.stored(banned)["approval_status"], lifecycle.REJECTED)

    def test_an_admin_without_the_permission_gets_nothing(self):
        listing_id = self.insert_listing()
        self.sign_in(REVIEWER, permitted=False)
        self.assertEqual(self.review(rv.APPROVE, [listing_id]).status_code, 403)
        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.PENDING_REVIEW)

    # -- §37: what the reviewer is told happened -------------------------------

    def test_an_approval_onto_a_suspended_seller_reports_approved_and_not_live(self):
        """The failure §37 exists for: 200, "approved", and no buyer can reach
        it. Two of five conditions are the reviewer's; three are not."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE marketplace_sellers SET status='suspended' WHERE user_id=?",
                     (SELLER,))
        conn.commit()
        conn.close()

        listing_id = self.insert_listing()
        entry = self.outcomes(self.review(rv.APPROVE, [listing_id]).get_json())[listing_id]

        self.assertEqual(entry["outcome"], rv.SUCCEEDED)
        self.assertEqual(entry["new_review_state"], lifecycle.APPROVED)
        self.assertFalse(entry["live"])
        self.assertEqual(entry["blockers"], ["seller_approved"])
        self.assertEqual(entry["note"], "the seller account is not approved")

    def test_an_approval_of_an_out_of_stock_listing_reports_why_it_is_not_live(self):
        listing_id = self.insert_listing(quantity=0)
        entry = self.outcomes(self.review(rv.APPROVE, [listing_id]).get_json())[listing_id]
        self.assertEqual(entry["outcome"], rv.SUCCEEDED)
        self.assertFalse(entry["live"])
        self.assertEqual(entry["blockers"], ["in_stock"])

    # -- §17: a retry is not a second decision ---------------------------------

    def test_the_same_key_twice_replays_the_first_answer(self):
        listing_id = self.insert_listing()
        first = self.review(rv.APPROVE, [listing_id], key="double-tap").get_json()
        second = self.review(rv.APPROVE, [listing_id], key="double-tap").get_json()

        self.assertTrue(second.get("replayed"))
        self.assertEqual(second["batch_id"], first["batch_id"])
        self.assertEqual(second["results"], first["results"])

    def test_a_double_tap_notifies_the_seller_once_and_audits_once(self):
        """The side effects are the reason idempotency is not cosmetic. A batch
        that ran twice tells the seller twice and files the decision twice."""
        listing_id = self.insert_listing()
        self.review(rv.APPROVE, [listing_id], key="double-tap")
        self.review(rv.APPROVE, [listing_id], key="double-tap")

        notes = self.query("SELECT id FROM pulse_notifications WHERE user_id=?"
                           " AND type='seller_listing_review_changed'", (SELLER,))
        self.assertEqual(len(notes), 1, "the seller was told twice about one decision")

        audits = self.query("SELECT id FROM admin_audit_logs WHERE target_id=?",
                            (str(listing_id),))
        self.assertEqual(len(audits), 1)

    def test_the_same_key_over_a_different_selection_is_refused(self):
        """Replaying the first summary would report products approved that
        nobody looked at."""
        first = self.insert_listing()
        second = self.insert_listing()
        self.assertEqual(self.review(rv.APPROVE, [first], key="k").status_code, 200)

        response = self.review(rv.APPROVE, [second], key="k")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "IDEMPOTENCY_KEY_CONFLICT")
        self.assertEqual(self.stored(second)["approval_status"], lifecycle.PENDING_REVIEW)

    def test_two_reviewers_may_use_the_same_key(self):
        mine = self.insert_listing()
        theirs = self.insert_listing()
        self.assertEqual(self.review(rv.APPROVE, [mine], key="k").status_code, 200)
        self.sign_in(REVIEWER + 50)
        self.assertEqual(self.review(rv.APPROVE, [theirs], key="k").status_code, 200)
        self.assertEqual(self.stored(theirs)["approval_status"], lifecycle.APPROVED)

    # -- §19 / §21 / §43: the trail and the seller -----------------------------

    def test_every_decision_leaves_an_audit_row_naming_the_batch(self):
        listing_ids = [self.insert_listing() for _ in range(3)]
        batch_id = self.review(rv.APPROVE, listing_ids).get_json()["batch_id"]

        rows = self.query("SELECT * FROM admin_audit_logs WHERE action=?",
                          ("marketplace_listing_approve",))
        self.assertEqual(len(rows), 3)
        for row in rows:
            payload = json.loads(row["metadata"] or "{}")
            self.assertEqual(payload["batch_id"], batch_id)
            self.assertEqual(payload["previous_approval_status"], lifecycle.PENDING_REVIEW)
            self.assertEqual(payload["new_approval_status"], lifecycle.APPROVED)

    def test_the_seller_is_notified_with_a_tap_target_for_the_listing(self):
        listing_id = self.insert_listing()
        self.review(rv.REJECT, [listing_id], reason_code=rv.INVALID_PRICE,
                    note="internal: seller has three prior rejections")

        notes = self.query("SELECT * FROM pulse_notifications WHERE user_id=?", (SELLER,))
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note["target_url"], f"/pulse/marketplace/{listing_id}")

        payload = json.loads(note["metadata_json"] or "{}")
        self.assertEqual(payload["reason_category"], rv.INVALID_PRICE)
        self.assertEqual(payload["seller_message"], rv.SELLER_MESSAGES[rv.INVALID_PRICE])

    def test_the_reviewers_private_note_never_reaches_the_seller(self):
        """§43. The note is written to the listing for the audit trail and is
        not the sentence the seller reads — ``seller_message`` is a lookup on
        the structured code, so nothing assembles the two together."""
        secret = "internal: risk 91, cj_account acct_71ff, do not tell them"
        listing_id = self.insert_listing()
        self.review(rv.REJECT, [listing_id], reason_code=rv.POLICY_VIOLATION, note=secret)

        self.assertEqual(self.stored(listing_id)["moderation_reason"], secret)
        for note in self.query("SELECT * FROM pulse_notifications WHERE user_id=?", (SELLER,)):
            self.assertNotIn("risk 91", json.dumps(note, default=str))
            self.assertNotIn("acct_71ff", json.dumps(note, default=str))

    # -- request validation over the wire --------------------------------------

    def test_a_rejection_with_no_reason_is_refused_before_anything_moves(self):
        listing_id = self.insert_listing()
        response = self.review(rv.REJECT, [listing_id])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "REASON_REQUIRED")
        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.PENDING_REVIEW)

    def test_an_unknown_reason_code_is_refused_rather_than_filed_as_other(self):
        listing_id = self.insert_listing()
        response = self.review(rv.REJECT, [listing_id], reason_code="BAD_VIBES")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNKNOWN_REASON")

    def test_an_unknown_action_is_refused(self):
        listing_id = self.insert_listing()
        response = self.review("delete", [listing_id])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNKNOWN_ACTION")

    def test_a_request_with_no_idempotency_key_is_refused(self):
        listing_id = self.insert_listing()
        response = self.client.post(
            ENDPOINT, data=json.dumps({"action": rv.APPROVE, "listing_ids": [listing_id]}),
            content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "IDEMPOTENCY_KEY_REQUIRED")

    def test_a_duplicate_tick_is_one_decision_and_one_result(self):
        listing_id = self.insert_listing()
        body = self.review(rv.APPROVE, [listing_id, listing_id, listing_id]).get_json()
        self.assertEqual(body["requested_count"], 1)
        self.assertEqual(len(body["results"]), 1)

    def test_a_selection_larger_than_the_cap_is_refused(self):
        response = self.review(rv.APPROVE, list(range(1, rv.MAX_BATCH + 2)))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "BATCH_TOO_LARGE")

    # -- §24: an approval refreshes the supplier ------------------------------
    #
    # `tests/business_os/test_review_supplier_sync.py` pins what the plan
    # *decides*, without a database. What only the route can get wrong is the
    # ordering: `suppliers.worker.schedule` opens its own connection and commits
    # it, so run from inside the batch's open write transaction it is the
    # `log_admin_audit` failure -- refused insert, swallowed exception, a 200
    # that queued nothing. That failure is completely invisible from the pure
    # side, and completely invisible from the response unless the response is
    # built after the enqueue. So these tests read the job table.

    def bind_supplier(self, listing_id, seller_user_id=SELLER, **overrides):
        """Give a listing a CJ binding, the way an import would have."""
        from services import marketplace_supplier_schema as supplier_schema

        row = {
            "listing_id": listing_id,
            "seller_user_id": seller_user_id,
            "provider": "cj",
            "provider_product_id": f"CJ-PROD-{listing_id}",
            "fulfillment_mode": "DROPSHIP",
            "supplier_connection_id": "conn_7",
            "business_id": "biz_2",
            "store_id": "store_5",
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            supplier_schema.ensure_supplier_schema(cur, force=True)
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            cur.execute(f"INSERT INTO {supplier_schema.SOURCE_TABLE} ({cols}) "
                        f"VALUES ({marks})", tuple(row.values()))
            conn.commit()
        finally:
            conn.close()

    def sync_jobs(self):
        from services.business_os.suppliers import worker as supplier_worker

        supplier_worker.ensure_schema()
        return self.query("SELECT * FROM business_os_supplier_sync_jobs "
                          "ORDER BY resource_id, kind")

    def clear_sync_jobs(self):
        from services.business_os.suppliers import worker as supplier_worker

        supplier_worker.ensure_schema()
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM business_os_supplier_sync_jobs")
        conn.commit()
        conn.close()

    def test_approving_a_dropshipped_product_queues_a_real_supplier_refresh(self):
        """The row must exist in the table, not merely be reported in the JSON.

        A response that says "queued" is exactly what the swallowed-exception
        bug produces, so the response is the one piece of evidence that cannot
        be trusted here.
        """
        self.clear_sync_jobs()
        listing_id = self.insert_listing()
        self.bind_supplier(listing_id)

        body = self.review(rv.APPROVE, [listing_id], key="sync-1").get_json()
        self.assertEqual(body["successful_count"], 1, body)

        jobs = self.sync_jobs()
        self.assertEqual({job["kind"] for job in jobs}, {"product", "inventory"}, jobs)
        for job in jobs:
            self.assertEqual(job["resource_id"], f"CJ-PROD-{listing_id}")
            self.assertEqual(job["connection_id"], "conn_7")
            self.assertEqual(job["business_id"], "biz_2")
            self.assertEqual(job["store_id"], "store_5")

    def test_the_response_says_queued_only_once_the_row_is_there(self):
        """`pending` is the in-loop value and must never survive to the client.

        If it does, the post-commit enqueue did not run at all -- which is worth
        seeing precisely because every other signal in the response would still
        read as a clean success.
        """
        self.clear_sync_jobs()
        listing_id = self.insert_listing()
        self.bind_supplier(listing_id)

        entry = self.outcomes(self.review(rv.APPROVE, [listing_id], key="sync-2").get_json())[listing_id]
        self.assertEqual(entry["supplier_sync"], "queued", entry)
        self.assertNotEqual(entry["supplier_sync"], "pending")
        self.assertTrue(self.sync_jobs())

    def test_a_hand_made_product_is_skipped_with_words_not_silence(self):
        self.clear_sync_jobs()
        listing_id = self.insert_listing()

        entry = self.outcomes(self.review(rv.APPROVE, [listing_id], key="sync-3").get_json())[listing_id]
        self.assertEqual(entry["supplier_sync"], "skipped")
        self.assertIn("no supplier to refresh", entry["supplier_sync_note"].lower())
        self.assertEqual(self.sync_jobs(), [])

    def test_rejecting_a_dropshipped_product_queues_nothing(self):
        """§24 is scoped to approvals. A rejected listing cannot be bought, so a
        supplier read against it is quota spent on a page no buyer reaches."""
        self.clear_sync_jobs()
        listing_id = self.insert_listing()
        self.bind_supplier(listing_id)

        entry = self.outcomes(self.review(
            rv.REJECT, [listing_id], key="sync-4",
            reason_code=rv.REASON_CODES[0], note="No.").get_json())[listing_id]
        self.assertEqual(entry["supplier_sync"], "skipped")
        self.assertEqual(self.sync_jobs(), [])

    def test_a_blocked_listing_earns_no_refresh(self):
        """The reviewer's own listing is refused under §18 and never moves, so
        there is nothing newly sellable and nothing to refresh."""
        self.clear_sync_jobs()
        listing_id = self.insert_listing(seller_user_id=REVIEWER)
        self.bind_supplier(listing_id, seller_user_id=REVIEWER)

        body = self.review(rv.APPROVE, [listing_id], key="sync-5").get_json()
        self.assertEqual(body["blocked_count"], 1, body)
        self.assertEqual(self.sync_jobs(), [])

    def test_a_mixed_batch_refreshes_only_the_rows_that_moved(self):
        """§15 again, one layer down. A batch that queued a refresh per *ticked*
        listing rather than per *approved* one would look identical in the
        summary and spend quota on products that were refused."""
        self.clear_sync_jobs()
        approved = self.insert_listing()
        blocked = self.insert_listing(seller_user_id=REVIEWER)
        self.bind_supplier(approved)
        self.bind_supplier(blocked, seller_user_id=REVIEWER)

        body = self.review(rv.APPROVE, [approved, blocked], key="sync-6").get_json()
        self.assertEqual((body["successful_count"], body["blocked_count"]), (1, 1), body)

        resources = {job["resource_id"] for job in self.sync_jobs()}
        self.assertEqual(resources, {f"CJ-PROD-{approved}"})

    def test_a_replayed_batch_does_not_queue_a_second_refresh(self):
        """§17 reaches the supplier too. Two jobs for one approval is two reads
        of the same product, and the second one is bought with quota.
        """
        self.clear_sync_jobs()
        listing_id = self.insert_listing()
        self.bind_supplier(listing_id)

        self.review(rv.APPROVE, [listing_id], key="sync-7")
        first = len(self.sync_jobs())
        self.review(rv.APPROVE, [listing_id], key="sync-7")
        self.assertEqual(len(self.sync_jobs()), first)

    def test_an_approval_still_lands_when_the_supplier_queue_refuses(self):
        """The listing is approved; only the refresh failed. Rolling the verdict
        back would mean a supplier outage silently blocks moderation -- and the
        reviewer, who did nothing wrong, would see their decision vanish.
        """
        self.clear_sync_jobs()
        listing_id = self.insert_listing()
        self.bind_supplier(listing_id)

        from services.business_os.suppliers import worker as supplier_worker

        real_schedule = supplier_worker.schedule

        def _boom(**kwargs):
            raise RuntimeError("supplier queue unavailable")

        supplier_worker.schedule = _boom
        try:
            entry = self.outcomes(self.review(
                rv.APPROVE, [listing_id], key="sync-8").get_json())[listing_id]
        finally:
            supplier_worker.schedule = real_schedule

        self.assertEqual(entry["outcome"], rv.SUCCEEDED)
        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.APPROVED)
        self.assertEqual(entry["supplier_sync"], "failed")
        self.assertIn("supplier queue unavailable", entry["supplier_sync_note"])


if __name__ == "__main__":
    unittest.main()
