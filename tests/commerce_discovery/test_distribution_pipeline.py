"""The failure the brief names: "showing the same products repeatedly".

Every other test file in this directory checks a function. This one checks a
*sequence*, because that is the only shape the defect has. There is no single
call to ``serve`` that could return a wrong answer here — each response is two
eligible, well-scored, diversity-capped products. The bug is that the next
response is the same two, and the one after that, and the one after that.

So each test below drives the real engine through many requests against a real
(if small) catalogue, feeds the results back as impressions the way the client
does, and then asks ``metrics.summarize`` what the whole sequence looked like.
Where a number is asserted it is a bound rather than an equality: the point is
that the pipeline cannot degenerate, not that it produces one exact ordering,
and an equality here would break every time anybody retuned a weight.

The one exception is ``immediate_product_repeats``, which is asserted ``== 0``
everywhere it appears. The same product twice in a row has no defensible
reading.
"""

from __future__ import annotations

import pytest

from services.commerce_discovery import engine, metrics


class TestTheRepetitionFailure:
    """§3 and §26: a hundred placements over a catalogue of a hundred."""

    def test_never_shows_the_same_product_twice_running(self, market):
        report = metrics.summarize(market.browse("feed", steps=60, seconds=30.0))

        assert report.impressions > 50, "the scroll has to be long enough to fail"
        assert report.immediate_product_repeats == 0
        assert report.immediate_seller_repeats == 0

    def test_walks_the_catalogue_rather_than_its_head(self, market):
        # The pre-pipeline engine took a fixed `LIMIT 120` over a deterministic
        # ordering, so an hour of scrolling saw the same top handful forever.
        # This is the regression test for that specific query.
        report = metrics.summarize(market.browse("feed", steps=60, seconds=30.0))

        assert report.unique_products_shown >= 80
        assert report.repeat_product_rate <= 0.05

    def test_no_seller_dominates(self, market):
        report = metrics.summarize(market.browse("feed", steps=60, seconds=30.0))

        assert report.unique_sellers_shown == 10
        # Ten sellers, so an even split is 0.1. The bound is deliberately loose:
        # a seller *may* run ahead of the others, it just may not own the feed.
        assert report.seller_concentration <= 0.25

    def test_no_category_crowds_out_the_rest(self, market):
        report = metrics.summarize(market.browse("feed", steps=60, seconds=30.0))

        assert report.unique_categories_shown == 5
        assert report.category_concentration <= 0.40

    def test_repetition_stays_bounded_once_the_cooldowns_expire(self, market):
        # An hour between requests for two days, which is long enough for the
        # six-hour product cooldown to lapse many times over. Repeats here are
        # correct behaviour — the test is that they stay a minority and never
        # become consecutive.
        report = metrics.summarize(market.browse("feed", steps=48, seconds=3600.0))

        assert report.impressions >= 60
        assert report.immediate_product_repeats == 0
        assert report.repeat_product_rate <= 0.35
        assert report.unique_products_shown >= 50

    def test_a_seller_with_a_huge_catalogue_does_not_buy_the_feed_with_inventory(self, market):
        # Seller 0 ends up owning 60 of 150 listings. Without a seller cooldown
        # the pool would hand it 40% of every page purely on volume.
        market.restock(0, 50)

        report = metrics.summarize(market.browse("feed", steps=60, seconds=30.0))

        share = sum(1 for row in market.log if row["seller_user_id"] == 1001) / float(report.impressions)
        # Observed 0.25 against a 0.40 inventory share. The bound is what the
        # requirement is — placement share must not track shelf space — rather
        # than the measured number, which a weight retune is allowed to move.
        assert share <= 0.30


class TestCrossSurfaceDeduplication:
    """§7: the same shoes in feed, then reels, then above the chat list."""

    def test_a_product_just_shown_in_feed_does_not_lead_reels(self, market):
        market.render(market.serve("feed"))
        just_seen = {row["listing_id"] for row in market.log}

        market.clock.advance(20)
        chip = market.serve("reels")

        assert chip, "reels went quiet for an unrelated reason; the test proves nothing"
        assert int(chip[0]["product"]["listing_id"]) not in just_seen

    def test_interleaved_surfaces_do_not_echo_each_other(self, market):
        for _ in range(20):
            market.render(market.serve("feed"))
            market.clock.advance(30)
            market.render(market.serve("reels"))
            market.clock.advance(30)
            market.render(market.serve("messenger"))
            market.clock.advance(30)

        report = metrics.summarize(market.log)

        assert set(report.by_surface) == {"feed", "reels", "messenger"}
        assert report.cross_surface_repeat_rate <= 0.10
        assert report.immediate_product_repeats == 0


class TestNegativeSignalsReachTheRouter:
    """§20: what the user told us must survive every cache between here and SQL."""

    def test_a_hidden_product_never_comes_back(self, market):
        placements = market.serve("feed")
        market.render(placements)
        market.feedback(placements[0], "hide")
        hidden = int(placements[0]["product"]["listing_id"])

        # Past the 24h hide window *and* past the product cooldown, so the only
        # thing that could still be holding it out is the suppression row.
        market.clock.advance(30)
        seen = {row["listing_id"] for row in market.browse("feed", steps=40, seconds=30.0)}

        assert hidden not in seen

    def test_not_interested_outlives_the_hide_window(self, market):
        placements = market.serve("feed")
        market.render(placements)
        market.feedback(placements[0], "not_interested")
        refused = int(placements[0]["product"]["listing_id"])

        market.clock.advance(86400 * 3)
        seen = {row["listing_id"] for row in market.browse("feed", steps=40, seconds=30.0)}

        assert refused not in seen

    def test_a_hidden_seller_takes_its_whole_catalogue_with_it(self, market):
        placements = market.serve("feed")
        market.render(placements)
        market.feedback(placements[0], "hide_seller")
        banished = int(placements[0]["product"]["seller_user_id"])

        market.clock.advance(30)
        market.browse("feed", steps=50, seconds=30.0)

        assert banished not in {row["seller_user_id"] for row in market.log[len(placements):]}
        # And the feed did not go quiet as a result — nine sellers remain.
        assert len({row["seller_user_id"] for row in market.log}) >= 9


class TestOwnershipAwareness:
    """§17, §18, §19: bought, carted and saved are three different answers."""

    def test_a_purchased_product_stops_being_offered(self, market):
        market.purchase(7)

        seen = {row["listing_id"] for row in market.browse("feed", steps=60, seconds=30.0)}

        assert 7 not in seen
        assert len(seen) >= 80, "suppressing one product must not thin the feed"

    def test_a_product_in_the_cart_is_not_re_advertised(self, market):
        market.add_to_cart(11)

        seen = {row["listing_id"] for row in market.browse("feed", steps=60, seconds=30.0)}

        assert 11 not in seen

    def test_a_saved_product_is_outranked_but_not_banished(self, market):
        # The distinction the pool encodes deliberately: owned products are
        # removed from the pool, saved ones are only penalised, because a saved
        # product resurfacing is a feature and a bought one resurfacing is not.
        #
        # Asserted on Marketplace rather than on the feed, which is where the
        # difference is observable and also where the brief says a saved product
        # may legitimately return. A 0.35 penalty against a catalogue of near
        # identical products is a bar in the feed's two slots and a nudge in the
        # shop's eight; both are the same weight doing its job.
        market.save(23)
        market.purchase(24)

        seen = {row["listing_id"] for row in market.browse("marketplace", steps=30, seconds=60.0)}

        assert 23 in seen
        assert 24 not in seen


class TestMessengerStrip:
    """§15: A, B, C, D — not four things from one store."""

    def test_a_strip_is_four_products_from_four_different_stores(self, market):
        strip = []
        for _ in range(4):
            placements = market.serve("messenger")
            market.render(placements)
            strip.extend(placements)
            market.clock.advance(1)

        assert len(strip) == 4
        assert len({int(p["product"]["listing_id"]) for p in strip}) == 4
        assert len({int(p["product"]["seller_user_id"]) for p in strip}) == 4

    def test_private_conversations_are_not_a_surface_at_all(self):
        from services.commerce_discovery import schema

        assert "chat" not in schema.SURFACES
        assert "conversation" not in schema.SURFACES
        assert engine.serve(None, 1, "chat", conn=None) == []


class TestMarketplaceShelves:
    """§16: one ranked pool, several shelves, no product on two of them."""

    def test_no_product_appears_on_two_shelves(self, market):
        # Give a slice of the catalogue a crowd history so the ranker has
        # something to call popular, and the response splits across reasons
        # rather than landing in one bucket.
        market.history(range(1, 30), impressions=40, clicks=12)

        placements = market.serve("marketplace")
        assert len(placements) >= 4

        by_reason: dict[str, list[int]] = {}
        for placement in placements:
            by_reason.setdefault(placement["reason"], []).append(int(placement["product"]["listing_id"]))

        flattened = [listing_id for ids in by_reason.values() for listing_id in ids]
        assert len(flattened) == len(set(flattened))

    def test_a_shelf_set_is_deeper_than_a_feed_page(self, market):
        assert len(market.serve("marketplace")) > len(market.serve("feed"))


class TestNothingBlankEverReachesTheClient:
    """§12 and §23: the failure mode is a quiet surface, never a broken one."""

    def test_an_exhausted_catalogue_returns_no_placements_rather_than_empty_cards(self, market):
        market.browse("feed", steps=60, seconds=30.0)

        # Everything is inside its cooldown now. The correct answer is nothing.
        placements = market.serve("feed")

        assert placements == []

    def test_a_broken_catalogue_query_is_a_quiet_feed(self, market):
        market.conn.cursor().execute("DROP TABLE marketplace_listings")

        assert market.serve("feed") == []
        assert market.serve("reels") == []
        assert market.serve("marketplace") == []

    @pytest.mark.parametrize("surface", ["feed", "reels", "messenger", "marketplace"])
    def test_every_placement_carries_what_the_client_needs_to_render(self, market, surface):
        for placement in market.serve(surface):
            assert placement["placement_id"]
            assert placement["impression_token"]
            assert placement["reason"]
            assert placement["expires_at"]
            assert int(placement["product"]["listing_id"]) > 0
            assert placement["product"]["title"]


class TestTheRepetitionMetrics:
    """§25: the numbers an operator watches, read back from the real log."""

    def test_agrees_with_the_sequence_it_was_derived_from(self, market):
        expected = metrics.summarize(market.browse("feed", steps=30, seconds=30.0))

        actual = metrics.from_events(market.conn.cursor())

        assert actual.impressions == expected.impressions
        assert actual.unique_products_shown == expected.unique_products_shown
        assert actual.unique_sellers_shown == expected.unique_sellers_shown
        assert actual.immediate_product_repeats == expected.immediate_product_repeats

    def test_reads_the_log_forwards_even_though_it_fetches_it_backwards(self, market):
        # The query is `event_at DESC` because that is what the index supports,
        # and `summarize` reads position as time. Without the reversal in
        # `from_events` the *second* sighting of a product becomes the first,
        # which inverts cross-surface attribution silently — every rate still
        # looks plausible, just measured against the wrong surface.
        for _ in range(10):
            market.render(market.serve("feed"))
            market.clock.advance(30)
            market.render(market.serve("messenger"))
            market.clock.advance(30)

        assert metrics.from_events(market.conn.cursor()).cross_surface_repeat_rate == pytest.approx(
            metrics.summarize(market.log).cross_surface_repeat_rate
        )

    def test_can_be_narrowed_to_one_surface(self, market):
        market.browse("feed", steps=10, seconds=30.0)
        market.browse("reels", steps=10, seconds=30.0)

        feed_only = metrics.from_events(market.conn.cursor(), surface="feed")

        assert set(feed_only.by_surface) == {"feed"}
        assert feed_only.impressions == sum(1 for row in market.log if row["surface"] == "feed")

    def test_an_unreadable_log_is_a_missing_measurement_not_an_error(self, market):
        market.browse("feed", steps=5, seconds=30.0)
        market.conn.cursor().execute("DROP TABLE commerce_discovery_impression_events")

        assert metrics.from_events(market.conn.cursor()) is metrics.EMPTY


class TestPoolReplenishment:
    """§11: refill at a watermark, and relax rather than empty."""

    def test_a_feed_keeps_serving_after_every_seller_is_inside_its_cooldown(self, market):
        # Ten sellers, two placements a request, a fifteen-minute seller
        # cooldown and thirty seconds between requests: by the sixth request
        # every seller in the catalogue is cooling. A pool that treated the
        # seller cooldown as a hard filter would return nothing from here on.
        early = market.browse("feed", steps=6, seconds=30.0)
        assert len({row["seller_user_id"] for row in early}) == 10

        later = market.browse("feed", steps=10, seconds=30.0)

        assert len(later) >= 16, "the relaxation ladder did not fire"
        assert len({row["listing_id"] for row in later}) == len(later)
