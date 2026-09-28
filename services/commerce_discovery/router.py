"""One candidate system, four surface policies.

The architecture this module exists to make true: Feed, Reels, Messenger Home
and Marketplace are *branches* of a single distribution pipeline, not four
engines that happen to query the same table. Everything upstream of here —
eligibility, the candidate pool, exposure history, scoring — is shared. This is
the only place the four differ, and the difference is data rather than code.

Why the differences are what they are
-------------------------------------

Each surface interrupts something else, and the policy is the price of that
interruption.

* **Feed** is the baseline. A card sits between two complete posts and the user
  can scroll past it in a moment. Two per seller, three per category.
* **Reels** is the most intrusive surface in the app: a chip shares the frame
  with something the user is actively watching. So one of everything, and the
  cooldowns are doubled — a chip that repeated a product would be repeating it
  *over content*, which is a different order of annoyance.
* **Messenger Home** shows a horizontal strip, and a strip is judged as a set.
  Four products from one seller is the specific failure the brief names, so the
  seller cap is one: every item in the strip comes from a different store.
* **Marketplace** is the surface the user *opened in order to shop*. Suggesting
  products to someone browsing a shop is the feature working, not intruding, so
  the budget is the largest and the cooldowns shrink to a quarter. It keeps a
  cross-surface cooldown rather than dropping it, because a product that was in
  the feed thirty seconds ago should still not lead the shop — but the shop is
  allowed to show it again far sooner than another social surface would be.

Private conversations appear nowhere in this file, and that is the point.
``messenger`` means Messenger *Home* — the conversation list. There is no
policy for an open chat because there is no placement in an open chat, and
adding one would require adding a surface here, to ``schema.SURFACES``, and to
the client. The absence is load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional

from . import config


@dataclass(frozen=True)
class SurfacePolicy:
    """Everything one branch of the router decides differently."""

    surface: str
    #: Placements from one seller inside a single response.
    max_per_seller: int
    #: Placements on one leaf category path inside a single response.
    max_per_category: int
    #: Placements in one top-level category segment inside a single response.
    #: Coarser than ``max_per_category`` and never larger: it is the cap that
    #: notices "four different women's-clothing shelves" when the leaf cap sees
    #: four unrelated buckets. See ``taxonomy`` for why both keys exist.
    max_per_segment: int
    #: Seconds a product stays out of *this* surface after being shown here.
    product_cooldown_seconds: int
    #: Seconds a product stays out after being shown on a *different* surface.
    cross_surface_cooldown_seconds: int
    #: Seconds any product from one seller keeps that seller quiet.
    seller_cooldown_seconds: int
    #: Times one product may be shown to this viewer *in total* inside
    #: ``config.product_window_seconds()``, counted across every surface rather
    #: than just this one.
    #:
    #: Distinct from ``product_cooldown_seconds`` in kind, not degree. A cooldown
    #: is spacing, and spacing is a preference that yields to scarcity — which is
    #: why ``pool.build``'s ladder relaxes it on a thin catalogue. This is volume,
    #: and it does not yield: a catalogue too small to space products out is not a
    #: reason to show the same product a sixth time. Enforced in ``pool._reject``
    #: as a drop, never as a penalty.
    product_cap: int
    #: Candidates the pool tries to hold for this surface.
    pool_target: int

    def relevance_floor(self) -> float:
        return config.min_score(self.surface)


def _scaled(base: int, factor: float, *, minimum: int = 0) -> int:
    return max(minimum, int(round(base * factor)))


#: Multipliers on the globally-configured cooldowns, per surface. Kept as
#: factors rather than as four independent env vars so that an operator who
#: turns the product cooldown down gets a proportionally quieter Reels for free
#: — four absolute knobs would be four chances to set one and forget three, and
#: the failure mode of forgetting Reels is the loudest one in the app.
_COOLDOWN_FACTORS = {
    "feed": (1.0, 1.0, 1.0),
    "reels": (2.0, 2.0, 2.0),
    "messenger": (1.5, 1.0, 1.5),
    "marketplace": (0.25, 0.5, 0.25),
    # Post detail sits below a post the user chose to open, so it interrupts
    # less than reels and more than the shop. The product cooldown is the feed's
    # because the two surfaces show the same card shape and a product that just
    # scrolled past in the feed should not be the one waiting under the post the
    # user tapped. The *seller* cooldown is longer than the feed's: a post-detail
    # card is one card, so a repeated seller here is not diluted by a second
    # placement the way it is on a feed page.
    "post_detail": (1.0, 1.0, 1.5),
    # The shop's factors, for the shop's reason: the user is looking at a
    # product. A fifteen-minute product cooldown here would mean browsing back to
    # a page you visited earlier in the session shows an empty related row, which
    # reads as a broken screen rather than as considerate spacing.
    "product_detail": (0.25, 0.5, 0.25),
}

_SELLER_CAPS = {
    "feed": 2,
    "reels": 1,
    "messenger": 1,
    "marketplace": 3,
    "post_detail": 1,
    "product_detail": 3,
}
_CATEGORY_CAPS = {
    "feed": 3,
    "reels": 1,
    "messenger": 2,
    "marketplace": 4,
    "post_detail": 1,
    # Deliberately the whole row. Everywhere else a category cap buys variety;
    # here it would delete the feature, because on a product page relatedness
    # *is* category similarity — six items that pass the relevance floor are
    # six items from roughly one category by construction. Seller diversity is
    # what this surface protects instead, and _SELLER_CAPS does that.
    "product_detail": 6,
}

#: Placements permitted from one *top-level* category segment. Roughly half the
#: response budget on every surface that has room for a choice, which is the
#: weakest statement worth making: it forbids a response that is mostly one
#: aisle without pretending to know which two aisles a shopper wants.
#:
#: These exist because the leaf caps above were unable to fire at all on the
#: production catalogue. Measured 2026-09-27 on the 15 eligible listings: the
#: largest leaf bucket held 3 against Marketplace's cap of 4, while
#: ``womens clothing`` held 6 of the 15. Feed was worse — a budget of 2 against a
#: leaf cap of 3 cannot reach the cap by arithmetic, so the surface had no
#: operative category control whatsoever.
#:
#: Written as constants rather than env vars for the same reason the caps above
#: are: four absolute knobs are four chances to set one and forget three, and
#: ``fit_to_pool`` already adapts them to whatever diversity the catalogue holds.
_SEGMENT_CAPS = {
    # Budget 2. One apiece, so the pair is two aisles whenever the catalogue can
    # supply two. `fit_to_pool` restores 2 on a single-aisle catalogue.
    "feed": 1,
    # Budget 1 both. The cap is unreachable and named only so that a future
    # budget increase inherits a policy instead of inheriting a gap — which is
    # exactly how the leaf caps came to be dead.
    "reels": 1,
    "messenger": 1,
    # Budget 8. Half the shop's response may be one aisle; the rest must not be.
    "marketplace": 4,
    "post_detail": 1,
    # Budget 6, and deliberately the whole row, for the same reason
    # `_CATEGORY_CAPS` is: on a product page, aisle similarity *is* the feature.
    # Seller diversity is what this surface protects, via `_SELLER_CAPS`.
    "product_detail": 6,
}


#: Multipliers on ``config.product_cap()`` — how many total sightings of one
#: product this surface tolerates. Factors rather than absolute values for the
#: same reason ``_COOLDOWN_FACTORS`` are: an operator who tightens the global cap
#: should get a proportionally quieter Reels without editing six numbers.
#:
#: The split is between surfaces the user came to *shop* and surfaces they came
#: to do something else. Seeing the same jacket a fourth time while browsing a
#: shop is how shops work; seeing it a fourth time between two friends' posts is
#: the failure this pipeline exists to prevent. Because the underlying count is
#: cross-surface, a shopper who uses up the shop's allowance also goes quiet in
#: the feed — which is the intended direction: the feed should back off for
#: somebody already seeing the product elsewhere.
_PRODUCT_CAP_FACTORS = {
    "feed": 1.0,
    # The most interruptive surface in the app, and the one with the highest
    # relevance floor. Two sightings is already generous for a full-screen
    # interstitial between videos the user chose to watch.
    "reels": 0.67,
    "messenger": 0.67,
    # The shop. Re-encountering a product while browsing a catalogue is not
    # repetition, it is navigation.
    "marketplace": 2.0,
    "product_detail": 2.0,
    # One card under a post the user opened deliberately. Between the feed's
    # tolerance and Reels'.
    "post_detail": 1.0,
}


def policy_for(surface: str) -> SurfacePolicy:
    """The branch policy for one surface. Unknown surfaces get the feed's.

    Falling back to the feed rather than raising is the fail-safe rule again:
    every caller is already inside a ``try`` that answers an exception with an
    empty placement list, so raising here would turn a typo'd surface name into
    a silently commerce-free app rather than a loud one. The surface allowlist
    in ``schema.SURFACES`` is the actual gate; this is a default, not a check.
    """
    key = str(surface or "").strip().lower()
    product_factor, cross_factor, seller_factor = _COOLDOWN_FACTORS.get(key, _COOLDOWN_FACTORS["feed"])
    return SurfacePolicy(
        surface=key or "feed",
        max_per_seller=_SELLER_CAPS.get(key, 2),
        max_per_category=_CATEGORY_CAPS.get(key, 3),
        max_per_segment=_SEGMENT_CAPS.get(key, 1),
        product_cooldown_seconds=_scaled(config.product_cooldown_seconds(), product_factor),
        cross_surface_cooldown_seconds=_scaled(config.cross_surface_cooldown_seconds(), cross_factor),
        seller_cooldown_seconds=_scaled(config.seller_cooldown_seconds(), seller_factor),
        # `minimum=1` because a factor that rounded to zero would cap the surface
        # at zero sightings — an accidental kill switch for the whole surface,
        # reachable by setting the global cap to 1.
        product_cap=_scaled(
            config.product_cap(),
            _PRODUCT_CAP_FACTORS.get(key, _PRODUCT_CAP_FACTORS["feed"]),
            minimum=1,
        ),
        pool_target=_pool_target(key),
    )


def _pool_target(surface: str) -> int:
    """How deep a pool this surface needs.

    Marketplace builds seven shelves out of one ranked pool, so it needs several
    times the candidates the feed does — a shallow pool there does not produce a
    shorter shelf, it produces *fewer shelves*, because a shelf under
    ``MIN_MODULE_ITEMS`` is dropped entirely.
    """
    base = config.candidate_target_size()
    if surface == "marketplace":
        return max(base, config.marketplace_module_limit() * 8)
    if surface in ("reels", "messenger"):
        # Small budgets, but not small pools: these surfaces have the tightest
        # diversity caps, so most of the pool is rejected by admissibility
        # rather than by score, and a thin pool would simply come back empty.
        return max(12, base // 2)
    return base


def _share(budget: int, distinct: int) -> int:
    """How many placements one group must carry for ``budget`` to be reachable.

    Ceiling division, with an empty pool answering ``budget`` rather than
    dividing by zero — a cap of ``budget`` over nothing selects nothing, which
    is the same answer by a shorter road.
    """
    if distinct <= 0:
        return max(0, budget)
    return -(-max(0, budget) // distinct)


def fit_to_pool(
    policy: SurfacePolicy,
    *,
    budget: int,
    seller_ids: Iterable,
    categories: Iterable,
    segments: Optional[Iterable] = None,
) -> SurfacePolicy:
    """The same policy, loosened to the diversity the catalogue can actually supply.

    ``max_per_seller`` and ``max_per_category`` are diversity controls, and a
    diversity control only means anything when there is diversity to spread the
    response across. They are written as absolute counts because they were
    written against the catalogue this engine is designed for — many sellers,
    many categories — where "at most two from one store" reliably delivers "at
    least two different stores".

    Against a thin catalogue the identical numbers stop buying variety and start
    buying emptiness. With one eligible seller, Marketplace's cap of three holds
    the whole response to three placements; the shelf assembler then splits
    those across seven reason codes, each of which needs three items to render,
    so the shop's recommendation rails vanish entirely. Nothing was withheld for
    variety's sake — there was no variety there to protect. The same arithmetic
    caps Feed at two cards and reduces Reels and Messenger to one apiece.

    So the caps become floors as well as ceilings: no cap may sit below the
    share each group would have to carry for the budget to be filled at all,
    ``ceil(budget / distinct)``. On a diverse pool that share is 1 and every cap
    already clears it, so this returns the policy untouched. It only ever
    loosens, and only by exactly what the missing diversity costs.

    It must be given the **whole scored pool**, not the set that cleared the
    relevance floor. Cooldowns are expressed as score penalties, so at any
    moment the qualifying set is narrow precisely *because* the spacing rules
    are working — a seller shown thirty seconds ago is suppressed, not absent.
    Measuring there would read a working cooldown as a thin catalogue and relax
    the cap to let the one remaining seller take every slot, which is the exact
    behaviour the seller cooldown exists to prevent. Measured over the pool, a
    ten-seller catalogue yields a share of 1, every cap already clears it, and
    nothing moves.

    Counting sellers whose only listings are genuinely poor slightly *over*-
    states diversity and so under-relaxes. That is the safe direction: it can
    only ever cost a placement, never spend one on a seller who should not have
    had it.

    ``segments`` is the coarse key and is optional only so that a caller written
    against the two-key signature keeps working; omitting it leaves
    ``max_per_segment`` alone, which on a thin single-aisle catalogue is the
    difference between a relaxed cap and an empty surface. ``engine`` passes it.

    The three caps are relaxed independently and are *not* reconciled with each
    other afterwards. A segment cap below the leaf cap is the intended shape on
    the feed — 1 against 3 — and subsumes the leaf cap there, because two
    listings sharing a leaf path necessarily share its root. Clamping the segment
    cap up to the leaf cap to "protect" the finer knob would raise the feed's
    from 1 to 3 and put it back above the budget of 2, which is precisely the
    arithmetic that left the surface uncontrolled in the first place. A tighter
    coarse cap is never a loss of diversity.
    """
    sellers = {int(value or 0) for value in seller_ids}
    sellers.discard(0)
    cats = {str(value or "").strip().lower() for value in categories}
    cats.discard("")

    max_per_seller = max(policy.max_per_seller, _share(budget, len(sellers)))
    max_per_category = max(policy.max_per_category, _share(budget, len(cats)))

    max_per_segment = policy.max_per_segment
    if segments is not None:
        roots = {str(value or "").strip().lower() for value in segments}
        roots.discard("")
        max_per_segment = max(max_per_segment, _share(budget, len(roots)))

    if (
        max_per_seller == policy.max_per_seller
        and max_per_category == policy.max_per_category
        and max_per_segment == policy.max_per_segment
    ):
        return policy
    return replace(
        policy,
        max_per_seller=max_per_seller,
        max_per_category=max_per_category,
        max_per_segment=max_per_segment,
    )


def admissible(
    policy: SurfacePolicy,
    *,
    seller_id: int,
    category: str,
    seller_counts: dict,
    category_counts: dict,
    segment: str = "",
    segment_counts: Optional[dict] = None,
) -> bool:
    """Whether one more placement from this seller/category fits the response.

    Counts are passed in and mutated by the caller rather than held here so that
    the selection loop stays the single owner of what it has chosen — a policy
    object that accumulated state would be a policy that could not be reused
    across the two selection passes ``engine`` runs.

    The segment pair defaults to inert so that a caller which knows nothing about
    coarse buckets keeps its previous behaviour rather than silently having every
    candidate admitted under a cap of 1 against an empty counter.

    A falsy ``seller_id``, ``category`` or ``segment`` is "no opinion" and is
    never counted against its cap — see ``taxonomy.category_key`` for why an
    uncategorised listing must not share a bucket with the other uncategorised
    listings.
    """
    if seller_id and seller_counts.get(seller_id, 0) >= policy.max_per_seller:
        return False
    if category and category_counts.get(category, 0) >= policy.max_per_category:
        return False
    if segment and segment_counts is not None and segment_counts.get(segment, 0) >= policy.max_per_segment:
        return False
    return True


def cooldowns(surface: str) -> dict:
    """The three spacing numbers for one surface, for logs and tests."""
    policy = policy_for(surface)
    return {
        "product": policy.product_cooldown_seconds,
        "cross_surface": policy.cross_surface_cooldown_seconds,
        "seller": policy.seller_cooldown_seconds,
    }
