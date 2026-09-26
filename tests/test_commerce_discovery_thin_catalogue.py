"""Diversity caps must not throttle a surface below the diversity that exists.

``router._SELLER_CAPS`` and ``router._CATEGORY_CAPS`` are variety controls, and
they were written as absolute counts against the catalogue the engine is
designed for: many sellers, many categories, where "at most two from one store"
reliably delivers "at least two different stores".

PulseSoc's live catalogue is not that catalogue. Every publishable listing in
production belongs to a single seller, and against one seller the same numbers
buy no variety at all — they only subtract. The arithmetic is exact and it is
what these cases pin:

* Marketplace asks for eight modules, builds them from one ranked pool, and
  drops any shelf holding fewer than three items (``MIN_MODULE_ITEMS``). A
  per-seller cap of three holds the entire pool to three placements, which are
  then split across seven reason codes. Three items cannot make two shelves, and
  usually do not make one — so the shop's own recommendation rails render
  nothing. That is the defect: not a shelf that is short, a shelf that is gone.
* Feed is held to two cards, Reels and Messenger to one apiece, by the same rule.

The fix makes each cap a floor as well as a ceiling: no cap may sit below
``ceil(budget / distinct_groups)``, the share each group must carry for the
budget to be fillable at all. The property worth protecting is that this is a
*one-way* adjustment — it must be invisible on a diverse pool, because a cap
that relaxed when it did not have to would be a diversity rule that stopped
enforcing diversity. Several cases below exist only to assert that nothing
moves when nothing needs to.
"""

import math
import unittest

from services.commerce_discovery import engine, router


def _row(listing_id, seller_id, category="home"):
    return {"id": listing_id, "seller_user_id": seller_id, "category": category}


def _scored(rows, score=0.9):
    """Pairs shaped the way ``engine._select`` consumes them."""
    return [(row, {"score": score, "signals": {"exploration_bonus": 0.0}}) for row in rows]


class FitToPoolLeavesADiversePoolAlone(unittest.TestCase):
    """The adjustment must be unobservable when diversity is available."""

    def test_a_pool_with_a_seller_per_slot_is_returned_unchanged(self):
        policy = router.policy_for("feed")
        fitted = router.fit_to_pool(
            policy, budget=2, seller_ids=[11, 12, 13, 14], categories=["home", "tech", "beauty"]
        )
        # Identity, not merely equality: on a diverse pool there is nothing to
        # decide, and returning the same object says so.
        self.assertIs(fitted, policy)

    def test_the_caps_are_never_lowered(self):
        for surface in ("feed", "reels", "messenger", "marketplace"):
            with self.subTest(surface=surface):
                policy = router.policy_for(surface)
                fitted = router.fit_to_pool(
                    policy, budget=1, seller_ids=range(50), categories=[f"c{n}" for n in range(50)]
                )
                self.assertGreaterEqual(fitted.max_per_seller, policy.max_per_seller)
                self.assertGreaterEqual(fitted.max_per_category, policy.max_per_category)

    def test_an_empty_pool_does_not_divide_by_zero(self):
        policy = router.policy_for("marketplace")
        fitted = router.fit_to_pool(policy, budget=8, seller_ids=[], categories=[])
        self.assertGreaterEqual(fitted.max_per_seller, policy.max_per_seller)


class FitToPoolRelaxesExactlyAsMuchAsIsMissing(unittest.TestCase):
    def test_a_lone_seller_may_carry_the_whole_budget(self):
        policy = router.policy_for("marketplace")
        self.assertEqual(policy.max_per_seller, 3, "the configured cap this test is about")
        fitted = router.fit_to_pool(policy, budget=8, seller_ids=[1] * 40, categories=["home"] * 40)
        self.assertGreaterEqual(fitted.max_per_seller, 8)
        self.assertGreaterEqual(fitted.max_per_category, 8)

    def test_two_sellers_each_carry_half_and_no_more(self):
        policy = router.policy_for("marketplace")
        fitted = router.fit_to_pool(policy, budget=9, seller_ids=[1, 2] * 20, categories=["home", "tech"] * 20)
        # ceil(9/2) == 5. Not 9: with two sellers present the rule still refuses
        # to let one of them take the response, which is the whole difference
        # between loosening a cap and removing it.
        self.assertEqual(fitted.max_per_seller, max(policy.max_per_seller, math.ceil(9 / 2)))
        self.assertLess(fitted.max_per_seller, 9)

    def test_the_surface_identity_survives_the_adjustment(self):
        policy = router.policy_for("reels")
        fitted = router.fit_to_pool(policy, budget=4, seller_ids=[7] * 9, categories=["home"] * 9)
        # Only the two caps move. A cooldown or a floor that drifted here would
        # mean a thin catalogue quietly buying itself a louder surface.
        self.assertEqual(fitted.surface, policy.surface)
        self.assertEqual(fitted.product_cooldown_seconds, policy.product_cooldown_seconds)
        self.assertEqual(fitted.cross_surface_cooldown_seconds, policy.cross_surface_cooldown_seconds)
        self.assertEqual(fitted.seller_cooldown_seconds, policy.seller_cooldown_seconds)
        self.assertEqual(fitted.relevance_floor(), policy.relevance_floor())


class SelectionOnASingleSellerCatalogue(unittest.TestCase):
    """The end the caps exist for, measured through ``engine._select``."""

    def test_marketplace_fills_its_budget_from_one_seller(self):
        rows = [_row(100 + n, 1, "home") for n in range(20)]
        chosen = engine._select(_scored(rows), 8, 0.0, router.policy_for("marketplace"))
        # Pre-fix this returned 3 — the configured per-seller cap — and three
        # placements split across seven reason codes render zero shelves.
        self.assertEqual(len(chosen), 8)
        self.assertEqual(len({row["id"] for row, _ in chosen}), 8, "no product twice")

    def test_the_feed_strip_is_no_longer_two_cards_wide_by_arithmetic(self):
        rows = [_row(200 + n, 1, "home") for n in range(12)]
        chosen = engine._select(_scored(rows), 4, 0.0, router.policy_for("feed"))
        self.assertEqual(len(chosen), 4)

    def test_a_thin_pool_still_cannot_produce_more_than_it_holds(self):
        rows = [_row(300 + n, 1, "home") for n in range(2)]
        chosen = engine._select(_scored(rows), 8, 0.0, router.policy_for("marketplace"))
        self.assertEqual(len(chosen), 2)

    def test_relevance_still_outranks_the_relaxed_cap(self):
        # The floor is not a diversity control and must not move with one. A
        # surface starved of variety is still not permitted to fill itself with
        # answers it judged too weak to show.
        rows = [_row(400 + n, 1, "home") for n in range(10)]
        scored = _scored(rows, score=0.10)
        chosen = engine._select(scored, 8, 0.45, router.policy_for("marketplace"))
        self.assertEqual(chosen, [])


class SelectionStillEnforcesDiversityWhenThereIsSome(unittest.TestCase):
    """The regression direction: this change must not cost a diverse catalogue."""

    def test_one_store_cannot_take_a_messenger_strip_shared_with_others(self):
        # Messenger's budget is 1, so every seller's share is 1 and the cap is
        # untouched — the strip keeps the property its docstring claims.
        rows = [_row(500, 1), _row(501, 1), _row(502, 2), _row(503, 3)]
        chosen = engine._select(_scored(rows), 1, 0.0, router.policy_for("messenger"))
        self.assertEqual(len(chosen), 1)

    def test_a_feed_page_with_many_sellers_keeps_the_configured_cap(self):
        rows = [_row(600 + n, 10 + n, f"c{n}") for n in range(12)]
        policy = router.policy_for("feed")
        chosen = engine._select(_scored(rows), 2, 0.0, policy)
        sellers = [row["seller_user_id"] for row, _ in chosen]
        self.assertEqual(len(chosen), 2)
        self.assertEqual(len(set(sellers)), 2, "two cards, two stores")

    def test_a_crowded_category_is_still_capped_when_others_are_present(self):
        # Six sellers, but five of them in one category. ceil(2/6) == 1, so no
        # relaxation applies and the category cap governs as configured.
        rows = [_row(700 + n, 20 + n, "home") for n in range(5)] + [_row(799, 99, "tech")]
        policy = router.policy_for("messenger")
        chosen = engine._select(_scored(rows), 1, 0.0, policy)
        self.assertLessEqual(
            sum(1 for row, _ in chosen if row["category"] == "home"), policy.max_per_category
        )


if __name__ == "__main__":
    unittest.main()
