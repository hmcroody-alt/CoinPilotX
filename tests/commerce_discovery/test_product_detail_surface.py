"""The product page: a row that must not recommend the product it sits under.

Two failures are unique to this surface, and neither one is visible on any of the
other five.

**A product page recommending itself.** Every other surface can repeat a product
the viewer saw a minute ago and the worst outcome is a dull row. Here the
duplicate is the page's own subject, sitting under a heading that says "similar
products" — a card advertising the thing the viewer already has open. It is the
one wrong answer on this screen that every user would notice.

**A relatedness claim that is not true.** This is the only surface whose reason
code makes an assertion about a *comparison*: `similar_to_this_product`. Saying
it on a card that was ranked on browsing history would be the brief's forbidden
fabricated reason, and the ranker cannot tell the difference on its own — it is
surface-agnostic by design, so the substitution happens in the engine, where the
surface is known.

Both are tested end to end against the real catalogue and the real eligibility
SQL, because both are properties of a *response*, not of a function.
"""

from __future__ import annotations

from services.commerce_discovery import config, pool, ranking, router, schema


ANCHOR = 1  # "Product 1", category "shoes" — see conftest's catalogue.


class TestTheSurfaceIsRegistered:
    """Registration, from the angles the shared gate does not cover.

    `test_commerce_discovery_surface_registration.py` proves every surface in
    `SURFACES` has a policy, a cap and a cadence. What it cannot say is whether
    the *numbers* are the ones this surface wants, which is the part a retune
    would silently change.
    """

    def test_the_surface_exists(self):
        assert "product_detail" in schema.SURFACES

    def test_the_floor_is_below_the_feeds_and_above_nothing(self):
        # The one surface deliberately more permissive than the feed. The viewer
        # is mid-purchase and asked for this page, so a mediocre suggestion costs
        # little — but the floor still has to exist, because a row of six
        # unrelated products under "Similar products" is worse than no row.
        floor = config.min_score("product_detail")
        assert floor < config.min_score("feed")
        assert floor > 0.0

    def test_the_whole_row_may_be_one_category(self):
        # Everywhere else a category cap is diversity. Here it would be a bug:
        # on a product page relatedness *is* category similarity, and capping it
        # would guarantee that some of the cards under "Similar products" were
        # not similar.
        caps = router.policy_for("product_detail")
        assert router._CATEGORY_CAPS["product_detail"] >= config.surface_caps()["product_detail"][0]
        assert caps.max_per_seller < config.surface_caps()["product_detail"][0]

    def test_the_session_cap_is_navigation_sized_not_interruption_sized(self):
        # Every other cap answers "how often may we interrupt". This one answers
        # "how many product pages may a shopper open", and a shopper comparing
        # twenty pairs of shoes is doing the thing the shop is for.
        per_session = config.surface_caps()["product_detail"][1]
        assert per_session > config.surface_caps()["feed"][1]


def ids_of(placements) -> set[int]:
    return {int(placement["product"]["listing_id"]) for placement in placements}


class TestTheRowNeverContainsItsOwnProduct:
    """Each case carries its own control, for a reason worth stating.

    The obvious test — "serve with an exclusion, assert the id is missing" —
    passes just as happily when the surface serves nothing at all, and a surface
    that serves nothing is a much larger bug than the one being guarded. So every
    case below first proves the product *is* servable in the identical request
    without the exclusion, then names it as the anchor. Nothing is rendered
    between the two calls and the clock does not move, so the only difference
    between them is the exclusion itself.

    Picking the anchor out of the response rather than hard-coding an id matters
    too: the pool starts at a rotation offset, so most of a hundred-product
    catalogue is legitimately absent from any single response.
    """

    def test_the_anchor_is_absent_from_the_row(self, market):
        baseline = market.serve("product_detail")
        assert baseline, "control: the fixture must be servable on this surface"
        anchor = sorted(ids_of(baseline))[0]

        placements = market.serve("product_detail", exclude_listing_ids=(anchor,))
        assert placements, "excluding one product must not empty the row"
        assert anchor not in ids_of(placements)

    def test_the_anchor_survives_a_truncated_sql_exclusion_list(self):
        # The ordering claim in `_hard_exclusions`: with more cooled-down
        # products than the SQL list can hold, the id the caller named is the one
        # that must not be the one dropped.
        crowd = tuple(range(500, 500 + pool.MAX_SQL_EXCLUSIONS + 50))

        class Policy:
            suppressed_listings = crowd

        class State:
            recent_products = ()
            purchased = ()

            def seconds_since_product(self, _listing_id):
                return 0

        excluded = pool._hard_exclusions(Policy(), State(), 0, frozenset({ANCHOR}))
        assert len(excluded) == pool.MAX_SQL_EXCLUSIONS
        assert excluded[0] == ANCHOR

    def test_python_side_rejection_holds_when_the_sql_list_loses_the_anchor(self, market, monkeypatch):
        """Defence in depth, proven by removing the first line of defence.

        `_hard_exclusions` is an optimisation — the module's own docstring says
        so — and an optimisation is allowed to be changed. What must not change
        is that `_reject` catches the anchor regardless, so this test simulates
        the truncation the ordering above is designed to prevent and asserts the
        answer is still correct.
        """
        anchor = sorted(ids_of(market.serve("product_detail")))[0]

        # The stub drops `excluded_ids` and forwards everything else untouched.
        # `*args, **kwargs` rather than a fixed parameter list, because a stub
        # that restates the signature makes every future argument to the real
        # function a TypeError here — and `engine.serve` catches broadly, so that
        # TypeError presents as an empty surface rather than as a failure anyone
        # can read. It did exactly that when `product_cap` was added.
        real = pool._hard_exclusions
        monkeypatch.setattr(
            pool,
            "_hard_exclusions",
            lambda policy, state, cooldown, _excluded=frozenset(), *args, **kwargs: real(
                policy, state, cooldown, frozenset(), *args, **kwargs
            ),
        )
        placements = market.serve("product_detail", exclude_listing_ids=(anchor,))
        assert placements, "the scan must still return a row, just not the anchor's"
        assert anchor not in ids_of(placements)


class TestTheRelatednessClaimIsTrue:
    def test_a_matching_anchor_earns_the_product_wording(self, market):
        placements = market.serve(
            "product_detail",
            limit=1,
            context={"category": market.category_of(ANCHOR)},
            exclude_listing_ids=(ANCHOR,),
        )
        assert placements[0]["reason"] == ranking.REASON_SIMILAR_PRODUCT

    def test_the_post_wording_never_reaches_a_product_page(self, market):
        # The failure this substitution exists to prevent: "Related to this post"
        # printed under a product, describing a post that is not on screen.
        for placement in market.serve(
            "product_detail",
            context={"category": market.category_of(ANCHOR)},
            exclude_listing_ids=(ANCHOR,),
        ):
            assert placement["reason"] != ranking.REASON_CONTEXT

    def test_the_product_wording_never_reaches_a_post(self, market):
        # And the inverse, so the substitution stays a substitution rather than
        # becoming a rename. A feed card is beside a post, not beside a product.
        for placement in market.serve("feed", context={"category": "shoes"}):
            assert placement["reason"] != ranking.REASON_SIMILAR_PRODUCT

    def test_an_unrelated_anchor_does_not_claim_similarity(self, market):
        # The claim is earned by the relevance score, not by the surface. A
        # product page whose category matches nothing in the catalogue still gets
        # a row — this surface's floor is low on purpose — but it must not label
        # that row "similar".
        for placement in market.serve(
            "product_detail",
            context={"category": "taxidermy", "topic": "vintage accordion repair"},
            exclude_listing_ids=(ANCHOR,),
        ):
            assert placement["reason"] != ranking.REASON_SIMILAR_PRODUCT

    def test_both_context_reasons_are_known_to_the_priority_order(self):
        # `choose_reason` falls back to the post wording for a code it does not
        # recognise, so an unregistered code would silently reintroduce exactly
        # the mislabelling this file is about.
        assert ranking.REASON_SIMILAR_PRODUCT in ranking.REASON_PRIORITY
        assert ranking.REASON_CONTEXT in ranking.REASON_PRIORITY
