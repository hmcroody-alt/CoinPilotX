"""Which listings may be pushed at someone who did not ask for them.

The distinction this module exists to keep is between *may be sold* and *may be
inserted into a stranger's feed*, and the tests are written to fail if those two
ever collapse into each other — in either direction:

* Discovery must be a strict **subset** of purchasable. A recommendation for
  something the buyer cannot then buy is the worst possible first impression of
  the whole feature, and it is the kind of bug that only appears for listings
  that sold out an hour ago.
* Discovery must be allowed to be **stricter**. A missing cover image or a live
  moderation flag blocks the push without touching the sale.

The nullable columns carry most of the risk, and one of them is misnamed.
``marketplace_listings.safety_score`` holds a **risk** score: bot.py writes
``revenue_safety_engine.marketplace_listing_review()["risk_score"]`` into it, 0
clean and 100 worst. Reading the name rather than the writer inverts the gate in
both directions at once — it rejects every clean listing, and the only listings
it admits are the ones the scorer wanted a human to look at. This file asserted
the inverted version for a while, because the fixture invented a value instead of
mirroring what production writes.

So the orientation is now pinned against the real scorer
(:class:`TestTheRiskScaleRunsTheWayTheScorerWritesIt`) rather than against a
number this file chose. A fixture may invent a value; it may not invent which
direction that value points.
"""

import pytest

from services.commerce_discovery import eligibility


def parse_price(label, currency):
    """Stand-in for the monolith's parser: "$49.99" -> (4999, "USD")."""
    text = str(label or "").strip().replace("$", "").replace(",", "")
    if not text:
        raise ValueError("unparseable")
    return int(round(float(text) * 100)), currency or "USD"


def listing(**overrides):
    """A listing that passes every gate, so each test breaks exactly one.

    The keys are the ones :data:`eligibility.CANDIDATE_COLUMNS` actually
    projects, not plausible-looking synonyms. That matters more here than in
    most fixtures: the publication rules answer "unknown" for a column that was
    not projected, and two of them *pass* when unknown — so a fixture written
    with ``seller_approved`` instead of ``seller_status`` would not fail loudly,
    it would quietly test a widened gate.
    """
    base = {
        "id": 1,
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "seller_store_name": "M&W Store",
        "quantity": 5,
        "cover_image_url": "https://cdn.example/s.jpg",
        "price_label": "$49.99",
        "currency": "USD",
        "moderation_reason": "",
        # What the scorer writes for a listing it found nothing wrong with.
        "safety_score": 0,
        "seller_risk_score": 0,
    }
    base.update(overrides)
    return base


class TestTheHappyPath:
    def test_a_complete_listing_is_eligible(self):
        # If this fails, every other test in the file is asserting the wrong
        # thing — they all work by breaking one field of this row.
        assert eligibility.gate(listing(), parse_price) == ""
        assert eligibility.is_eligible(listing(), parse_price) is True


class TestDiscoveryIsASubsetOfPurchasable:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("status", "draft"),
            ("approval_status", "pending"),
            ("seller_status", "suspended"),
            ("quantity", 0),
        ],
    )
    def test_anything_not_for_sale_is_not_recommended(self, field, value):
        # Reported as `not_purchasable` rather than as the specific downstream
        # failure: it explains every later gate and none of them explains it.
        assert eligibility.gate(listing(**{field: value}), parse_price) == "not_purchasable"


class TestDiscoveryIsStricterThanPurchasable:
    @pytest.mark.parametrize("url", ["", None, "   ", "null", "NONE", "undefined"])
    def test_a_card_with_no_picture_is_not_an_offer(self, url):
        # The card is mostly image. Without one it renders as a grey rectangle
        # with a price, which reads as broken rather than as something to buy.
        assert eligibility.gate(listing(cover_image_url=url), parse_price) == "no_cover_image"

    def test_a_relative_path_still_counts_as_a_picture(self):
        # The CDN prefix is applied at serialization, so a relative path is a
        # real image and rejecting it would silently shrink the catalogue.
        assert eligibility.has_cover_image({"cover_image_url": "/media/x.jpg"}) is True

    @pytest.mark.parametrize("label", ["", "Request access", "Contact for price", "$0.00"])
    def test_a_card_that_cannot_show_a_number_is_not_an_offer_either(self, label):
        assert eligibility.gate(listing(price_label=label), parse_price) == "no_resolvable_price"

    def test_a_flag_raised_after_approval_holds_the_push_without_pulling_the_sale(self):
        # `approval_status='approved'` means a moderator said yes once; it does
        # not mean nothing has been flagged since.
        flagged = listing(moderation_reason="reported: counterfeit")
        assert eligibility.gate(flagged, parse_price) == "moderation_flagged"
        # And the asymmetry that makes this safe: the listing is untouched as a
        # sale. Discovery may be more conservative than commerce, never less.
        assert eligibility.moderation_clean(flagged) is False
        assert flagged["status"] == "published"

    def test_a_listing_at_the_risk_ceiling_is_not_pushed(self):
        at_ceiling = eligibility.MAX_LISTING_RISK
        assert eligibility.gate(listing(safety_score=at_ceiling), parse_price) == "listing_risk"

    def test_a_seller_at_the_risk_ceiling_is_not_promoted(self):
        at_ceiling = eligibility.MAX_SELLER_RISK
        assert eligibility.gate(listing(seller_risk_score=at_ceiling), parse_price) == "seller_risk"


class TestTheNullableColumnsFailOpen:
    @pytest.mark.parametrize("raw", [None, "", "not-a-number", []])
    def test_an_unscored_listing_is_not_treated_as_risky(self, raw):
        # Most of the catalogue predates the scorer, and an unparseable value is
        # a data problem rather than evidence about the product.
        assert eligibility.listing_risk_ok({"safety_score": raw}) is True

    @pytest.mark.parametrize("raw", [None, "", "not-a-number"])
    def test_a_seller_with_no_risk_score_is_not_treated_as_risky(self, raw):
        assert eligibility.seller_ok({"seller_risk_score": raw}) is True

    def test_a_scored_listing_is_still_held_to_the_ceiling(self):
        # Failing open on *missing* must not become failing open on *present*.
        assert eligibility.listing_risk_ok({"safety_score": eligibility.MAX_LISTING_RISK - 1}) is True
        assert eligibility.listing_risk_ok({"safety_score": eligibility.MAX_LISTING_RISK}) is False


class TestTheRiskScaleRunsTheWayTheScorerWritesIt:
    """The gate's orientation, pinned against the module that fills the column.

    Every other test here could be satisfied by a consistent-but-inverted gate,
    because they compare the fixture against the threshold and both are chosen
    in this file. These compare against ``revenue_safety_engine``, which is what
    bot.py actually runs on publish and on resume.
    """

    def test_a_clean_listing_scores_below_the_ceiling(self):
        from services import revenue_safety_engine

        review = revenue_safety_engine.marketplace_listing_review({
            "title": "Denim Jacket",
            "description": "Cotton denim jacket, regular fit.",
            "category": "Women's Clothing > Outerwear",
        })
        assert review["status"] == "review_ready"
        assert review["risk_score"] < eligibility.MAX_LISTING_RISK
        assert eligibility.listing_risk_ok({"safety_score": review["risk_score"]}) is True

    def test_a_listing_the_scorer_routes_to_a_moderator_is_held_back(self):
        from services import revenue_safety_engine

        review = revenue_safety_engine.marketplace_listing_review({
            "title": "Sure win trading course",
            "description": "Risk free method, no experience needed.",
            "category": "Education",
        })
        # Not blocked — still purchasable. Just not something to push at a
        # stranger, which is the whole asymmetry this module exists for.
        assert review["status"] == "needs_review"
        assert eligibility.listing_risk_ok({"safety_score": review["risk_score"]}) is False

    def test_the_ceiling_is_the_scorers_own_review_boundary(self):
        """Pins the coupling rather than the number.

        ``MAX_LISTING_RISK`` is not an independent judgement — it is "anything
        the scorer would send to a human". If the scorer moves that boundary and
        this constant does not, discovery silently starts pushing listings that
        are sitting in a moderation queue.
        """
        from services import revenue_safety_engine

        at_ceiling = revenue_safety_engine.score_text("", "", "trading signals")
        assert at_ceiling["status"] == "needs_review"
        assert at_ceiling["risk_score"] >= eligibility.MAX_LISTING_RISK

        below = revenue_safety_engine.score_text("", "risk free", "")
        assert below["status"] == "review_ready"
        assert below["risk_score"] < eligibility.MAX_LISTING_RISK

    def test_zero_is_the_clean_end_of_the_scale(self):
        """The whole defect in one line: 0 means nothing was found wrong."""
        assert eligibility.listing_risk_ok({"safety_score": 0}) is True
        assert eligibility.listing_risk_ok({"safety_score": 100}) is False


class TestPriceResolution:
    def test_returns_minor_units_and_currency(self):
        assert eligibility.resolvable_price(listing(), parse_price) == (4999, "USD")

    def test_a_parser_that_raises_is_a_missing_price_not_a_crash(self):
        def explodes(label, currency):
            raise RuntimeError("boom")

        # The engine ranks thousands of rows; one unparseable price must drop
        # one listing, never the request.
        assert eligibility.resolvable_price(listing(), explodes) is None

    def test_carries_the_listing_currency_rather_than_assuming_dollars(self):
        amount, currency = eligibility.resolvable_price(
            listing(price_label="49.99", currency="EUR"), parse_price
        )
        assert (amount, currency) == (4999, "EUR")


class TestGateOrdering:
    def test_reports_the_earliest_failure_when_several_apply(self):
        # An operator reading `no_cover_image` on an unpublished listing would
        # go and add a picture to a draft.
        broken = listing(status="draft", cover_image_url="", price_label="")
        assert eligibility.gate(broken, parse_price) == "not_purchasable"
