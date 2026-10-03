"""``services.search_truth`` -- does a page tell one consistent story?

WHY THE FIXTURES ARE REAL PAGES
-------------------------------
``tests/fixtures/search_truth/`` holds two product pages fetched from
https://pulsesoc.com with a Googlebot user agent on 2026-10-03, while
``/api/service/health`` reported the deployed commit as
``5bdf4e431d1fd9164706962d7610d287a1f3092b`` -- the same commit this branch is
based on. So the bytes under test are the bytes production served from the code
in this checkout.

That matters more than usual here. This module's whole job is to notice that two
renderings of one fact disagree, and an invented fixture is written by the same
person writing the parser, from the same mental model, so it agrees with the
parser by construction and proves nothing about the wire. The specific trap:
``data-mkt-variants`` is a **single-quoted** attribute whose body is
``&quot;``-escaped JSON sitting next to ordinary double-quoted ``content=``
attributes on the same page. A hand-written fixture would almost certainly have
used double quotes, the parser would have passed, and the richest price surface
on the page would have been silently unread in production. That is not a
hypothetical -- it is what happened during development, twice.

The two pages are chosen for the distinction that drives the design:

``pdp_163_point_price.html``
    One variant, one price. States ``$30.50`` in **seven** places: the
    ``description``, ``og:description`` and ``twitter:description`` metas, the
    JSON-LD ``WebPage.description`` prose, the JSON-LD ``Offer.price``, the
    ``data-mkt-variants`` bootstrap, and the visible ``data-mkt-price`` pill.

``pdp_15_range_price.html``
    42 variants across 6 distinct prices, so the page states a *span*,
    ``$15.92 – $51.74``, and its JSON-LD is an ``AggregateOffer`` with
    ``lowPrice``/``highPrice`` rather than an ``Offer`` with ``price``. Ranged
    rows are where ``marketplace_seo.price_label_contradicts_variants``
    documents real production drift, so a parser blind to ``AggregateOffer``
    is blind on exactly the rows that have been wrong before.

WHAT IS NOT TESTED HERE, DELIBERATELY
-------------------------------------
Status codes, redirect chains and ``X-Robots-Tag``-vs-meta contradictions belong
to ``scripts/search_os/verify_sitemap_vs_live.py``.
``tests/test_merchant_center_feed.py`` already pins the feed against the page --
though note its ``test_the_feed_price_appears_on_the_landing_page`` is an
``assertIn`` over the whole body, which passes when the page states the feed's
number *and also* a different one somewhere else. Closing that is
``test_a_second_contradictory_price_anywhere_on_the_page_is_a_fault`` below.
"""

from __future__ import annotations

import ast
import os
import sys
import tokenize
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import marketplace_seo, search_truth  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "search_truth")

#: The commit production was serving when the fixtures were captured.
FIXTURE_DEPLOYED_SHA = "5bdf4e431d1fd9164706962d7610d287a1f3092b"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


POINT_PAGE = fixture("pdp_163_point_price.html")
POINT_ID = 163
RANGE_PAGE = fixture("pdp_15_range_price.html")
RANGE_ID = 15


def row(**overrides):
    """A listing row shaped like ``bot.marketplace_public_listings`` returns.

    Defaults describe listing 163 truthfully, so a test that wants a fault has
    to introduce one explicitly. The inverse -- defaults that are already
    slightly wrong -- is how a suite ends up asserting production's bug.
    """

    base = {
        "id": POINT_ID,
        "title": "Multifunctional Car Phone Holder",
        "price_label": "$30.50",
        "currency": "USD",
        "quantity": 5,
        "product_type": "physical",
        "description": (
            "Color: Chuck air outlet accessories, Material: Plastic, "
            "Style: Universal. A sturdy air-vent car mount."
        ),
        "media": [{"media_type": "image", "media_url": "https://cdn.example/holder.jpg"}],
        "variants": [
            {"id": 2708, "status": "active", "price_cents": 3050, "currency": "USD", "quantity": 5}
        ],
    }
    base.update(overrides)
    return base


class MoneyGrammarTestCase(unittest.TestCase):
    """``normalize_money`` inherits ``marketplace_seo``'s refusals."""

    def test_a_point_price_reads_as_a_degenerate_span(self):
        claim = search_truth.normalize_money("$30.50")
        self.assertEqual((claim.low, claim.high, claim.currency), ("30.50", "30.50", "USD"))
        self.assertFalse(claim.is_range)

    def test_the_storefronts_en_dash_range_reads_as_a_span(self):
        claim = search_truth.normalize_money("$15.92 – $51.74")
        self.assertEqual((claim.low, claim.high, claim.currency), ("15.92", "51.74", "USD"))
        self.assertTrue(claim.is_range)

    def test_a_hyphen_or_em_dash_range_reads_the_same(self):
        for text in ("$15.92 - $51.74", "$15.92 — $51.74"):
            with self.subTest(text=text):
                self.assertEqual(search_truth.normalize_money(text).high, "51.74")

    def test_a_backwards_span_refuses_rather_than_sorting_itself(self):
        # Silently sorting would hide a renderer fault behind a plausible span.
        self.assertIsNone(search_truth.normalize_money("$51.74 – $15.92"))

    def test_a_symbol_contradicting_the_currency_column_refuses(self):
        # Delegated to marketplace_seo.parse_price; asserted here because this
        # module must not loosen it.
        self.assertIsNone(search_truth.normalize_money("$40", "EUR"))

    def test_a_zero_price_is_not_a_free_product(self):
        self.assertIsNone(search_truth.normalize_money("$0.00"))

    def test_a_bare_number_in_prose_is_not_a_price(self):
        self.assertIsNone(search_truth.prose_money("2 Pack, 12V, fits 55 inch"))

    def test_two_unrelated_amounts_in_prose_are_ambiguous_not_a_price(self):
        self.assertIsNone(search_truth.prose_money("was $40 now $25 today"))

    def test_a_span_in_prose_is_one_claim_not_two(self):
        claim = search_truth.prose_money("Acetate fiber $15.92 – $51.74 on PulseSoc Marketplace.")
        self.assertEqual((claim.low, claim.high), ("15.92", "51.74"))


class WireReadingTestCase(unittest.TestCase):
    """The parser reads the real page, including the shapes that fooled it."""

    def test_the_single_quoted_quot_escaped_bootstrap_is_read(self):
        variants = search_truth.variant_bootstrap(POINT_PAGE)
        self.assertIsNotNone(variants, "data-mkt-variants is single-quoted; it must still parse")
        self.assertEqual(variants[0]["price"], "$30.50")

    def test_all_seven_price_surfaces_on_the_real_page_are_found(self):
        surfaces = search_truth.price_surfaces(POINT_PAGE)
        known = {name for name, obs in surfaces.items() if obs.known}
        self.assertEqual(
            known,
            {
                "jsonld_offer",
                "visible_pill",
                "variant_bootstrap",
                "prose:description",
                "prose:og:description",
                "prose:twitter:description",
                "prose:jsonld_webpage",
            },
        )
        self.assertEqual({str(surfaces[n].value) for n in known}, {"30.50 USD"})

    def test_an_aggregate_offer_span_is_read_not_treated_as_absent(self):
        surfaces = search_truth.price_surfaces(RANGE_PAGE)
        offer = surfaces["jsonld_offer"]
        self.assertTrue(offer.known, "AggregateOffer carries lowPrice/highPrice, not price")
        self.assertEqual(str(offer.value), "15.92-51.74 USD")

    def test_many_option_prices_collapse_to_the_span_the_page_prints(self):
        surfaces = search_truth.price_surfaces(RANGE_PAGE)
        self.assertEqual(str(surfaces["variant_bootstrap"].value), "15.92-51.74 USD")
        self.assertEqual(str(surfaces["visible_pill"].value), "15.92-51.74 USD")

    def test_unavailable_is_not_read_as_available(self):
        # "available" is a substring of "unavailable"; a substring test on that
        # pair reads a withdrawn product as in stock.
        self.assertEqual(
            search_truth._stock_label_to_schema("Currently unavailable"),
            marketplace_seo.OUT_OF_STOCK,
        )
        self.assertEqual(search_truth._stock_label_to_schema("In stock"), "https://schema.org/InStock")

    def test_canonical_and_robots_come_off_the_real_page(self):
        self.assertEqual(
            search_truth.canonical_from_html(POINT_PAGE),
            "https://pulsesoc.com/pulse/marketplace/163",
        )
        self.assertIn("index,follow", search_truth.robots_from_html(POINT_PAGE))

    def test_a_malformed_json_ld_block_is_absent_never_a_mismatch(self):
        broken = POINT_PAGE.replace('"@type": "Product"', '"@type": Product', 1)
        surfaces = search_truth.price_surfaces(broken)
        self.assertFalse(surfaces["jsonld_offer"].known)
        verdict = search_truth.compare_page(POINT_ID, broken)
        self.assertNotIn("PRICE_SURFACES_DISAGREE", verdict.codes)


class TheRealPagesAreCleanTestCase(unittest.TestCase):
    """Production, as captured, contradicts itself nowhere."""

    def test_the_point_priced_page_has_no_faults(self):
        verdict = search_truth.compare_page(POINT_ID, POINT_PAGE)
        self.assertEqual(verdict.faults, (), f"unexpected: {verdict.faults}")

    def test_the_range_priced_page_has_no_faults(self):
        verdict = search_truth.compare_page(RANGE_ID, RANGE_PAGE)
        self.assertEqual(verdict.faults, (), f"unexpected: {verdict.faults}")

    def test_every_comparison_was_actually_possible_on_both_pages(self):
        """A clean verdict with everything skipped would be worthless.

        This is the assertion that stops the suite above from passing
        vacuously: it is not enough that no fault was raised, the comparisons
        have to have been *attempted*.
        """

        for listing_id, page in ((POINT_ID, POINT_PAGE), (RANGE_ID, RANGE_PAGE)):
            with self.subTest(listing=listing_id):
                self.assertEqual(search_truth.compare_page(listing_id, page).skipped, {})

    def test_the_full_row_comparison_is_clean_on_a_truthful_row(self):
        verdict = search_truth.compare_listing(row(), POINT_PAGE, sitemap_ids={POINT_ID})
        self.assertEqual(verdict.faults, (), f"unexpected: {verdict.faults}")
        self.assertEqual(verdict.skipped, {})


class EveryFaultCodeCanFireTestCase(unittest.TestCase):
    """A fault code that cannot fire is a false reassurance, so each fires once."""

    def assert_fires(self, verdict, code, severity):
        self.assertIn(code, verdict.codes, f"expected {code}, got {verdict.codes}")
        fault = next(f for f in verdict.faults if f.code == code)
        self.assertEqual(fault.severity, severity)
        self.assertTrue(fault.detail, "a fault with no detail is not actionable")

    def test_price_surfaces_disagree_when_the_visible_pill_drifts(self):
        page = POINT_PAGE.replace("data-mkt-price>$30.50", "data-mkt-price>$41.99")
        self.assert_fires(
            search_truth.compare_page(POINT_ID, page), "PRICE_SURFACES_DISAGREE", search_truth.P0
        )

    def test_price_surfaces_disagree_when_the_structured_data_drifts(self):
        page = POINT_PAGE.replace('"price": "30.50"', '"price": "41.99"')
        self.assert_fires(
            search_truth.compare_page(POINT_ID, page), "PRICE_SURFACES_DISAGREE", search_truth.P0
        )

    def test_the_same_number_in_a_different_currency_is_a_disagreement(self):
        """30.50 EUR and $30.50 are not the same price.

        There is no ``CURRENCY_MISMATCH`` code, by design: currency is part of
        the comparison key, so a divergence trips the price codes rather than a
        separate one. That design is only safe if the key really does carry the
        currency, which is what this asserts. The mutation harness found this
        untested -- dropping currency from the key was caught only by a test
        that formats money, which asserts nothing about comparing it.
        """

        page = POINT_PAGE.replace('"priceCurrency": "USD"', '"priceCurrency": "EUR"')
        self.assertNotEqual(page, POINT_PAGE)
        verdict = search_truth.compare_page(POINT_ID, page)
        self.assert_fires(verdict, "PRICE_SURFACES_DISAGREE", search_truth.P0)
        detail = {f.code: f.detail for f in verdict.faults}["PRICE_SURFACES_DISAGREE"]
        self.assertIn("EUR", detail)
        self.assertIn("USD", detail)

    def test_a_second_contradictory_price_in_one_sentence_is_a_fault(self):
        """The gap left by an ``assertIn`` containment check.

        ``test_merchant_center_feed`` asserts the feed's number appears in the
        body. It still does here -- $30.50 is present seven times. What this
        catches is the eighth claim that says something else.

        This fires ``PRICE_PROSE_STATES_TWO_AMOUNTS`` rather than
        ``PRICE_SURFACES_DISAGREE``, and the distinction is the point: a
        sentence holding two amounts yields no comparable value, so the
        cross-surface comparison sees one fewer surface and reports the rest as
        agreeing. Writing this test is what found that hole.
        """

        page = POINT_PAGE.replace(
            'property="og:description" content="', 'property="og:description" content="$99.99 '
        )
        self.assertIn("30.50", page)  # the containment check still passes
        verdict = search_truth.compare_page(POINT_ID, page)
        self.assert_fires(verdict, "PRICE_PROSE_STATES_TWO_AMOUNTS", search_truth.P0)
        detail = {f.code: f.detail for f in verdict.faults}["PRICE_PROSE_STATES_TWO_AMOUNTS"]
        self.assertIn("99.99", detail)
        self.assertIn("30.50", detail)

    def test_a_contradictory_prose_price_is_never_recorded_as_an_absence(self):
        """Once a surface states two amounts it must not read as stating none."""

        page = POINT_PAGE.replace(
            'property="og:description" content="', 'property="og:description" content="$99.99 '
        )
        obs = search_truth.price_surfaces(page)["prose:og:description"]
        self.assertFalse(obs.known)
        self.assertIn("99.99", obs.detail)
        self.assertIn("30.50", obs.detail)

    def test_a_backwards_span_surfaces_as_its_two_endpoints(self):
        """A span printed high-to-low is a contradiction, not a silence."""

        self.assertEqual(
            sorted(
                search_truth._show(search_truth._money_key(claim))
                for claim in search_truth.prose_mentions("Now $51.74 - $15.92 on PulseSoc.")
            ),
            ["15.92 USD", "51.74 USD"],
        )

    def test_an_aggregate_offer_span_that_drifts_from_the_printed_span(self):
        page = RANGE_PAGE.replace('"highPrice": "51.74"', '"highPrice": "61.74"')
        self.assertNotEqual(page, RANGE_PAGE)
        self.assert_fires(
            search_truth.compare_page(RANGE_ID, page), "PRICE_SURFACES_DISAGREE", search_truth.P0
        )

    def test_a_visible_pill_showing_only_the_low_end_of_a_span_is_a_fault(self):
        """The mirror of the test above, and the likelier bug of the two.

        A template that prints `min(prices)` instead of the span is an ordinary
        mistake, and it is the direction that costs money: the structured data
        and the feed keep saying 15.92-51.74 while the buyer reads 15.92 and
        gets charged more at checkout. Nothing about the page looks broken.

        Asserted separately from the `AggregateOffer` drift because they fail in
        opposite directions, and a comparison that caught one could plausibly be
        written so it missed the other -- the narrowed value is a *subset* of
        the span rather than a different number.
        """

        old = '<span class="mkt-price-value" data-mkt-price>$15.92 – $51.74</span>'
        self.assertEqual(RANGE_PAGE.count(old), 1, "fixture markup moved; this test is vacuous")
        page = RANGE_PAGE.replace(old, '<span class="mkt-price-value" data-mkt-price>$15.92</span>')
        verdict = search_truth.compare_page(RANGE_ID, page)
        self.assert_fires(verdict, "PRICE_SURFACES_DISAGREE", search_truth.P0)
        detail = {f.code: f.detail for f in verdict.faults}["PRICE_SURFACES_DISAGREE"]
        self.assertIn("visible_pill=15.92 USD", detail)
        self.assertIn("jsonld_offer=15.92-51.74 USD", detail)

    def test_canonical_mismatch_on_a_trailing_slash(self):
        page = POINT_PAGE.replace(
            'rel="canonical" href="https://pulsesoc.com/pulse/marketplace/163"',
            'rel="canonical" href="https://pulsesoc.com/pulse/marketplace/163/"',
        )
        self.assert_fires(
            search_truth.compare_page(POINT_ID, page), "CANONICAL_MISMATCH", search_truth.P0
        )

    def test_canonical_absent(self):
        page = POINT_PAGE.replace('rel="canonical"', 'rel="alternate"')
        self.assert_fires(search_truth.compare_page(POINT_ID, page), "CANONICAL_ABSENT", search_truth.P1)

    def test_robots_absent(self):
        page = POINT_PAGE.replace('name="robots"', 'name="robots-disabled"')
        self.assert_fires(search_truth.compare_page(POINT_ID, page), "ROBOTS_ABSENT", search_truth.P2)

    def test_identity_mismatch_when_the_sku_stops_naming_the_listing(self):
        page = POINT_PAGE.replace('"pulsesoc-listing-163"', '"MW-HOLDER-001"')
        self.assert_fires(
            search_truth.compare_page(POINT_ID, page), "IDENTITY_MISMATCH", search_truth.P1
        )

    def test_availability_surfaces_disagree(self):
        page = POINT_PAGE.replace("data-mkt-stock>In stock", "data-mkt-stock>Out of stock")
        self.assert_fires(
            search_truth.compare_page(POINT_ID, page),
            "AVAILABILITY_SURFACES_DISAGREE",
            search_truth.P1,
        )

    def test_price_wire_vs_db_when_both_row_authorities_moved_together(self):
        moved = row(
            price_label="$38.00",
            variants=[
                {"id": 1, "status": "active", "price_cents": 3800, "currency": "USD", "quantity": 5}
            ],
        )
        self.assert_fires(
            search_truth.compare_listing(moved, POINT_PAGE), "PRICE_WIRE_VS_DB", search_truth.P0
        )

    def test_db_price_authorities_disagree_is_blamed_on_the_row_not_the_page(self):
        """Listing 112's live defect, in miniature.

        The page is correct -- it prices from ``derive_price``, which is what
        checkout charges. It is ``price_label``, which the feed publishes, that
        is wrong. So the fault must name the row, and ``PRICE_WIRE_VS_DB`` must
        *not* also fire and send someone to debug the template.
        """

        verdict = search_truth.compare_listing(row(price_label="$38.00"), POINT_PAGE)
        self.assert_fires(verdict, "DB_PRICE_AUTHORITIES_DISAGREE", search_truth.P0)
        self.assertNotIn("PRICE_WIRE_VS_DB", verdict.codes)

    def test_price_absent_on_wire_is_a_different_code_from_a_wrong_price(self):
        bare = (
            "<html><head>"
            "<link rel='canonical' href='https://pulsesoc.com/pulse/marketplace/163'>"
            "<meta name='robots' content='index,follow'>"
            "</head><body></body></html>"
        )
        verdict = search_truth.compare_listing(row(), bare)
        self.assert_fires(verdict, "PRICE_ABSENT_ON_WIRE", search_truth.P1)
        self.assertNotIn("PRICE_WIRE_VS_DB", verdict.codes)

    def test_availability_wire_vs_db_when_the_row_has_withdrawn_the_stock(self):
        verdict = search_truth.compare_listing(row(quantity=0), POINT_PAGE)
        self.assert_fires(verdict, "AVAILABILITY_WIRE_VS_DB", search_truth.P1)

    def test_robots_contradicts_policy_when_a_thin_row_is_still_indexed(self):
        verdict = search_truth.compare_listing(row(description="short"), POINT_PAGE)
        self.assert_fires(verdict, "ROBOTS_CONTRADICTS_POLICY", search_truth.P0)

    def test_private_in_sitemap(self):
        verdict = search_truth.compare_listing(
            row(description="short"), POINT_PAGE, sitemap_ids={POINT_ID}
        )
        self.assert_fires(verdict, "PRIVATE_IN_SITEMAP", search_truth.P0)

    def test_sitemap_missing_an_indexable_listing_is_only_p2(self):
        # Sitemap generation is not this module's to own and a freshly
        # published listing is legitimately absent until regeneration.
        verdict = search_truth.compare_listing(row(), POINT_PAGE, sitemap_ids=set())
        self.assert_fires(verdict, "SITEMAP_MISSING_INDEXABLE", search_truth.P2)

    def test_feed_row_refused_is_recorded_not_raised(self):
        # `feed_row` raises when `eligibility` and `parse_price` disagree about
        # one string. That is evidence about our own modules, so it is captured.
        import services.merchant_center_feed as feed_module

        original = feed_module.feed_row

        def exploding(listing):
            raise ValueError("feed_eligible but the price does not parse")

        feed_module.feed_row = exploding
        try:
            verdict = search_truth.compare_listing(row(), POINT_PAGE)
        finally:
            feed_module.feed_row = original
        self.assert_fires(verdict, "FEED_ROW_REFUSED", search_truth.P1)

    def test_every_declared_fault_code_is_covered_by_a_test_above(self):
        """Keeps this class honest as codes are added.

        A new ``Fault(...)`` with no test is a code that has never been seen to
        fire, which is indistinguishable from a code that cannot.
        """

        import re

        source = open(search_truth.__file__, encoding="utf-8").read()
        declared = set(re.findall(r'Fault\(\s*"([A-Z_0-9]+)"', source))
        tested = set(re.findall(r'"([A-Z_0-9]+)"', open(__file__, encoding="utf-8").read()))
        self.assertEqual(
            declared - tested, set(), "fault codes declared but never asserted to fire"
        )


class UnknownIsNotAFaultTestCase(unittest.TestCase):
    """The principle the whole module is built around, asserted directly."""

    def test_a_page_we_could_not_fetch_produces_no_faults(self):
        verdict = search_truth.compare_page(POINT_ID, None)
        self.assertEqual(verdict.faults, ())
        self.assertTrue(verdict.skipped, "and it must say what it therefore does not know")

    def test_a_row_with_no_variants_does_not_guess_at_the_second_authority(self):
        no_variants = {k: v for k, v in row().items() if k != "variants"}
        verdict = search_truth.compare_listing(no_variants, POINT_PAGE)
        self.assertNotIn("DB_PRICE_AUTHORITIES_DISAGREE", verdict.codes)
        self.assertIn("DB_PRICE_AUTHORITIES_DISAGREE", verdict.skipped)

    def test_noindex_under_an_indexable_path_is_unknown_without_the_row(self):
        """A thin listing is *supposed* to look like this.

        Wire-only, record-level eligibility is unknowable, so the honest answer
        is UNKNOWN. Calling it a fault would page someone for correct behaviour
        on every thin listing in the catalogue.
        """

        page = POINT_PAGE.replace('content="index,follow', 'content="noindex,follow')
        verdict = search_truth.compare_page(POINT_ID, page)
        self.assertNotIn("ROBOTS_CONTRADICTS_POLICY", verdict.codes)
        self.assertIn("ROBOTS_CONTRADICTS_POLICY", verdict.skipped)

    def test_the_same_row_with_the_database_resolves_that_unknown_either_way(self):
        page = POINT_PAGE.replace('content="index,follow', 'content="noindex,follow')
        thin = search_truth.compare_listing(row(description="short"), page)
        self.assertNotIn("ROBOTS_CONTRADICTS_POLICY", thin.codes)

        fat = search_truth.compare_listing(row(), page)
        self.assertIn("ROBOTS_CONTRADICTS_POLICY", fat.codes)

    def test_a_page_disagreeing_with_itself_does_not_also_get_compared_to_the_row(self):
        # Two faults for one defect sends two people to two different places.
        page = POINT_PAGE.replace("data-mkt-price>$30.50", "data-mkt-price>$41.99")
        verdict = search_truth.compare_listing(row(), page)
        self.assertIn("PRICE_SURFACES_DISAGREE", verdict.codes)
        self.assertNotIn("PRICE_WIRE_VS_DB", verdict.codes)
        self.assertIn("PRICE_WIRE_VS_DB", verdict.skipped)

    def test_mixed_currency_options_are_not_collapsed_into_an_invented_span(self):
        page = RANGE_PAGE.replace("&quot;$15.92&quot;", "&quot;£15.92&quot;", 1)
        surfaces = search_truth.price_surfaces(page)
        self.assertFalse(surfaces["variant_bootstrap"].known)
        self.assertIn("currencies", surfaces["variant_bootstrap"].detail)


class EvidenceLineageTestCase(unittest.TestCase):
    """Every claim is labelled with where it came from."""

    def test_each_observation_names_its_source(self):
        verdict = search_truth.compare_listing(row(), POINT_PAGE, sitemap_ids={POINT_ID})
        self.assertTrue(verdict.evidence)
        valid = {
            search_truth.SOURCE_POLICY,
            search_truth.SOURCE_DATABASE,
            search_truth.SOURCE_WIRE,
            search_truth.SOURCE_FEED,
            search_truth.SOURCE_PROVIDER,
        }
        for name, obs in verdict.evidence.items():
            with self.subTest(observation=name):
                self.assertIn(obs.source, valid)

    def test_the_two_row_price_authorities_are_reported_separately(self):
        verdict = search_truth.compare_listing(row(), POINT_PAGE)
        self.assertIn("db:price_label", verdict.evidence)
        self.assertIn("db:price_variants", verdict.evidence)

    def test_no_provider_observation_is_ever_produced(self):
        """There is no inbound provider evidence for this property.

        No Search Console client, no Merchant diagnostics ingest, no Bing WMT
        API, no IndexNow submission code. ``SOURCE_PROVIDER`` is declared so
        that third-party claims are labelled as such when they arrive; until
        then, anything presenting provider state is inventing it.
        """

        verdict = search_truth.compare_listing(row(), POINT_PAGE, sitemap_ids={POINT_ID})
        sources = {obs.source for obs in verdict.evidence.values()}
        self.assertNotIn(search_truth.SOURCE_PROVIDER, sources)

    def test_the_feed_price_is_evidence_and_never_a_check(self):
        """It cannot be an independent check and must not pretend to be.

        ``merchant_center_feed.feed_row`` and the page's structured data both
        read ``price_label`` through the same ``parse_price``, so comparing them
        in-process compares a number to itself. The claim worth verifying is
        what Merchant Center actually ingested, and nothing here can see that.
        """

        verdict = search_truth.compare_listing(row(), POINT_PAGE)
        self.assertIn("feed:price", verdict.evidence)
        self.assertNotIn("FEED_PRICE_VS_WIRE", verdict.codes)
        source = open(search_truth.__file__, encoding="utf-8").read()
        self.assertNotIn("FEED_PRICE_VS_WIRE", source)


class NoDatabaseOnTheWireOnlyPathTestCase(unittest.TestCase):
    """``compare_page`` must stay safe to point at production."""

    def test_comparing_a_page_never_imports_bot(self):
        """``import bot`` connects and runs ``init_db()`` at module scope.

        A sentinel that pulled that in could not be aimed at production without
        touching a database, so the wire-only path must not reach it -- directly
        or through any module it imports.
        """

        self.assertNotIn("bot", sys.modules, "precondition: bot must not be imported already")
        search_truth.compare_page(POINT_ID, POINT_PAGE)
        self.assertNotIn("bot", sys.modules)


class ObserverNeverBecomesAnAuthorityTestCase(unittest.TestCase):
    """The engine reports disagreement. It must never resolve one.

    This is the failure mode that would quietly destroy the thing being
    measured: the sentinel notices the page and the row disagree, decides which
    one is right, and writes. From then on the two surfaces agree because the
    sentinel made them agree, and the comparison is a feedback loop reporting on
    its own output. Every fault it had been catching disappears, and the report
    goes green at the exact moment it stops meaning anything.

    No fault output can reveal that -- a silenced catalogue and a correct one
    print the same thing -- so the guarantee has to be structural.

    PulseSoc already has two price authorities that disagree
    (``price_label`` vs ``marketplace_listing_variants``). A third one, owned by
    the module whose job is to audit the first two, is the version of this
    mistake that would be hardest to unwind.
    """

    #: Substring checks over the source. They cannot catch SQL assembled at
    #: runtime, and are not meant to: they catch the way this would actually
    #: arrive -- a later contributor "fixing" the drift at the point the engine
    #: detects it, which is the most natural place to put it and the one place
    #: it must never go.
    WRITE_TOKENS = ("INSERT", "UPDATE ", "DELETE", "UPSERT", "ON CONFLICT", "commit(", "executemany")

    #: Not just "no write" -- no connection to write *through*. `services/db.py`
    #: is the accessor and `bot` owns the schema; the engine is handed its rows
    #: by the caller. Adding a write means adding one of these imports first, so
    #: this is the line someone would have to delete to do it.
    REACH_TOKENS = ("from . import db", "from services import db", "import bot", "sqlite3")

    def source_of(self, path):
        """The module's *code*, with comments and docstrings blanked out.

        Scanning raw source would make this guard read prose as behaviour --
        which is how `route_auth` in this repo counts a commented-out call as a
        call, and is exactly what happened on the first run of this test: the
        engine's own docstring explains that it never does `import bot`, and the
        scan read that sentence as the import.

        Other string literals are deliberately kept: SQL lives in them, so
        excluding strings wholesale would turn this into a guard that cannot
        fail. Only the docstrings go, because they are the only strings that
        describe the code instead of being it.
        """

        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if ast.get_docstring(node, clean=False) is None:
                continue
            doc = node.body[0]
            for index in range(doc.lineno - 1, doc.end_lineno):
                lines[index] = ""
        kept = []
        readline = iter(lines).__next__
        comments = {
            token.start[0]: token.start[1]
            for token in tokenize.generate_tokens(lambda: readline() + "\n")
            if token.type == tokenize.COMMENT
        }
        for number, line in enumerate(lines, 1):
            kept.append(line[: comments[number]] if number in comments else line)
        return "\n".join(kept)

    def test_the_comparison_engine_contains_no_write(self):
        source = self.source_of(search_truth.__file__)
        for token in self.WRITE_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_the_comparison_engine_cannot_reach_a_connection(self):
        source = self.source_of(search_truth.__file__)
        for token in self.REACH_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_the_runner_contains_no_write(self):
        """The runner is the half that gets aimed at production.

        It may import `bot` for the row-level mode -- that is why the mode is
        documented local/CI-only -- but importing the schema owner in order to
        *read* a row is not permission to change one.
        """

        source = self.source_of(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "search_os",
                "search_truth_sentinel.py",
            )
        )
        for token in self.WRITE_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
