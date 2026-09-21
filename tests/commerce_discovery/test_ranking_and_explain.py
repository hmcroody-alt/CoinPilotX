"""The ranker's explainability contract, and the floor under every surface.

Two things here are user-facing promises rather than internal details, and both
fail silently:

**"Why am I seeing this?" must be true and sayable.** The reason code is a claim
about the *user's own history* — "Because you viewed Shoes" is checkable by the
person reading it, and wrong is worse than absent. The factor names are the
second half of an i18n key, so a name with no catalog entry renders as a raw key
string rather than as an error.

**The floor must mean the same thing after a retune.** ``config.min_score``
enforces "no placement is better than a bad placement" as a number. That only
holds because ``score_listing`` normalises by the positive weight mass — without
it, raising one weight would silently move every threshold in the system, and
the reels floor would quietly stop being a floor.

The allowlist tests below are deliberately paranoid about one specific route:
``COMMERCE_DISCOVERY_WEIGHTS`` lets an operator override weights from a JSON env
var, so a single sign typo can turn a penalty positive. If the explain endpoint
selected purely on ``contribution > 0``, that typo would publish ``seller_risk``
— an internal judgement about a named store — to that store's own customers.
"""

import json

import pytest

from services.commerce_discovery import config, promotion, ranking


class TestTheExplainableVocabulary:
    def test_names_only_terms_the_model_actually_has(self):
        # A name here with no matching signal would be dead vocabulary that can
        # never be shown; a signal missing from here is simply not explainable.
        assert ranking.EXPLAINABLE_FACTORS <= set(config.DEFAULT_WEIGHTS)

    def test_is_exactly_the_positively_weighted_terms(self):
        positive = {name for name, weight in config.DEFAULT_WEIGHTS.items() if weight > 0}
        assert ranking.EXPLAINABLE_FACTORS == positive

    @pytest.mark.parametrize(
        "penalty", ["repetition_penalty", "hide_penalty", "refund_risk", "seller_risk"]
    )
    def test_never_admits_a_penalty(self, penalty):
        # Two independent reasons, either sufficient: these have no translation,
        # and two of them are risk judgements about a seller that must not be
        # published to that seller's potential customers.
        assert penalty in config.DEFAULT_WEIGHTS
        assert penalty not in ranking.EXPLAINABLE_FACTORS


class TestScoreNormalisation:
    def test_score_stays_inside_the_unit_interval(self):
        result = ranking.score_listing({"title": "Sneakers", "category": "shoes"})
        assert 0.0 <= result["score"] <= 1.0

    def test_a_hidden_product_scores_below_a_neutral_one(self):
        neutral = ranking.score_listing({"title": "Sneakers"}, hidden_strength=0.0)
        hidden = ranking.score_listing({"title": "Sneakers"}, hidden_strength=1.0)
        # `hide_penalty` carries the heaviest weight in the model on purpose:
        # "don't show me this" outranks every positive signal.
        assert hidden["score"] < neutral["score"]

    def test_doubling_every_positive_weight_does_not_move_the_score(self):
        # This is what makes `config.min_score` a stable threshold. Without the
        # division by positive mass, a retune would silently re-scale every
        # floor in the system and nothing would report it.
        listing = {"title": "Sneakers", "category": "shoes", "short_description": "comfy"}
        base = ranking.score_listing(listing, weights=config.DEFAULT_WEIGHTS)
        doubled = ranking.score_listing(
            listing, weights={k: v * 2 for k, v in config.DEFAULT_WEIGHTS.items()}
        )
        assert base["score"] == pytest.approx(doubled["score"], abs=1e-9)

    def test_reports_every_signal_and_its_contribution(self):
        result = ranking.score_listing({"title": "Sneakers"})
        assert set(result["signals"]) == set(config.DEFAULT_WEIGHTS)
        assert set(result["contributions"]) == set(config.DEFAULT_WEIGHTS)

    def test_stamps_the_ranking_version_so_two_models_are_never_pooled(self):
        result = ranking.score_listing({"title": "Sneakers"})
        assert result["ranking_version"] == config.RANKING_VERSION
        assert result["ranking_version"]


class TestChooseReasonIsAClaimNotACaption:
    def test_falls_back_to_popular_when_nothing_specific_is_true(self):
        assert ranking.choose_reason({}, {}) == ranking.REASON_POPULAR

    def test_claims_because_you_viewed_only_for_a_category_actually_viewed(self):
        listing = {"category": "shoes"}
        assert (
            ranking.choose_reason(listing, {}, viewed_categories=["shoes"])
            == ranking.REASON_BECAUSE_YOU_VIEWED
        )
        # The claim is checkable by the person reading it, so an unrelated
        # history must not produce it.
        assert (
            ranking.choose_reason(listing, {}, viewed_categories=["kitchen"])
            == ranking.REASON_POPULAR
        )

    def test_claims_context_only_when_the_post_is_genuinely_related(self):
        strong = ranking.choose_reason({}, {"relevance": 0.9}, has_context=True)
        weak = ranking.choose_reason({}, {"relevance": 0.2}, has_context=True)
        assert strong == ranking.REASON_CONTEXT
        assert weak == ranking.REASON_POPULAR

    def test_does_not_claim_context_without_a_post_to_be_related_to(self):
        assert ranking.choose_reason({}, {"relevance": 0.99}, has_context=False) != (
            ranking.REASON_CONTEXT
        )

    def test_claims_a_followed_seller_only_for_a_seller_actually_followed(self):
        listing = {"seller_user_id": 42}
        assert (
            ranking.choose_reason(listing, {}, followed_sellers=frozenset({42}))
            == ranking.REASON_SELLER_FOLLOWED
        )
        assert (
            ranking.choose_reason(listing, {}, followed_sellers=frozenset({7}))
            == ranking.REASON_POPULAR
        )

    def test_survives_a_seller_id_that_is_not_a_number(self):
        # Ranking thousands of rows: one malformed id drops one claim, never
        # the request.
        assert (
            ranking.choose_reason(
                {"seller_user_id": "abc"}, {}, followed_sellers=frozenset({42})
            )
            == ranking.REASON_POPULAR
        )

    def test_prefers_the_more_specific_of_two_true_claims(self):
        # Both are true here. "Because you viewed Shoes" tells the user
        # something; "Trending" tells them nothing about themselves, and a vague
        # reason on a well-targeted card reads as evasion.
        reason = ranking.choose_reason(
            {"category": "shoes"},
            {},
            viewed_categories=["shoes"],
            stats={"clicks": 500},
        )
        assert reason == ranking.REASON_BECAUSE_YOU_VIEWED

    def test_every_reachable_reason_is_in_the_priority_order(self):
        # A reason missing from REASON_PRIORITY can be claimed but never
        # returned — it would silently degrade to "popular" forever.
        declared = {
            value
            for name, value in vars(ranking).items()
            if name.startswith("REASON_") and isinstance(value, str)
        }
        assert declared == set(ranking.REASON_PRIORITY)


class TestSurfaceFloors:
    def test_reels_is_the_strictest_surface(self):
        # It is the only surface where a placement shares the frame with content
        # the user is actively watching.
        floors = {s: config.min_score(s) for s in ("feed", "reels", "messenger", "marketplace")}
        assert floors["reels"] == max(floors.values())
        assert floors["reels"] > floors["feed"]

    def test_marketplace_is_the_most_permissive(self):
        # Someone browsing the Marketplace asked to see products.
        floors = {s: config.min_score(s) for s in ("feed", "reels", "messenger", "marketplace")}
        assert floors["marketplace"] == min(floors.values())

    def test_every_floor_is_a_usable_threshold(self):
        for surface in ("feed", "reels", "messenger", "marketplace"):
            assert 0.0 <= config.min_score(surface) <= 1.0

    def test_an_unknown_surface_gets_the_base_floor_rather_than_none(self):
        assert config.min_score("carousel") == config.min_score("feed")


class TestCadenceReachesTheClient:
    """The rhythm knobs were defined and then read by nobody.

    `feed_lead_in`, `feed_interval` and `reels_lead_in` all existed, all had
    env overrides, and nothing in the codebase called them — the client held
    its own hardcoded copies. An operator could set
    `COMMERCE_DISCOVERY_FEED_INTERVAL`, restart, and watch the feed place cards
    at exactly the rhythm it did before. These tests fail if the wiring is ever
    cut again, which is the only way to notice.
    """

    @pytest.mark.parametrize("surface", ["feed", "reels", "messenger", "marketplace"])
    def test_every_surface_answers_with_a_usable_rhythm(self, surface):
        values = config.cadence(surface)
        assert set(values) == {"lead_in", "interval", "max_per_page"}
        assert values["interval"] >= 1, "a zero interval would stack every chip on one item"
        assert values["lead_in"] >= 0
        assert values["max_per_page"] >= 0

    def test_reels_waits_longer_and_shows_less_than_the_feed(self):
        # The brief calls Reels the most intrusive surface: a chip there shares
        # the frame with something the user is actively watching.
        reels, feed = config.cadence("reels"), config.cadence("feed")
        assert reels["interval"] > feed["interval"]
        assert reels["max_per_page"] <= feed["max_per_page"]

    def test_an_operator_retune_actually_changes_the_answer(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_FEED_INTERVAL", "20")
        monkeypatch.setenv("COMMERCE_DISCOVERY_REELS_LEAD_IN", "9")
        assert config.cadence("feed")["interval"] == 20
        assert config.cadence("reels")["lead_in"] == 9

    def test_an_unknown_surface_gets_the_feed_rhythm_rather_than_none(self):
        # Same direction as `min_score`: fall back to the surface with the
        # gentlest cadence, never to "no cadence at all", which a client would
        # read as zero and place a card on every item.
        assert config.cadence("carousel") == config.cadence("feed")


class TestOperatorWeightOverrides:
    def test_ignores_a_weight_for_a_term_the_model_does_not_have(self, monkeypatch):
        # Accepting an unknown key would let a typo look like a working retune.
        monkeypatch.setenv("COMMERCE_DISCOVERY_WEIGHTS", json.dumps({"relevanse": 0.9}))
        resolved = config.weights()
        assert "relevanse" not in resolved
        assert resolved["relevance"] == config.DEFAULT_WEIGHTS["relevance"]

    def test_applies_a_partial_override_without_dropping_the_rest(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_WEIGHTS", json.dumps({"relevance": 0.5}))
        resolved = config.weights()
        assert resolved["relevance"] == 0.5
        assert set(resolved) == set(config.DEFAULT_WEIGHTS)

    def test_falls_back_to_the_defaults_for_unparseable_json(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_WEIGHTS", "{not json")
        assert config.weights() == config.DEFAULT_WEIGHTS

    def test_a_sign_typo_cannot_make_a_penalty_explainable(self, monkeypatch):
        # The scenario the allowlist exists for. An operator flips a penalty
        # positive; the term now contributes positively and would pass a
        # `contribution > 0` filter. It must still be unsayable.
        monkeypatch.setenv("COMMERCE_DISCOVERY_WEIGHTS", json.dumps({"seller_risk": 0.9}))
        resolved = config.weights()
        assert resolved["seller_risk"] == 0.9
        assert "seller_risk" not in ranking.EXPLAINABLE_FACTORS


class TestLabelsComeFromTheClassNotTheScore:
    def test_a_high_scoring_organic_listing_is_still_not_sponsored(self):
        # There is no score at which unpaid reach becomes an advertisement.
        assert promotion.label_key(promotion.ORGANIC) != promotion.LABEL_KEYS[promotion.PAID]
