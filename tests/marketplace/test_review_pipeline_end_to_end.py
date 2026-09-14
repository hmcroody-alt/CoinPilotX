"""The whole loop, walked once, through the surfaces a human actually touches.

Every other file in this directory proves one hop. The queue page lists rows,
the batch endpoint moves them, the seller payload carries a verdict, the edit
route sends a changed listing back. Each is tested against a fixture written by
hand to look like the output of the hop before it — which means the *seams* are
the one thing nothing checks. A fixture is an assumption about what the previous
stage produces, and thirteen files of correct hops joined by four wrong
assumptions is a pipeline that fails on the first real product while every suite
stays green.

This is the mission's problem statement read as a single sentence and executed:

    a product arrives in review -> a reviewer finds it in the queue -> opens it
    -> rejects it with a reason -> the seller reads that reason -> fixes it ->
    it comes back to the queue as a new revision -> a reviewer approves it ->
    a buyer can reach it

Nothing here is mocked except identity. The rows are read back from the database
between stages rather than assumed, and each stage's *input* is the previous
stage's real output.

Three identities, because the pipeline needs three and conflating any two is how
the seams get hidden:

  * ``SELLER`` drives the merchant routes through ``api_account_user``;
  * ``REVIEWER`` drives the admin page and the batch endpoint;
  * they are deliberately **different users**, which is the only honest way to
    walk this. §18 refuses a verdict where the reviewer is the seller, so a
    single-identity rehearsal could not get past stage four — and the fact that
    the owner-admin of a real store hits exactly that wall is an operational
    finding, not something to design around here.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_review_pipeline_end_to_end.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_pipeline_e2e_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 91201
REVIEWER = 91202
NOW = "2026-09-01T00:00:00"

QUEUE = "/admin/marketplace-command"
DETAIL = "/admin/marketplace-command/listing/{}"
BATCH = "/api/admin/marketplace/review/batch"
SELLER_LIST = "/api/pulse/marketplace/seller/listings"
SELLER_EDIT = "/api/pulse/marketplace/seller/listings/{}"
SELLER_SUBMIT = "/api/pulse/marketplace/seller/listings/{}/submit"

#: Written by the reviewer on a screen that also displays supplier cost, and
#: carrying a number off it. §43's whole point is that this must not reach the
#: merchant, and the pipeline is where that claim is worth re-testing: the note
#: crosses four stages and a serializer between being typed and not being shown.
INTERNAL_NOTE = "Images are stock photos; CJ cost 18.40 leaves 4% margin anyway."


class ReviewPipelineEndToEndTestCase(unittest.TestCase):
    """One listing, walked from arrival to storefront and back again."""

    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_require_admin_api = bot.require_admin_api
        cls._real_require_admin_page = bot.require_admin_page
        cls._real_admin_login_required = bot.admin_login_required
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

        admin = {"id": REVIEWER, "username": "reviewer", "role": "owner",
                 "email": "reviewer@example.com"}
        bot.require_admin_api = lambda permission="users.view": (dict(admin), None)
        bot.require_admin_page = lambda permission="users.view": (dict(admin), None)
        bot.admin_login_required = lambda: dict(admin)
        bot.api_account_user = lambda *a, **k: {"user_id": SELLER, "username": "seller"}

    @classmethod
    def tearDownClass(cls):
        bot.require_admin_api = cls._real_require_admin_api
        bot.require_admin_page = cls._real_require_admin_page
        bot.admin_login_required = cls._real_admin_login_required
        bot.api_account_user = cls._real_account_user

    def setUp(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)",
                        (SELLER, REVIEWER))
            cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                        (SELLER, REVIEWER))
            for user_id, store in ((SELLER, "Lamp Co"), (REVIEWER, "Reviewer Store")):
                cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                            " VALUES (?,?,?)", (user_id, f"user{user_id}", store))
                cur.execute("INSERT INTO marketplace_sellers"
                            " (user_id, display_name, status, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (user_id, store, "approved", NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    # -- harness ---------------------------------------------------------------

    def arrive_in_review(self, **overrides):
        """A product as the import path leaves it: released, undecided.

        Deliberately built here rather than driven through the CJ importer. The
        importer is a stage *before* the one under test and has its own suite;
        what this file needs is its output shape, and inserting it makes the
        starting state legible in one place instead of buried in a fixture two
        modules away.
        """
        row = {
            "seller_user_id": SELLER,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "currency": "USD",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": lifecycle.PENDING_REVIEW,
            "approval_status": lifecycle.PENDING_REVIEW,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 12,
            "review_version": 1,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({', '.join(row)}) "
                    f"VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
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

    def decide(self, action, listing_ids, **extra):
        """A reviewer's click, and an assertion that it was actually accepted.

        The status check lives *here* rather than in the callers on purpose. The
        first run of this file had four tests pass green while every one of
        their decisions was being refused with ``IDEMPOTENCY_KEY_REQUIRED`` --
        including the §18 self-review test, which reported that a reviewer could
        not approve their own product when the truth was only that the request
        never reached the gate. A helper that performs an action and does not
        check that it happened turns every test built on it into a test of the
        starting state. Callers that need the body still get it back.

        The key is fresh per call because §17 replays a repeated one: two
        decisions sharing a key would return the first one's answer, and the
        second listing would silently never move.
        """
        body = {"action": action, "listing_ids": listing_ids,
                "idempotency_key": f"e2e-{uuid.uuid4()}"}
        body.update(extra)
        response = self.client.post(BATCH, data=json.dumps(body),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response

    def buyer_view(self, listing_id):
        """The listing as the publication rules see it, seller row and all.

        ``is_public`` is not a question about the two review columns. It also
        asks whether the *store* is approved and named and whether there is
        stock -- a merchant whose store was suspended has approved listings no
        buyer query returns. So the seller row is joined here rather than the
        field being hand-set to ``"approved"``: stuffing the value this file is
        trying to verify is how a fixture quietly becomes the thing under test.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT l.*, ms.status AS seller_status, ms.display_name AS seller_name"
            " FROM marketplace_listings l"
            " LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id"
            " WHERE l.id=?", (listing_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}

    def correct(self, listing_id, **fields):
        """The merchant's half of the loop, which is two requests and not one.

        Worth stating plainly because the first version of this file got it
        wrong and the mistake was invisible: editing a *rejected* listing does
        not send it back. The re-review reset in the edit route fires only for
        a listing that was ``published`` **and** ``approved`` -- a live product
        that changed materially has to return to the queue. A rejected one is
        already out of the queue, so the seller resubmits explicitly, which is
        the right design: a rejection usually takes several edits, and
        re-queueing on each keystroke would show reviewers half-fixed drafts.

        The consequence is that the return leg is a *button*, and it is the only
        thing standing between "we told the seller what was wrong" and the
        product actually coming back. Both legs are walked here so that a
        regression in either one fails this test.
        """
        edited = self.client.patch(SELLER_EDIT.format(listing_id),
                                   data=json.dumps(fields),
                                   content_type="application/json")
        self.assertEqual(edited.status_code, 200, edited.get_data(as_text=True))
        sent = self.client.post(SELLER_SUBMIT.format(listing_id),
                                data=json.dumps({}), content_type="application/json")
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True))
        return sent

    def seller_sees(self, listing_id):
        """The listing as its own merchant's app receives it."""
        items = self.client.get(SELLER_LIST).get_json()["items"]
        for item in items:
            if int(item["id"]) == listing_id:
                return item
        self.fail(f"listing {listing_id} is missing from the seller's own store")

    # -- stage 1-2: it arrives, and a reviewer can find it ----------------------

    def test_an_arrived_product_is_discoverable_in_the_queue(self):
        """§38. The pipeline's first seam, and the one that fails silently: a
        listing in ``pending_review`` that the queue's own filter does not
        select is invisible to the only people who can release it, and the
        merchant is told to wait for a review nobody can perform."""
        listing_id = self.arrive_in_review()
        html = self.client.get(QUEUE).get_data(as_text=True)
        self.assertIn(f"value='{listing_id}'", html)
        # And actionable — an unblocked tick, not a row it can list but refuse.
        self.assertIn(f"value='{listing_id}' data-block=\"\"", html)

    def test_the_reviewer_can_open_it_and_see_the_product(self):
        """§7. The queue says a decision is needed; the detail page is where the
        reviewer gets enough to make one."""
        listing_id = self.arrive_in_review()
        response = self.client.get(DETAIL.format(listing_id))
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Brass desk lamp", html)
        self.assertIn("$24.00", html)

    # -- stage 3: the rejection arc --------------------------------------------

    def test_a_rejection_reaches_the_merchant_as_a_sentence_they_can_act_on(self):
        """The mission's headline, walked rather than unit-tested.

        Four components between the reviewer's click and the merchant's screen:
        the batch endpoint writes the code, the row stores it, the seller SELECT
        names the column, and the serializer strips its neighbours but keeps
        this one. Each is proven alone. This proves the chain.
        """
        listing_id = self.arrive_in_review()
        self.decide(rv.REJECT, [listing_id],
                    reason_code=rv.INVALID_MEDIA, note=INTERNAL_NOTE)
        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.REJECTED)

        verdict = self.seller_sees(listing_id)["review"]
        self.assertTrue(verdict["needs_action"])
        self.assertEqual(verdict["reason_code"], rv.INVALID_MEDIA)
        self.assertEqual(verdict["message"], rv.SELLER_MESSAGES[rv.INVALID_MEDIA])

    def test_the_reviewers_note_does_not_survive_the_journey(self):
        """§43 at pipeline scope. The note is written, stored, read by the
        dossier and rendered on the admin page — four places it legitimately
        lives — and the assertion that matters is about the fifth."""
        listing_id = self.arrive_in_review()
        self.decide(rv.REJECT, [listing_id],
                    reason_code=rv.INVALID_MEDIA, note=INTERNAL_NOTE)

        body = json.dumps(self.seller_sees(listing_id))
        self.assertNotIn(INTERNAL_NOTE, body)
        self.assertNotIn("18.40", body, "supplier cost reached the merchant payload")
        self.assertNotIn("reviewed_by", body, "platform staff identity on a merchant payload")

    # -- stage 4: the seller corrects it, and it comes back --------------------

    def test_a_corrected_listing_returns_to_the_queue_as_a_new_revision(self):
        """§23 through the seam. The edit route bumps and blanks; the queue has
        to select the result. A revision that comes back invisible is the §38
        failure again, reached this time through a working correction — and it
        is worse here, because the seller has done their part and is waiting."""
        listing_id = self.arrive_in_review()
        self.decide(rv.REJECT, [listing_id], reason_code=rv.INVALID_MEDIA,
                    note=INTERNAL_NOTE)

        self.correct(listing_id,
                     cover_image_url="https://cdn.example/lamp-real.jpg",
                     title="Brass desk lamp (real photos)")

        row = self.stored(listing_id)
        self.assertEqual(row["approval_status"], lifecycle.PENDING_REVIEW)
        self.assertEqual(int(row["review_version"]), 2)
        self.assertTrue(lifecycle.awaiting_moderation(row))
        # And a reviewer can reach it again.
        self.assertIn(f"value='{listing_id}'", self.client.get(QUEUE).get_data(as_text=True))

    def test_the_old_rejection_does_not_follow_the_correction(self):
        """The corrected listing must not tell its seller it is still rejected.

        `seller_verdict` gates the code on `decided`, and the edit route blanks
        the column — two independent reasons this is silent. Asserted at the
        merchant's screen because that is where a stale "Rejected · replace the
        images" over a listing they already fixed would actually be read.
        """
        listing_id = self.arrive_in_review()
        self.decide(rv.REJECT, [listing_id], reason_code=rv.INVALID_MEDIA,
                    note=INTERNAL_NOTE)
        self.correct(listing_id, title="Brass desk lamp (real photos)")

        verdict = self.seller_sees(listing_id)["review"]
        self.assertFalse(verdict["needs_action"])
        self.assertEqual(verdict["message"], "")
        self.assertEqual(verdict["reason_code"], "")

    # -- stage 5: approval, and a buyer can finally reach it -------------------

    def test_the_approved_product_becomes_reachable_by_a_buyer(self):
        """§37. The last seam, and the one worth ending on: the pipeline's whole
        purpose is a product a buyer can buy, and every stage before this can be
        correct while the row still fails ``is_public`` on the other axis."""
        listing_id = self.arrive_in_review()
        self.decide(rv.APPROVE, [listing_id])

        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.APPROVED)
        live = self.buyer_view(listing_id)
        self.assertTrue(lifecycle.is_public(live),
                        "approved on the moderation axis but not reachable by a buyer")
        self.assertTrue(rv.publication_readback(live)["live"])

    def test_the_merchant_is_told_their_product_is_through(self):
        """An approval the seller cannot see is the §9 gap wearing the other
        face — they are left refreshing a store that already went live."""
        listing_id = self.arrive_in_review()
        self.decide(rv.APPROVE, [listing_id])

        verdict = self.seller_sees(listing_id)["review"]
        self.assertTrue(verdict["decided"])
        self.assertFalse(verdict["needs_action"])

    def test_the_full_loop_ends_with_the_corrected_product_on_sale(self):
        """Arrival, rejection, correction, re-review, approval — one listing.

        The individual stages above can each pass while the composition fails,
        because every one of them starts from a state this test has to *reach*.
        The revision number is asserted at the end as proof the journey actually
        happened rather than the row having been approved on its first pass.
        """
        listing_id = self.arrive_in_review()

        self.decide(rv.REJECT, [listing_id], reason_code=rv.INVALID_MEDIA,
                    note=INTERNAL_NOTE)
        self.assertTrue(self.seller_sees(listing_id)["review"]["needs_action"])

        self.correct(listing_id, title="Brass desk lamp (real photos)")
        self.assertTrue(lifecycle.awaiting_moderation(self.stored(listing_id)))

        self.decide(rv.APPROVE, [listing_id])

        row = self.buyer_view(listing_id)
        self.assertTrue(lifecycle.is_public(row))
        self.assertEqual(int(row["review_version"]), 2)
        self.assertFalse(self.seller_sees(listing_id)["review"]["needs_action"])

    def test_a_rejection_cannot_be_laundered_by_resubmitting(self):
        """The security half of the resubmit fix, asserted so it stays.

        Letting a rejected listing back into the queue means the submit route no
        longer treats "was rejected" as a refusal. The refusal that must survive
        that is the standing one about the *product*: a prohibited item whose
        seller edits nothing material and taps resubmit has to be refused on its
        copy, not waved through because the only thing stopping it was a verdict
        the resubmission clears.

        Without this, the fix above reads as "rejected products may resubmit"
        and the obvious simplification -- drop the goods-policy call, the
        rejection already covers it -- turns the review queue into a retry loop
        a prohibited listing eventually wins.
        """
        listing_id = self.arrive_in_review(title="Counterfeit Rolex replica")
        self.decide(rv.REJECT, [listing_id],
                    reason_code=rv.PROHIBITED_PRODUCT, note=INTERNAL_NOTE)

        sent = self.client.post(SELLER_SUBMIT.format(listing_id),
                                data=json.dumps({}), content_type="application/json")
        self.assertEqual(sent.status_code, 409, sent.get_data(as_text=True))
        self.assertIn("RESTRICTED_PRODUCT", sent.get_data(as_text=True))
        self.assertEqual(self.stored(listing_id)["approval_status"], lifecycle.REJECTED)

    # -- the wall a single-identity operator hits ------------------------------

    def test_a_reviewer_cannot_walk_this_loop_on_their_own_product(self):
        """§18, asserted here as a *pipeline* fact rather than a unit one.

        This is the operational finding the rehearsal exists to make concrete:
        an owner-admin who imports products into their own store reaches stage
        three and stops. The gate is correct and it is not a bug — but it means
        one person with one account cannot run this pipeline end to end, which
        is exactly the situation a house store is in.

        Asserted so that nobody later "fixes" the blocker by narrowing §18 and
        discovers the loosening only when a moderator approves their own goods.
        """
        mine = self.arrive_in_review(seller_user_id=REVIEWER)
        self.decide(rv.APPROVE, [mine])
        self.assertEqual(self.stored(mine)["approval_status"], lifecycle.PENDING_REVIEW,
                         "a reviewer published their own listing")


if __name__ == "__main__":
    unittest.main()
