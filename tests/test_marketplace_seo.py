"""What a marketplace listing is allowed to claim to Google.

``services/marketplace_seo.py`` is built on one rule — *derive or refuse* — and
the cases worth pinning are almost all refusals. That is the unusual shape of
this file: most tests assert that something is **absent**. A structured-data
module that emits a plausible value where the row has none does not fail loudly;
it publishes a false claim about a product a person pays money for, and Merchant
Center's response to that is account suspension rather than a validation warning.
So the absences are the contract.

The module imports no Flask app and touches no database, which is why this file
does not import ``bot``. The route half — anonymous 200, member shell, 404, the
``Cache-Control`` header — is ``tests/test_marketplace_public_pages.py``.

Run: python3 -m pytest tests/test_marketplace_seo.py
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import marketplace_seo  # noqa: E402


def listing(**overrides):
    """A row that is complete, so every test below can subtract from it.

    Shaped like ``pulse_marketplace_listing_payload``'s output rather than like
    the table, because that is what the module is handed. ``media`` in
    particular is the payload's resolved list and not a raw column.
    """

    row = {
        "id": 777,
        "listing_id": 777,
        "title": "Linen Duvet Cover Set",
        "description": "A washed European linen duvet cover with two pillowcases, "
                       "prewashed so it arrives soft and does not shrink.",
        "short_description": "Washed linen duvet set",
        "category": "Home",
        "price_label": "$465.74",
        "currency": "USD",
        "quantity": 12,
        "status": "published",
        "approval_status": "approved",
        "seller_store_name": "M&W Store",
        "media": [{"media_type": "image", "media_url": "https://cdn.example/bed.jpg", "is_cover": 1}],
    }
    row.update(overrides)
    return row


def graph_nodes(row):
    """The graph as ``{@type: node}``, which is how every assertion here reads it."""
    return {node["@type"]: node for node in marketplace_seo.product_schema_graph(row)}


class ParsePriceTestCase(unittest.TestCase):
    """``price_label`` is TEXT, so reading it is a parse and may fail."""

    def test_reads_the_shape_production_actually_holds(self):
        price = marketplace_seo.parse_price("$465.74", "USD")
        self.assertEqual((price.amount, price.currency), ("465.74", "USD"))

    def test_normalises_to_two_decimal_places(self):
        """``price`` in an Offer is compared against checkout as a number.

        ``"$9"`` and ``"9.00"`` are the same money, and emitting the first would
        make the feed and the page differ as strings for no reason.
        """
        self.assertEqual(marketplace_seo.parse_price("$9", "USD").amount, "9.00")

    def test_reads_thousands_separators(self):
        self.assertEqual(marketplace_seo.parse_price("$1,234.56", "USD").amount, "1234.56")

    def test_takes_the_currency_from_the_column_when_the_label_has_no_symbol(self):
        price = marketplace_seo.parse_price("40.00", "GBP")
        self.assertEqual((price.amount, price.currency), ("40.00", "GBP"))

    def test_reads_a_trailing_iso_code(self):
        self.assertEqual(marketplace_seo.parse_price("40.00 EUR").currency, "EUR")

    def test_refuses_prose(self):
        for label in ("Request access", "Free", "Ask for a quote", "TBD"):
            with self.subTest(label=label):
                self.assertIsNone(marketplace_seo.parse_price(label, "USD"))

    def test_refuses_a_range_because_it_does_not_say_which_number_is_the_price(self):
        for label in ("$20-$40", "$20 to $40", "from $20"):
            with self.subTest(label=label):
                self.assertIsNone(marketplace_seo.parse_price(label, "USD"))

    def test_refuses_an_empty_or_missing_label(self):
        for label in ("", "   ", None):
            with self.subTest(label=label):
                self.assertIsNone(marketplace_seo.parse_price(label, "USD"))

    def test_refuses_zero_and_negative(self):
        """A 0 is a data fault, not a giveaway.

        Nothing in this product creates a free listing, so publishing ``0.00``
        to Shopping would advertise a price the checkout will not honour.
        """
        for label in ("$0", "$0.00", "0"):
            with self.subTest(label=label):
                self.assertIsNone(marketplace_seo.parse_price(label, "USD"))

    def test_refuses_when_the_symbol_contradicts_the_currency_column(self):
        """The refusal the module's docstring is really about.

        ``"$40"`` on a row marked EUR is two different claims about how much
        money the buyer owes. There is no basis in the row for preferring
        either, so resolving it would publish a price that may not be charged.
        """
        self.assertIsNone(marketplace_seo.parse_price("$40.00", "EUR"))

    def test_refuses_when_a_trailing_code_contradicts_the_symbol(self):
        self.assertIsNone(marketplace_seo.parse_price("$40.00 GBP", None))

    def test_accepts_a_symbol_and_column_that_agree(self):
        """The cross-check must not reject agreement, or nothing parses at all."""
        self.assertEqual(marketplace_seo.parse_price("$40.00", "USD").currency, "USD")

    def test_refuses_a_bare_number_with_no_currency_anywhere(self):
        """A number with no denomination is not a price.

        Defaulting to USD here is the tempting shortcut and it is how a GBP
        listing gets advertised at a USD number.
        """
        self.assertIsNone(marketplace_seo.parse_price("40.00", None))


class AvailabilityTestCase(unittest.TestCase):
    def test_a_stocked_row_is_in_stock(self):
        self.assertEqual(marketplace_seo.availability(listing()), marketplace_seo.IN_STOCK)

    def test_a_row_with_no_inventory_is_out_of_stock(self):
        self.assertEqual(marketplace_seo.availability(listing(quantity=0)),
                         marketplace_seo.OUT_OF_STOCK)


class EligibilityTestCase(unittest.TestCase):
    """Two verdicts, because a good web page can still be a bad Shopping offer."""

    def test_a_complete_listing_is_both_indexable_and_feedable(self):
        verdict = marketplace_seo.eligibility(listing())
        self.assertTrue(verdict.indexable)
        self.assertTrue(verdict.feed_eligible)

    def test_a_thin_description_is_neither(self):
        """The production case: two published rows with 2- and 0-char descriptions."""
        verdict = marketplace_seo.eligibility(listing(description="ok", short_description=""))
        self.assertFalse(verdict.indexable)
        self.assertFalse(verdict.feed_eligible)
        self.assertIn("description", verdict.reason)

    def test_an_imageless_listing_is_neither(self):
        verdict = marketplace_seo.eligibility(listing(media=[]))
        self.assertFalse(verdict.indexable)
        self.assertFalse(verdict.feed_eligible)

    def test_a_video_only_listing_counts_as_imageless(self):
        """Merchant Center requires an *image*; a video is not a substitute."""
        verdict = marketplace_seo.eligibility(listing(
            media=[{"media_type": "video", "media_url": "https://cdn.example/clip.mp4"}]))
        self.assertFalse(verdict.indexable)

    def test_an_unpriced_listing_keeps_its_page_but_stays_out_of_the_feed(self):
        """The one case where the two verdicts differ, which is why there are two.

        The page is a real page and is worth indexing; it simply makes no price
        claim. Collapsing the verdicts would either bury it or feed it.
        """
        verdict = marketplace_seo.eligibility(listing(price_label="Request access"))
        self.assertTrue(verdict.indexable)
        self.assertFalse(verdict.feed_eligible)

    def test_a_titleless_listing_is_neither(self):
        verdict = marketplace_seo.eligibility(listing(title="  "))
        self.assertFalse(verdict.indexable)
        self.assertEqual(verdict.reason, "no title")


class ProductPageMetaTestCase(unittest.TestCase):
    def test_the_canonical_is_the_one_product_path(self):
        self.assertEqual(marketplace_seo.product_page_meta(listing())["canonical"],
                         "https://pulsesoc.com/pulse/marketplace/777")

    def test_the_h1_is_the_sellers_title_verbatim(self):
        """Not rewritten. It is the string buyers search for, and Merchant Center
        compares the feed's title against the landing page."""
        self.assertEqual(marketplace_seo.product_page_meta(listing())["h1"],
                         "Linen Duvet Cover Set")

    def test_a_long_description_is_cut_on_a_word_boundary(self):
        row = listing(description="word " * 80)
        description = marketplace_seo.product_page_meta(row)["description"]
        self.assertLessEqual(len(description), 156)
        self.assertTrue(description.endswith("…"))
        self.assertNotIn("wor…", description)

    def test_an_empty_description_gets_a_derived_one_rather_than_an_empty_tag(self):
        meta = marketplace_seo.product_page_meta(listing(description="", short_description=""))
        self.assertIn("Linen Duvet Cover Set", meta["description"])

    def test_the_image_falls_back_to_the_share_image_so_the_tag_is_never_empty(self):
        """A meta image is a presentation default, not a claim about the product.

        Unlike price or brand there is nothing to misrepresent, so unlike those
        this one is allowed a fallback -- and `eligibility` has already kept the
        imageless row out of the index regardless.
        """
        self.assertTrue(marketplace_seo.product_page_meta(listing(media=[]))["image"])


class ProductGraphTestCase(unittest.TestCase):
    def test_the_graph_is_the_five_nodes_this_page_is_about(self):
        """Exactly five. ``seo_schema.schema_graph`` would append
        ``MobileApplication`` and ``Service``, leaving Google to decide which of
        three entities a product page describes."""
        self.assertEqual(
            [node["@type"] for node in marketplace_seo.product_schema_graph(listing())],
            ["Organization", "WebSite", "WebPage", "Product", "BreadcrumbList"],
        )

    def test_the_offer_carries_price_currency_availability_and_condition(self):
        offer = graph_nodes(listing())["Product"]["offers"]
        self.assertEqual(offer["price"], "465.74")
        self.assertEqual(offer["priceCurrency"], "USD")
        self.assertEqual(offer["availability"], marketplace_seo.IN_STOCK)
        self.assertEqual(offer["itemCondition"], "https://schema.org/NewCondition")

    def test_the_seller_is_the_store_and_not_pulsesoc(self):
        """Who the buyer contracts with. Naming PulseSoc here would misstate it."""
        offer = graph_nodes(listing())["Product"]["offers"]
        self.assertEqual(offer["seller"], {"@type": "Organization", "name": "M&W Store"})

    def test_an_unparseable_price_emits_no_offer_at_all(self):
        """Not an Offer with a missing price, and not a zero. No node."""
        self.assertNotIn("offers", graph_nodes(listing(price_label="Free"))["Product"])

    def test_an_out_of_stock_row_says_so_rather_than_omitting_availability(self):
        offer = graph_nodes(listing(quantity=0))["Product"]["offers"]
        self.assertEqual(offer["availability"], marketplace_seo.OUT_OF_STOCK)

    def test_no_brand_is_invented(self):
        """``"PulseSoc"`` is the tempting filler and it is false: PulseSoc is the
        marketplace, not the manufacturer, and a brand mismatch between feed and
        landing page is a data-quality failure."""
        self.assertNotIn("brand", graph_nodes(listing())["Product"])

    def test_no_product_identifier_is_invented(self):
        """A GTIN is somebody else's registered identifier."""
        product = graph_nodes(listing())["Product"]
        for field in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "mpn", "sku"):
            with self.subTest(field=field):
                self.assertNotIn(field, product)

    def test_no_rating_is_invented(self):
        """There is no rating column and no review table behind these rows, so a
        star rating would be fabricated -- the most heavily penalised
        structured-data abuse Google names."""
        product = graph_nodes(listing())["Product"]
        self.assertNotIn("aggregateRating", product)
        self.assertNotIn("review", product)

    def test_the_singletons_keep_the_site_wide_ids(self):
        """Sharing ``@id`` is what consolidates these into the existing entities.

        A second, differently-worded Organization node would not create a second
        organisation; it would create one organisation with contradictory
        properties resolved by crawl order.
        """
        nodes = graph_nodes(listing())
        self.assertEqual(nodes["Organization"]["@id"], "https://pulsesoc.com/#organization")
        self.assertEqual(nodes["WebSite"]["@id"], "https://pulsesoc.com/#website")

    def test_the_breadcrumb_trail_is_home_marketplace_product(self):
        items = graph_nodes(listing())["BreadcrumbList"]["itemListElement"]
        self.assertEqual([entry["name"] for entry in items],
                         ["Home", "Marketplace", "Linen Duvet Cover Set"])

    def test_the_page_graph_is_a_json_string_the_template_can_write_out(self):
        """The contract ``app_page_graph`` already set: the route passes a string
        and no template serialises JSON itself."""
        payload = marketplace_seo.product_page_graph(listing())
        self.assertIsInstance(payload, str)
        parsed = json.loads(payload)
        self.assertEqual(parsed["@context"], "https://schema.org")
        self.assertEqual(len(parsed["@graph"]), 5)

    def test_non_ascii_in_a_seller_title_survives_serialisation(self):
        """Seller titles in production contain typographic punctuation. Escaping
        it would leave the structured title subtly different from the visible
        one, which is a mismatch Merchant Center checks."""
        payload = marketplace_seo.product_page_graph(listing(title="Café Table — 2 seats"))
        self.assertIn("Café Table — 2 seats", payload)


if __name__ == "__main__":
    unittest.main()
