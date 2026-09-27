"""Anti-repetition, fairness and pacing — decided once per tick, in memory.

## Why one snapshot instead of a query per rule

There are eight rules and, on a good tick, a few dozen candidates. Asking the
database per candidate per rule is 200+ round trips on a connection pool sized 8
with a three-second checkout timeout, which is the exact shape that has emptied
this pool before. So the curator loads PulseDrop's own recent history once —
``pulsedrop_publications`` is a small, PulseDrop-only table — and every rule is
then a scan over a list. The snapshot is also what makes the decision
reproducible: every rule saw the same history, rather than each one seeing the
table at a slightly different moment.

## Why the seller share rule does not enforce equality

Production's eligible catalog is one seller. A rule that said "no seller may
exceed 40% of recent publications" and meant it would, on that catalog, publish
nothing — forever, silently, with a healthy-looking run log. That is a worse
outcome than an unbalanced feed, so the rule only binds once the catalog can
satisfy it: if fewer than two distinct sellers have eligible products right now,
the share ceiling is not applied at all. The moment a second seller appears it
starts working, without a config change and without anyone remembering to flip
it. See :func:`seller_share_blocked`.

## Why cooldowns are per surface and also across surfaces

A product's Signal and its Reel are different posts and get different cooldowns,
because seeing a product twice in two formats is not the same experience as
seeing the same card twice. But they are not independent either: a Reel of a
product whose Signal went out this morning is still the same product twice in a
day. :func:`cross_format_blocked` is that third rule, and its interval is
configurable down to zero precisely so the "mechanically both" behaviour is
reachable on purpose and never by default.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from services.pulsedrop import config

log = logging.getLogger(__name__)

SIGNAL = "signal"
REEL = "reel"
SURFACES: tuple[str, ...] = (SIGNAL, REEL)

#: Publication states that count as "PulseDrop put this in front of members".
#: A claimed-but-never-finished row does not: it published nothing, and letting
#: a crashed attempt impose a two-week product cooldown would let one failure
#: silently retire a product.
PUBLISHED_STATES: tuple[str, ...] = ("published",)

#: Reasons, stable and countable, same contract as the eligibility codes.
PRODUCT_COOLDOWN = "product_cooldown"
SELLER_COOLDOWN = "seller_cooldown"
CATEGORY_COOLDOWN = "category_cooldown"
CROSS_FORMAT_COOLDOWN = "cross_format_cooldown"
SELLER_SHARE = "seller_share"

#: Narrowest fact first. :meth:`History.blocker` already returns the most
#: specific true reason for *one* surface; this is how a caller holding two
#: reasons — one per surface — picks which to record. Sorting them alphabetically
#: would answer "category_cooldown" for a product published two hours ago, which
#: is true and is not the thing an operator needs to read.
REASON_SPECIFICITY: tuple[str, ...] = (
    PRODUCT_COOLDOWN,
    CROSS_FORMAT_COOLDOWN,
    SELLER_COOLDOWN,
    SELLER_SHARE,
    CATEGORY_COOLDOWN,
)


def most_specific(reasons) -> str:
    """The narrowest reason in ``reasons``, or the first unknown one.

    Unknown codes sort last rather than being dropped: a reason this module has
    not heard of is still the truth about why nothing published, and silently
    preferring a known-but-broader one would hide it.
    """
    known = [code for code in REASON_SPECIFICITY if code in reasons]
    if known:
        return known[0]
    return sorted(reasons)[0] if reasons else ""


class Publication(NamedTuple):
    listing_id: int
    seller_user_id: int
    category: str
    surface: str
    published_at: str


def _hours_ago(hours: int, now: datetime) -> str:
    return (now - timedelta(hours=max(0, int(hours)))).isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


class History:
    """PulseDrop's recent publications, and every question asked of them.

    Construct with :meth:`load` inside the tick's transaction. All predicates
    are pure functions of the snapshot plus ``now``, so the whole fairness layer
    is testable by handing it a list of tuples.
    """

    def __init__(self, rows, now: datetime, *, distinct_eligible_sellers: int = 0):
        self.now = now
        self.rows: list[Publication] = list(rows or [])
        #: How many distinct sellers the *current* eligible catalog has. Not a
        #: property of the history — it is what decides whether the share rule
        #: is applicable at all.
        self.distinct_eligible_sellers = int(distinct_eligible_sellers or 0)

    # -- loading ------------------------------------------------------------

    @classmethod
    def load(cls, cur, now: datetime | None = None, *, distinct_eligible_sellers: int = 0):
        now = now or datetime.utcnow()
        horizon = _hours_ago(cls.horizon_hours(), now)
        rows: list[Publication] = []
        try:
            states = ", ".join(f"'{state}'" for state in PUBLISHED_STATES)
            cur.execute(
                f"""
                SELECT listing_id, seller_user_id, category, surface, published_at
                FROM pulsedrop_publications
                WHERE state IN ({states})
                  AND COALESCE(published_at,'') >= ?
                ORDER BY published_at DESC
                """,
                (horizon,),
            )
            for row in cur.fetchall() or []:
                item = dict(row)
                rows.append(
                    Publication(
                        int(item.get("listing_id") or 0),
                        int(item.get("seller_user_id") or 0),
                        _norm(item.get("category")),
                        _norm(item.get("surface")) or SIGNAL,
                        str(item.get("published_at") or ""),
                    )
                )
        except Exception:
            # An unreadable history is not an empty history, and treating it as
            # one would drop every cooldown at once — the single worst failure
            # mode this module has. The caller sees the raise and abandons the
            # tick; PulseDrop publishing nothing is always recoverable.
            log.warning("pulsedrop_history_load_failed", exc_info=True)
            raise
        return cls(rows, now, distinct_eligible_sellers=distinct_eligible_sellers)

    @staticmethod
    def horizon_hours() -> int:
        """The longest window any rule looks back over, so one read serves all."""
        return max(
            config.product_cooldown_hours(),
            config.seller_cooldown_hours(),
            config.category_cooldown_hours(),
            config.reel_product_cooldown_hours(),
            config.reel_seller_cooldown_hours(),
            config.cross_format_cooldown_hours(),
            24,
        )

    # -- primitives ---------------------------------------------------------

    def since(self, hours: int, surface: str | None = None) -> list[Publication]:
        cutoff = _hours_ago(hours, self.now)
        return [
            row
            for row in self.rows
            if row.published_at >= cutoff and (surface is None or row.surface == surface)
        ]

    def last_published_at(self, surface: str | None = None) -> str:
        candidates = [
            row.published_at
            for row in self.rows
            if surface is None or row.surface == surface
        ]
        return max(candidates) if candidates else ""

    # -- pacing -------------------------------------------------------------

    def min_interval_blocked(self) -> bool:
        """True while PulseDrop is inside its floor between any two posts.

        Separate from the evaluation interval on purpose: several ticks in a row
        can each find a worthy candidate, and without this a backlog of newly
        approved listings becomes a burst.
        """
        seconds = config.min_publish_interval_seconds()
        if seconds <= 0:
            return False
        last = self.last_published_at()
        return bool(last and last >= (self.now - timedelta(seconds=seconds)).isoformat(timespec="seconds"))

    def reel_interval_blocked(self) -> bool:
        seconds = config.reel_min_interval_seconds()
        if seconds <= 0:
            return False
        last = self.last_published_at(REEL)
        return bool(last and last >= (self.now - timedelta(seconds=seconds)).isoformat(timespec="seconds"))

    def daily_cap_reached(self, surface: str | None = None) -> bool:
        """Rolling 24 hours, not calendar days.

        A calendar cap lets a cap of 8 produce 16 posts across a midnight, which
        is the burst the cap exists to prevent, and it does it in whichever
        timezone the server happens to think it is in.
        """
        cap = config.daily_reel_cap() if surface == REEL else config.daily_publication_cap()
        if cap <= 0:
            return True
        return len(self.since(24, surface)) >= cap

    # -- anti-repetition ----------------------------------------------------

    def blocker(self, listing, surface: str) -> str:
        """``""`` when this listing may be published to ``surface`` right now.

        Order is product, seller, category, cross-format, share — narrowest
        fact first, so the reason reported is the most specific true one.
        """
        listing_id = int(listing.get("id") or 0)
        seller_user_id = int(listing.get("seller_user_id") or 0)
        category = _norm(listing.get("category"))
        is_reel = surface == REEL

        product_hours = (
            config.reel_product_cooldown_hours() if is_reel else config.product_cooldown_hours()
        )
        if any(row.listing_id == listing_id for row in self.since(product_hours, surface)):
            return PRODUCT_COOLDOWN

        seller_hours = (
            config.reel_seller_cooldown_hours() if is_reel else config.seller_cooldown_hours()
        )
        if seller_user_id and any(
            row.seller_user_id == seller_user_id for row in self.since(seller_hours, surface)
        ):
            return SELLER_COOLDOWN

        # Category is not per surface. A member scrolling one feed sees Signals
        # and Reels interleaved, so "three phone cases this afternoon" is the
        # same experience whichever format they arrived in.
        if category and any(
            row.category == category for row in self.since(config.category_cooldown_hours())
        ):
            return CATEGORY_COOLDOWN

        if is_reel and self._cross_format_blocked(listing_id):
            return CROSS_FORMAT_COOLDOWN

        if self.seller_share_blocked(seller_user_id):
            return SELLER_SHARE
        return ""

    def _cross_format_blocked(self, listing_id: int) -> bool:
        hours = config.cross_format_cooldown_hours()
        if hours <= 0:
            return False
        return any(row.listing_id == listing_id for row in self.since(hours, SIGNAL))

    def seller_share_blocked(self, seller_user_id: int) -> bool:
        """True when this seller already owns too much of the recent window.

        Inapplicable — and therefore False — while the eligible catalog has
        fewer than two sellers. See the module docstring: on a single-seller
        catalog this rule is the difference between an imperfect feed and no
        feed, and it must not be the reason PulseDrop goes quiet.
        """
        if self.distinct_eligible_sellers < 2:
            return False
        window = config.seller_share_window()
        recent = self.rows[:window]
        if len(recent) < window:
            # Too little history to measure a share. Enforcing a percentage over
            # three rows means the second post by any seller is 66% and blocked.
            return False
        owned = sum(1 for row in recent if row.seller_user_id == int(seller_user_id or 0))
        return (owned * 100) >= (config.max_per_seller_share_percent() * len(recent))


def partition(history: History, ranked, surface: str) -> tuple[list, dict[str, int]]:
    """Split ranked candidates into (allowed, blocker histogram) for one surface."""
    allowed = []
    blocked: dict[str, int] = {}
    for item in ranked or []:
        reason = history.blocker(item.listing, surface)
        if reason:
            blocked[reason] = blocked.get(reason, 0) + 1
            continue
        allowed.append(item)
    return allowed, blocked
