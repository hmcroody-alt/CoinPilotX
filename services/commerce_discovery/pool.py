"""The one canonical pool of eligible products, built once per request.

Every surface asks the same question — "what may this person be shown right
now?" — and the answer must be computed once. The alternative, which is what the
engine did before this module existed, is four screens each running their own
``ORDER BY featured DESC, updated_at DESC LIMIT 120`` and each receiving the
identical head of the catalogue. Four independent queries with one deterministic
ordering is not four opinions; it is the same opinion four times, and it is why
the same products appeared everywhere at once.

Batched, not bulk
-----------------

The pool is filled in batches (:func:`config.candidate_batch_size`) and stops as
soon as it holds :func:`config.candidate_target_size` survivors. It refills at a
low watermark rather than on exhaustion, because a pool that refills when empty
is a pool that is periodically empty, and an empty pool is a surface with
nothing to show.

The loop exists because *eligibility is not expressible in SQL*. The cheap
conditions are pushed down (``eligibility.candidate_sql``), but the judgemental
ones — safety score with a nullable "never scored" meaning, price labels that
are free text — run in Python over the returned rows. So a batch of 60 can
yield anywhere from 60 survivors to none, and the only honest way to reach a
target is to keep asking. :func:`config.candidate_max_batches` is the stop, so a
catalogue that rejects everything costs a bounded number of queries rather than
a request that never returns.

Why the pool rotates
--------------------

Cooldowns rotate the shallow head: a product shown today is excluded tomorrow,
so the next-best products move up. That is sufficient for a small catalogue and
insufficient for a large one — the 500th-ranked listing is never *fetched*,
however novel it is, because 499 listings would have to cool down first.

So the starting offset moves, per viewer and per time epoch
(:func:`exposure.rotation_offset`). It is not randomness: two requests inside
one pull-to-refresh must see the same pool or they will duplicate each other's
products rather than avoid them. An offset that runs off the end of the
catalogue wraps once to zero, which is the whole reason a small marketplace
behaves identically with rotation on or off.

What this module does **not** do
--------------------------------

It does not score, rank, or choose. It answers "eligible and not under
cooldown", and hands an unordered-by-preference set to the ranker. Keeping
selection out of here is what lets four surface policies share one pool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from . import config, eligibility, exposure as exposure_module

LOGGER = logging.getLogger(__name__)

#: Ceiling on how many listing ids are excluded inside the SQL statement. Above
#: this the exclusions are applied in Python instead: a ``NOT IN`` with a few
#: thousand bind parameters is slower than the scan it was meant to save, and
#: some drivers refuse it outright.
MAX_SQL_EXCLUSIONS = 200

#: Reasons a row was dropped after it came back from the database. Counted, not
#: logged per row — these are the numbers that tell an operator whether an empty
#: surface means "nothing eligible" or "everything on cooldown", which are very
#: different problems with the same symptom.
DROP_CODES = (
    "suppressed_listing",
    "suppressed_seller",
    "ineligible",
    "product_cooldown",
    "seller_cooldown",
    "duplicate",
)


@dataclass(frozen=True)
class PoolResult:
    """The candidate set, plus how much work it took to build."""

    rows: tuple = ()
    #: Rows the database returned, before Python-side filtering.
    scanned: int = 0
    batches: int = 0
    #: ``drop_code -> count``. Sums with ``len(rows)`` to ``scanned``.
    dropped: Mapping[str, int] = field(default_factory=dict)
    #: True when the catalogue ran out before the target was reached. Not an
    #: error — a marketplace with 12 eligible products is a real marketplace.
    exhausted: bool = False
    #: True when the starting offset ran past the end and restarted at zero.
    wrapped: bool = False

    def __len__(self) -> int:
        return len(self.rows)


EMPTY = PoolResult()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _rows(cur) -> list[dict]:
    try:
        fetched = cur.fetchall() or []
    except Exception:
        return []
    return [row if isinstance(row, dict) else dict(row) for row in fetched]


def build(
    cur,
    *,
    viewer_user_id: Any,
    policy,
    exposure: Optional[exposure_module.ExposureState] = None,
    parse_price=None,
    surface: str = "feed",
    product_cooldown: Optional[int] = None,
    seller_cooldown: Optional[int] = None,
    target: Optional[int] = None,
    rotation_offset: int = 0,
) -> PoolResult:
    """Eligible, non-cooled-down candidates for one viewer.

    ``product_cooldown`` and ``seller_cooldown`` are passed in rather than read
    from config because they are a *surface policy* decision: Reels is twice as
    quiet as the feed, and Marketplace — where the user came to shop — is far
    less quiet than either. The router owns that judgement; this module owns
    applying it.
    """
    state = exposure or exposure_module.EMPTY
    target = int(target or config.candidate_target_size())
    product_cooldown = (
        config.product_cooldown_seconds() if product_cooldown is None else max(0, int(product_cooldown))
    )
    seller_cooldown = (
        config.seller_cooldown_seconds() if seller_cooldown is None else max(0, int(seller_cooldown))
    )

    # Cooldowns are a spacing preference, and a preference has to yield to
    # scarcity. On a marketplace with three sellers, a fifteen-minute seller
    # cooldown removes the entire catalogue after two impressions — the surface
    # would go quiet for reasons that have nothing to do with the user and
    # everything to do with inventory size. So the ladder: ask with full
    # spacing, and only if that comes back under the low watermark, ask again
    # with less. Relaxing the seller cooldown first is deliberate — showing two
    # products from one seller is a much smaller failure than showing the same
    # product twice, which is the one this pipeline exists to prevent.
    watermark = config.candidate_low_watermark()
    ladder = ((product_cooldown, seller_cooldown),)
    if seller_cooldown > 0:
        ladder += ((product_cooldown, 0),)
    if product_cooldown > 0:
        ladder += ((product_cooldown // 4, 0),)

    result = EMPTY
    for product_gap, seller_gap in ladder:
        result = _scan(
            cur,
            viewer_user_id=viewer_user_id,
            policy=policy,
            state=state,
            surface=surface,
            parse_price=parse_price,
            product_cooldown=product_gap,
            seller_cooldown=seller_gap,
            target=target,
            rotation_offset=rotation_offset,
        )
        if len(result.rows) >= min(watermark, target):
            break
    return result


def _scan(
    cur,
    *,
    viewer_user_id: Any,
    policy,
    state,
    surface: str,
    parse_price,
    product_cooldown: int,
    seller_cooldown: int,
    target: int,
    rotation_offset: int,
) -> PoolResult:
    """One pass of the batched fetch-and-filter loop, at fixed cooldowns."""
    batch_size = config.candidate_batch_size()
    max_batches = config.candidate_max_batches()

    hard_excluded = _hard_exclusions(policy, state, product_cooldown)

    accepted: list[dict] = []
    seen_ids: set[int] = set()
    dropped: dict[str, int] = {}
    scanned = 0
    batches = 0
    offset = max(0, int(rotation_offset))
    wrapped = False
    exhausted = False

    while batches < max_batches:
        try:
            fetched = _fetch(cur, viewer_user_id, offset, batch_size, hard_excluded)
        except Exception:
            LOGGER.warning("COMMERCE_DISCOVERY_POOL_QUERY_FAILED offset=%s", offset, exc_info=True)
            break
        batches += 1

        if not fetched:
            # Ran off the end. Wrapping once is what makes rotation safe on a
            # catalogue smaller than the offset — without it, a marketplace with
            # 40 listings and an offset of 120 would serve nothing at all for
            # three quarters of every rotation period.
            if offset > 0 and not wrapped:
                offset = 0
                wrapped = True
                continue
            exhausted = True
            break

        scanned += len(fetched)
        for row in fetched:
            code = _reject(
                row,
                policy=policy,
                state=state,
                surface=surface,
                parse_price=parse_price,
                product_cooldown=product_cooldown,
                seller_cooldown=seller_cooldown,
                seen_ids=seen_ids,
            )
            if code:
                dropped[code] = dropped.get(code, 0) + 1
                continue
            seen_ids.add(_int(row.get("id")))
            accepted.append(row)

        if len(accepted) >= target:
            break
        if len(fetched) < batch_size:
            if offset > 0 and not wrapped:
                offset = 0
                wrapped = True
                continue
            exhausted = True
            break
        offset += batch_size

    return PoolResult(
        rows=tuple(accepted),
        scanned=scanned,
        batches=batches,
        dropped=dropped,
        exhausted=exhausted,
        wrapped=wrapped,
    )


def _hard_exclusions(policy, state, product_cooldown: int) -> tuple[int, ...]:
    """Listing ids worth excluding in SQL rather than in Python.

    Only the certainties go here — explicitly suppressed products, and products
    inside their cooldown. Both are decisions already made; re-fetching them to
    throw them away is the work the batching loop is trying to avoid.

    Ordered most-recently-seen first so that when the list is truncated at
    :data:`MAX_SQL_EXCLUSIONS` the ids that survive are the ones most likely to
    be near the head of the candidate ordering. The truncated remainder is not
    lost — :func:`_reject` re-checks every row anyway. This is an optimisation,
    never the enforcement.
    """
    excluded: list[int] = []
    seen: set[int] = set()

    for listing_id in getattr(policy, "suppressed_listings", ()) or ():
        key = _int(listing_id)
        if key and key not in seen:
            seen.add(key)
            excluded.append(key)

    if product_cooldown > 0:
        for listing_id in state.recent_products:
            key = _int(listing_id)
            if not key or key in seen:
                continue
            if state.seconds_since_product(key) < product_cooldown:
                seen.add(key)
                excluded.append(key)

    for listing_id in state.purchased:
        key = _int(listing_id)
        if key and key not in seen:
            seen.add(key)
            excluded.append(key)

    return tuple(excluded[:MAX_SQL_EXCLUSIONS])


def _fetch(cur, viewer_user_id: Any, offset: int, limit: int, excluded: Sequence[int]) -> list[dict]:
    """One batch of catalogue rows, in the canonical candidate ordering.

    The ordering must be **total** for ``OFFSET`` to mean anything — two rows
    that tie on every sort key can swap between batches, which is how paginated
    queries quietly serve one row twice and skip another. ``l.id DESC`` is the
    tiebreak that makes it total.
    """
    clause = ""
    params: list[Any] = [_int(viewer_user_id)]
    if excluded:
        marks = ",".join("?" for _ in excluded)
        clause = f"AND l.id NOT IN ({marks}) "
        params.extend(int(value) for value in excluded)
    params.extend([int(limit), int(offset)])

    cur.execute(
        f"SELECT {eligibility.candidate_projection()} "
        "FROM marketplace_listings l "
        "LEFT JOIN users u ON u.user_id=l.seller_user_id "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id "
        f"WHERE {eligibility.candidate_sql()} "
        "AND COALESCE(l.seller_user_id,0)<>? "
        f"{clause}"
        "ORDER BY l.featured DESC, l.updated_at DESC, l.id DESC "
        "LIMIT ? OFFSET ?",
        tuple(params),
    )
    return _rows(cur)


def _reject(
    row: Mapping[str, Any],
    *,
    policy,
    state,
    surface: str,
    parse_price,
    product_cooldown: int,
    seller_cooldown: int,
    seen_ids: set,
) -> str:
    """``""`` to keep the row, else the drop code.

    Order is chosen so the code an operator reads is the most specific true
    statement: a listing that is both suppressed and ineligible reports
    ``suppressed_listing``, because that is the one the *user* asked for.
    """
    listing_id = _int(row.get("id"))
    if not listing_id or listing_id in seen_ids:
        return "duplicate"

    if listing_id in (getattr(policy, "suppressed_listings", ()) or ()):
        return "suppressed_listing"

    seller_id = _int(row.get("seller_user_id"))
    if seller_id and seller_id in (getattr(policy, "suppressed_sellers", ()) or ()):
        return "suppressed_seller"

    if parse_price is not None and eligibility.gate(row, parse_price):
        return "ineligible"

    # Purchased and carted products are removed here rather than penalised in
    # ranking. A penalty is a statement about how good a recommendation is; this
    # is a statement about whether it is a recommendation at all. Saved products
    # are *not* dropped — they are penalised downstream, because a saved product
    # resurfacing on a price drop is a feature and a bought one resurfacing is
    # not.
    if state.owns(listing_id):
        return "suppressed_listing"

    if product_cooldown > 0 and state.seconds_since_product(listing_id) < product_cooldown:
        return "product_cooldown"

    # The cross-surface window is checked in ranking, not here. A product seen
    # on another surface a moment ago should be heavily *outranked*, not made
    # unavailable — if it is the only thing left in the pool, showing it beats
    # showing a blank, and the brief forbids the blank.

    if seller_cooldown > 0 and seller_id and state.seconds_since_seller(seller_id) < seller_cooldown:
        return "seller_cooldown"

    return ""
