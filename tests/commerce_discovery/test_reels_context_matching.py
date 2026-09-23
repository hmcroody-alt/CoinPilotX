"""§8: the chip on a Reel is about the Reel, or there is no chip.

The mobile client did not send a context for Reels until `reelContext.ts`
existed. Everything below is the server half of that change — the behaviour the
client is now relying on, pinned so that a retune of the weights cannot quietly
turn context matching back into decoration.

The counterintuitive claim, and the one worth a test rather than a comment:
**sending a context is not a one-way improvement.** `ranking.relevance` returns
`NEUTRAL` (0.5) when it is handed nothing, and `0.0` when it is handed a context
the listing does not match. So a context makes a good match better *and a bad
match worse*, and since `min_score("reels")` is the strictest floor in the
system, "worse" on Reels means the response is empty. That is the brief's own
rule — no recommendation is better than a bad recommendation — expressed as
arithmetic rather than as a policy someone has to remember.

If this file ever starts failing because a mismatched context still returns a
placement, the floor has stopped being a floor and the Reels surface is back to
putting sneakers on a gardening video.
"""

from __future__ import annotations

from services.commerce_discovery import config, ranking


class TestTheFloorIsHighestOnReels:
    def test_reels_demands_more_than_any_other_surface(self):
        # The client's whole design — predicting the carrying reel so it can
        # describe *that* video — is only worth doing because this floor will
        # reject what the description does not justify.
        reels = config.min_score("reels")
        for surface in ("feed", "messenger", "marketplace"):
            assert reels > config.min_score(surface), surface


class TestRelevanceIsATwoSidedBet:
    """The arithmetic the client's `null`-vs-`{}` distinction rests on."""

    LISTING = {
        "category": "shoes",
        "subcategory": "",
        "title": "Women's Casual Sneakers",
        "tags_json": '["sneakers", "running"]',
    }

    def test_no_context_is_neutral_rather_than_zero(self):
        # Marketplace's own shelves have no surrounding content to be relevant
        # to. Scoring 0.0 there would make every module fight a headwind the
        # surface cannot remove.
        assert ranking.relevance(self.LISTING, None) == ranking.NEUTRAL
        assert ranking.relevance(self.LISTING, {}) == ranking.NEUTRAL

    def test_an_empty_context_is_also_neutral_not_a_penalty(self):
        # This is why `reelCommerceContext` returns `null` and the hook omits
        # the field: a reel with no metadata must not be punished for it. The
        # server agrees, but the client must not rely on that by accident —
        # `{"category": "", "tags": []}` is what a naive client would send.
        assert ranking.relevance(self.LISTING, {"category": "", "topic": "", "tags": []}) == (
            ranking.NEUTRAL
        )

    def test_a_matching_context_scores_above_neutral(self):
        scored = ranking.relevance(self.LISTING, {"category": "shoes", "tags": ["sneakers"]})
        assert scored > ranking.NEUTRAL

    def test_a_mismatched_context_scores_below_neutral(self):
        # The half that makes §8 real. Without this, context matching could only
        # ever promote and never reject, and "no recommendation is better than a
        # bad recommendation" would have no mechanism behind it.
        scored = ranking.relevance(self.LISTING, {"category": "lamps", "tags": ["lighting"]})
        assert scored < ranking.NEUTRAL

    def test_an_exact_category_match_saturates_on_its_own(self):
        # A reel categorised "shoes" is a much stronger statement than token
        # overlap, and should not be diluted by a caption full of other words.
        bare = ranking.relevance(self.LISTING, {"category": "shoes"})
        assert bare >= 0.85

    def test_a_hash_prefixed_tag_matches_nothing(self):
        # Pinning the exact trap `reelCommerceContext` strips for: the listing
        # side stores `sneakers`, so `#sneakers` is a total miss that looks like
        # a perfectly well-formed request. A client that stopped stripping the
        # hash would silence its own chips and nothing would report an error.
        assert ranking.relevance(self.LISTING, {"tags": ["#sneakers"]}) == 0.0
        assert ranking.relevance(self.LISTING, {"tags": ["sneakers"]}) > 0.0

    def test_a_two_character_tag_cannot_contribute(self):
        # Why the client drops them rather than spending one of its twelve
        # slots: `_tokens` discards them, so a context made only of short tags
        # tokenises to nothing and is indistinguishable from having sent none.
        # Note the score is NEUTRAL, not 0.0 — an untokenisable context is
        # treated as absent rather than as a mismatch, so these tags are pure
        # waste on the wire rather than actively harmful.
        assert ranking.relevance(self.LISTING, {"tags": ["no", "ok", "a"]}) == ranking.NEUTRAL
        # And with one real tag beside them, only the real one does any work.
        assert ranking.relevance(self.LISTING, {"tags": ["no", "sneakers"]}) > ranking.NEUTRAL


class TestTheReelsSurfaceEndToEnd:
    """Against the real catalogue, the real eligibility SQL and the real floor."""

    def test_a_matching_context_puts_a_chip_on_the_reel(self, market):
        placements = market.serve("reels", limit=1, context={"category": "shoes"})
        assert len(placements) == 1
        assert placements[0]["product"]["category"] == "shoes"

    def test_an_unmatched_context_returns_nothing_rather_than_something_else(self, market):
        # The whole point. The catalogue is full of eligible, high-quality,
        # perfectly servable products — and the honest answer to "what goes with
        # a video about nothing in this catalogue" is still none of them.
        placements = market.serve(
            "reels",
            limit=1,
            context={"category": "taxidermy", "topic": "vintage accordion repair",
                     "tags": ["accordion", "bellows", "reedblock"]},
        )
        assert placements == []

    def test_the_same_catalogue_does_serve_when_asked_without_a_context(self, market):
        # Control for the test above: proves the empty result is the context
        # being rejected, not the fixture being unservable on reels at all. A
        # missing control here is how a §8 test goes vacuous.
        assert market.serve("reels", limit=1, context=None)

    def test_a_matching_context_earns_the_related_to_this_post_reason(self, market):
        # The label the user reads has to be true. `choose_reason` only reaches
        # for this one when a context was actually supplied *and* scored well,
        # which is exactly the state the client now puts it in.
        placements = market.serve("reels", limit=1, context={"category": "shoes"})
        assert placements[0]["reason"] == "related_to_this_post"

    def test_the_reason_is_never_related_to_this_post_without_a_context(self, market):
        # The inverse, because the failure is a lie rather than a blank: a chip
        # captioned "Related to this post" that was ranked on browsing history
        # tells the viewer something false about their own feed.
        for placement in market.serve("feed", context=None):
            assert placement["reason"] != "related_to_this_post"
