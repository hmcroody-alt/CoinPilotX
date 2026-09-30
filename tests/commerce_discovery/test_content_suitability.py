"""Every post may be commerce-capable; not every post should display commerce.

The rule this file defends is a product rule, so most of it is written as the
cheapest thing that can fail: a verdict on a specific post. The two posts worth
naming up front are the ones that were measured against the shipped engine
before ``suitability.py`` existed, on the 100-listing simulated catalogue:

    "we lost my father this morning, rest in peace dad"   tags: memorial, grief
        -> feed 2 placements, post_detail 1, reels 0

    "wearing dad's old watch to the funeral today, miss you"  tags: memorial, watches
        -> feed 2 placements, post_detail 1, reels 1

The second is the one that matters. Reels refused the first only because those
words matched no product tokens, so ``relevance`` scored 0.0 and the strictest
floor in the system was out of reach — a coincidence, not a protection. Express
the same grief through an object and every floor in the system clears, because
the sensitive post now *matches better*. ``TestTheFloorCannotSeparateThem``
pins that arithmetic, since it is the whole reason this gate is a refusal rather
than a weight: on this input, raising a floor admits the sensitive post sooner
than the benign one beside it.

The other half of the file is the half that keeps the gate honest in the
opposite direction. A suppression gate is trivial to make "safe" by refusing
everything, and the failure is invisible — no error, no alarm, just a commerce
layer that quietly stopped appearing. ``TestOrdinaryCommercePostsSurvive`` is
therefore not a nice-to-have: it is the test that makes over-suppression fail
loudly, and every term added to the word lists has to get past it.
"""

from __future__ import annotations

import pytest

from services.business_os.ads_intelligence import context as ads_context
from services.commerce_discovery import content, ranking, suitability


def post(**overrides):
    """A post that is permitted, so a test can change exactly one thing.

    Written as a helper rather than a constant because every test here is about
    a single difference from "fine", and a shared mutable dict would let one
    test's edit decide another test's verdict.
    """
    base = {
        "post_type": "image",
        "title": "",
        "body": "new brass desk lamp finally arrived #lamp #desksetup",
        "tags_json": '["lamp", "desksetup"]',
        "ai_tags_json": "",
        "ai_summary": "",
        "moderation_status": "approved",
        "risk_score": 0,
    }
    base.update(overrides)
    return base


def verdict(p):
    """The gate's answer for a post, with context derived the way callers do."""
    return suitability.assess(p, context=content.derive(p))


# --------------------------------------------------------------------------- #
# The measured cases
# --------------------------------------------------------------------------- #
class TestTheMeasuredGriefPostsAreRefused:
    PLAIN = post(body="we lost my father this morning, rest in peace dad",
                 tags_json='["memorial", "grief"]')
    HEIRLOOM = post(body="wearing dad's old watch to the funeral today, miss you",
                    tags_json='["memorial", "watches"]')

    def test_plain_grief_is_refused(self):
        out = verdict(self.PLAIN)
        assert out["permitted"] is False
        assert out["code"] == suitability.SENSITIVE_CONTEXT

    def test_the_heirloom_post_is_refused_even_though_it_matches_products(self):
        # The one the floors could not catch. "watches" is a real marketplace
        # category and the post genuinely is about a watch, which is precisely
        # why relevance rises and the floor stops helping.
        out = verdict(self.HEIRLOOM)
        assert out["permitted"] is False
        assert out["code"] == suitability.SENSITIVE_CONTEXT

    def test_the_refusal_never_returns_the_text_that_caused_it(self):
        # `ads_intelligence.context.describe` makes this argument about its own
        # log: storing the evidence builds the sensitive-content record the
        # refusal exists to prevent. A category is safe to keep; a quote is not.
        for p in (self.PLAIN, self.HEIRLOOM):
            out = verdict(p)
            blob = " ".join(str(v) for v in out.values()).lower()
            for leaked in ("father", "dad", "watch", "miss you", "rest in peace"):
                assert leaked not in blob, out

    def test_a_refusal_names_a_category_and_a_strength(self):
        # Both are needed to audit a suppression without reading the post: the
        # category says what fired, the strength says how much to trust it.
        out = verdict(self.PLAIN)
        assert out["category"] in suitability.SENSITIVE_CATEGORIES
        assert out["evidence"] in (
            suitability.CERTAIN, suitability.STRONG,
            suitability.CORROBORATED, suitability.DECLARED,
        )


class TestTheFloorRewardsTheSensitivePost:
    """Why this is a refusal and not a higher floor.

    No database and no engine: the claim is about the arithmetic every surface's
    floor is applied to. The end-to-end counts in the module docstring are the
    same fact observed through ``engine.serve`` — reels served the heirloom post
    and refused the plain one, and this is the reason.
    """

    WATCH = {"category": "watches", "subcategory": "", "title": "Vintage Steel Watch",
             "tags_json": '["watch", "vintage", "steel"]'}

    def _ctx(self, p):
        derived = content.derive(p)
        assert derived is not None
        return derived

    def test_grief_expressed_through_an_object_scores_far_better_than_grief_alone(self):
        # This is the mechanism, stated as a comparison rather than a threshold:
        # the *more* a sensitive post is about a thing, the better it matches the
        # thing. Relevance is doing its job correctly and that is the problem —
        # it was never asked whether the post should carry commerce.
        heirloom = ranking.relevance(
            self.WATCH, self._ctx(TestTheMeasuredGriefPostsAreRefused.HEIRLOOM))
        plain = ranking.relevance(
            self.WATCH, self._ctx(TestTheMeasuredGriefPostsAreRefused.PLAIN))
        assert plain == 0.0, plain
        assert heirloom > plain, (heirloom, plain)

    def test_it_lands_at_or_above_the_no_context_baseline(self):
        # An unreadable post defaults to NEUTRAL and still gets a card today.
        # The sensitive post is not below that, so no floor separates the two:
        # any threshold low enough to keep the ordinary feed working admits this.
        heirloom = ranking.relevance(
            self.WATCH, self._ctx(TestTheMeasuredGriefPostsAreRefused.HEIRLOOM))
        assert heirloom >= ranking.relevance(self.WATCH, None)

    def test_a_post_derived_context_can_never_reach_the_category_saturation(self):
        # Worth pinning, because it bounds how bad this can get *and* how good:
        # `relevance` jumps to 0.85 on a bare category equality, and `derive`
        # never emits a category, so a post-derived match is capped by token
        # overlap. A future change that starts guessing a category for a post
        # would hand the strongest signal in the ranker to the weakest evidence
        # in the system — and would do it to sensitive posts too.
        ctx = self._ctx(TestTheMeasuredGriefPostsAreRefused.HEIRLOOM)
        assert "category" not in ctx
        assert ranking.relevance(self.WATCH, ctx) < 0.85


# --------------------------------------------------------------------------- #
# The over-suppression guard
# --------------------------------------------------------------------------- #
class TestOrdinaryCommercePostsSurvive:
    """The test that makes a too-eager word list fail.

    Each body here is a shape that really occurs and that a naive blacklist gets
    wrong. If a term is ever added to the lists that breaks one of these, the
    term is wrong, not the test.
    """

    PERMITTED_BODIES = (
        # Hyperbole is how enthusiasm is written. All four of these contain a
        # word from a WEAK list and none of them is about what that word means.
        "i'm dying over these shoes 😍 link in bio #sneakers",
        "this jacket is sick, obsessed #ootd",
        "killer deal on the lamp i wanted #homedecor",
        "my phone died halfway through the shoot #camera",
        # One weak term from each of two *different* categories. Two hits, no
        # corroboration, because they do not agree about what the post is.
        "this jacket is sick and these boots are a crash course in blisters",
        # The documented exclusion: a funeral wake is what STRONG_TERMS is for,
        # and "wake up" is the most ordinary sentence in the feed.
        "wake up early, coffee, new mug #morningroutine",
        # "lost" and "miss" are in the grief list on purpose. One of each, in a
        # sentence about nothing of the kind.
        "lost my keys again, this keyring is saving me #edc",
        "i miss summer already, restocked these sandals #shoes",
    )

    @pytest.mark.parametrize("body", PERMITTED_BODIES)
    def test_an_ordinary_post_still_carries_commerce(self, body):
        out = verdict(post(body=body, tags_json=""))
        assert out["permitted"] is True, (body, out)

    def test_a_beauty_treatment_is_not_a_medical_context(self):
        # "treatment" and "ill" were removed from the health list for this. A
        # suppressed shelf here buys no safety at all.
        out = verdict(post(body="this hair treatment is sick, my curls are back",
                           tags_json='["haircare"]'))
        assert out["permitted"] is True, out


# --------------------------------------------------------------------------- #
# Evidence tiers
# --------------------------------------------------------------------------- #
class TestTheTiersHaveDifferentConsequences:
    def test_one_weak_term_alone_is_not_enough(self):
        assert verdict(post(body="the lamp i ordered died after a week"))["permitted"]

    def test_two_weak_terms_from_one_category_corroborate(self):
        out = verdict(post(body="lost my dad this morning, i miss him so much"))
        assert out["permitted"] is False
        assert out["evidence"] == suitability.CORROBORATED
        assert out["category"] == "grief"

    def test_one_strong_term_alone_is_enough(self):
        out = verdict(post(body="writing his obituary tonight"))
        assert out["permitted"] is False
        assert out["evidence"] == suitability.STRONG

    def test_a_certain_phrase_is_not_cleared_by_looking_commercial(self):
        # The removed override would have rescued this. A bereavement that also
        # links a fundraiser is still not a post to hang a third party's shelf
        # on, so the phrase tier is deliberately unclearable.
        out = verdict(post(
            body="my mother passed away on tuesday. link in bio to the "
                 "fundraiser, shop now to help with costs",
            tags_json=""))
        assert out["permitted"] is False
        assert out["evidence"] == suitability.CERTAIN

    def test_corroboration_is_counted_per_category_not_in_total(self):
        # Three weak hits across three categories is still permitted; that is
        # the property that makes the weak tier usable instead of noise.
        out = verdict(post(body="sick trick, brutal crash, i'm dead #skate"))
        assert out["permitted"] is True, out

    def test_a_styled_unicode_phrase_does_not_slip_past(self):
        # NFKC first, or fullwidth text is a free bypass of every phrase.
        out = verdict(post(body="ｐａｓｓｅｄ　ａｗａｙ this morning"))
        assert out["permitted"] is False
        assert out["evidence"] == suitability.CERTAIN

    def test_punctuation_does_not_break_a_phrase(self):
        out = verdict(post(body="he passed away."))
        assert out["permitted"] is False


# --------------------------------------------------------------------------- #
# Structural gates
# --------------------------------------------------------------------------- #
class TestStructuralFactsOutrankText:
    def test_a_scam_report_never_carries_commerce(self):
        # The fraud being reported could buy the slot.
        out = verdict(post(post_type="scam_report",
                           body="this seller took my money #warning"))
        assert out["code"] == suitability.POST_TYPE_EXCLUDED

    def test_unapproved_moderation_suspends_commerce(self):
        out = verdict(post(moderation_status="pending"))
        assert out["code"] == suitability.MODERATION_NOT_CLEARED

    def test_an_unknown_moderation_state_is_refused_not_inherited(self):
        # Default-deny. A status another team adds should suspend commerce until
        # somebody decides it shouldn't, rather than being permitted by absence
        # from a denylist.
        out = verdict(post(moderation_status="shadow_review_v2"))
        assert out["code"] == suitability.MODERATION_NOT_CLEARED

    def test_content_risk_at_the_limit_refuses(self):
        assert verdict(post(risk_score=suitability.MAX_CONTENT_RISK))["code"] == (
            suitability.CONTENT_RISK
        )
        assert verdict(post(risk_score=suitability.MAX_CONTENT_RISK - 1))["permitted"]

    def test_an_unreadable_risk_score_is_treated_as_risky(self):
        # A score we cannot parse is a score. Defaulting it to clean would make
        # a malformed row the safest kind of row to have.
        assert verdict(post(risk_score="n/a"))["code"] == suitability.CONTENT_RISK

    def test_a_declared_sensitive_tag_refuses_without_reading_prose(self):
        # The path a moderator or a future classifier uses to suppress commerce
        # on a post without this module having to learn new words.
        out = verdict(post(body="a completely ordinary caption about a lamp",
                           tags_json='["memorial"]'))
        assert out["permitted"] is False
        assert out["evidence"] == suitability.DECLARED


class TestNoSubjectMeansNoCommerce:
    def test_a_post_with_nothing_readable_is_refused(self):
        out = verdict(post(body="", title="", tags_json="", ai_summary=""))
        assert out["code"] == suitability.NO_SUBJECT

    def test_a_missing_post_is_refused_rather_than_defaulted(self):
        assert suitability.assess(None)["code"] == suitability.NO_SUBJECT

    def test_this_is_the_rule_that_covers_the_word_lists_blind_spot(self):
        # A bereavement in a language the lists do not cover, or in an image
        # with no caption, lands here rather than being permitted: there is no
        # derivable subject, so there is nothing for a shelf to be about.
        out = verdict(post(body="   ", title="", tags_json="[]"))
        assert out["permitted"] is False


# --------------------------------------------------------------------------- #
# The shared vocabulary
# --------------------------------------------------------------------------- #
class TestTheSensitiveVocabularyIsSharedNotCopied:
    """One list, two policies.

    ``promotion.py`` keeps a wall between this package and the advertising one,
    and is explicit that placement safety is the thing the two sides share. A
    second copy of the category list here would let one subsystem learn about a
    sensitive category while the other did not.
    """

    def test_commerce_reads_the_advertising_layers_list_rather_than_its_own(self):
        assert suitability.SENSITIVE_CATEGORIES is (
            ads_context.SENSITIVE_CONTEXT_CATEGORIES
        )

    def test_a_category_added_on_either_side_is_inherited_by_both(self):
        # Identity, not equality: equality would pass against a copy that
        # happened to agree today and silently diverge on the next edit.
        for name in ("death", "crisis", "self_harm", "medical", "disaster"):
            assert name in suitability.SENSITIVE_CATEGORIES

    def test_the_commerce_policy_is_not_the_advertising_policy(self):
        # `ad_permitted` refuses every surface outside {feed, reels, explore,
        # search}, which is four live commerce surfaces. Importing the policy
        # along with the vocabulary would have taken the shop down.
        for surface in ("post_detail", "messenger", "marketplace", "product_detail"):
            refused = ads_context.ad_permitted({"surface": surface})
            assert refused["permitted"] is False, surface

    def test_refusal_codes_are_closed(self):
        # These reach admin diagnostics and aggregate suppression counts. A
        # suppression nobody can count is one nobody notices has stopped firing.
        for p in (post(), post(post_type="scam_report"), post(body=""),
                  post(moderation_status="pending"), post(risk_score=99),
                  post(body="he passed away")):
            assert verdict(p)["code"] in suitability.ALL_CODES


# --------------------------------------------------------------------------- #
# Context derivation
# --------------------------------------------------------------------------- #
class TestServerSideDerivationRecoversWhatTheClientCannotSee:
    def test_author_declared_tags_are_used(self):
        # `postContext.ts:20` documents that `CreatePostPayload` accepts `tags`
        # but `PulsePost` drops them, so the client scrapes hashtags instead.
        # The column is right here.
        ctx = content.derive(post(body="finally!", tags_json='["walnut", "midcentury"]'))
        assert ctx is not None
        assert "walnut" in ctx["tags"] and "midcentury" in ctx["tags"]

    def test_declared_tags_outrank_hashtags_which_outrank_ai_tags(self):
        # The order matters because the list is truncated at twelve: a
        # classifier's guess must not evict a human's statement.
        ctx = content.derive(post(
            body="a lamp #scraped", tags_json='["declared"]',
            ai_tags_json='["inferred"]'))
        assert ctx["tags"].index("declared") < ctx["tags"].index("scraped")
        assert ctx["tags"].index("scraped") < ctx["tags"].index("inferred")

    def test_the_hash_is_stripped_so_a_post_tag_can_match_a_listing_tag(self):
        # `#sneakers` and a listing's `sneakers` are one word. Keeping the hash
        # makes them two, which is a silent total miss rather than a weak match.
        ctx = content.derive(post(body="new pair #sneakers", tags_json=""))
        assert "sneakers" in ctx["tags"]
        assert not any(tag.startswith("#") for tag in ctx["tags"])

    def test_a_title_outranks_a_body_as_the_subject(self):
        ctx = content.derive(post(title="Brass desk lamp review", body="hello!!"))
        assert ctx["topic"] == "Brass desk lamp review"

    def test_ai_summary_is_used_when_there_is_no_title(self):
        # A signal the client never receives at all.
        ctx = content.derive(post(title="", body="look 😍",
                                  ai_summary="A restored mid-century desk lamp"))
        assert ctx["topic"] == "A restored mid-century desk lamp"

    def test_a_post_that_says_nothing_derives_none_not_an_empty_dict(self):
        # `relevance` scores NEUTRAL for an absent context and 0.0 for one that
        # matches nothing. Returning {} would file "not measured" under the
        # score a real mismatch earns.
        assert content.derive(post(body="", title="", tags_json="")) is None
        assert content.derive(None) is None

    def test_no_category_is_ever_invented(self):
        # `relevance` saturates at 0.85 on a bare category equality, so a
        # guessed category is the largest single thing this module could get
        # wrong. A post has no category at any layer.
        ctx = content.derive(post(body="brass desk lamp", tags_json='["lamp"]'))
        assert "category" not in ctx

    def test_malformed_tag_json_yields_no_tags_rather_than_one_huge_tag(self):
        ctx = content.derive(post(body="a lamp", tags_json='["unterminated'))
        assert ctx is not None
        assert all(len(tag) <= content.MAX_TAG_CHARS for tag in ctx.get("tags", ()))
        assert not any("[" in tag for tag in ctx.get("tags", ()))

    def test_the_wire_caps_are_respected_so_both_paths_agree(self):
        # A context derived here and one that arrived over HTTP must be the same
        # shape and size, or the engine behaves differently per entry point.
        ctx = content.derive(post(
            title="x" * 300,
            body=" ".join(f"#tag{n:02d}" for n in range(40)),
            tags_json="[]"))
        assert len(ctx["topic"]) <= content.MAX_FIELD_CHARS
        assert len(ctx["tags"]) <= content.MAX_TAGS

    def test_two_character_tags_are_dropped_as_unmatchable(self):
        # `ranking._tokens` discards words of two characters or fewer, so a
        # shorter tag cannot contribute a match — it only consumes a slot.
        ctx = content.derive(post(body="ok", tags_json='["ab", "lamp"]'))
        assert ctx["tags"] == ["lamp"]
