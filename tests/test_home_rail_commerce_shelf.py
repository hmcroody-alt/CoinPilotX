"""``pulse_rail_shop_html`` -- the Home rail's "From the Marketplace" shelf.

## The bug this closes

The shelf had its own copy of the publication predicate:

    WHERE l.status IN ('active','approved')
      AND COALESCE(l.approval_status,'approved') IN ('approved','review_ready','')

No row in production has ever held ``active`` or ``approved`` in ``status``. The
publication vocabulary is ``published``/``live``/``active``
(``marketplace_listing_lifecycle.PUBLIC_STATUSES``) and every writer in the tree
uses ``published``. Measured against production on 2026-09-30: 196 rows
``published``, 4 ``seller_deleted``, 2 ``review_ready``, and zero matching the
copy above. So the shelf fell to its ``if not listings: return ""`` branch on
every render since it shipped, and the Home commerce card has never once been
visible to a reader.

## Why it was silent

``return ""`` is also what the shelf correctly returns when a seller genuinely
has nothing to show, and when the web-storefront import is unavailable. An empty
commerce card and a switched-off commerce card render identically -- there is no
third rendering that says "this predicate matched nothing" -- so a broken
eligibility clause produces no error, no log line and no visual difference from
intended behaviour. That is the failure mode this file exists to make loud: the
first test below fails with the old predicate and passes with the shared one, on
a listing shaped exactly like the 196 real ones.

## Why the assertions are about rows, not about SQL

Asserting that the function's query string contains
``lifecycle.public_sql('l','ms')`` would pass against a predicate that is
textually shared and semantically wrong for this table, and would keep passing if
``public_sql`` itself drifted. These tests seed listings in the five shapes the
predicate decides between and assert which ones reach the shelf, so they bind to
reachability rather than to spelling.

The seller-not-approved and out-of-stock cases are not redundant with
``tests/test_marketplace_listing_lifecycle.py``: they pin that *this* surface
asks those two questions at all. The predicate it replaced asked neither, so a
suspended seller's stock-zero listing was eligible for Home on the one axis the
old clause did check.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db.

Run: python3 -m pytest tests/test_home_rail_commerce_shelf.py
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="home_rail_shop_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

SELLER = 95301
NOW = "2026-09-01T00:00:00"
TITLE = "Linen Duvet Cover Set"


class HomeRailCommerceShelfTestCase(unittest.TestCase):
    """What reaches the shelf, by listing and seller shape."""

    @classmethod
    def setUpClass(cls):
        # `init_db()` returns early on a process global, so a second suite in the
        # same pytest process would find this file's database empty. See the long
        # note in tests/test_marketplace_public_pages.py.
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        bot.INIT_DB_COMPLETED = False
        bot.init_db()

    def setUp(self):
        # On SQLite `services.db.connect()` re-reads DATABASE_URL per call, and
        # pytest imports every selected module before running anything, so the
        # last suite imported owns the database unless each one re-pins it.
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id=?", (SELLER,))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id=?", (SELLER,))
        cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                    (SELLER, "home_rail_seller", "home_rail_seller"))
        conn.commit()
        conn.close()

    # -- fixtures -------------------------------------------------------------

    def seed_seller(self, *, display_name="M&W Store", status="approved"):
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (SELLER, display_name, status, NOW, NOW),
        )
        conn.commit()
        conn.close()

    def seed_listing(self, *, status="published", approval_status="approved",
                     quantity=12, product_type="physical", title=TITLE):
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, description, short_description, category, price_label,"
            " currency, quantity, product_type, listing_type, status, approval_status,"
            " cover_image_url, safety_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SELLER, title, "A washed European linen duvet cover.", "Washed linen duvet set",
             "Home", "$465.74", "USD", quantity, product_type, product_type, status,
             approval_status, "https://cdn.example/bed.jpg", 7, NOW, NOW),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def render(self):
        """The shelf's HTML, through a cursor shaped like the one Home passes."""
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return bot.pulse_rail_shop_html(conn.cursor())
        finally:
            conn.close()

    # -- the regression -------------------------------------------------------

    def test_a_published_approved_listing_reaches_the_shelf(self):
        """The whole mission: this shape is the 196 real rows, and it rendered "".

        ``published`` is what every writer in the tree sets. The predicate this
        replaced accepted only ``active`` and ``approved``, so this assertion is
        the one that fails before the change and passes after it.
        """
        self.seed_seller()
        self.seed_listing()
        markup = self.render()
        self.assertIn(TITLE, markup)
        self.assertIn("From the Marketplace", markup)

    def test_the_shelf_is_empty_when_no_listing_qualifies(self):
        """``""`` is still the answer when nothing is reachable, not a stub card.

        Pinned so the fix cannot be read as "always render the card": the shelf
        advertising a product the shopper cannot reach is the defect the
        docstring's eligibility rule exists to prevent, and it is worse than an
        absent card.
        """
        self.seed_seller()
        self.assertEqual(self.render(), "")

    # -- the four conditions the predicate decides between --------------------

    def test_a_listing_still_in_review_is_withheld(self):
        """``review_ready`` on both axes -- two production rows look like this."""
        self.seed_seller()
        self.seed_listing(status="review_ready", approval_status="review_ready")
        self.assertEqual(self.render(), "")

    def test_a_published_listing_awaiting_moderation_is_withheld(self):
        """Publishing is the merchant's act; approving is the moderator's.

        ``suppliers.drafts.publish`` sets ``status='published'`` and deliberately
        leaves moderation untouched, so this shape exists in quantity on the
        dropship path. The clause this replaced accepted
        ``approval_status='review_ready'`` and the empty string, which would have
        advertised an unreviewed supplier import on Home.
        """
        self.seed_seller()
        self.seed_listing(approval_status="review_ready")
        self.assertEqual(self.render(), "")

    def test_a_listing_whose_seller_is_not_approved_is_withheld(self):
        """A condition the old clause did not check at all."""
        self.seed_seller(status="suspended")
        self.seed_listing()
        self.assertEqual(self.render(), "")

    def test_a_physical_listing_with_no_stock_is_withheld(self):
        """Also unchecked before: Home must not advertise what cannot be bought."""
        self.seed_seller()
        self.seed_listing(quantity=0)
        self.assertEqual(self.render(), "")

    def test_an_intangible_listing_needs_no_stock(self):
        """The stock rule exempts the types that have no inventory to run out of."""
        self.seed_seller()
        self.seed_listing(quantity=0, product_type="digital")
        self.assertIn(TITLE, self.render())

    # -- the privacy rule the docstring turns on ------------------------------

    def test_a_listing_whose_seller_has_no_store_name_is_withheld(self):
        """Excluded, rather than labelled with the account holder's legal name.

        ``business_name`` is the registered legal name and for a sole trader it is
        usually their own; a nameless seller is not publishable at all, so the
        shelf must drop the row rather than invent an identity for it.
        """
        self.seed_seller(display_name="   ")
        self.seed_listing()
        self.assertEqual(self.render(), "")

    # -- the cap the layout expresses -----------------------------------------

    def test_the_shelf_shows_at_most_three_products(self):
        """Commerce gets one card and three rows; the cap is the frequency rule."""
        self.seed_seller()
        for index in range(5):
            self.seed_listing(title=f"Rail Product {index}")
        markup = self.render()
        self.assertEqual(markup.count("desktop-intel-row"), 3)


if __name__ == "__main__":
    unittest.main()
