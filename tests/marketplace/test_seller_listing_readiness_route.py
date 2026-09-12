"""The readiness verdict on the wire: does the seller get it, and only the seller?

`tests/business_os/test_listing_readiness.py` pins what the engine decides. It
cannot pin any of the three things that only the route can get wrong, which is
what this file is for:

  * the verdict is actually ATTACHED. A perfect engine nobody calls leaves the
    client deriving its own answer, which is the defect being repaired;
  * it is computed from the DATABASE ROW, not from the serialized payload. The
    serializer coerces `quantity` and defaults a blank price, and readiness has
    to see the NULL that separates "no stock tracked" from "none left". Passing
    it the payload instead would compile, pass every engine test, and silently
    reintroduce the exact bug -- so the NULL is asserted end to end, through
    Flask, against a row written as NULL;
  * it does NOT reach a buyer. `pulse_marketplace_listing_payload` also feeds
    the public listing page and `/api/pulse/marketplace/search`, so attaching
    readiness inside the serializer would have published every merchant's
    unfinished listings to strangers. It is attached in the seller route
    instead, and that separation is asserted rather than trusted.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="readiness_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services.business_os.marketplace import listing_readiness as readiness  # noqa: E402

SELLER = 94501
BUYER = 94502
NOW = "2026-09-01T00:00:00"


class SellerListingReadinessRouteTestCase(unittest.TestCase):
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

    def setUp(self):
        bot.api_account_user = lambda *a, **k: {
            "user_id": SELLER, "username": "readiness_seller",
            "email": "readiness_seller@example.com"}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)", (SELLER, BUYER))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)", (SELLER, BUYER))
        for user_id, username in ((SELLER, "readiness_seller"), (BUYER, "readiness_buyer")):
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                        (user_id, username, username))
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", (SELLER, "Readiness Seller", "approved", NOW, NOW))
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def insert_listing(self, **overrides):
        """A listing written straight to the table, so a test can write a NULL.

        Deliberately not via the create route: the route validates, and the
        states this file cares about (a NULL quantity, a blank price) are states
        the route would refuse to create but the table already holds -- rows
        predating the validation, and rows written by the supplier importer.
        """
        row = {
            "seller_user_id": SELLER,
            "title": "Brass desk lamp",
            "description": "A well described listing.",
            "category": "Home",
            "price_label": "$24.00",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": "active",
            "approval_status": "approved",
            "listing_type": "physical",
            "quantity": 40,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({cols}) VALUES ({marks})", tuple(row.values()))
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def seller_item(self, listing_id):
        response = self.client.get("/api/pulse/marketplace/seller/listings")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        items = response.get_json()["items"]
        item = next((i for i in items if int(i.get("id") or 0) == listing_id), None)
        self.assertIsNotNone(item, f"listing {listing_id} missing from the seller's own store")
        return item

    # -- the verdict is on the wire -------------------------------------------

    def test_a_ready_listing_carries_the_verdict(self):
        item = self.seller_item(self.insert_listing())
        self.assertEqual(item["readiness"], {
            "publishable": True, "checkout_ready": True,
            "blockers": [], "warnings": []})

    def test_the_verdict_names_the_gap_the_row_renders_as_silence(self):
        """§12: a listing with no price must say so.

        The row renders a blank `price_label` as nothing at all, which is safe
        from "Free" but tells the merchant nothing. The verdict is where the gap
        gets a name.
        """
        item = self.seller_item(self.insert_listing(price_label=""))
        self.assertEqual(item["readiness"]["blockers"], [readiness.MISSING_PRICE])
        self.assertFalse(item["readiness"]["publishable"])
        # And the listing still says nothing misleading about money.
        self.assertNotIn("$0.00", repr(item.get("price_label")))
        self.assertNotIn("Free", repr(item.get("price_label")))

    # -- the NULL survives the round trip -------------------------------------

    def test_an_untracked_quantity_reaches_the_verdict_as_unknown(self):
        """The regression, asserted through Flask against a real NULL.

        This is the test that fails if the route ever computes readiness from
        the serialized payload instead of the row, or if any layer in between
        reintroduces `x or 0`. The engine's own tests cannot see that mistake:
        they hand `evaluate` a dict directly.
        """
        item = self.seller_item(self.insert_listing(quantity=None))
        self.assertEqual(item["readiness"]["warnings"], [readiness.UNKNOWN_INVENTORY])
        # Unknown does not stop the merchant listing, but it does stop a promise
        # to a buyer.
        self.assertTrue(item["readiness"]["publishable"])
        self.assertFalse(item["readiness"]["checkout_ready"])

    def test_unknown_and_sold_out_stay_different_answers_on_the_wire(self):
        unknown = self.seller_item(self.insert_listing(quantity=None))
        sold_out = self.seller_item(self.insert_listing(quantity=0))
        self.assertEqual(unknown["readiness"]["warnings"], [readiness.UNKNOWN_INVENTORY])
        self.assertEqual(sold_out["readiness"]["warnings"], [readiness.OUT_OF_STOCK])
        self.assertNotEqual(unknown["readiness"]["warnings"], sold_out["readiness"]["warnings"])

    def test_the_row_really_did_hold_a_null(self):
        """Guards the test above from passing for the wrong reason.

        If the column ever gained a NOT NULL default, `quantity=None` would be
        stored as 0 and the unknown test would be asserting against a zero that
        the engine happens to call unknown for some other reason.
        """
        listing_id = self.insert_listing(quantity=None)
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                              (listing_id,)).fetchone()[0]
        conn.close()
        self.assertIsNone(stored, "the column no longer stores NULL; the unknown/empty "
                                  "distinction has nowhere to live")

    # -- and it stays the merchant's business ---------------------------------

    def test_a_buyer_facing_listing_carries_no_verdict(self):
        """§64 in spirit: readiness is merchant-internal.

        The public search endpoint shares the serializer with the seller route.
        A verdict there would tell every shopper which sellers have unpriced
        drafts and empty shelves.
        """
        self.insert_listing(title="Brass desk lamp public", quantity=0, price_label="")
        response = self.client.get("/api/pulse/marketplace/search")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        items = response.get_json().get("items") or []
        for item in items:
            self.assertNotIn("readiness", item,
                             "the public search endpoint is publishing merchant readiness")

    def test_the_verdict_carries_no_money_or_supplier_facts(self):
        item = self.seller_item(self.insert_listing(quantity=0, price_label=""))
        verdict = item["readiness"]
        self.assertEqual(set(verdict), {"publishable", "checkout_ready", "blockers", "warnings"})
        flat = repr(verdict).lower()
        for word in ("cost", "margin", "supplier", "token", "openid", "connection", "cents"):
            self.assertNotIn(word, flat, f"{word!r} has no business in a readiness verdict")

    # -- every row gets one ---------------------------------------------------

    def test_every_listing_in_the_store_carries_a_verdict(self):
        """A client that must ask "did this one get a verdict?" will grow a
        fallback, and the fallback is the client-side derivation being retired."""
        for overrides in ({}, {"quantity": None}, {"quantity": 0}, {"price_label": ""},
                          {"title": ""}, {"listing_type": "digital", "quantity": None}):
            self.insert_listing(**overrides)
        response = self.client.get("/api/pulse/marketplace/seller/listings")
        items = response.get_json()["items"]
        self.assertGreaterEqual(len(items), 6)
        for item in items:
            self.assertIn("readiness", item, f"listing {item.get('id')} has no verdict")
            self.assertEqual(set(item["readiness"]),
                             {"publishable", "checkout_ready", "blockers", "warnings"})


if __name__ == "__main__":
    unittest.main()
