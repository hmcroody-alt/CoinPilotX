"""``/feeds/merchant-center.xml`` -- the claims we make to Google Shopping.

Two things are being tested here and they have different stakes.

The first is ordinary: the feed is well-formed XML with the fields Merchant
Center requires. A failure there is caught by Google's own validator on the
first fetch and costs a day.

The second is the reason this file is long. Merchant Center enforces its
misrepresentation policy by **comparing the feed against the landing page it
links to**, and the penalty is account suspension rather than a rejected item.
So the assertions that matter most are not "the field is present" but "the field
says the same thing the page says" -- the price, the availability, the image and
the link. Those are asserted by fetching the actual product page through the
test client and comparing, not by calling the same helper twice and watching it
agree with itself.

## The gap between the sitemap and the feed is deliberate, and is pinned here

``eligibility`` returns two verdicts and the two surfaces read different ones:
``/sitemap-products.xml`` reads ``.indexable``, this feed reads
``.feed_eligible``. A listing with a real description and image but an
unparseable ``price_label`` belongs in Search and cannot be a Shopping offer.

That asymmetry had **no live instance** until 2026-10-01, which is why these
tests carried the whole weight. Counted on 2026-09-26 with the predicates the
two surfaces actually select on: 15 publishable listings, all 15 priced, 13
over the description floor -- so the only two exclusions failed on description,
which bars them from Search too, and nothing live distinguished the verdicts.

It has one now, and not the shape that was anticipated. Four listings in the
live feed advertised a ``price_label`` their own variant rows contradict, and
``FeedPriceMatchesCheckoutTestCase`` below is that case: indexable, because the
page prices correctly from ``derive_price``; not feedable, because the number
the feed would send is not the number checkout charges. So the gap is now real
policy with real rows behind it rather than a hypothetical, and a "fix" that
collapses the two verdicts would put four misrepresented prices into Shopping
rather than merely risking it.

Pinning it took three tests rather than one, and the reason is worth recording.
``test_a_priced_page_and_an_unpriced_page_split_between_the_two`` reads like
enough: it asserts the unpriced row is in the sitemap and out of the feed. But
``feed_row`` drops that row twice -- once on the verdict, once on a price guard
below it -- so collapsing the verdicts onto ``.indexable`` leaves it green. A
mutation probe found that. ``test_reading_the_wider_verdict_is_a_loud_contradiction``
and ``test_the_feed_module_never_consults_indexable`` are the two that fail.

## Why the escaping tests use a real ampersand and not a mock

Every ``title`` and ``description`` in this feed was typed by a seller. An
unescaped ``&`` does not need to be an attack to be a problem: it makes the file
malformed, and Merchant Center rejects a malformed file **whole**, so one bad
character takes the entire catalogue out of Shopping at once. The test seeds a
title containing ``&``, ``<`` and ``"`` and re-parses the output, because
asserting on the serialised string would pass for output that merely *looks*
escaped.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db.

Run: python3 -m pytest tests/test_merchant_center_feed.py
"""

import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mkt_feed_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import marketplace_seo, merchant_center_feed, search_visibility  # noqa: E402

G = merchant_center_feed.G_NS
SELLER = 96101
NOW = "2026-09-01T00:00:00"

DESCRIPTION = ("A washed European linen duvet cover with two pillowcases, prewashed "
               "so it arrives soft and does not shrink in the first wash.")


def pin_database():
    """Point DATABASE_URL at *this* file's temp sqlite, again, per test.

    Same reasoning as ``tests/test_marketplace_public_pages.py``, which documents
    it at length: on SQLite ``services.db.connect()`` re-reads DATABASE_URL on
    every call, so the database a request is answered from is chosen at request
    time. Setting it once above the import loses to whichever suite pytest
    imported last.
    """

    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


class FeedFixture(unittest.TestCase):
    """Seeding shared by every case below. Holds no tests itself."""

    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        pin_database()
        # `init_db()` returns early on the process global `INIT_DB_COMPLETED`,
        # which is right in production and wrong for the second suite in one
        # pytest process: the first suite already built a schema in a temp
        # database of its own, so without this the database here has no tables
        # and the only symptom is one hidden stdout line.
        bot.INIT_DB_COMPLETED = False
        bot.init_db()
        cls._real_require_account = bot.require_account
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.require_account = cls._real_require_account

    def setUp(self):
        pin_database()
        # The feed is fetched by Merchant Center with no session, so every test
        # here runs signed out. Not incidental: a feed that only builds for a
        # logged-in caller would be empty in production and green here.
        bot.require_account = lambda *args, **kwargs: None
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id=?", (SELLER,))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id=?", (SELLER,))
        cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                    (SELLER, "feed_seller", "feed_seller"))
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (SELLER, "M&W Store", "approved", NOW, NOW),
        )
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def make_listing(self, *, title="Linen Duvet Cover Set", description=DESCRIPTION,
                     price_label="$465.74", currency="USD", status="published",
                     approval_status="approved", cover="https://cdn.example/bed.jpg",
                     quantity=12, product_type="physical"):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, description, short_description, category, price_label, currency,"
            " quantity, product_type, listing_type, status, approval_status, cover_image_url,"
            " safety_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SELLER, title, description, "Washed linen duvet set", "Home", price_label, currency,
             quantity, product_type, product_type, status, approval_status, cover, 7, NOW, NOW),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def make_variant(self, listing_id, price_cents, *, status="active", currency="USD",
                     variant_key="default"):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listing_variants "
            "(listing_id, seller_user_id, variant_key, price_cents, currency, status,"
            " position, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (listing_id, SELLER, variant_key, price_cents, currency, status, 0, NOW, NOW),
        )
        variant_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return variant_id

    def fetch(self):
        return self.client.get(merchant_center_feed.FEED_PATH)

    def items(self, response=None):
        """Every ``<item>`` as a dict, keyed by the tag names Google reads.

        Parsed rather than regexed, and the ``g:`` keys are rebuilt from the
        resolved namespace URI -- so a feed that declared the wrong namespace
        would produce items with no ``g:price`` here rather than silently
        matching on the prefix text.
        """

        response = response or self.fetch()
        root = ET.fromstring(response.get_data(as_text=True))
        out = []
        for item in root.find("channel").findall("item"):
            fields = {}
            for child in item:
                tag = child.tag.replace(f"{{{G}}}", "g:")
                fields[tag] = child.text
            out.append(fields)
        return out

    def only_item(self):
        items = self.items()
        self.assertEqual(len(items), 1, f"expected exactly one feed item, got {len(items)}")
        return items[0]


class FeedShapeTestCase(FeedFixture):
    """The document itself: namespace, required fields, and what is omitted."""

    def test_the_feed_is_well_formed_xml_with_the_google_namespace(self):
        self.make_listing()
        root = ET.fromstring(self.fetch().get_data(as_text=True))
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.get("version"), "2.0")
        # The namespace has to *resolve*, not merely appear in the xmlns. Google
        # keys on the URI, so the tag is checked in its expanded form -- a feed
        # that declared the wrong URI would still read as `g:price` in the source.
        item = root.find("channel").find("item")
        self.assertIn(f"{{{G}}}price", [child.tag for child in item])

    def test_every_required_merchant_center_field_is_present(self):
        self.make_listing()
        item = self.only_item()
        for field in ("g:id", "title", "description", "link", "g:image_link",
                      "g:availability", "g:price", "g:condition", "g:identifier_exists"):
            with self.subTest(field=field):
                self.assertTrue((item.get(field) or "").strip(), f"{field} is missing or empty")

    def test_brand_gtin_and_mpn_are_omitted_and_declared_absent(self):
        """The module's central honesty decision, pinned.

        ``brand`` has no column behind it and the tempting filler is "PulseSoc",
        which is false -- PulseSoc is the marketplace, not the manufacturer, and
        Google treats a brand mismatch between feed and landing page as a
        data-quality failure. ``identifier_exists: no`` is the mechanism for a
        product that genuinely has no identifier, so the absence has to be
        *declared* rather than merely present.
        """

        self.make_listing()
        item = self.only_item()
        for absent in ("g:brand", "g:gtin", "g:mpn"):
            with self.subTest(field=absent):
                self.assertNotIn(absent, item)
        self.assertEqual(item["g:identifier_exists"], "no")

    def test_shipping_is_not_sent_at_item_level(self):
        """Omitted on purpose: it is an account-level setting with no column here.

        An item-level ``g:shipping`` overrides the Merchant Center account
        setting, so inventing one would publish a shipping price this codebase
        has no source for.
        """

        self.make_listing()
        self.assertNotIn("g:shipping", self.only_item())

    def test_the_price_carries_its_currency_in_googles_format(self):
        self.make_listing(price_label="$465.74", currency="USD")
        self.assertEqual(self.only_item()["g:price"], "465.74 USD")

    def test_availability_uses_merchant_center_tokens_not_schema_org_urls(self):
        """The one place a naive reuse of ``marketplace_seo`` would break.

        ``availability()`` returns ``https://schema.org/InStock`` for JSON-LD.
        Merchant Center rejects the URL form outright and wants ``in_stock``.
        """

        self.make_listing()
        availability = self.only_item()["g:availability"]
        self.assertEqual(availability, "in_stock")
        self.assertNotIn("schema.org", availability)

    def test_an_unmapped_availability_value_raises_rather_than_shipping(self):
        """Anti-vacuity for the mapping: it must not fall back to the raw value.

        If a third schema.org availability is ever introduced,
        ``_FEED_AVAILABILITY`` has to be updated deliberately. A pass-through
        would put a URL Google rejects into the feed silently.
        """

        listing = {
            "id": 5, "title": "x", "description": DESCRIPTION, "price_label": "$1.00",
            "currency": "USD", "type": "physical", "quantity": 1,
            "media": [{"media_type": "image", "media_url": "https://cdn.example/a.jpg"}],
        }
        real = marketplace_seo.availability
        marketplace_seo.availability = lambda _listing: "https://schema.org/PreOrder"
        try:
            with self.assertRaises(ValueError) as caught:
                merchant_center_feed.feed_row(listing)
            self.assertIn("_FEED_AVAILABILITY", str(caught.exception))
        finally:
            marketplace_seo.availability = real

    def test_condition_is_new(self):
        self.make_listing()
        self.assertEqual(self.only_item()["g:condition"], merchant_center_feed.CONDITION)
        self.assertEqual(merchant_center_feed.CONDITION, "new")


class FeedAgreesWithTheLandingPageTestCase(FeedFixture):
    """The suspension-risk assertions: feed field == what the page says.

    Each of these fetches the real product page through the test client and
    compares. Calling the same helper twice would prove only that a function is
    deterministic; Merchant Center compares two *surfaces*, so the test has to
    as well.
    """

    def page(self, listing_id):
        return self.client.get(f"/pulse/marketplace/{listing_id}").get_data(as_text=True)

    def test_the_link_is_the_page_that_actually_answers(self):
        listing_id = self.make_listing()
        link = self.only_item()["link"]
        self.assertEqual(link, marketplace_seo.product_url(listing_id))
        # Submitting a URL that 404s is a disapproval, so the link is fetched.
        response = self.client.get(f"/pulse/marketplace/{listing_id}")
        self.assertEqual(response.status_code, 200)

    def test_the_feed_price_appears_on_the_landing_page(self):
        listing_id = self.make_listing(price_label="$465.74")
        amount = self.only_item()["g:price"].split(" ")[0]
        self.assertIn(amount, self.page(listing_id))

    def test_the_feed_title_is_the_title_on_the_page(self):
        listing_id = self.make_listing(title="Linen Duvet Cover Set")
        self.assertIn(self.only_item()["title"], self.page(listing_id))

    def test_the_feed_image_is_the_image_on_the_page(self):
        listing_id = self.make_listing(cover="https://cdn.example/bed.jpg")
        self.assertIn(self.only_item()["g:image_link"], self.page(listing_id))

    def test_the_feed_and_the_page_json_ld_report_the_same_availability(self):
        """Both are derived from ``marketplace_seo.availability``; this proves it.

        The page says ``https://schema.org/InStock`` and the feed says
        ``in_stock``. Different spellings of one fact is correct. Different facts
        is the suspension case.
        """

        listing_id = self.make_listing()
        self.assertEqual(self.only_item()["g:availability"], "in_stock")
        self.assertIn(marketplace_seo.IN_STOCK, self.page(listing_id))


class FeedEligibilityTestCase(FeedFixture):
    """Which rows are in, which are out, and the sitemap/feed split."""

    def test_a_listing_with_no_parseable_price_is_not_in_the_feed(self):
        """``price_label`` is TEXT and sellers leave it blank.

        No *publishable* row lacked a price on 2026-09-26 -- the 26 unpriced rows
        were all drafts -- so this is the case the catalogue does not currently
        exhibit rather than the common one. Asserted anyway: ``g:price`` is
        required, and guessing zero would publish "free" for a product that
        charges.
        """

        self.make_listing(price_label="")
        self.assertEqual(self.items(), [])

    def test_a_listing_with_no_image_is_not_in_the_feed(self):
        self.make_listing(cover="")
        self.assertEqual(self.items(), [])

    def test_a_listing_with_a_thin_description_is_not_in_the_feed(self):
        self.make_listing(description="Nice.")
        self.assertEqual(self.items(), [])

    def test_an_unpublished_listing_is_not_in_the_feed(self):
        """The lifecycle predicate, which is the same one the page applies.

        A feed item whose landing page 404s is a Merchant Center disapproval, so
        the feed must not select on looser predicates than the page.
        """

        self.make_listing(status="draft")
        self.assertEqual(self.items(), [])

    def test_an_unapproved_listing_is_not_in_the_feed(self):
        self.make_listing(approval_status="pending")
        self.assertEqual(self.items(), [])

    def test_a_priced_page_and_an_unpriced_page_split_between_the_two(self):
        """The sitemap/feed gap, measured on the one row where the two differ.

        One priced listing and one unpriced one. Both are indexable and belong in
        the sitemap; only the priced one may be a Shopping offer.

        Note what this does *not* prove. The unpriced row is the only shape where
        ``indexable`` and ``feed_eligible`` disagree at all, so swapping which
        verdict ``feed_row`` reads leaves this assertion green -- the row still
        leaves the feed, just via the price guard below the eligibility check
        instead of via the check itself. A mutation probe caught that;
        ``test_reading_the_wider_verdict_is_a_loud_contradiction`` and
        ``test_the_feed_module_never_consults_indexable`` are what close it.
        """

        priced = self.make_listing(price_label="$465.74")
        unpriced = self.make_listing(price_label="")

        feed_ids = {item["g:id"] for item in self.items()}
        self.assertEqual(feed_ids, {str(priced)}, "the feed must hold only the priced listing")

        sitemap_paths = {path for path, _lastmod in bot.marketplace_public_entries()}
        for listing_id in (priced, unpriced):
            with self.subTest(listing_id=listing_id):
                self.assertIn(
                    marketplace_seo.PRODUCT_PATH.format(listing_id=listing_id), sitemap_paths,
                    "both listings are indexable and belong in the sitemap")

    def test_reading_the_wider_verdict_is_a_loud_contradiction(self):
        """An unpriced row that claims feed eligibility raises rather than skips.

        ``feed_row`` has two reasons to drop an unpriced listing: the
        ``feed_eligible`` verdict, and a price guard immediately below it. Two
        guards for one condition means a change that defeats the first is
        invisible -- which is exactly what happens if someone "simplifies"
        ``feed_eligible`` to ``indexable``, since those differ on nothing but
        price.

        So the lower guard no longer skips quietly, and that turns the collapse
        into something observable: an unpriced row would now reach a raise,
        ``feed_xml`` would log ``MERCHANT_FEED_ROW_FAILED``, and an ordinary
        catalogue would start reporting errors. The second half of this test is
        that assertion, and it is the half that fails under the mutation.
        """

        unpriced = self.make_listing(price_label="")
        listing, = [row for row in bot.marketplace_feed_listings()
                    if int(row["id"]) == unpriced]
        wider = marketplace_seo.eligibility(listing)
        # The premise: this row is the disagreement, and it is the *only* shape
        # that is. If that ever stops holding, say so here rather than silently
        # testing nothing.
        self.assertTrue(wider.indexable, "an unpriced page is still a real page")
        self.assertFalse(wider.feed_eligible, "but it is not a Shopping offer")

        real = marketplace_seo.eligibility
        marketplace_seo.eligibility = lambda row: marketplace_seo.Eligibility(
            real(row).indexable, real(row).indexable, "probe: collapsed onto indexable")
        try:
            with self.assertRaises(ValueError) as caught:
                merchant_center_feed.feed_row(listing)
        finally:
            marketplace_seo.eligibility = real
        self.assertIn("feed_eligible", str(caught.exception))

        # And unpatched: a mixed catalogue is the ordinary case, so dropping the
        # unpriced row must be silent. An error here means the module is reading
        # the wider verdict and hitting the contradiction guard on every row a
        # seller simply has not priced.
        self.make_listing(price_label="$465.74")
        with self.assertNoLogs(level="ERROR"):
            merchant_center_feed.feed_xml(bot.marketplace_feed_listings())

    def test_the_feed_module_never_consults_indexable(self):
        """The same invariant stated as source, because it is one about reading.

        The behavioural test above detects the collapse through its consequence.
        This one names the rule: the feed consumes exactly one verdict, and the
        narrower one. Cheap, and it cannot be defeated by a later refactor that
        happens to make the consequence quiet again.
        """

        source = open(merchant_center_feed.__file__, encoding="utf-8").read()
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        self.assertIn(".feed_eligible", code)
        self.assertNotIn(
            ".indexable", code,
            "merchant_center_feed must read feed_eligible, never the wider "
            "indexable verdict -- an unpriced listing is an indexable page and "
            "a disapproved Shopping offer at the same time")

    def test_both_surfaces_read_the_same_query(self):
        """They must agree about which rows *exist*, if not which to publish.

        A second copy of the listing query is how a feed ends up advertising a
        product the sitemap already dropped. The feed's row source is asserted to
        be a projection of the sitemap's.
        """

        self.make_listing()
        self.make_listing(price_label="")
        from_shared = {int(row["id"]) for row, _listing in bot.marketplace_public_listings()}
        from_feed_source = {int(listing["id"]) for listing in bot.marketplace_feed_listings()}
        self.assertEqual(from_shared, from_feed_source)
        self.assertEqual(len(from_shared), 2)


class FeedPriceMatchesCheckoutTestCase(FeedFixture):
    """The feed may not advertise a price the buyer will not be asked to pay.

    This is the misrepresentation case the file's header describes, caught live.
    Two authorities read money off a listing: ``price_label``, which is what
    this feed sends, and ``marketplace_listing_variants.price_cents``, which is
    what the product page shows and what ``_line_price_minor`` charges. On
    2026-10-01 four of the 35 items in the production feed disagreed -- listing
    36 advertised $38.00 against a $2.29 variant, and listing 112 advertised
    $29.31 against a $27.84-$37.72 range, so a buyer taking the top option paid
    $8.41 over the advertised price.

    The tests fetch the feed through the route rather than calling
    ``eligibility`` directly, because the predicate is only half the fix: it
    reads ``listing["variants"]``, and nothing populated that key until
    ``marketplace_public_listings`` was taught to. A unit test of the predicate
    passes with the loader unchanged and the feed still wrong.
    """

    def test_a_listing_whose_label_matches_its_variants_stays_in_the_feed(self):
        listing_id = self.make_listing(price_label="$465.74")
        self.make_variant(listing_id, 46574)
        self.make_variant(listing_id, 46574, variant_key="second")
        self.assertEqual(self.only_item()["g:price"], "465.74 USD")

    def test_a_listing_the_page_prices_differently_leaves_the_feed(self):
        """Production listing 36's shape: advertised $38.00, charged $2.29."""
        listing_id = self.make_listing(price_label="$38.00")
        self.make_variant(listing_id, 229)
        self.assertEqual(self.items(), [], "a price we cannot substantiate is not sent")

    def test_the_withdrawn_listing_keeps_its_page_in_the_sitemap(self):
        """Out of Shopping, still in Search -- the page itself is correct.

        The page prices from ``derive_price``, so it shows the number checkout
        charges. Dropping it from the sitemap as well would take a working,
        honestly-priced product page out of Search to settle a disagreement
        Search does not have an opinion about.
        """

        listing_id = self.make_listing(price_label="$38.00")
        self.make_variant(listing_id, 229)
        sitemap_paths = {path for path, _lastmod in bot.marketplace_public_entries()}
        self.assertIn(marketplace_seo.PRODUCT_PATH.format(listing_id=listing_id), sitemap_paths)

    def test_a_label_inside_a_variant_range_still_leaves_the_feed(self):
        """Production listing 112, and the row that overcharged.

        The advertised $29.31 sits between $27.84 and $37.72, so a check that
        compared only the cheapest variant would pass exactly the row that cost
        a buyer money.
        """

        listing_id = self.make_listing(price_label="$29.31")
        self.make_variant(listing_id, 2784)
        self.make_variant(listing_id, 3772, variant_key="large")
        self.assertEqual(self.items(), [])

    def test_the_shared_loader_is_what_carries_the_variants(self):
        """Names the plumbing, since the predicate is inert without it.

        ``price_label_contradicts_variants`` fails open on a listing with no
        ``variants`` key -- a page must not stop rendering because a caller
        skipped a join -- so the loader populating it *is* the fix. Asserted
        here rather than left implicit, because removing the join is a change
        that looks like it only costs a query.
        """

        listing_id = self.make_listing()
        self.make_variant(listing_id, 46574)
        listing, = [row for row in bot.marketplace_feed_listings()
                    if int(row["id"]) == listing_id]
        self.assertEqual([int(v["price_cents"]) for v in listing["variants"]], [46574])

    def test_a_variant_the_seller_archived_cannot_withdraw_the_listing(self):
        """The loader excludes archived rows and ``derive_price`` ignores
        non-active ones, so neither may create a disagreement that is not real."""

        listing_id = self.make_listing(price_label="$465.74")
        self.make_variant(listing_id, 46574)
        self.make_variant(listing_id, 99, status="archived", variant_key="old")
        self.assertEqual(self.only_item()["g:price"], "465.74 USD")

    def test_a_catalogue_with_no_variants_at_all_is_unaffected(self):
        """Most of the live catalogue. The label remains the only authority."""
        self.make_listing(price_label="$465.74")
        self.assertEqual(self.only_item()["g:price"], "465.74 USD")


class FeedEscapingTestCase(FeedFixture):
    """Seller-typed strings, serialised safely.

    Merchant Center rejects a malformed file whole, so one unescaped ``&`` in one
    product title removes the entire catalogue from Shopping.
    """

    def test_xml_special_characters_in_a_title_survive_a_round_trip(self):
        title = 'Bed & Sofa <Large> "Deluxe" Set'
        listing_id = self.make_listing(title=title)
        item = self.only_item()
        # Re-parsed, not string-matched: output that merely looks escaped would
        # pass a substring check and fail a parser.
        self.assertEqual(item["title"], title)
        self.assertEqual(item["g:id"], str(listing_id))

    def test_the_serialised_form_really_is_escaped(self):
        """The other half: the bytes on the wire contain entities, not raw markup.

        Asserted separately from the round-trip above because a serialiser that
        emitted raw ``<Large>`` and a parser that tolerated it would agree with
        each other while producing a file Google rejects.
        """

        self.make_listing(title='Bed & Sofa <Large> "Deluxe" Set')
        body = self.fetch().get_data(as_text=True)
        self.assertIn("Bed &amp; Sofa &lt;Large&gt;", body)
        self.assertNotIn("<Large>", body)

    def test_one_unrenderable_row_does_not_take_the_feed_down(self):
        """13 correct items beat a 500, and a bad row must not cost the other 12."""

        good = self.make_listing()
        real = merchant_center_feed.feed_row
        calls = {"n": 0}

        def exploding(listing):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("synthetic row failure")
            return real(listing)

        merchant_center_feed.feed_row = exploding
        try:
            self.make_listing(title="Second Listing")
            items = self.items()
        finally:
            merchant_center_feed.feed_row = real
        self.assertEqual(len(items), 1, "the surviving row must still be served")
        self.assertTrue(items[0]["g:id"] in {str(good), str(good + 1)})


class FeedTitleTruncationTestCase(FeedFixture):
    """Google's 150-character title limit.

    Not hypothetical: production row 14 is a 160-character bed title, 1 of the 15
    publishable listings.
    Truncating is a transformation ``marketplace_seo`` deliberately refuses to
    make, and it is justified here only because the alternative is dropping the
    product from Shopping entirely.
    """

    def test_a_title_over_the_limit_is_truncated_on_a_word_boundary(self):
        # Shaped after production row 14, the one publishable listing exceeding the
        # limit. Length asserted rather than assumed: an earlier draft of this
        # fixture was accidentally exactly 150 characters and the test passed
        # while truncating nothing.
        title = ("Upholstered Bed 135 X 190 Cm With LED Lighting USB Type-C Charging Storage "
                 "Headboard And Reinforced Slatted Base In Anthracite Grey Fabric For Bedroom Use")
        self.assertGreater(len(title), merchant_center_feed.MAX_TITLE_CHARS)
        self.make_listing(title=title)
        sent = self.only_item()["title"]
        self.assertLessEqual(len(sent), merchant_center_feed.MAX_TITLE_CHARS)
        # A word boundary, not a hard cut mid-word.
        self.assertTrue(title.startswith(sent), "truncation must preserve the leading words")
        self.assertNotEqual(sent[-1], " ")
        self.assertIn(" ", sent)
        self.assertTrue(title[len(sent)] in " ", "cut fell inside a word")

    def test_a_title_under_the_limit_is_sent_unchanged(self):
        """The limit must not become a rewrite of every title."""
        title = "Linen Duvet Cover Set"
        self.make_listing(title=title)
        self.assertEqual(self.only_item()["title"], title)

    def test_no_ellipsis_is_appended(self):
        """An ellipsis reads as part of the product name and costs a character."""
        self.make_listing(title="Word " * 60)
        self.assertNotIn("…", self.only_item()["title"])
        self.assertNotIn("...", self.only_item()["title"])


class FeedTransportTestCase(FeedFixture):
    """How the response is served: status, type, caching, and robots."""

    def test_the_feed_answers_200_with_no_session(self):
        """Merchant Center fetches unauthenticated. This is the whole route."""
        self.make_listing()
        response = self.fetch()
        self.assertEqual(response.status_code, 200)
        self.assertIn("xml", response.headers.get("Content-Type", ""))

    def test_the_feed_is_cacheable(self):
        """``add_pwa_headers`` must not stamp ``no-store`` on a scheduled fetch."""
        response = self.fetch()
        self.assertEqual(response.headers.get("Cache-Control"), "public, max-age=300")

    def test_the_feed_is_noindex_via_a_header_because_xml_has_no_head(self):
        """The policy is declared in ``search_visibility``; this is its delivery.

        An XML document has nowhere to put a meta tag, so a ``noindex`` verdict
        that is only in the table governs nothing. The header is read from
        ``robots_meta`` so the two cannot drift.
        """

        response = self.fetch()
        self.assertEqual(response.headers.get("X-Robots-Tag"),
                         search_visibility.robots_meta(merchant_center_feed.FEED_PATH))
        self.assertIn("noindex", response.headers.get("X-Robots-Tag", ""))

    def test_the_feed_stays_crawlable(self):
        """``noindex`` yes, ``Disallow`` no -- a blocked feed is never fetched.

        The distinction matters: ``robots_disallow_prefixes`` only offers
        ``noindex,nofollow`` paths, and classifying the feed ``nofollow`` would
        have made it eligible for the very ``Disallow`` that would stop Merchant
        Center reading it.
        """

        robots = self.client.get("/robots.txt").get_data(as_text=True)
        self.assertNotIn("/feeds/", robots)
        self.assertFalse(search_visibility.classify(merchant_center_feed.FEED_PATH).indexable)

    def test_the_feed_is_not_in_any_sitemap(self):
        """It is a machine surface, not a page to rank."""
        self.assertFalse(search_visibility.sitemap_eligible(merchant_center_feed.FEED_PATH))
        for child in bot.SITEMAP_CHILDREN:
            with self.subTest(child=child):
                body = self.client.get(child).get_data(as_text=True)
                self.assertNotIn(merchant_center_feed.FEED_PATH, body)

    def test_an_empty_catalogue_is_a_valid_empty_feed_not_a_500(self):
        """A crawler gets a parseable document either way.

        Same reasoning as the sitemap routes: an empty feed is indistinguishable
        from a catalogue with nothing sellable in it, and a 500 tells Merchant
        Center the account is broken.
        """

        response = self.fetch()
        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(response.get_data(as_text=True))
        self.assertEqual(root.find("channel").findall("item"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
