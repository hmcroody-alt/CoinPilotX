"""§23 — what a listing carries when a material edit sends it back to review.

Sending it back was already wired. ``requires_rereview`` is tested where it
lives, the seller edit route calls it, and a live approved listing whose title
or price changes does return to ``pending_review``. That much worked.

What did not work is what the listing looked like when it got there. The two
*explicit* resubmission routes — ``/listings/<id>/submit`` and the seller batch
resume — both bump ``review_version`` and blank the stored verdict text. The
material-edit path did neither, so a product came back to the queue wearing the
approval of a version that no longer existed:

    reviewer approves revision 3
    seller rewrites the title and triples the price
    queue shows: revision 3, last decision "Looked fine to me." — approved

A reviewer opening that sees their own signature on a product they have never
read. The cheapest action available — recognise it, wave it through — is also
the wrong one, and nothing on the screen argues against it. That is the exact
failure re-review exists to prevent, reached *through* a working re-review.

Three fields are deliberately **not** reset, matching what the submit routes
already do: ``reviewed_by``, ``reviewed_at`` and ``approved_at``. Those record
when the product was last decided, which stays true. The revision number is
what says the decision was about a different version — which is why the bump
is the load-bearing half, not the blanking.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_listing_rereview_reset.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="rereview_reset_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 88801
PRIOR_REVIEWER = 88899
NOW = "2026-09-01T00:00:00"

EDIT = "/api/pulse/marketplace/seller/listings/{}"


class ListingRereviewResetTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()
        bot.api_account_user = lambda *a, **k: {"user_id": SELLER, "username": "seller"}

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def setUp(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id=?", (SELLER,))
            cur.execute("DELETE FROM marketplace_sellers WHERE user_id=?", (SELLER,))
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                        " VALUES (?,?,?)", (SELLER, "seller88801", "Lamp Co"))
            cur.execute("INSERT INTO marketplace_sellers"
                        " (user_id, display_name, status, created_at, updated_at)"
                        " VALUES (?,?,?,?,?)", (SELLER, "Lamp Co", "approved", NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    # -- harness ---------------------------------------------------------------

    def insert_listing(self, **overrides):
        """A listing that is live and was approved once, at revision 3."""
        row = {
            "seller_user_id": SELLER,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "currency": "USD",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": lifecycle.PUBLISHED,
            "approval_status": lifecycle.APPROVED,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 5,
            "review_version": 3,
            "reviewed_by": PRIOR_REVIEWER,
            "reviewed_at": NOW,
            "approved_at": NOW,
            "published_at": NOW,
            "moderation_reason": "Looked fine to me.",
            "moderation_category": "QUALITY",
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

    def edit(self, listing_id, **fields):
        return self.client.patch(EDIT.format(listing_id), data=json.dumps(fields),
                                 content_type="application/json")

    def stored(self, listing_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM marketplace_listings WHERE id=?",
                           (listing_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}

    # -- the material edit -----------------------------------------------------

    def test_a_material_edit_arrives_at_a_new_revision_number(self):
        """The load-bearing half. Without it the queue cannot tell the reviewer
        that this is not the version they already looked at."""
        listing_id = self.insert_listing()
        response = self.edit(listing_id, title="Brass desk lamp XL", price_label="$99.00")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        row = self.stored(listing_id)
        self.assertEqual(row["approval_status"], lifecycle.PENDING_REVIEW)
        self.assertEqual(int(row["review_version"]), 4)

    def test_the_previous_verdict_text_does_not_follow_the_edit_into_the_queue(self):
        """A reason written about the old version is a sentence about a product
        that no longer exists, sitting in the field the review UI reads as the
        current one."""
        listing_id = self.insert_listing()
        self.edit(listing_id, title="Brass desk lamp XL")

        row = self.stored(listing_id)
        self.assertEqual(row["moderation_reason"], "")
        self.assertEqual(row["moderation_category"], "")

    def test_who_decided_it_last_and_when_are_kept(self):
        """Not an oversight — it matches both submit routes.

        "This was approved by X on the 1st" stays true after the seller edits
        it. What changes is which version that sentence is about, and the
        revision number is what carries that. Blanking the history instead would
        delete the §19 answer to "who approved this before".
        """
        listing_id = self.insert_listing()
        self.edit(listing_id, title="Brass desk lamp XL")

        row = self.stored(listing_id)
        self.assertEqual(int(row["reviewed_by"]), PRIOR_REVIEWER)
        self.assertEqual(row["reviewed_at"], NOW)
        self.assertEqual(row["approved_at"], NOW)

    def test_the_edited_listing_stops_being_visible_to_buyers(self):
        """§37 in the other direction. A live product whose price changed must
        not keep selling at the new price on the old approval."""
        listing_id = self.insert_listing()
        self.edit(listing_id, price_label="$99.00")

        row = self.stored(listing_id)
        self.assertEqual(row["status"], lifecycle.PENDING_REVIEW)
        self.assertIsNone(row["published_at"])
        self.assertFalse(rv.publication_readback(dict(row, seller_status="approved"))["live"])

    def test_the_edited_listing_is_waiting_for_a_decision(self):
        """§38. Back in review and *discoverable* as such — a row that reads as
        pending on one axis and released on the other is the zombie state."""
        listing_id = self.insert_listing()
        self.edit(listing_id, title="Brass desk lamp XL")
        self.assertTrue(lifecycle.awaiting_moderation(self.stored(listing_id)))

    # -- an edit that changes nothing material ---------------------------------

    def test_a_cosmetic_edit_leaves_the_listing_live_and_the_revision_alone(self):
        """``seller_notes`` is not a MATERIAL_FIELD. Bumping the revision for it
        would put a live product back in the queue for a note no buyer reads,
        and a queue that fills with those is a queue nobody drains.
        """
        listing_id = self.insert_listing()
        response = self.edit(listing_id, seller_notes="Reorder from the Tuesday pallet.")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        row = self.stored(listing_id)
        self.assertEqual(row["status"], lifecycle.PUBLISHED)
        self.assertEqual(row["approval_status"], lifecycle.APPROVED)
        self.assertEqual(int(row["review_version"]), 3)
        self.assertEqual(row["moderation_reason"], "Looked fine to me.")

    def test_a_material_field_rewritten_to_the_same_value_is_not_a_change(self):
        """The republish-by-accident case. Saving a form without touching the
        title must not cost the seller their listing's visibility."""
        listing_id = self.insert_listing()
        self.edit(listing_id, title="Brass desk lamp", price_label="$24.00")

        row = self.stored(listing_id)
        self.assertEqual(row["status"], lifecycle.PUBLISHED)
        self.assertEqual(int(row["review_version"]), 3)

    def test_a_draft_edit_does_not_burn_a_revision(self):
        """A draft was never reviewed, so there is no decision to invalidate and
        no reviewer to inform. Counting drafts would make the revision number
        mean "times saved" rather than "versions submitted"."""
        listing_id = self.insert_listing(
            status="draft", approval_status="draft", published_at=None,
            reviewed_by=None, reviewed_at=None, approved_at=None,
            moderation_reason="", moderation_category="")
        self.edit(listing_id, title="Brass desk lamp XL")

        row = self.stored(listing_id)
        self.assertEqual(row["status"], "draft")
        self.assertEqual(int(row["review_version"]), 3)

    # -- the reviewer actually sees the new number ------------------------------

    def test_the_dossier_reports_the_revision_the_edit_produced(self):
        """The bump is worth nothing if the review surface reads the old value.
        This is the assertion that ties the column to the screen."""
        listing_id = self.insert_listing()
        self.edit(listing_id, title="Brass desk lamp XL", price_label="$99.00")

        dossier = rv.inspection(self.stored(listing_id), reviewer_id=1)
        self.assertEqual(dossier["review_version"], 4)
        self.assertEqual(dossier["moderation_reason"], "")

    # -- the three resubmission sites must agree --------------------------------

    def test_every_resubmission_site_bumps_and_blanks_together(self):
        """§1, enforced on source, because this is how the sites drifted apart.

        Two routes reset the verdict and bumped the revision; a third moved the
        listing and did neither, and nothing anywhere compared them. Half a
        reset is worse than none: a blanked reason with a stale revision says
        "never reviewed" about a product that was, and a bumped revision with a
        stale reason says the opposite.

        Anchored on the bump rather than on a route name, so a fourth
        resubmission path added later is checked the day it is written.
        """
        source = open(bot.__file__, encoding="utf-8").read()
        statements = re.findall(
            r"UPDATE marketplace_listings.*?(?=\"\"\"|WHERE id=\?)", source, re.S)
        bumps = [sql for sql in statements if "review_version" in sql
                 and "COALESCE(review_version,0)+1" in sql]
        self.assertGreaterEqual(len(bumps), 3, "the known resubmission sites are gone")
        for sql in bumps:
            self.assertIn("moderation_reason=", sql,
                          "a site bumps the revision without clearing the old verdict")
            self.assertIn("moderation_category=", sql)


if __name__ == "__main__":
    unittest.main()
