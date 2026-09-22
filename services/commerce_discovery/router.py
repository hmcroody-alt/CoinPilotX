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

from dataclasses import dataclass
from typing import Optional

from . import config


@dataclass(frozen=True)
class SurfacePolicy:
    """Everything one branch of the router decides differently."""

    surface: str
    #: Placements from one seller inside a single response.
    max_per_seller: int
    #: Placements in one category inside a single response.
    max_per_category: int
    #: Seconds a product stays out of *this* surface after being shown here.
    product_cooldown_seconds: int
    #: Seconds a product stays out after being shown on a *different* surface.
    cross_surface_cooldown_seconds: int
    #: Seconds any product from one seller keeps that seller quiet.
    seller_cooldown_seconds: int
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
}

_SELLER_CAPS = {"feed": 2, "reels": 1, "messenger": 1, "marketplace": 3}
_CATEGORY_CAPS = {"feed": 3, "reels": 1, "messenger": 2, "marketplace": 4}


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
        product_cooldown_seconds=_scaled(config.product_cooldown_seconds(), product_factor),
        cross_surface_cooldown_seconds=_scaled(config.cross_surface_cooldown_seconds(), cross_factor),
        seller_cooldown_seconds=_scaled(config.seller_cooldown_seconds(), seller_factor),
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


def admissible(
    policy: SurfacePolicy,
    *,
    seller_id: int,
    category: str,
    seller_counts: dict,
    category_counts: dict,
) -> bool:
    """Whether one more placement from this seller/category fits the response.

    Counts are passed in and mutated by the caller rather than held here so that
    the selection loop stays the single owner of what it has chosen — a policy
    object that accumulated state would be a policy that could not be reused
    across the two selection passes ``engine`` runs.
    """
    if seller_id and seller_counts.get(seller_id, 0) >= policy.max_per_seller:
        return False
    if category and category_counts.get(category, 0) >= policy.max_per_category:
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
