"""``GET /api/pulse/marketplace/listings/<id>`` — the route a product link needs.

Before this route existed every buyer-facing marketplace read returned a *list*,
and ``MarketplaceProductScreen`` could only render a listing that arrived whole
in its navigation params. Six call sites navigate with an id alone — the four
commerce discovery surfaces, the Page product block and the seller store — and
all six rendered "This item is no longer available" for listings that were on
sale. So the cases pinned here are the ones that decide whether a tap on a
product reaches the product.

Visibility is the same ``public_sql`` gate search uses, with one addition: the
owner. Those are the two halves worth testing separately, because getting the
owner clause wrong in the other direction — dropping ``seller_user_id`` from the
comparison — would hand every buyer every draft in the table.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db.

Run: python3 -m pytest tests/test_marketplace_listing_detail.py
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mkt_listing_detail_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


SELLER = 94501
BUYER = 94502
NOW = "2026-09-01T00:00:00"


def _account(user_id, username):
    return {"user_id": user_id, "username": username, "email": f"{username}@example.com"}


class MarketplaceListingDetailTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def login(self, user_id, username):
        bot.api_account_user = lambda *args, **kwargs: _account(user_id, username)

    def logout(self):
        bot.api_account_user = lambda *args, **kwargs: None

    def setUp(self):
        self.login(BUYER, "detail_buyer")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)", (SELLER, BUYER))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)", (SELLER, BUYER))
        for user_id, username in ((SELLER, "detail_seller"), (BUYER, "detail_buyer")):
            cur.execute(
                "INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                (user_id, username, username),
            )
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (SELLER, "Detail Store", "approved", NOW, NOW),
        )
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def make_listing(self, *, status="published", approval_status="approved", seller_user_id=SELLER):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, description, short_description, category, price_label, currency, "
            " quantity, product_type, listing_type, status, approval_status, cover_image_url, "
            " safety_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                seller_user_id, "Detail Widget", "A widget described well enough to sell.",
                "A widget", "Education", "$24.00", "USD", 5, "physical", "physical",
                status, approval_status, "/static/uploads/pulse_media/widget.jpg",
                7, NOW, NOW,
            ),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def get(self, listing_id):
        return self.client.get(f"/api/pulse/marketplace/listings/{listing_id}")

    # -- the route exists and answers ----------------------------------------

    def test_a_buyer_holding_only_an_id_gets_the_listing(self):
        """The whole point: an id in, a renderable listing out."""
        listing_id = self.make_listing()
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body.get("ok"))
        item = body.get("item") or {}
        self.assertEqual(item.get("listing_id"), listing_id)
        self.assertEqual(item.get("id"), listing_id)
        self.assertEqual(item.get("title"), "Detail Widget")
        self.assertEqual(item.get("price_label"), "$24.00")
        self.assertEqual(item.get("seller_user_id"), SELLER)
        self.assertTrue(item.get("cover_image_url"))

    def test_the_payload_is_also_under_listing_for_a_client_reading_either_key(self):
        """Both keys carry the same document; neither client has to guess."""
        listing_id = self.make_listing()
        body = self.get(listing_id).get_json()
        self.assertEqual(body.get("item"), body.get("listing"))

    def test_reviewer_only_columns_do_not_reach_a_buyer(self):
        """``safety_score`` is a moderation signal and is stored non-zero here.

        The row is spread into the payload by the shared serializer, so a
        reviewer-only column is removed by the serializer's own deny-list rather
        than by this route remembering to omit it. Asserted here because this is
        a new way for that row to reach a phone.
        """
        listing_id = self.make_listing()
        item = self.get(listing_id).get_json().get("item") or {}
        self.assertNotIn("safety_score", item)
        self.assertNotIn("moderation_reason", item)

    # -- visibility -----------------------------------------------------------

    def test_a_listing_that_is_not_public_is_not_readable_by_a_buyer(self):
        for status, approval in (("draft", "approved"), ("published", "pending"), ("paused", "approved")):
            with self.subTest(status=status, approval=approval):
                listing_id = self.make_listing(status=status, approval_status=approval)
                response = self.get(listing_id)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.get_json().get("error_code"), "LISTING_UNAVAILABLE")

    def test_the_owner_can_still_open_their_own_hidden_listing(self):
        """A seller whose listing is paused should see the listing, not a headstone."""
        listing_id = self.make_listing(status="paused")
        self.login(SELLER, "detail_seller")
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.get_json().get("item") or {}).get("listing_id"), listing_id)

    def test_the_owner_clause_does_not_widen_to_everyone(self):
        """The regression that would matter: a viewer id compared against nothing.

        If the owner branch ever stops comparing ``seller_user_id`` to the
        viewer, every signed-in user reads every draft. A buyer seeing a draft is
        the observable form of that, so it is asserted rather than left to the
        shape of the SQL.
        """
        listing_id = self.make_listing(status="draft")
        self.login(BUYER, "detail_buyer")
        self.assertEqual(self.get(listing_id).status_code, 404)

    def test_an_unknown_id_is_a_404_and_not_a_500(self):
        response = self.get(98765432)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json().get("error_code"), "LISTING_UNAVAILABLE")

    def test_a_signed_out_viewer_is_refused_before_any_listing_is_read(self):
        listing_id = self.make_listing()
        self.logout()
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
