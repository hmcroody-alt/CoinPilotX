"""Every signal must come from the thing it is named after.

``ranking.py`` is scrupulous. ``choose_reason`` adds a claim only when the
evidence for that exact claim is present, and each score term is documented at
its site with a reason for its weight. None of that care survives being handed
the wrong data, and it was being handed the wrong data: ``_interest_profile``
read ``marketplace_saved_products`` once and returned it under three different
keys, so a viewer who had saved a pair of shoes was told "Because you viewed
Shoes" and "From sellers you follow" about a seller they had never followed.

A reason code is not decoration. It is a factual claim about the viewer's own
history, shown to the only person in the world who can check it, and a false one
costs more trust than no reason at all would.

The other half is time. Every engagement count was lifetime, which cannot
express the one thing a commerce ranker most needs to know — whether interest is
happening *now*. "Trending" fired on a lifetime click total, and a lifetime total
only rises, so trending was a permanent and unfalsifiable property a listing
earned once and could never lose.

These tests are written to fail against the code as it was. Each one names, in
its own body, the wrong answer it would have gotten.
"""

from __future__ import annotations

from services.commerce_discovery import config, engine, preferences, ranking, schema


class TestTrendingIsAClaimAboutNow:
    """A badge you cannot lose is not a measurement."""

    def test_recent_clicks_make_a_listing_trending(self, market):
        market.history([4], impressions=200, clicks=40, age_days=1)
        cur = market.conn.cursor()
        stats = engine._listing_stats(cur, [4])
        assert stats[4]["recent_clicks"] == 40
        assert ranking.choose_reason({}, {}, stats=stats[4]) == ranking.REASON_TRENDING

    def test_old_clicks_do_not(self, market):
        # Identical volume, thirty days old. Under the lifetime read this was
        # indistinguishable from the case above and returned "trending" — a
        # listing's biography presented as its current temperature.
        market.history([4], impressions=200, clicks=40, age_days=30)
        cur = market.conn.cursor()
        stats = engine._listing_stats(cur, [4])
        assert stats[4]["clicks"] == 40, "lifetime history is still counted"
        assert stats[4]["recent_clicks"] == 0
        assert ranking.choose_reason({}, {}, stats=stats[4]) != ranking.REASON_TRENDING

    def test_the_claim_expires_as_the_clock_moves(self, market):
        # The strongest form of the property: one listing, one set of clicks, and
        # nothing changing but the time. Falsifiability means the answer flips.
        market.history([4], impressions=200, clicks=40, age_days=1)
        cur = market.conn.cursor()
        before = ranking.choose_reason({}, {}, stats=engine._listing_stats(cur, [4])[4])
        market.clock.advance(config.trend_window_seconds() + 3600)
        after = ranking.choose_reason({}, {}, stats=engine._listing_stats(cur, [4])[4])
        assert before == ranking.REASON_TRENDING
        assert after != ranking.REASON_TRENDING

    def test_the_bar_is_a_real_one(self, market):
        # Two clicks is not a trend. A threshold of one would make every product
        # anybody touched trending, which is the same as having no badge.
        market.history([4], impressions=40, clicks=2, age_days=1)
        cur = market.conn.cursor()
        stats = engine._listing_stats(cur, [4])
        assert stats[4]["recent_clicks"] < config.trend_min_clicks()
        assert ranking.choose_reason({}, {}, stats=stats[4]) != ranking.REASON_TRENDING


class TestConversionIsMeasuredOverAWindowWhenTheWindowIsWorthMeasuring:
    """Recency wins above the volume gate. Below it, information wins."""

    def test_a_collapse_is_visible(self, market):
        # A listing that converted well for a year and has converted nothing this
        # week. The lifetime rate says it is excellent; it is not, any more.
        lifetime_only = {"impressions": 1000, "clicks": 200, "recent_impressions": 0, "recent_clicks": 0}
        collapsed = {"impressions": 1000, "clicks": 200, "recent_impressions": 400, "recent_clicks": 2}
        assert ranking.conversion_probability(collapsed) < ranking.conversion_probability(lifetime_only)

    def test_a_thin_window_does_not_overrule_a_thick_history(self, market):
        gate = config.conversion_window_min_impressions()
        thin = {"impressions": 1000, "clicks": 200, "recent_impressions": gate - 1, "recent_clicks": 0}
        no_window = {"impressions": 1000, "clicks": 200}
        # Four impressions and no clicks is not a rate of zero, it is an absence
        # of evidence, and must not outweigh a thousand impressions.
        assert ranking.conversion_probability(thin) == ranking.conversion_probability(no_window)

    def test_the_gate_is_the_only_thing_that_switches_it(self, market):
        gate = config.conversion_window_min_impressions()
        below = {"impressions": 1000, "clicks": 200, "recent_impressions": gate - 1, "recent_clicks": 0}
        at = {"impressions": 1000, "clicks": 200, "recent_impressions": gate, "recent_clicks": 0}
        assert ranking.conversion_probability(at) < ranking.conversion_probability(below)

    def test_a_rising_listing_is_rewarded(self, market):
        flat = {"impressions": 1000, "clicks": 20, "recent_impressions": 200, "recent_clicks": 4}
        rising = {"impressions": 1000, "clicks": 20, "recent_impressions": 200, "recent_clicks": 60}
        assert ranking.conversion_probability(rising) > ranking.conversion_probability(flat)

    def test_no_stats_is_the_neutral_prior_not_zero(self, market):
        # An unproven listing must not be punished for having no history, or the
        # catalogue can never introduce anything.
        assert ranking.conversion_probability(None) == ranking.NEUTRAL
        assert ranking.conversion_probability({}) == ranking.NEUTRAL


class TestEachProfileKeyComesFromItsOwnSource:
    """Three keys, three reads. It used to be three keys, one read."""

    def _profile(self, market):
        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        return engine._interest_profile(cur, market.viewer_id, subject_ref=policy.subject_ref)

    def test_a_save_fills_topics(self, market):
        market.save(1)  # listing 1 is "shoes"
        profile = self._profile(market)
        assert "shoes" in profile["topics"]

    def test_a_save_does_not_claim_a_view(self, market):
        # The original defect, stated plainly. Saving is not viewing, and the
        # card used to say "Because you viewed Shoes" on this evidence alone.
        market.save(1)
        assert self._profile(market)["viewed_categories"] == ()

    def test_a_save_does_not_claim_a_follow(self, market):
        # The same defect wearing a different name. `followed_sellers` was also
        # filled from saved products, so the card said "From sellers you follow"
        # about a store the viewer had never followed.
        market.save(1)
        assert self._profile(market)["followed_sellers"] == frozenset()

    def test_a_real_view_fills_viewed_categories(self, market):
        placements = market.serve("feed")
        market.render(placements)
        market.engage(placements[0], "click")
        viewed = self._profile(market)["viewed_categories"]
        shown = market.category_of(placements[0]["product"]["listing_id"])
        assert shown in viewed

    def test_a_real_follow_fills_followed_sellers(self, market):
        market.follow_seller(1003)
        assert self._profile(market)["followed_sellers"] == frozenset({1003})

    def test_views_age_out_of_the_profile(self, market):
        placements = market.serve("feed")
        market.render(placements)
        market.engage(placements[0], "click")
        assert self._profile(market)["viewed_categories"] != ()
        market.clock.advance(config.exposure_lookback_seconds() + 3600)
        # Someone who looked at lamps last month is not currently shopping for
        # lamps, and telling them otherwise is the engine talking about itself.
        assert self._profile(market)["viewed_categories"] == ()

    def test_an_absent_follow_graph_costs_claims_and_nothing_else(self, market):
        market.save(1)
        market.conn.cursor().execute("DROP TABLE pulse_follows")
        profile = self._profile(market)
        # Fewer signals, never wrong ones: the read fails, the request does not,
        # and no card claims a follow it cannot substantiate.
        assert profile["followed_sellers"] == frozenset()
        assert "shoes" in profile["topics"]


class TestTheCallSitePassesTheSubject:
    """The profile's view history is keyed by ``subject_ref``, and a default
    argument is a silent way to never have any."""

    def test_the_serve_path_supplies_a_subject_ref(self, market, monkeypatch):
        seen: list[dict] = []
        real = engine._interest_profile

        def recorder(cur, user_id, *, subject_ref=""):
            seen.append({"subject_ref": subject_ref})
            return real(cur, user_id, subject_ref=subject_ref)

        monkeypatch.setattr(engine, "_interest_profile", recorder)
        market.serve("feed")
        assert seen, "the personalised feed must build a profile"
        # Without this the `if subject_ref:` branch never runs, view history is
        # always empty, and both the claim and half of `predicted_interest` are
        # disabled — while every test that stubs the profile still passes.
        assert all(entry["subject_ref"] for entry in seen)

    def test_it_matches_the_policy_the_request_ranked_with(self, market, monkeypatch):
        seen: list[str] = []
        real = engine._interest_profile
        monkeypatch.setattr(
            engine,
            "_interest_profile",
            lambda cur, uid, *, subject_ref="": (seen.append(subject_ref), real(cur, uid, subject_ref=subject_ref))[1],
        )
        market.serve("feed")
        cur = market.conn.cursor()
        expected = preferences.viewer_policy(cur, market.viewer_id).subject_ref
        assert seen == [expected]


class TestBothInterestBranchesAreReachable:
    """``predicted_interest`` takes ``max(topic, viewed * 1.1)``. With one source
    behind both arguments that compared a value to itself, the 1.1 made the
    viewed branch win unconditionally, and the topic branch was dead code that
    looked alive.

    Honest about what these are. The four tests calling ``predicted_interest``
    directly pass separated arguments and therefore passed before the fix too —
    they pin the *contract* the engine now honours, not the wiring. The guard is
    :meth:`test_the_two_sources_can_disagree`, which asserts the property the old
    code made impossible: that the two arguments are ever different values.
    """

    def test_the_two_sources_can_disagree(self, market):
        # This is the whole defect in one assertion. `topics` and
        # `viewed_categories` were the same tuple from the same query, so no input
        # could make them differ and `max` had nothing to choose between.
        placements = market.serve("feed")
        market.render(placements)
        market.engage(placements[0], "click")
        viewed_category = market.category_of(placements[0]["product"]["listing_id"])
        saved = next(
            listing_id
            for listing_id in range(1, 20)
            if market.category_of(listing_id) != viewed_category
        )
        market.save(saved)

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        profile = engine._interest_profile(cur, market.viewer_id, subject_ref=policy.subject_ref)
        assert set(profile["topics"]) != set(profile["viewed_categories"])
        assert market.category_of(saved) in profile["topics"]
        assert viewed_category in profile["viewed_categories"]

    def test_topics_alone_produce_interest(self, market):
        listing = {"category": "shoes", "title": "Product 1"}
        assert ranking.predicted_interest(listing, ("shoes",), ()) > 0

    def test_views_alone_produce_interest(self, market):
        listing = {"category": "shoes", "title": "Product 1"}
        assert ranking.predicted_interest(listing, (), ("shoes",)) > 0

    def test_a_view_outranks_a_save_of_the_same_category(self, market):
        # The documented intent — purchase intent dominates rather than averages
        # — is only a real preference once the two arguments can differ.
        listing = {"category": "shoes", "title": "Product 1"}
        viewed = ranking.predicted_interest(listing, (), ("shoes",))
        saved = ranking.predicted_interest(listing, ("shoes",), ())
        assert viewed >= saved

    def test_an_unrelated_interest_does_not_score(self, market):
        listing = {"category": "lamps", "title": "Product 9"}
        assert ranking.predicted_interest(listing, ("shoes",), ("shoes",)) == 0


class TestARelatednessClaimNamesSomethingOnScreen:
    """"Related to this post" is a claim about the frame, not about the viewer.

    Every other reason code is falsifiable from the viewer's own history, which is
    what the rest of this file is about. This one is falsifiable at a glance: the
    viewer either can see the thing the card says it relates to, or they cannot.

    It was not true on two of the six surfaces. `engine` picked the wording as
    ``REASON_SIMILAR_PRODUCT if surface == "product_detail" else REASON_CONTEXT``,
    so a Messenger strip inside a conversation and a Marketplace shelf on the shop
    tab both said "related to this post" with no post anywhere on the screen —
    measured at eight of eight cards on the Marketplace shelf. Neither is reachable
    from the shipped clients, which send no context for those surfaces, but the
    serve route accepts a context for any surface in ``SURFACES``; the claim was one
    new caller away, and the web build is the next new caller.

    On Messenger it is not merely inaccurate. The code renders through
    ``commerce:discovery.subtitle.related_to_this_post``, whose English reads
    "Related to what you're reading" — under a product inside a private
    conversation, that tells the reader their messages were read to choose it.
    Nothing in this pipeline can see a message. The caption would be advertising a
    capability the product does not have.

    The tests below pass a context to every surface, because a context is the only
    way to make the claim fire at all — and the point is that a *supplied* context
    is not by itself a licence to claim relatedness.
    """

    #: Matches the conftest catalogue, so relevance saturates and the claim fires
    #: wherever it is permitted. A context that matched nothing would make every
    #: assertion below pass for the wrong reason.
    CONTEXT = {"category": "shoes"}

    def reasons(self, market, surface):
        return [placement["reason"] for placement in market.serve(surface, context=self.CONTEXT)]

    def test_a_messenger_card_does_not_claim_to_relate_to_a_post(self, market):
        reasons = self.reasons(market, "messenger")
        assert reasons, "control: the surface must still be servable with a context"
        assert ranking.REASON_CONTEXT not in reasons

    def test_a_marketplace_shelf_card_does_not_claim_to_relate_to_a_post(self, market):
        reasons = self.reasons(market, "marketplace")
        assert reasons, "control: the surface must still be servable with a context"
        assert ranking.REASON_CONTEXT not in reasons

    def test_neither_surface_claims_similarity_to_a_product_either(self, market):
        # The wrong fix, named so it cannot be mistaken for the right one. There is
        # no product on screen on these surfaces any more than there is a post, so
        # swapping one wording for the other would move the false claim rather than
        # remove it.
        for surface in ("messenger", "marketplace"):
            assert ranking.REASON_SIMILAR_PRODUCT not in self.reasons(market, surface)

    def test_the_cards_are_still_served_and_still_explained(self, market):
        # Dropping the claim must not drop the card or leave it captionless. The
        # viewer still gets a reason; it is just one that is true.
        for surface in ("messenger", "marketplace"):
            reasons = self.reasons(market, surface)
            assert reasons
            assert all(reason in ranking.REASON_PRIORITY for reason in reasons)

    def test_the_claim_is_unchanged_where_there_is_a_post(self, market):
        # The other half. A rule that removed the claim everywhere would pass all
        # four tests above and destroy the feature.
        for surface in ("feed", "reels", "post_detail"):
            assert ranking.REASON_CONTEXT in self.reasons(market, surface)

    def test_every_surface_declares_its_own_wording_or_none(self, market):
        # The map is exhaustive over the registered surfaces, so a surface added
        # later is a missing key — which reads as "no claim" — rather than
        # inheriting the most specific claim in the vocabulary by default.
        for surface in schema.SURFACES:
            assert engine.CONTEXT_CLAIM.get(surface) in (
                None, ranking.REASON_CONTEXT, ranking.REASON_SIMILAR_PRODUCT
            )
        assert set(engine.CONTEXT_CLAIM) < set(schema.SURFACES)

    def test_an_unregistered_wording_produces_no_claim(self, market):
        # `choose_reason` used to answer an unrecognised declaration with
        # `REASON_CONTEXT` — the single most specific claim available — so a typo
        # became the exact mislabelling this class is about. Silence is the safe
        # answer to "I don't know what to call this".
        reason = ranking.choose_reason(
            {"category": "shoes"},
            {"relevance": 1.0},
            has_context=True,
            context_reason="not_a_registered_code",
        )
        assert reason != ranking.REASON_CONTEXT

    def test_none_is_an_accepted_declaration(self, market):
        # Not a TypeError and not a fallback: the caller is allowed to say that no
        # relatedness wording applies.
        reason = ranking.choose_reason(
            {"category": "shoes"},
            {"relevance": 1.0},
            has_context=True,
            context_reason=None,
        )
        assert reason in ranking.REASON_PRIORITY
        assert reason != ranking.REASON_CONTEXT
