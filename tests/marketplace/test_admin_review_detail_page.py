"""§7 — the review detail page, rendered.

``test_review_inspection.py`` proves the dossier is correct as a data structure.
That is half of it. The other half is that a page assembles the structure without
losing anything on the way to the screen, and the failures there are specific:

  * **a NameError in a 200-line f-string chain.** ``py_compile`` is happy with a
    reference to a helper that does not exist. The page 500s on load. Only
    rendering it catches that;
  * **a section that renders empty because the data was read with the wrong
    accessor.** This already happened once in this feature —
    ``getattr(evaluate(...), "decision", "")`` on a dict compiles, never raises,
    and blanks the policy panel on exactly the prohibited products it exists to
    explain;
  * **the page offering a decision the endpoint refuses.** Twice now on two
    different surfaces in this codebase. The assertion here is that a disabled
    button and a ``block_reason`` are the same fact;
  * **§43 leaking supplier cost.** The dossier namespaces it; the *page* is where
    it turns into HTML, and HTML has no namespaces. The check is that the
    admin-only page shows it and no seller-facing surface can be built from the
    same call.

Run standalone — this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_admin_review_detail_page.py
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_detail_page_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 79301
REVIEWER = 79302
NOW = "2026-09-01T00:00:00"


def url(listing_id):
    return f"/admin/marketplace-command/listing/{listing_id}"


class AdminReviewDetailPageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_require_admin_page = bot.require_admin_page
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.require_admin_page = cls._real_require_admin_page

    def setUp(self):
        self.sign_in(REVIEWER)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings")
        for table in ("marketplace_product_media", "marketplace_listing_variants"):
            try:
                cur.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                    (SELLER, REVIEWER))
        for user_id, store in ((SELLER, "Lamp Co"), (REVIEWER, "Reviewer's Own Store")):
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                        " VALUES (?,?,?)", (user_id, f"user{user_id}", store))
            cur.execute("INSERT INTO marketplace_sellers (user_id, display_name, status,"
                        " verification_status, risk_score, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (user_id, store, "approved", "verified", 3, NOW, NOW))
        conn.commit()
        conn.close()

    # -- harness ---------------------------------------------------------------

    def sign_in(self, admin_id):
        def _require_admin_page(permission="users.view"):
            return {"id": admin_id, "username": f"admin{admin_id}",
                    "email": f"admin{admin_id}@example.com", "role": "owner"}, None
        bot.require_admin_page = _require_admin_page

    def insert_listing(self, seller_user_id=SELLER, **overrides):
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade and a dimmer.",
            "category": "Home",
            "price_label": "$24.00",
            "currency": "USD",
            "status": lifecycle.PENDING_REVIEW,
            "approval_status": lifecycle.PENDING_REVIEW,
            "product_type": "physical",
            "delivery_type": "standard_shipping",
            "estimated_delivery": "5-9 days",
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

    def add_variant(self, listing_id, **overrides):
        row = {
            "listing_id": listing_id,
            "seller_user_id": SELLER,
            "variant_key": "default",
            "sku": "LAMP-1",
            "currency": "USD",
            "price_cents": 2400,
            "cost_cents": 900,
            "stock_quantity": 12,
            "stock_state": "in_stock",
            "stock_synced_at": NOW,
            "position": 0,
            "status": "active",
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"INSERT INTO marketplace_listing_variants ({cols}) VALUES ({marks})",
                     tuple(row.values()))
        conn.commit()
        conn.close()

    def add_media(self, listing_id, **overrides):
        row = {
            "product_id": listing_id,
            "merchant_id": SELLER,
            "media_type": "image",
            "media_url": "https://cdn.example/lamp.jpg",
            "thumbnail_url": "https://cdn.example/lamp-t.jpg",
            "position": 0,
            "is_cover": 1,
            "moderation_status": "approved",
            "created_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"INSERT INTO marketplace_product_media ({cols}) VALUES ({marks})",
                     tuple(row.values()))
        conn.commit()
        conn.close()

    def load(self, listing_id, expect=200):
        response = self.client.get(url(listing_id))
        self.assertEqual(response.status_code, expect,
                         response.get_data(as_text=True)[:2000])
        return response.get_data(as_text=True)

    def button(self, html, action):
        # Matched without pinning attribute order: the verdict buttons carry a
        # weight class now, and a test that breaks when an unrelated attribute
        # is added in front of the one it cares about is a test that punishes
        # every future edit to this markup.
        match = re.search(r"<button[^>]*data-detail-action='"
                          + re.escape(action) + r"'([^>]*)>", html)
        self.assertIsNotNone(match, f"no {action!r} button on the detail page")
        return match.group(1)

    # -- it renders ------------------------------------------------------------

    def test_the_detail_page_renders_instead_of_five_hundreding(self):
        """A referenced-but-undefined helper inside a long string chain compiles
        and then 500s on load. Nothing short of a request proves otherwise."""
        listing_id = self.insert_listing()
        self.add_variant(listing_id)
        self.add_media(listing_id)
        html = self.load(listing_id)
        for heading in ("Decision", "Needs a look", "Gallery", "Seller",
                        "Fulfilment", "Safety", "Pricing and supplier cost"):
            self.assertIn(heading, html, f"missing the {heading!r} section")

    def test_a_listing_that_does_not_exist_says_so_under_a_404(self):
        html = self.load(999999, expect=404)
        self.assertIn("does not exist", html)
        self.assertIn("/admin/marketplace-command", html)

    def test_a_listing_with_no_variants_or_media_still_renders(self):
        """The listings most likely to have neither are the stranded imports this
        queue exists to clear, so this is the common case, not the edge."""
        listing_id = self.insert_listing()
        html = self.load(listing_id)
        self.assertIn("No media on this listing.", html)
        self.assertIn("no supplier cost is recorded", html)

    def test_the_queue_row_links_to_the_detail_page(self):
        """A page nothing links to is a page that does not exist — the exact
        defect that made the mission brief say there was no review queue."""
        listing_id = self.insert_listing()
        response = self.client.get("/admin/marketplace-command")
        self.assertEqual(response.status_code, 200)
        self.assertIn(url(listing_id), response.get_data(as_text=True))

    # -- §31/§1: the page offers only what the endpoint would take --------------

    def test_your_own_listing_offers_no_verdict_at_all(self):
        """§18 is per-*reviewer*, not per-action: every verdict is refused, not
        just Approve. Rejecting your own listing is the same conflict wearing the
        other hat — it is how you bury a listing you decided you regret without
        it appearing in anyone's queue. So the page offers nothing here."""
        listing_id = self.insert_listing(seller_user_id=REVIEWER)
        html = self.load(listing_id)
        for action in rv.ACTIONS:
            self.assertIn("disabled", self.button(html, action), action)
            self.assertIn("cannot decide their own listing",
                          self.button(html, action), action)

    def test_a_prohibited_product_loses_approve_and_keeps_the_rest(self):
        """§34, which is the asymmetry §18 does not have. A prohibited listing
        must stay decidable by *somebody* or the queue accumulates rows no
        reviewer is permitted to clear — the backlog it was built to end."""
        listing_id = self.insert_listing(title="Case of whisky", category="Alcohol")
        html = self.load(listing_id)
        self.assertIn("disabled", self.button(html, rv.APPROVE))
        for action in (rv.REJECT, rv.REQUEST_CHANGES, rv.RESTRICT):
            self.assertNotIn("disabled", self.button(html, action), action)

    def test_every_button_state_matches_what_block_reason_says(self):
        """Not "a disabled attribute appears somewhere" — the same function, per
        action. A page that computed its own eligibility would be the third copy
        of this rule in the codebase."""
        listing_id = self.insert_listing(title="Case of whisky", category="Alcohol")
        html = self.load(listing_id)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM marketplace_listings WHERE id=?",
                                (listing_id,)).fetchone())
        conn.close()
        for action in rv.ACTIONS:
            expected = rv.block_reason(row, action, reviewer_id=REVIEWER)
            rendered = self.button(html, action)
            self.assertEqual(bool(expected), "disabled" in rendered,
                             f"{action}: block_reason={expected!r} markup={rendered!r}")

    def test_the_decision_posts_the_same_endpoint_as_the_queue(self):
        """§1. One decision authority. A second single-listing write path is how
        the audit trail, the idempotency ledger and the verdict rule drift."""
        listing_id = self.insert_listing()
        html = self.load(listing_id)
        self.assertIn("/api/admin/marketplace/review/batch", html)
        # And no form posting anywhere else.
        self.assertNotIn("<form method='post'", html)

    # -- what the reviewer is actually shown -----------------------------------

    def test_margin_is_on_the_page_and_an_unknown_cost_is_not_shown_as_zero(self):
        """A missing supplier cost rendered as $0.00 reports perfect margin on
        the listings with the thinnest data."""
        priced = self.insert_listing(title="Priced lamp")
        self.add_variant(priced, price_cents=2400, cost_cents=900)
        html = self.load(priced)
        self.assertIn("62.5%", html)

        unpriced = self.insert_listing(title="Costless lamp")
        self.add_variant(unpriced, price_cents=2400, cost_cents=None)
        html = self.load(unpriced)
        self.assertNotIn("100.0%", html)
        self.assertIn("margin cannot be checked", html)

    def test_the_gallery_says_how_much_of_it_a_buyer_will_see(self):
        listing_id = self.insert_listing()
        self.add_media(listing_id)
        self.add_media(listing_id, position=1, is_cover=0, moderation_status="rejected")
        html = self.load(listing_id)
        self.assertIn("2 item(s)", html)
        self.assertIn("1 rejected", html)
        self.assertIn("Some media was rejected", html)

    def test_the_policy_panel_names_the_rule_rather_than_rendering_blank(self):
        """The `getattr` on a dict bug, caught at the surface a human reads."""
        listing_id = self.insert_listing(title="Case of whisky", category="Alcohol")
        html = self.load(listing_id)
        self.assertIn("PROHIBITED", html)

    def test_the_page_forecasts_whether_approving_achieves_anything(self):
        """§37 asks after the write. A reviewer about to approve a listing that
        will stay invisible should not have to decide in order to find out."""
        healthy = self.insert_listing()
        self.assertIn("would make it visible to buyers", self.load(healthy))

        stuck = self.insert_listing(title="Out of stock lamp", quantity=0)
        html = self.load(stuck)
        self.assertIn("would <strong>not</strong> make it visible", html)
        self.assertIn("The decision would still be recorded", html)

    def test_seller_standing_comes_from_the_seller_record_not_the_listing(self):
        listing_id = self.insert_listing()
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE marketplace_sellers SET status='suspended', risk_score=88"
                     " WHERE user_id=?", (SELLER,))
        conn.commit()
        conn.close()
        html = self.load(listing_id)
        self.assertIn("suspended", html)
        self.assertIn("Seller risk score is 60 or above", html)

    # -- §43 --------------------------------------------------------------------

    def test_supplier_cost_appears_here_and_is_labelled_internal(self):
        """It is legitimate on this page and forbidden everywhere a seller or a
        buyer can reach. Labelling it is what stops the next person copying the
        section into a seller dashboard."""
        listing_id = self.insert_listing()
        self.add_variant(listing_id, cost_cents=900)
        html = self.load(listing_id)
        self.assertIn("Supplier cost", html)
        self.assertIn("never sent to a seller or a buyer", html)

    def test_the_dossier_keeps_cost_under_one_key_so_a_leak_is_a_grep(self):
        """The page is allowed to print it. Any *other* caller assembling a
        payload from `inspection()` has to reach into a single named section to
        do so, which is a reviewable act rather than an accident."""
        listing_id = self.insert_listing()
        self.add_variant(listing_id, cost_cents=900)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM marketplace_listings WHERE id=?",
                                (listing_id,)).fetchone())
        variants = [dict(r) for r in conn.execute(
            "SELECT * FROM marketplace_listing_variants WHERE listing_id=?", (listing_id,))]
        conn.close()
        dossier = rv.inspection(row, variants=variants, reviewer_id=REVIEWER)
        public = {k: v for k, v in dossier.items() if k != rv.INTERNAL_SECTION}
        self.assertNotIn("cost", repr(public).lower())
        self.assertEqual(
            dossier[rv.INTERNAL_SECTION]["variants"][0]["cost_cents"], 900)


if __name__ == "__main__":
    unittest.main()
