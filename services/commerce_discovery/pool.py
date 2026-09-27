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

Why the pool has several sources
--------------------------------

Rotation fixes *which page* of one ordering a viewer gets. It does nothing about
the ordering being the same for everybody: ``featured DESC, updated_at DESC,
id DESC`` mentions the viewer exactly once, to exclude their own listings.

That is a ceiling on personalisation, and it is a hard one, because ranking can
only reorder rows retrieval already returned. Measured 2026-09-27 on a
2000-listing catalogue holding 200 cameras and 1800 widgets, with a viewer whose
only twenty clicks were all on cameras:

    retrieval            cameras served   catalogue share   lift
    one blind ordering        14.1%            10.0%        1.41x

A viewer who has told the system, twenty times, that they only care about one
category was served that category barely more often than chance. The affinity
bonus in :mod:`ranking` was firing correctly the whole time — it had almost
nothing to fire on.

So retrieval asks several questions instead of one. Each :class:`_Source` is the
same eligibility query with one extra condition and its own small quota:
``affinity`` restricts to categories the viewer has actually opened,
``followed`` to sellers they actually follow, ``trending`` to listings earning
engagement right now, and ``rotation`` — the original, untargeted, span-rotated
query — fills whatever is left. Every accepted row carries ``candidate_source``
naming the question that found it, so an operator can see which one is doing the
work rather than inferring it.

Three properties this shape is chosen to keep:

* A viewer with no history gets ``rotation`` alone, which is byte-identical to
  the behaviour before sources existed. Personalised retrieval must not be the
  thing that changes what a brand-new account sees.
* The union is a *candidate* set, not a ranking. A source getting a row into the
  pool is not a promise the row is shown; :mod:`ranking` still decides, and the
  cooldown, cap and diversity machinery still applies to every row regardless of
  which source produced it. A targeted source cannot be used to smuggle a
  product past the frequency controls.
* Quotas are fractions of the pool target, so no source can crowd out the
  others. Affinity is the largest at just over a third — enough to matter,
  small enough that a viewer with one narrow interest is not sealed inside it.

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

from . import config, eligibility, exposure as exposure_module, subject as subject_module

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
    # The volume cap, as opposed to the spacing one above. Worth counting
    # separately: a rising `product_cap` against a flat `product_cooldown` means
    # viewers are *exhausting* the catalogue rather than merely moving through it
    # faster than the spacing allows. The answer to the first is more inventory;
    # the answer to the second is a looser cooldown. One number cannot say which.
    "product_cap",
    "seller_cooldown",
    "duplicate",
    # Expected to be 0 or 1 per response rather than a number worth watching: it
    # counts the product the viewer is already looking at, and only on the pass
    # where the SQL exclusion list did not already remove it. A sustained
    # non-zero count means the list is being truncated, which is a signal about
    # the viewer's cooldown volume rather than about the catalogue.
    "context_excluded",
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
    #: Describes the ``rotation`` source only — it is the only one that rotates.
    wrapped: bool = False
    #: ``candidate_source -> count``. Sums to ``len(rows)``. Absent sources are
    #: absent rather than zero, so an operator can tell "this viewer has no
    #: follows" from "this viewer's follows produced nothing eligible".
    sources: Mapping[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)


EMPTY = PoolResult()


#: The untargeted query. Named because several places need to say "the source
#: that is not personalised", and a literal would drift from the descriptor.
SOURCE_ROTATION = "rotation"

#: Every source that can appear in :attr:`PoolResult.sources`, in the order they
#: are asked. Declared so metrics and the explain endpoint can enumerate them
#: without a viewer who happens to have follows being the only way to discover
#: that ``followed`` exists.
CANDIDATE_SOURCES = ("affinity", "followed", "trending", SOURCE_ROTATION)


@dataclass(frozen=True)
class _Source:
    """One retrieval question: the eligibility query plus a restriction.

    ``clause`` is AND-ed onto :func:`eligibility.candidate_sql`, so a source can
    only ever *narrow* the eligible set. It cannot admit a listing the catalogue
    rules reject, which is what makes adding a source a safe operation: the worst
    a broken source can do is return nothing.

    ``rotates`` is false for every targeted source, and that is a deliberate
    asymmetry with ``rotation``. The span-driven offset is computed for the whole
    catalogue; applied to a two-hundred-row category subset most of its values
    would land past the end and wrap, costing a query to arrive back at offset
    zero. Targeted subsets are small enough that the mechanism the module
    docstring calls "sufficient for a small catalogue" — cooldowns pushing
    already-seen rows out of the SQL, so the next-best rows move up — is the
    right one. A viewer does not see the same fourteen cameras forever; they see
    the next fourteen once the first are cooling down.

    ``share`` is a fraction of the pool target. ``rotation`` has no share: it
    fills whatever the targeted sources left, which is how a viewer with no
    history ends up with a pool that is entirely rotation and therefore identical
    to the pre-sources behaviour.
    """

    name: str
    clause: str = ""
    params: tuple = ()
    rotates: bool = False
    share: float = 0.0


#: ``(expires_at_epoch, span)``. Process-local and shared by every viewer, which
#: is correct: this is a property of the catalogue, not of anybody browsing it.
_SPAN_CACHE: list = [0.0, 0]


def catalogue_span(cur) -> int:
    """Roughly how many rows there are worth rotating over.

    Fed to :func:`exposure.rotation_offset`, which without it can only produce
    ``rotation_slots`` distinct starting offsets — a constant, and therefore a
    constant ceiling on how much of the catalogue any viewer can ever be served.
    See that function for the measurement.

    Three deliberate imprecisions, because this number chooses a page offset and
    an offset that is slightly wrong costs one wrapped query:

    * It counts what :func:`eligibility.candidate_sql` can express and ignores
      :func:`eligibility.gate`, so it is an over-estimate by however many rows
      fail the judgemental checks. An over-estimate is the safe direction: some
      offsets land in a thin region and wrap, which is the behaviour ``_scan``
      already has for a catalogue smaller than its offset.
    * It does not exclude the viewer's own listings. That is per-viewer, and
      paying for a per-viewer count to refine a page offset would be spending
      the cost this cache exists to avoid.
    * It is cached for a whole rotation period. Recomputing more often than the
      offset can change would be work nobody can observe.

    Returns ``0`` when the count cannot be read, and ``0`` means "fall back to
    the fixed slots" rather than "the catalogue is empty" — a failed count must
    degrade to the old behaviour, not to serving nothing.

    The TTL is measured on ``subject.now_utc`` rather than on ``time.time``, for
    the same reason every other clock read in this package is: a test that
    advances its own clock past the rotation period expects the next request to
    behave like a new epoch, and a cache holding a real-wall-clock expiry would
    quietly serve the previous epoch's catalogue size forever.
    """
    now = subject_module.now_utc().timestamp()
    if _SPAN_CACHE[0] > now:
        return int(_SPAN_CACHE[1])
    try:
        cur.execute(
            "SELECT COUNT(*) AS n FROM marketplace_listings l "
            "LEFT JOIN users u ON u.user_id=l.seller_user_id "
            "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id "
            f"WHERE {eligibility.candidate_sql()}"
        )
        rows = _rows(cur)
        span = _int(rows[0].get("n")) if rows else 0
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_SPAN_COUNT_FAILED", exc_info=True)
        return 0
    # Cached even when it is 0 — an empty catalogue is a real answer and
    # recounting it on every request is the most expensive time to do so.
    ttl = max(60, config.rotation_period_seconds() or 0)
    _SPAN_CACHE[0] = now + ttl
    _SPAN_CACHE[1] = span
    return span


def reset_span_cache() -> None:
    """Forget the cached catalogue size.

    Exists for tests, which build a catalogue *after* something has already
    counted it. Without this a fixture's second scenario inherits the first
    scenario's span, and a reachability measurement would silently be measuring
    the wrong catalogue.
    """
    _SPAN_CACHE[0] = 0.0
    _SPAN_CACHE[1] = 0


#: How many categories and sellers a targeted source will name in its ``IN``
#: clause. Both are bounded so a viewer with a long history cannot turn one
#: retrieval question into a several-hundred-parameter statement — the same
#: reasoning as :data:`MAX_SQL_EXCLUSIONS`, and the profile reads that feed these
#: already apply their own ``LIMIT``, so this is the second of two bounds.
MAX_SOURCE_TERMS = 24

#: Listing ids the trending pre-query will consider. Deliberately larger than any
#: quota: the ids it returns still have to survive eligibility and the viewer's
#: own cooldowns, and a trending set that collapses to nothing after filtering is
#: worse than a slightly longer ``IN`` clause.
TRENDING_POOL_SIZE = 120


def _trending_ids(cur, *, exclude_subject_ref: str = "") -> tuple[int, ...]:
    """Listings earning engagement inside the trend window, busiest first.

    A pre-query rather than a join. Ordering the whole catalogue by a correlated
    count means visiting every listing to rank a handful; grouping the event table
    first visits only listings that had an event at all, which in production is a
    small minority, and the read is covered by ``idx_cd_engage_listing
    (listing_id, action, event_at)``.

    ``exclude_subject_ref`` drops the asking viewer's own events, and is not
    optional in practice. Without it the source is circular: a viewer's twenty
    clicks on cameras are enough to make those exact twenty listings the busiest
    rows in the window, so "trending" would partly mean "you clicked it" and the
    source would become a fourth route back to the products the viewer just
    engaged with — the one thing the exposure ledger exists to prevent. Trending
    is meant to be a *crowd* signal. The viewer's own history is already
    represented by ``affinity``, which names categories rather than listing ids
    and therefore widens toward siblings instead of replaying the same rows.

    Deliberately *not* a rate. This is retrieval, and retrieval only has to decide
    which rows are worth looking at — :func:`engine._listing_stats` already
    computes impressions alongside clicks and :mod:`ranking` already divides. A
    velocity here would be a second, staler opinion about the same thing, and the
    two would disagree.

    Returns ``()`` on any failure, which removes the source. That is the correct
    degradation: a missing source narrows the pool's provenance, and ``rotation``
    absorbs the freed quota.
    """
    own = str(exclude_subject_ref or "").strip()
    try:
        cur.execute(
            "SELECT listing_id, COUNT(*) AS n FROM commerce_discovery_engagement_events "
            "WHERE action IN ('click','product_view','add_to_cart') AND event_at>? "
            "AND listing_id IS NOT NULL "
            + ("AND COALESCE(subject_ref,'')<>? " if own else "")
            + "GROUP BY listing_id ORDER BY n DESC, listing_id DESC LIMIT ?",
            (
                subject_module.window_start_iso(config.trend_window_seconds()),
                *((own,) if own else ()),
                TRENDING_POOL_SIZE,
            ),
        )
        return tuple(_int(row.get("listing_id")) for row in _rows(cur) if _int(row.get("listing_id")))
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_TRENDING_UNAVAILABLE", exc_info=True)
        return ()


def _terms(values, cast) -> tuple:
    """De-duplicated, falsy-stripped, bounded source terms in first-seen order."""
    out: list = []
    seen: set = set()
    for value in values or ():
        try:
            key = cast(value)
        except (TypeError, ValueError):
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= MAX_SOURCE_TERMS:
            break
    return tuple(out)


def _sources(
    cur,
    *,
    interests: Sequence[str] = (),
    followed_sellers: Sequence[int] = (),
    rotation_offset: int = 0,
    subject_ref: str = "",
) -> tuple[_Source, ...]:
    """The retrieval questions worth asking about this viewer, in order.

    Targeted sources come first so their quotas are spent before ``rotation``
    fills the remainder. That ordering also decides provenance: a row is labelled
    with the *first* source that found it, so a camera the affinity query returned
    is ``affinity`` even though ``rotation`` would eventually have reached it.
    The label answers "which question surfaced this?", which is the question an
    operator debugging a feed is actually asking.

    ``rotation`` is unconditional and last. Nothing here can remove it, because a
    viewer whose every targeted source is empty must still get a full pool.
    """
    found: list[_Source] = []

    categories = _terms(interests, lambda value: str(value or "").strip().lower())
    if categories:
        marks = ",".join("?" for _ in categories)
        found.append(_Source(
            name="affinity",
            # Matched on the normalised category rather than the raw column: the
            # profile reads return whatever case the listing was written in, and
            # 'Cameras' and 'cameras' are one interest.
            clause=f"AND LOWER(TRIM(COALESCE(l.category,''))) IN ({marks})",
            params=categories,
            share=0.35,
        ))

    sellers = _terms(followed_sellers, lambda value: int(value or 0))
    if sellers:
        marks = ",".join("?" for _ in sellers)
        found.append(_Source(
            name="followed",
            clause=f"AND l.seller_user_id IN ({marks})",
            params=sellers,
            share=0.25,
        ))

    # The viewer's own events are excluded, so this is the crowd's opinion and not
    # an echo of their own last session. See :func:`_trending_ids`.
    trending = _trending_ids(cur, exclude_subject_ref=subject_ref)
    if trending:
        marks = ",".join("?" for _ in trending)
        found.append(_Source(
            name="trending",
            clause=f"AND l.id IN ({marks})",
            params=trending,
            share=0.2,
        ))

    found.append(_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)))
    return tuple(found)


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
    product_cap: Optional[int] = None,
    target: Optional[int] = None,
    rotation_offset: int = 0,
    exclude_listing_ids: Sequence[int] = (),
    interests: Sequence[str] = (),
    followed_sellers: Sequence[int] = (),
) -> PoolResult:
    """Eligible, non-cooled-down candidates for one viewer.

    ``product_cooldown`` and ``seller_cooldown`` are passed in rather than read
    from config because they are a *surface policy* decision: Reels is twice as
    quiet as the feed, and Marketplace — where the user came to shop — is far
    less quiet than either. The router owns that judgement; this module owns
    applying it.

    ``interests`` and ``followed_sellers`` turn on the targeted retrieval sources
    described in the module docstring. Both default to empty, and empty means the
    pool is built by ``rotation`` alone — exactly what it did before sources
    existed. They are passed in for the same reason the cooldowns are: whether a
    surface may personalise is ``preferences.viewer_policy``'s decision, not this
    module's, and a surface serving a viewer who opted out must pass nothing here.
    """
    state = exposure or exposure_module.EMPTY
    excluded_ids = frozenset(_int(value) for value in exclude_listing_ids) - {0}
    target = int(target or config.candidate_target_size())
    product_cooldown = (
        config.product_cooldown_seconds() if product_cooldown is None else max(0, int(product_cooldown))
    )
    seller_cooldown = (
        config.seller_cooldown_seconds() if seller_cooldown is None else max(0, int(seller_cooldown))
    )
    product_cap = config.product_cap() if product_cap is None else max(1, int(product_cap))

    # Cooldowns are a spacing preference, and a preference has to yield to
    # scarcity. On a marketplace with three sellers, a fifteen-minute seller
    # cooldown removes the entire catalogue after two impressions — the surface
    # would go quiet for reasons that have nothing to do with the user and
    # everything to do with inventory size. So the ladder: ask with full
    # spacing, and only if that comes back under the low watermark, ask again
    # with less. Relaxing the seller cooldown first is deliberate — showing two
    # products from one seller is a much smaller failure than showing the same
    # product twice, which is the one this pipeline exists to prevent.
    # `product_cap` is deliberately absent from the ladder. The ladder exists to
    # trade spacing for coverage, and that trade is sound: a product shown again
    # sooner than preferred is still a product the viewer has not exhausted. The
    # cap is the other kind of statement — how many times in total — and a thin
    # catalogue is not an argument for exceeding it. Before this was enforced, the
    # bottom rung (`// 4`) was the whole story on the shopping surfaces: measured
    # 2026-09-27, one product reached five sightings against a cap of three,
    # because nothing anywhere checked the total.
    watermark = config.candidate_low_watermark()
    ladder = ((product_cooldown, seller_cooldown),)
    if seller_cooldown > 0:
        ladder += ((product_cooldown, 0),)
    if product_cooldown > 0:
        ladder += ((product_cooldown // 4, 0),)

    # Built once, outside the ladder. The sources are a property of the viewer and
    # the catalogue, not of how much spacing this rung is asking for, and
    # `_trending_ids` runs a query — recomputing it per rung would triple the cost
    # of the exact situation the ladder exists to rescue, a thin catalogue.
    # `subject_ref` comes off the policy rather than being a parameter of its own.
    # It is not an option the caller tunes — it is *who is asking*, and the
    # trending source is only a crowd signal if the asker's own events are removed
    # from it. Taking it from the object every caller already has to pass means a
    # new call site cannot forget it and quietly get the circular version back.
    sources = _sources(
        cur,
        interests=interests,
        followed_sellers=followed_sellers,
        rotation_offset=rotation_offset,
        subject_ref=getattr(policy, "subject_ref", "") or "",
    )

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
            product_cap=product_cap,
            target=target,
            rotation_offset=rotation_offset,
            excluded_ids=excluded_ids,
            sources=sources,
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
    product_cap: int,
    target: int,
    rotation_offset: int,
    excluded_ids: frozenset = frozenset(),
    sources: Sequence[_Source] = (),
) -> PoolResult:
    """One pass over every source's batched fetch-and-filter loop, at fixed cooldowns.

    The per-row filtering, the duplicate set and the drop counters are shared
    across sources rather than per-source, and all three have to be. A product the
    affinity query returned and the rotation query also returns is one candidate,
    not two; a product at its cap is at its cap no matter which question found it;
    and a `dropped` map that reset per source would report the last source's
    rejections as the whole response's.

    ``exhausted`` and ``wrapped`` describe the rotation source alone. They are
    statements about running off the end of the catalogue, and a targeted source
    reaching the end of its own subset — all forty of this seller's listings — is
    not the catalogue running out.
    """
    batch_size = config.candidate_batch_size()
    max_batches = config.candidate_max_batches()
    # A targeted source gets a fraction of the query budget as well as a fraction
    # of the pool, so adding sources cannot multiply the worst-case query count by
    # the number of them. With the default six, the ceiling goes from 6 queries to
    # 6 + 2 + 2 + 2, and only for a viewer who has history in all three.
    targeted_batches = max(1, max_batches // 3)

    hard_excluded = _hard_exclusions(policy, state, product_cooldown, excluded_ids, product_cap)

    accepted: list[dict] = []
    seen_ids: set[int] = set()
    dropped: dict[str, int] = {}
    contributed: dict[str, int] = {}
    scanned = 0
    batches = 0
    wrapped = False
    exhausted = False

    for source in sources or (_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)),):
        # `rotation` is asked for everything still missing; a targeted source is
        # capped at its share so one narrow interest cannot become the whole pool.
        if source.share > 0:
            quota = min(target, max(1, int(target * source.share)))
        else:
            quota = target
        want = min(target, len(accepted) + quota)
        if len(accepted) >= want:
            continue

        # Recorded as zero the moment the source is *asked*, so that an absent key
        # means "this viewer has no such history" and a zero means "they do, and it
        # produced nothing eligible". Those are different operational problems —
        # the first is a cold-start question and the second is an inventory one —
        # and a map that only counted successes could not tell them apart.
        contributed.setdefault(source.name, 0)

        budget = max_batches if source.name == SOURCE_ROTATION else targeted_batches
        offset = max(0, int(rotation_offset)) if source.rotates else 0
        source_wrapped = False
        spent = 0

        while spent < budget and batches < max_batches + 3 * targeted_batches:
            try:
                fetched = _fetch(
                    cur, viewer_user_id, offset, batch_size, hard_excluded,
                    extra_sql=source.clause, extra_params=source.params,
                )
            except Exception:
                LOGGER.warning(
                    "COMMERCE_DISCOVERY_POOL_QUERY_FAILED source=%s offset=%s",
                    source.name, offset, exc_info=True,
                )
                break
            spent += 1
            batches += 1

            if not fetched:
                # Ran off the end. Wrapping once is what makes rotation safe on a
                # catalogue smaller than the offset — without it, a marketplace with
                # 40 listings and an offset of 120 would serve nothing at all for
                # three quarters of every rotation period.
                if offset > 0 and not source_wrapped:
                    offset = 0
                    source_wrapped = True
                    wrapped = wrapped or source.name == SOURCE_ROTATION
                    continue
                exhausted = exhausted or source.name == SOURCE_ROTATION
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
                    product_cap=product_cap,
                    seen_ids=seen_ids,
                    excluded_ids=excluded_ids,
                )
                if code:
                    dropped[code] = dropped.get(code, 0) + 1
                    continue
                seen_ids.add(_int(row.get("id")))
                # Mutating the fetched dict rather than wrapping it: these rows are
                # already this module's own `dict(row)` copies, made by `_rows`, and
                # every consumer downstream reads them as mappings.
                row["candidate_source"] = source.name
                contributed[source.name] = contributed.get(source.name, 0) + 1
                accepted.append(row)
                if len(accepted) >= want:
                    break

            if len(accepted) >= want:
                break
            if len(fetched) < batch_size:
                if offset > 0 and not source_wrapped:
                    offset = 0
                    source_wrapped = True
                    wrapped = wrapped or source.name == SOURCE_ROTATION
                    continue
                exhausted = exhausted or source.name == SOURCE_ROTATION
                break
            offset += batch_size

    return PoolResult(
        rows=tuple(accepted),
        scanned=scanned,
        batches=batches,
        dropped=dropped,
        exhausted=exhausted,
        wrapped=wrapped,
        sources=contributed,
    )


def _escalated(cooldown: int, seen: int) -> int:
    """The spacing required before sighting number ``seen + 1``.

    Linear in the number of times this viewer has already seen the product, so a
    product gets more boring the more often it appears: the gap before the second
    sighting is the configured cooldown, before the third it is twice that, and
    so on.

    Measured flat before this existed. 300 pages a minute apart, all inside the
    product window, gave gaps of *exactly* 90/90/90 minutes on the feed and
    23/23/23 on the marketplace — the second sighting and the third were spaced
    identically, so "fatigue" stopped increasing at the point it needed to start.
    The visible consequence was that a viewer spent a product's entire seven-day
    allowance inside one sitting: all three feed sightings within 180 minutes,
    all six marketplace sightings within 115, and then silence for a week. Three
    reminders across three days is a better sequence than three inside one
    afternoon, and it is the same three impressions.

    Linear rather than exponential because the cap is small (3 to 6). Doubling
    would put the last sighting of a six-cap surface 32 cooldowns out — past the
    seven-day window the count itself decays over, so the allowance could never
    be spent and the effective cap would silently become much lower than the
    configured one. A cap that cannot be reached is a different number from the
    one an operator set.

    This is *spacing*, so unlike the volume cap it is a preference and it yields
    to scarcity: ``build``'s relaxation ladder scales the base cooldown, and
    scaling the base scales every step of the escalation with it.
    """
    return int(cooldown) * max(1, int(seen))


def _hard_exclusions(
    policy,
    state,
    product_cooldown: int,
    excluded_ids: frozenset = frozenset(),
    product_cap: int = 0,
) -> tuple[int, ...]:
    """Listing ids worth excluding in SQL rather than in Python.

    Only the certainties go here — explicitly suppressed products, products at
    their exposure cap, and products inside their cooldown. All three are
    decisions already made; re-fetching them to throw them away is the work the
    batching loop is trying to avoid.

    ``product_cap`` defaults to ``0`` (meaning "leave it to :func:`_reject`")
    rather than to :func:`config.product_cap`, and that asymmetry with
    :func:`_reject` — where the same argument is *required* — is deliberate.
    Omitting it here costs a few wasted rows per batch and changes no outcome.
    Omitting it there would silently stop enforcing the cap, so it cannot be
    omitted.

    Ordered most-recently-seen first so that when the list is truncated at
    :data:`MAX_SQL_EXCLUSIONS` the ids that survive are the ones most likely to
    be near the head of the candidate ordering. The truncated remainder is not
    lost — :func:`_reject` re-checks every row anyway. This is an optimisation,
    never the enforcement.

    ``excluded_ids`` goes first for that reason: a viewer with two hundred
    cooled-down products would otherwise push the product they are *currently
    looking at* past the truncation point, and the only thing standing between
    that and a product page recommending itself would be :func:`_reject`. It
    would hold — but paying one wasted row per batch for the whole scan to lean
    on it is a poor trade when the caller has told us the answer up front.
    """
    excluded: list[int] = []
    seen: set[int] = set()

    for listing_id in excluded_ids:
        key = _int(listing_id)
        if key and key not in seen:
            seen.add(key)
            excluded.append(key)

    for listing_id in getattr(policy, "suppressed_listings", ()) or ():
        key = _int(listing_id)
        if key and key not in seen:
            seen.add(key)
            excluded.append(key)

    # One pass, two grounds. Both read the same newest-first list, so merging
    # them keeps the recency ordering the truncation rationale above depends on —
    # two separate passes would put an old at-cap product ahead of a product seen
    # a minute ago.
    if product_cooldown > 0 or product_cap > 0:
        for listing_id in state.recent_products:
            key = _int(listing_id)
            if not key or key in seen:
                continue
            # `product_seen` counts inside `config.product_window_seconds()`
            # only, so a listing that appears in `recent_products` from beyond
            # the window carries a count of zero and is not excluded on this
            # ground. That is the same window `_reject` measures against.
            at_cap = product_cap > 0 and state.product_seen(key) >= product_cap
            cooling = product_cooldown > 0 and state.seconds_since_product(key) < _escalated(
                product_cooldown, state.product_seen(key)
            )
            if at_cap or cooling:
                seen.add(key)
                excluded.append(key)

    for listing_id in state.purchased:
        key = _int(listing_id)
        if key and key not in seen:
            seen.add(key)
            excluded.append(key)

    return tuple(excluded[:MAX_SQL_EXCLUSIONS])


def _fetch(
    cur,
    viewer_user_id: Any,
    offset: int,
    limit: int,
    excluded: Sequence[int],
    *,
    extra_sql: str = "",
    extra_params: Sequence[Any] = (),
) -> list[dict]:
    """One batch of catalogue rows, in the canonical candidate ordering.

    The ordering must be **total** for ``OFFSET`` to mean anything — two rows
    that tie on every sort key can swap between batches, which is how paginated
    queries quietly serve one row twice and skip another. ``l.id DESC`` is the
    tiebreak that makes it total.

    ``extra_sql`` is a source's restriction (see :class:`_Source`). It is the only
    thing that varies between sources — the projection, the joins, the eligibility
    clause, the self-exclusion and the ordering are all shared, so a source cannot
    return a row the untargeted query would have rejected, and cannot return rows
    in an order that makes its own ``OFFSET`` mean something different. One
    retriever, several questions.
    """
    clause = ""
    params: list[Any] = [_int(viewer_user_id)]
    params.extend(extra_params or ())
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
        f"{(extra_sql + ' ') if extra_sql else ''}"
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
    product_cap: int,
    seen_ids: set,
    excluded_ids: frozenset = frozenset(),
) -> str:
    """``""`` to keep the row, else the drop code.

    Order is chosen so the code an operator reads is the most specific true
    statement: a listing that is both suppressed and ineligible reports
    ``suppressed_listing``, because that is the one the *user* asked for.
    """
    listing_id = _int(row.get("id"))
    if not listing_id or listing_id in seen_ids:
        return "duplicate"

    # The authoritative self-exclusion check. The SQL ``NOT IN`` above is an
    # optimisation that a truncated exclusion list can silently skip; this
    # cannot be skipped, so a product page can never recommend itself.
    if listing_id in excluded_ids:
        return "context_excluded"

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

    # Volume before spacing, because volume is the stronger statement and an
    # operator reading a drop code deserves the stronger one. A product at its cap
    # is also inside its cooldown almost by construction, and reporting that as
    # `product_cooldown` would say "come back later" about something that is not
    # coming back this week.
    #
    # This is the frequency filter `ranking.repetition_penalty` has always
    # described. Until it existed, the cap set the denominator of a penalty and
    # nothing else: past the cap the penalty saturated, so a fourth sighting and a
    # fortieth scored identically, and on the shopping surfaces — where the floor
    # is lowest and `build`'s ladder cuts the cooldown to a quarter — products were
    # measured reaching five sightings against a cap of three.
    #
    # It belongs here rather than in the score for the reason the comment above
    # gives about purchased products: a penalty is a statement about how good a
    # recommendation is, and this is a statement about whether it is one at all.
    if product_cap > 0 and state.product_seen(listing_id) >= product_cap:
        return "product_cap"

    if product_cooldown > 0 and state.seconds_since_product(listing_id) < _escalated(
        product_cooldown, state.product_seen(listing_id)
    ):
        return "product_cooldown"

    # The cross-surface window is checked in ranking, not here. A product seen
    # on another surface a moment ago should be heavily *outranked*, not made
    # unavailable — if it is the only thing left in the pool, showing it beats
    # showing a blank, and the brief forbids the blank.

    if seller_cooldown > 0 and seller_id and state.seconds_since_seller(seller_id) < seller_cooldown:
        return "seller_cooldown"

    return ""
