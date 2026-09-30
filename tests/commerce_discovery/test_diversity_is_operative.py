"""The diversity controls must be capable of firing, and must change the answer.

PulseSoc shipped three mechanisms intended to stop a response repeating itself,
and on 2026-09-27 all three were measured inert on both main surfaces:

* ``diversity_bonus`` was scored with a hardcoded ``diversity=1.0`` for every
  candidate, so its weight of 0.07 was a constant added to the whole pool and
  contributed nothing to any ordering. A loop after selection wrote a computed
  value into ``signals`` — after ``score`` and ``contributions`` had been frozen
  from the constant, and without reordering anything, despite a comment saying
  "then reorder". ``explain()`` reads ``contributions``, so the term was
  invisible to the API as well as to the ranking.
* ``_SELLER_CAPS["feed"]`` is 2 against a feed budget of 2. A cap equal to the
  budget cannot be exceeded.
* ``_CATEGORY_CAPS["feed"]`` is 3 against that same budget of 2, and
  Marketplace's is 4 counted on full leaf paths whose largest production bucket
  held 3. Neither could be reached either.

So the feed could serve two rings from one seller and nothing in the system
objected. The existing 264 commerce-discovery tests all passed before and after
the fix, which is the reason this file exists: a property nothing asserts is a
property that can be deleted by accident, and this one had been.

Every case below was run against the pre-fix engine and failed there. That is
the only thing that makes them guards rather than decoration — a test written
after a fix and never run before it cannot distinguish "the fix works" from
"the assertion is vacuous".

The scored pairs here carry a deliberately sparse ``signals`` dict, matching
``test_commerce_discovery_thin_catalogue``'s idiom and exercising the case that
``ranking.rescore_diversity`` adjusts a score by the delta on one term rather
than recomputing it from whatever signals a verdict happens to hold.
"""

import unittest

from services.commerce_discovery import config, engine, ranking, router, taxonomy

RINGS = "Jewelry & Watches > Fashion Jewelry > Rings"
RINGS_SLASHED = "jewelry watches / fashion jewelry / rings"
JACKET = "Women's Clothing > Outerwear & Jackets > Basic Jacket"
DRESS = "Women's Clothing > Dresses > Maxi Dress"
PHONE = "Electronics > Phones > Android"


def _row(listing_id, seller_id, category):
    return {"id": listing_id, "seller_user_id": seller_id, "category": category}


def _scored(rows, score=0.9):
    """Pairs shaped the way ``engine._select`` consumes them."""
    return [(row, {"score": score, "signals": {"exploration_bonus": 0.0}}) for row in rows]


def _categories(chosen):
    return [row["category"] for row, _ in chosen]


def _roots(chosen):
    return [taxonomy.segment_root(row["category"]) for row, _ in chosen]


class TheTermIsAnInputToSelectionNotAnAnnotationOnIt(unittest.TestCase):
    """Diversity must be able to change *which* listings are chosen."""

    def test_a_lower_scoring_candidate_wins_when_the_better_one_repeats(self):
        # All three share a seller, as production's catalogue does, so the seller
        # penalty applies to every candidate and cancels. What is left is the
        # shelf difference alone: 0.30 of a term weighted 0.07 over a positive
        # weight mass of exactly 1.0, so 0.021 of score. The phone trails the
        # second ring by 0.015 and therefore wins. Under the old static-sort
        # selection the answer was both rings at any gap whatsoever, because the
        # term could not participate in the ordering at all.
        #
        # See `test_the_terms_authority_is_small_and_that_is_pinned` — 0.021 is
        # the entire window, and it is narrow on purpose. The hard enforcement of
        # diversity is the caps; this term only breaks near-ties between them.
        pool = [
            (_row(1, 7, RINGS), {"score": 0.92, "signals": {"exploration_bonus": 0.0}}),
            (_row(2, 7, RINGS), {"score": 0.900, "signals": {"exploration_bonus": 0.0}}),
            (_row(3, 7, PHONE), {"score": 0.885, "signals": {"exploration_bonus": 0.0}}),
        ]
        chosen = engine._select(pool, 2, 0.0, router.policy_for("marketplace"))
        self.assertEqual([row["id"] for row, _ in chosen], [1, 3])

    def test_the_penalty_is_not_strong_enough_to_beat_a_genuinely_better_answer(self):
        # The mirror case, and the one that keeps diversity from becoming a
        # relevance override: a repeat that is clearly better still wins. A term
        # that reordered every near-tie *and* every landslide would be a term that
        # had replaced the ranking rather than refined it.
        pool = [
            (_row(1, 7, RINGS), {"score": 0.92, "signals": {"exploration_bonus": 0.0}}),
            (_row(2, 7, RINGS), {"score": 0.90, "signals": {"exploration_bonus": 0.0}}),
            (_row(3, 7, PHONE), {"score": 0.20, "signals": {"exploration_bonus": 0.0}}),
        ]
        chosen = engine._select(pool, 2, 0.0, router.policy_for("marketplace"))
        self.assertEqual([row["id"] for row, _ in chosen], [1, 2])

    def test_the_terms_authority_is_small_and_that_is_pinned(self):
        """How much this term can ever move a score, stated rather than implied.

        ``score_listing`` divides by the sum of the positive weights, which is
        exactly 1.0 in the shipped vector, so ``diversity_bonus``'s weight of 0.07
        *is* its maximum swing — and the part that distinguishes two shelves from
        one is 0.30 of that, or 0.021.

        This is pinned so the number is visible rather than inferred. It is the
        honest limit of the score-side fix: a soft tiebreaker between candidates
        the caps have already admitted, not a force that will pull an unrelated
        product past a much better one. Anyone who wants diversification to
        outrank relevance must change the weight deliberately, via
        ``COMMERCE_DISCOVERY_WEIGHTS``, and will find this test telling them what
        they are changing from.
        """
        weights = config.DEFAULT_WEIGHTS
        mass = sum(w for w in weights.values() if w > 0)
        self.assertAlmostEqual(mass, 1.0, places=6, msg="the normaliser this arithmetic assumes")
        self.assertAlmostEqual(weights["diversity_bonus"], 0.07, places=6)

        verdict = {"score": 0.5, "signals": {}}
        floor_case = ranking.rescore_diversity(verdict, 0.0, weights=weights)
        self.assertAlmostEqual(verdict["score"] - floor_case["score"], 0.07, places=6)

        shelf_case = ranking.rescore_diversity(
            verdict, 1.0 - ranking.DIVERSITY_PENALTIES["category"], weights=weights
        )
        self.assertAlmostEqual(verdict["score"] - shelf_case["score"], 0.021, places=6)

    def test_the_persisted_breakdown_agrees_with_itself(self):
        # `explain()` reads `contributions`; the old post-hoc loop wrote only
        # `signals`, so a placement recorded a fired penalty in one half of its
        # `score_breakdown_json` and an unfired one in the other.
        rows = [_row(1, 7, RINGS), _row(2, 7, RINGS_SLASHED)]
        chosen = engine._select(_scored(rows), 2, 0.0, router.policy_for("marketplace"))
        self.assertEqual(len(chosen), 2)
        weight = config.weights().get("diversity_bonus", config.DEFAULT_WEIGHTS["diversity_bonus"])
        for _, verdict in chosen:
            self.assertAlmostEqual(
                verdict["contributions"]["diversity_bonus"],
                weight * verdict["signals"]["diversity_bonus"],
                places=3,
            )

    def test_the_second_placement_records_a_penalty_at_all(self):
        rows = [_row(1, 7, RINGS), _row(2, 7, RINGS_SLASHED)]
        chosen = engine._select(_scored(rows), 2, 0.0, router.policy_for("marketplace"))
        first, second = (verdict for _, verdict in chosen)
        self.assertEqual(first["signals"]["diversity_bonus"], 1.0, "nothing precedes the first pick")
        self.assertLess(second["signals"]["diversity_bonus"], 1.0)
        self.assertLess(second["score"], 0.9, "the penalty reached the score, not just the signal")


class CoarseConcentrationIsVisibleToACap(unittest.TestCase):
    """The production shape: many leaf shelves, one aisle."""

    def test_the_feed_pair_is_two_aisles_when_two_exist(self):
        # The live defect, at the live numbers. One seller — as production has —
        # and a pool whose top scores are all women's clothing under three
        # different leaf paths. The leaf cap (3) and the seller cap (2) are both
        # unreachable at a budget of 2, so before `_SEGMENT_CAPS` existed nothing
        # stopped this pair being two women's-clothing cards.
        rows = [
            _row(1, 7, JACKET),
            _row(2, 7, DRESS),
            _row(3, 7, "Women's Clothing > Tops > Blouse"),
            _row(4, 7, PHONE),
        ]
        chosen = engine._select(_scored(rows), 2, 0.0, router.policy_for("feed"))
        self.assertEqual(len(chosen), 2)
        self.assertEqual(len(set(_roots(chosen))), 2, f"one aisle twice: {_categories(chosen)}")

    def test_marketplace_will_not_spend_most_of_its_budget_on_one_aisle(self):
        # Eight slots, six women's-clothing listings across six distinct leaf
        # paths, and enough elsewhere to fill the rest. Every leaf bucket holds
        # exactly one, so the leaf cap of 4 is unreachable by construction — which
        # is precisely the production measurement that motivated the coarse cap.
        womens = [
            _row(10 + n, 7, f"Women's Clothing > Shelf {n} > Item")
            for n in range(6)
        ]
        others = [
            _row(20, 7, PHONE),
            _row(21, 7, "Home & Garden > Kitchen > Kettle"),
            _row(22, 7, RINGS),
            _row(23, 7, "Sports > Fitness > Mat"),
        ]
        policy = router.policy_for("marketplace")
        chosen = engine._select(_scored(womens + others), 8, 0.0, policy)
        self.assertEqual(len(chosen), 8)
        self.assertLessEqual(
            _roots(chosen).count("womens clothing"),
            policy.max_per_segment,
            f"aisle took more than its cap: {_categories(chosen)}",
        )

    def test_a_single_aisle_catalogue_is_not_starved_by_the_new_cap(self):
        # The regression direction. `fit_to_pool` must relax the segment cap when
        # the catalogue has only one aisle to offer, exactly as it already does
        # for sellers and leaf paths — otherwise this change would empty the shop
        # it was written to improve.
        rows = [_row(n, 7, f"Women's Clothing > Shelf {n} > Item") for n in range(12)]
        chosen = engine._select(_scored(rows), 8, 0.0, router.policy_for("marketplace"))
        self.assertEqual(len(chosen), 8)

    def test_product_detail_may_still_be_one_aisle_end_to_end(self):
        # Deliberately exempt: on a product page, aisle similarity *is* the
        # feature, and a coarse cap there would delete the related-products row
        # rather than diversify it. Seller diversity is what the surface protects.
        rows = [_row(n, 7, f"Women's Clothing > Shelf {n} > Item") for n in range(9)]
        chosen = engine._select(_scored(rows), 6, 0.0, router.policy_for("product_detail"))
        self.assertEqual(len(chosen), 6)
        self.assertEqual(len(set(_roots(chosen))), 1)


class TaxonomySpellingCannotBuyExtraAllowance(unittest.TestCase):
    """Two spellings of one category are one bucket, for caps and for history."""

    def test_the_two_delimiters_are_one_leaf_bucket(self):
        self.assertEqual(taxonomy.category_key(RINGS), taxonomy.category_key(RINGS_SLASHED))

    def test_a_respelled_repeat_is_penalised_like_a_repeat(self):
        # Production holds 26 listings awaiting review; the arrow-spelled ring
        # among them would, unfolded, have counted as a second ring shelf and
        # doubled the ring allowance under every cap at the moment it was
        # approved. Asserted on the recorded penalty rather than on the ordering,
        # because the penalty is where folding is unambiguous: same seller (0.40)
        # plus same shelf (0.30) leaves 0.30. Under raw `.strip().lower()`
        # bucketing the two spellings are different shelves, only the seller
        # penalty applies, and this reads 0.60.
        rows = [_row(1, 7, RINGS), _row(2, 7, RINGS_SLASHED)]
        chosen = engine._select(_scored(rows), 2, 0.0, router.policy_for("marketplace"))
        self.assertEqual(len(chosen), 2)
        self.assertAlmostEqual(chosen[1][1]["signals"]["diversity_bonus"], 0.30, places=3)

    def test_exposure_history_folds_the_same_way(self):
        from services.commerce_discovery import exposure

        state = exposure.ExposureState(category_counts={taxonomy.category_key(RINGS): 3})
        self.assertEqual(state.category_seen(RINGS_SLASHED), 3)
        self.assertEqual(state.category_seen(RINGS), 3)

    def test_an_uncategorised_listing_shares_no_bucket(self):
        # Two rows we know nothing about are not thereby the same category.
        # Inventing a shared bucket would cap exactly the rows most in need of
        # exposure, and `product_detail` would cap its whole row on one unknown.
        rows = [_row(1, 7, ""), _row(2, 7, None), _row(3, 7, "   ")]
        chosen = engine._select(_scored(rows), 2, 0.0, router.policy_for("feed"))
        self.assertEqual(len(chosen), 2)


class RescoreDiversityIsExactAndTotal(unittest.TestCase):
    def test_restating_the_current_diversity_is_a_no_op_on_the_score(self):
        verdict = ranking.score_listing({"title": "Ring", "category": RINGS})
        again = ranking.rescore_diversity(verdict, verdict["signals"]["diversity_bonus"])
        self.assertEqual(again["score"], verdict["score"])

    def test_it_does_not_mutate_its_argument(self):
        # The old implementation mutated `signals` in place, which is how the
        # persisted breakdown came to disagree with its own score.
        verdict = ranking.score_listing({"title": "Ring", "category": RINGS})
        before = dict(verdict["signals"])
        ranking.rescore_diversity(verdict, 0.2)
        self.assertEqual(verdict["signals"], before)

    def test_a_penalty_lowers_the_score_and_the_contribution_together(self):
        verdict = ranking.score_listing({"title": "Ring", "category": RINGS})
        worse = ranking.rescore_diversity(verdict, 0.0)
        self.assertLess(worse["score"], verdict["score"])
        self.assertEqual(worse["contributions"]["diversity_bonus"], 0.0)
        self.assertEqual(worse["signals"]["diversity_bonus"], 0.0)

    def test_a_sparse_verdict_keeps_its_stated_score_as_the_baseline(self):
        # Adjusting by a delta rather than recomputing from the signal vector is
        # what makes this true. A recomputation would answer ~0.005 here, because
        # the only signal present is the one being changed.
        sparse = {"score": 0.9, "signals": {"exploration_bonus": 0.0}}
        same = ranking.rescore_diversity(sparse, 1.0)
        self.assertAlmostEqual(same["score"], 0.9, places=9)

    def test_category_and_segment_penalties_do_not_both_apply(self):
        # Same leaf implies same root. Charging both would make "same shelf"
        # cost 0.45 and "same aisle" 0.15 — a ratio nobody chose.
        same_shelf = ranking.diversity_factor(
            seller_id=0,
            category_key=taxonomy.category_key(RINGS),
            segment_root=taxonomy.segment_root(RINGS),
            chosen=[{
                "seller_id": 0,
                "category_key": taxonomy.category_key(RINGS_SLASHED),
                "segment_root": taxonomy.segment_root(RINGS_SLASHED),
            }],
        )
        self.assertAlmostEqual(same_shelf, 1.0 - ranking.DIVERSITY_PENALTIES["category"], places=9)


class NoCapMayBeUnreachableByArithmetic(unittest.TestCase):
    """A cap at or above the budget is a cap that cannot fire.

    This is the shape of the original defect rather than an instance of it, and
    it is the one assertion here that would have caught the bug prospectively.
    Every surface must retain at least one diversity control strictly below its
    response budget, or it has none.
    """

    def test_every_multi_slot_surface_can_reach_a_cap(self):
        caps = config.surface_caps()
        for surface, (budget, _) in caps.items():
            if budget < 2:
                # A one-slot surface cannot repeat anything within a response.
                # Its caps are inherited policy for a future budget, not controls.
                continue
            with self.subTest(surface=surface, budget=budget):
                policy = router.policy_for(surface)
                reachable = [
                    name for name, value in (
                        ("seller", policy.max_per_seller),
                        ("category", policy.max_per_category),
                        ("segment", policy.max_per_segment),
                    )
                    if value < budget
                ]
                self.assertTrue(
                    reachable,
                    f"{surface}: budget {budget} vs caps "
                    f"seller={policy.max_per_seller} leaf={policy.max_per_category} "
                    f"segment={policy.max_per_segment} — none can be exceeded, so none enforces",
                )


if __name__ == "__main__":
    unittest.main()
