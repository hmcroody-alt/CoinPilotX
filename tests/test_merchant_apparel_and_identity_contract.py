"""The 15 mutations the Merchant contract must refuse -- Agent 12's attack set.

Companion to three documents, which carry the evidence and are the authority:
  docs/search-os/agent-07/A_APPAREL_ATTRIBUTE_SOURCE_AUDIT.md
  docs/search-os/agent-07/B_MERCHANT_IDENTITY_CONTRACT.md
  docs/search-os/agent-07/C_MERCHANT_READINESS_POLICY.md

## What this file is for

Google requires ``color``, ``age_group`` and ``gender`` on every apparel offer, plus
``size`` on clothing and shoes. Between 24 and 27 of the 36 items in the live feed are
apparel. **None of those attributes exists anywhere in this system** -- not in the
supplier payload, not in a column, not in a seller field (Deliverable A).

So there is a standing temptation to synthesise them, and the synthesis has an obvious
shape: ``marketplace_listing_variants.options_json`` holds axes named ``option1`` and
``option2`` whose values, on apparel rows, look exactly like colours and sizes
(``'Rose Red'``, ``'XS'``). Reading them is a two-line change that would make three
quarters of the feed look compliant.

**That resemblance is the trap, and it has a live counterexample.** Listing 97 is
women's clothing in the current feed and its axes are:

    option1='S'  option2='White'

inverted relative to listing 42, also women's clothing in the current feed:

    option1='Snowflake Blue'  option2='S'

A global ``option1 -> color`` rule publishes ``color="S"`` for listing 97. Nothing in
the data separates the two: same seller, same supplier connection, same department,
same two-axis shape. 10 of the 36 feed items are additionally inconsistent about slot 1
*within a single listing*, so the rule cannot be rescued by deciding per listing.

``option1`` is not even a degraded upstream label. CJ sends one opaque string
(``"Silver 50CM"``); ``_cj_options`` in ``services/business_os/suppliers/normalize.py``
mints the names locally with ``f"option{index + 1}"``. There is no supplier assertion to
recover.

## Why these are tests and not just prose

A document saying "do not read option1 as colour" is advice. These assertions fail the
build. The distinction matters because the forbidden change is small, locally sensible,
and would be made by someone who had not read the document -- most likely while
clearing a Merchant Center disapproval report, which is exactly when the pressure to
ship a plausible value is highest.

## What is and is not asserted here

Asserted: the feed emits no apparel attribute, no invented identifier, no
``item_group_id``; ``g:id`` is stable across title and price edits and derives from
nothing else; the module makes no claim about Google having accepted anything.

Deliberately NOT asserted: that a readiness gate withholds incomplete apparel offers.
No such gate is wired -- Deliverable C freezes the policy and leaves activation to
Agent 0, because activating it today would cut the live feed from 36 items to 9-12 for
no reduction in real exposure (no Merchant Center account exists). Mutation 7 therefore
pins the *absence* of a premature claim rather than the presence of a gate, and says so.

## No database, no Flask, no bot import

``feed_row`` is a pure function of a listing dict, so every case here builds one and
calls it. That keeps the file out of the way of the process-isolation problems the
DB-backed feed suite has to manage, and makes it fast enough that nobody is tempted to
skip it.

Run: python3 -m pytest tests/test_merchant_apparel_and_identity_contract.py
"""

import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import merchant_center_feed, marketplace_seo  # noqa: E402

#: Every tag the feed is known to emit. The mutation tests below assert that
#: nothing semantic has been *added* to this set, so it is written out rather
#: than derived from a live row -- deriving it from the code under test would
#: make the assertion agree with any change.
EXPECTED_TAGS = {
    "g:id", "title", "description", "link", "g:image_link",
    "g:availability", "g:price", "g:condition", "g:identifier_exists",
}

#: Attributes Google requires on apparel offers. None is sourceable today.
APPAREL_ATTRS = {"g:color", "g:size", "g:gender", "g:age_group",
                 "g:material", "g:pattern", "g:size_type", "g:size_system"}

#: Identifiers this catalogue genuinely does not have, declared truthfully by
#: ``g:identifier_exists = no`` rather than filled in.
INVENTED_IDS = {"g:gtin", "g:brand", "g:mpn"}


def apparel_listing(**over):
    """Listing 97 as it really is: women's clothing whose axis 1 holds a SIZE.

    The real row, not a convenient one. A fixture with ``option1='Rose Red'``
    would let an ``option1 -> color`` mutation look correct, which is the exact
    failure mode ``env_fixture_chosen_values_can_validate_an_inverted_gate``
    describes -- a green suite asserting the inverse of production.
    """

    listing = {
        "id": 97,
        "title": "Baggy Pants Backless Women's Outdoor Sleeveless",
        "description": "A romper cut from washed linen, " + ("detail " * 40),
        "category": "Women's Clothing > Tops & Sets > Rompers",
        "price_label": "$12.00",
        "currency": "USD",
        "quantity": 5,
        "product_type": "physical",
        "status": "published",
        "approval_status": "approved",
        "media": [{"media_type": "image",
                   "media_url": "https://cf.cjdropshipping.com/quick/product/a.jpg"}],
        # The real axes, in the real order.
        "variant_axes": [{"name": "option1", "value": "S"},
                         {"name": "option2", "value": "White"}],
    }
    listing.update(over)
    return listing


def kids_listing(**over):
    """Listing 111: 'Toys， Kids & Baby > Boys Clothing', sizes 90cm-150cm.

    The category really does contain U+FF0C FULLWIDTH COMMA at index 4. It is
    preserved here because it is load-bearing: a classifier matching on a plain
    ASCII comma silently drops this listing out of the apparel population, and
    this is the one feed item where getting ``age_group`` wrong is worst.
    """

    return apparel_listing(
        id=111,
        title="Baby Polar Fleece Jacket Navy Blue Striped Thin Long Sleeve",
        category="Toys， Kids & Baby > Boys Clothing > Outerwear & Coats",
        variant_axes=[{"name": "option1", "value": "Navy Blue"},
                      {"name": "option2", "value": "90cm"}],
        **over)


class ApparelAttributesAreNeverSynthesisedTestCase(unittest.TestCase):
    """Mutations 1-6: the feed must not invent apparel semantics."""

    def test_the_feed_emits_no_apparel_attribute_at_all(self):
        """Mutations 1, 2, 3, 4, 5 in one assertion.

        Every forbidden synthesis -- positional, title-derived, image-derived,
        category-derived -- has to end by putting one of these tags in the feed.
        Asserting on the output rather than on the technique catches all of them
        including ones nobody has thought of yet.
        """

        row = merchant_center_feed.feed_row(apparel_listing())
        self.assertIsNotNone(row, "fixture must be feed_eligible or this proves nothing")
        leaked = APPAREL_ATTRS & set(row)
        self.assertEqual(
            leaked, set(),
            f"The feed emitted {sorted(leaked)}. No apparel attribute is sourceable: "
            "the supplier sends one opaque options string, no column exists, and no "
            "seller field exists. See A_APPAREL_ATTRIBUTE_SOURCE_AUDIT.md. If this "
            "came from reading option1/option2, note that listing 97 -- the fixture -- "
            "has option1='S', so the value just published as a colour is a size."
        )

    def test_mutation_1_and_2_option1_is_not_a_colour_and_option2_is_not_a_size(self):
        """The positional read, stated as the data refutes it.

        Listing 97 and listing 42 are both live-feed women's clothing with the two
        axes in opposite order. Any rule keyed on position is wrong for one of them
        and there is no signal saying which.
        """

        ninety_seven = apparel_listing()
        forty_two = apparel_listing(
            id=42,
            category="Women's Clothing > Outerwear & Jackets > Basic Jacket",
            variant_axes=[{"name": "option1", "value": "Snowflake Blue"},
                          {"name": "option2", "value": "S"}])

        slot1 = [L["variant_axes"][0]["value"] for L in (ninety_seven, forty_two)]
        self.assertEqual(
            slot1, ["S", "Snowflake Blue"],
            "If this fixture changed so that both slot-1 values are colours, the "
            "counterexample is gone and the suite would start agreeing with the "
            "mutation it exists to refuse."
        )
        for listing in (ninety_seven, forty_two):
            row = merchant_center_feed.feed_row(listing)
            self.assertNotIn("g:color", row)
            self.assertNotIn("g:size", row)

    def test_mutation_3_children_are_not_defaulted_to_adult(self):
        """``age_group='adult'`` would be a false statement about listing 111.

        Boys' clothing in sizes 90cm-150cm is a small child. A blanket default is
        not a conservative choice here; it is a wrong claim on a live feed item.
        """

        row = merchant_center_feed.feed_row(kids_listing())
        self.assertIsNotNone(row)
        self.assertNotIn(
            "g:age_group", row,
            "Listing 111 is 'Boys Clothing' with sizes 90cm-150cm. Any default -- "
            "including 'adult' -- is fabricated, and 'adult' is specifically false."
        )

    def test_mutation_3_the_kids_category_really_does_carry_a_fullwidth_comma(self):
        """Pins the homoglyph, because a classifier that misses it misses listing 111.

        Not a style note. Deliverable A section 6.1 records that my own first
        classifier dropped this listing for exactly this reason, which is how a
        category-derived attribute would silently exclude the row that matters most.
        """

        category = kids_listing()["category"]
        self.assertIn("，", category)
        self.assertNotIn(
            ",", category.split(">")[0],
            "The lead segment must still contain NO ascii comma -- that is the whole "
            "point. Normalising this fixture to ascii would hide the trap."
        )

    def test_mutation_4_a_womens_department_is_not_a_gender_assertion(self):
        """The supplier asserted a CATEGORY. Reading a gender off it is a guess.

        ``GUESSED_FROM_CATEGORY`` and ``GUESSED_FROM_TITLE`` are both on the
        forbidden provenance list, and the title here contains "Women's".
        """

        listing = apparel_listing()
        self.assertIn("Women", listing["title"])
        self.assertIn("Women", listing["category"])
        row = merchant_center_feed.feed_row(listing)
        self.assertNotIn(
            "g:gender", row,
            "The title and category both say \"Women's\" and the feed still must not "
            "claim a gender. The supplier asserted where to file the product, not who "
            "wears it."
        )

    def test_mutation_6_a_supplier_sku_is_never_promoted_to_a_gtin(self):
        """``CJLX205679501AZ`` is a CJ internal SKU. It is not a GTIN by any test.

        The feed states the absence truthfully instead, which is what makes it legal
        rather than a rejection.
        """

        row = merchant_center_feed.feed_row(
            apparel_listing(sku="CJLX205679501AZ",
                            external_sku="CJLX205679501AZ"))
        leaked = INVENTED_IDS & set(row)
        self.assertEqual(
            leaked, set(),
            f"The feed emitted {sorted(leaked)}. No brand, GTIN or MPN exists in any "
            "of the 20,247 supplier snapshots."
        )
        self.assertEqual(row["g:identifier_exists"], "no")
        self.assertNotIn("CJLX205679501AZ", "".join(str(v) for v in row.values()),
                         "A supplier SKU must not reach the feed under any tag.")


class ReadinessIsAProjectionNotAPublicationTestCase(unittest.TestCase):
    """Mutations 7 and 8: readiness may subtract from Google, never from PulseSoc."""

    def test_mutation_7_no_premature_claim_of_merchant_readiness_exists(self):
        """The gate is deliberately NOT active, and must not pretend to be.

        Deliverable C freezes the policy and leaves activation to Agent 0: turning it
        on today would cut the live feed from 36 items to 9-12 while no Merchant
        Center account exists to be protected. So the thing to pin now is that no
        module claims an offer is Merchant-ready, because such a claim would be
        false for 24-27 of the 36 live items.
        """

        for name in ("merchant_ready", "is_merchant_ready", "apparel_attributes_satisfied",
                     "merchant_readiness"):
            self.assertFalse(
                hasattr(merchant_center_feed, name),
                f"{name} appeared on merchant_center_feed. If the readiness gate is "
                "being activated, that is Agent 0's decision and this test must be "
                "replaced with one asserting incomplete apparel offers are WITHHELD "
                "-- not merely deleted."
            )

    def test_mutation_8_withholding_from_google_does_not_touch_the_listing(self):
        """A listing excluded from the feed stays published, indexable and buyable.

        ``feed_row`` must be a read-only projection. If it ever mutated the listing it
        was handed -- a status write being the obvious way -- then withholding an offer
        from Shopping would unpublish a working product page.
        """

        listing = apparel_listing()
        before = copy.deepcopy(listing)
        merchant_center_feed.feed_row(listing)
        self.assertEqual(listing, before,
                         "feed_row mutated the listing it was given.")

        # And an ineligible row is dropped from the feed while remaining a page.
        unpriced = apparel_listing(price_label="Request access")
        self.assertIsNone(merchant_center_feed.feed_row(unpriced),
                          "an unparseable price must not become a Shopping offer")
        self.assertEqual(
            unpriced["status"], "published",
            "Being refused by the feed must leave the listing published. Readiness is "
            "a projection eligibility state, not a publication state."
        )

    def test_the_feed_reads_the_narrow_verdict_so_readiness_can_only_subtract(self):
        """Readiness sits below ``feed_eligible`` and can never widen it.

        A page with no parseable price belongs in Search and cannot be a Shopping
        offer. If this ever read ``indexable`` instead, a future readiness check would
        be layered on top of the wrong base verdict.
        """

        unpriced = apparel_listing(price_label="Request access")
        verdict = marketplace_seo.eligibility(unpriced)
        self.assertTrue(verdict.indexable, "should still be a legitimate web page")
        self.assertFalse(verdict.feed_eligible, "but not a Shopping offer")
        self.assertIsNone(merchant_center_feed.feed_row(unpriced))


class MerchantIdentityIsStableTestCase(unittest.TestCase):
    """Mutations 9-13: what ``g:id`` may and may not depend on."""

    def test_mutation_9_g_id_does_not_change_when_the_title_changes(self):
        """A retitle must not delete the offer and create a new one.

        Google treats a changed ``g:id`` as a different product, discarding that
        offer's accumulated history. Title is not identity.
        """

        a = merchant_center_feed.feed_row(apparel_listing())
        b = merchant_center_feed.feed_row(
            apparel_listing(title="Linen Romper, Relaxed Fit, Sleeveless"))
        self.assertNotEqual(a["title"], b["title"], "the mutation must actually change")
        self.assertEqual(a["g:id"], b["g:id"],
                         "g:id changed because the title changed.")

    def test_mutation_10_g_id_does_not_change_when_the_price_changes(self):
        """Same, for price. ``g:price`` moves; ``g:id`` does not."""

        a = merchant_center_feed.feed_row(apparel_listing())
        b = merchant_center_feed.feed_row(apparel_listing(price_label="$19.50"))
        self.assertNotEqual(a["g:price"], b["g:price"])
        self.assertEqual(a["g:id"], b["g:id"],
                         "g:id changed because the price changed.")

    def test_mutation_11_identity_derives_from_the_listing_id_and_nothing_else(self):
        """Two listings differing only in id get different ids; everything else cannot move it.

        This is the assertion that makes mutation 11 meaningful: a recreated listing
        gets a fresh sequence value, so it necessarily gets a fresh ``g:id`` and cannot
        inherit the deleted listing's Merchant history.
        """

        base = merchant_center_feed.feed_row(apparel_listing())
        self.assertEqual(base["g:id"], "97")

        moved = merchant_center_feed.feed_row(apparel_listing(id=98))
        self.assertEqual(moved["g:id"], "98")

        # Every other field varies without touching identity.
        for field, value in (("title", "Something Else Entirely"),
                             ("price_label", "$1.00"),
                             ("category", "Home, Garden & Furniture > Storage"),
                             ("status", "published")):
            row = merchant_center_feed.feed_row(apparel_listing(**{field: value}))
            self.assertEqual(
                row["g:id"], "97",
                f"changing {field} moved g:id. Identity must derive from the listing "
                "primary key alone -- see B_MERCHANT_IDENTITY_CONTRACT.md."
            )

    def test_mutation_12_variant_key_is_never_a_merchant_identifier(self):
        """``variant_key`` collides: 2,571 distinct values across 3,797 variants.

        It is unique only as ``(listing_id, variant_key)``, it embeds the option
        values so it changes when a supplier retitles a colour, and it is not
        URL-safe. Three independent disqualifications.
        """

        row = merchant_center_feed.feed_row(
            apparel_listing(variant_key="option1:s|option2:white"))
        serialised = "".join(str(v) for v in row.values())
        self.assertNotIn("option1:", serialised)
        self.assertNotIn("|", row["g:id"])
        self.assertTrue(
            re.fullmatch(r"[A-Za-z0-9._~-]+", row["g:id"]),
            f"g:id {row['g:id']!r} must be URL-safe; variant_key is not."
        )

    def test_mutation_13_item_group_id_is_not_invented_before_grouping_semantics_exist(self):
        """Grouping apparel variants requires knowing which axis is colour and which
        is size. Deliverable A shows we do not know. So no grouping key may be emitted.
        """

        row = merchant_center_feed.feed_row(apparel_listing())
        self.assertNotIn(
            "g:item_group_id", row,
            "item_group_id implies a grouping semantics that does not exist yet. It "
            "becomes correct at the same moment apparel attributes do, not before."
        )

    def test_the_feed_emits_exactly_the_tags_it_is_known_to_emit(self):
        """A backstop for every mutation above.

        Each forbidden change ends by adding a tag. Pinning the whole set catches
        additions this file did not anticipate, which is most of them.
        """

        row = merchant_center_feed.feed_row(apparel_listing())
        self.assertEqual(
            set(row), EXPECTED_TAGS,
            "The feed's tag set changed. If a tag was added deliberately, confirm it "
            "is sourced from an asserted value rather than a derived one, then update "
            "EXPECTED_TAGS and say where the value comes from."
        )


class GeneratingAFeedIsNotDistributionTestCase(unittest.TestCase):
    """Mutations 14 and 15: the two failures of reporting rather than of data."""

    def test_mutation_15_building_a_row_is_not_evidence_google_ingested_it(self):
        """``feed_row`` returns a dict. That is the whole of what it knows.

        Generation is local; ingestion is a remote event. ``feed_eligible`` is our
        verdict about our own row and says nothing about Google's. These two are the
        mutations most likely to pass accidentally, because the temptation is to read
        "we emitted it correctly" as "it worked".
        """

        row = merchant_center_feed.feed_row(apparel_listing())
        self.assertIsInstance(row, dict)
        for claim in ("ingested", "accepted", "approved_by_google", "submitted",
                      "distributed", "live_on_google"):
            self.assertNotIn(claim, row,
                             f"the feed row asserted {claim!r} about Google's state")

    def test_mutation_14_no_module_claims_an_account_or_an_acceptance(self):
        """No Merchant Center account exists. Nothing may report otherwise.

        Acceptance is a claim only Google can make, and nothing in this system has
        ever heard back from a search provider.
        """

        for name in ("mark_ingested", "mark_accepted", "report_accepted",
                     "merchant_account_id", "submit_to_merchant_center",
                     "push_to_google"):
            self.assertFalse(
                hasattr(merchant_center_feed, name),
                f"{name} appeared on merchant_center_feed. The feed is a file Google "
                "may fetch; it is not an API call and it is not a confirmation."
            )

    def test_the_feed_is_scheduled_fetch_xml_not_a_retired_api_client(self):
        """Scheduled-fetch XML remains valid; the Content API sunset does not apply.

        Pinned because "the Content API was retired" invites rebuilding a working
        feed on the Merchant API for no reason.
        """

        self.assertEqual(merchant_center_feed.FEED_PATH, "/feeds/merchant-center.xml")
        xml = merchant_center_feed.feed_xml([apparel_listing()])
        self.assertIn("http://base.google.com/ns/1.0", xml)
        self.assertIn("<rss", xml)


if __name__ == "__main__":
    unittest.main()
