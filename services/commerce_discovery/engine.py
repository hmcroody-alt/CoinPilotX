"""The pipeline: eligible inventory → a small number of placements, or none.

    ELIGIBLE INVENTORY → QUALITY FILTER → CONTEXT MATCH → USER RELEVANCE
      → FREQUENCY CHECK → RANK → PLACEMENT

Everything downstream of a placement — impression, engagement, conversion
feedback — lives in ``events.py``. The split is not tidiness: serving is a read
path that must never block, and recording is a write path that must never lose a
row. Putting them in one module makes it far too easy for a write to end up on
the serve path.

Three rules the shape of this file exists to enforce
---------------------------------------------------

**Returning nothing is a success.** :func:`serve` returns ``[]`` for a viewer
who opted out, a surface below its relevance floor, an empty catalogue, a
frequency cap, a disabled flag, *and* an unexpected exception. Every one of
those is a normal outcome the clients already render as an ordinary quiet feed.
There is no error path that a client has to handle, which is what makes "a
recommendation failure must never break the feed" true rather than hoped for.

**Filtering happens before scoring, never after.** Suppressions and frequency
caps remove candidates from the pool; they are not a post-hoc sort. A filter
applied after ranking is a filter that can be defeated by a short candidate
list — score three items, drop two, serve the one the user told you to hide.

**Diversity is decided during selection, not by the scorer.** Whether a listing
is too similar to the one already chosen is a fact about the *response*, not
about the listing, so it cannot be a column in a scored row. :func:`_select`
re-scores the diversity term as it builds the output, which is the only place
the already-chosen set exists.

What this module stopped owning
-------------------------------

Candidate retrieval used to live here, as one fixed ``LIMIT 120`` over a
deterministic ordering, and that single query is what made the engine repeat
itself: the same head of the catalogue came back on every request, for every
surface, for everybody. It now lives in ``pool``, which pages and rotates, and
the per-viewer memory that gives the ranker something to rotate *against* lives
in ``exposure``. The four surfaces' differing spacing and diversity budgets live
in ``router``. What is left here is the order those four are asked in, which is
the one thing that genuinely belongs to a pipeline.

On the frequency cap reading across promotion classes
-----------------------------------------------------

Event storage never merges organic and paid (see ``promotion.py``). Frequency
accounting does, and deliberately: a buyer who has already seen four sponsored
products does not experience a fifth organic one as a different kind of event.
The wall exists to keep *accounting* honest, not to let each class spend the
user's patience as though the other did not exist.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from . import (
    config,
    eligibility,
    exposure,
    metrics,
    pool,
    preferences,
    promotion,
    ranking,
    relationship,
    router,
    schema,
    subject,
    tagging,
    taxonomy,
)
from .preferences import ViewerPolicy
from .router import SurfacePolicy

LOGGER = logging.getLogger(__name__)

#: What a relatedness claim would be *pointing at*, per surface.
#:
#: The reason code is a user-visible assertion, so it may only name something the
#: viewer can actually look at. Three surfaces have a post on screen and one has a
#: product; Messenger and Marketplace have neither, so a card there earns no
#: relatedness claim however well it scored — `choose_reason` reads the absence as
#: "no context claim available" and the card falls through to a reason that is
#: true, or to "popular".
#:
#: Written as an explicit map over `schema.SURFACES` rather than as
#: `SIMILAR_PRODUCT if surface == "product_detail" else CONTEXT`. That form was
#: correct for the surfaces it was written for and silently wrong for the other
#: two: it answered "related to this post" for a Messenger strip inside a
#: conversation and for a Marketplace shelf on the shop tab. Neither is reachable
#: from the shipped clients today — `useMessengerCommerce` and
#: `useMarketplaceCommerce` send no context — but the serve route accepts a
#: context for any surface in `SURFACES`, so the claim was one new caller away,
#: and the web build is the next new caller. A default of `None` also means a
#: surface added later has to declare its own wording instead of inheriting the
#: most specific claim in the vocabulary.
#:
#: On Messenger the mislabel would be worse than inaccurate. The code renders
#: through `commerce:discovery.subtitle.related_to_this_post`, whose English is
#: "Related to what you're reading" — printed under a product inside a private
#: conversation, that tells the reader their messages were read in order to pick
#: it. They were not: `useMessengerCommerce` sends nothing about the thread, and
#: nothing in this pipeline can see a message. A caption that invents a
#: surveillance capability the product does not have costs more than a missing
#: caption by a wide margin.
#:
#: This deliberately coincides with `suitability.CONTENT_SURFACES` without
#: reading it. Same three surfaces, two unrelated questions: that set is about
#: whether commerce may appear beside content, this map is about what a card may
#: claim once it does.
CONTEXT_CLAIM: Mapping[str, str] = {
    "feed": ranking.REASON_CONTEXT,
    "reels": ranking.REASON_CONTEXT,
    "post_detail": ranking.REASON_CONTEXT,
    "product_detail": ranking.REASON_SIMILAR_PRODUCT,
}


def _rows(cur) -> list[dict]:
    try:
        fetched = cur.fetchall() or []
    except Exception:
        return []
    return [row if isinstance(row, dict) else dict(row) for row in fetched]


def serve(
    cur,
    user_id: Any,
    surface: str,
    *,
    conn,
    context: Optional[Mapping[str, Any]] = None,
    session_id: str = "",
    limit: Optional[int] = None,
    promotion_class: str = promotion.ORGANIC,
    parse_price=None,
    serialize=None,
    exclude_listing_ids: Sequence[int] = (),
    content_post_id: Any = 0,
) -> list[dict]:
    """Placements for one surface, or ``[]``.

    ``parse_price`` and ``serialize`` are injected (they live on ``bot``) so the
    whole pipeline is testable without importing the monolith. The route pack
    supplies the real ones.

    ``conn`` is wanted only so the schema DDL can commit itself — see
    :func:`schema.ensure_schema`. Without it the tables are still created, but
    inside the caller's transaction, where a later failure rolls them back after
    the once-per-process guard has already recorded success.

    ``exclude_listing_ids`` names products that must not come back — in practice
    the one the viewer is already looking at. It can only ever narrow the result,
    which is what makes it safe for a route to populate from a request body.

    ``content_post_id`` is the post the viewer is reading, and unlike
    ``exclude_listing_ids`` it *widens*: it is the key for creator-tagged products
    (:mod:`tagging`). The route only supplies it on the three surfaces that have a
    post on screen, and the forgeability argument for it is re-derived for this
    additive use in :mod:`tagging`'s docstring rather than inherited from the
    restrictive one — they are different risk classes and the narrowing argument
    does not carry over.
    """
    try:
        return _serve(
            cur, user_id, surface, conn=conn,
            context=context, session_id=session_id, limit=limit,
            promotion_class=promotion_class,
            parse_price=parse_price, serialize=serialize,
            exclude_listing_ids=exclude_listing_ids,
            content_post_id=content_post_id,
        )
    except Exception:
        # The fail-safe. A bug anywhere above becomes a quiet feed, never a
        # broken one, and never a 500 on a surface whose real job is something
        # else entirely.
        LOGGER.exception("COMMERCE_DISCOVERY_SERVE_FAILED surface=%s", surface)
        return []


def _serve(
    cur,
    user_id: Any,
    surface: str,
    *,
    conn,
    context: Optional[Mapping[str, Any]],
    session_id: str,
    limit: Optional[int],
    promotion_class: str,
    parse_price,
    serialize,
    exclude_listing_ids: Sequence[int] = (),
    content_post_id: Any = 0,
) -> list[dict]:
    surface = str(surface or "").strip().lower()
    if surface not in schema.SURFACES:
        return []

    # Before `ensure_schema`, before `viewer_policy`, before anything that opens
    # a cursor: a surface an operator has switched off should cost one env read
    # and no queries. Placed here rather than in `viewer_policy` — which is where
    # the master switch is checked — because that function is not told which
    # surface it is resolving for, and giving it one so it could answer this
    # would make a viewer-scoped decision depend on a surface-scoped one.
    #
    # Silent, like the unknown-surface check above it and unlike the fail-safe
    # below. An operator-requested empty list is not an incident, and logging it
    # per request on `feed` would put a line on the hottest path in the product
    # to report that something is working as configured.
    if not config.surface_enabled(surface):
        return []

    klass = promotion.assert_unpaid(promotion_class)

    if not schema.ensure_schema(conn):
        return []

    policy = preferences.viewer_policy(cur, user_id)
    if not policy.allows_surface(surface):
        LOGGER.debug(
            "COMMERCE_DISCOVERY_BLOCKED surface=%s reason=%s",
            surface, policy.blocked_reason or "surface_suppressed",
        )
        return []

    budget = _budget(surface, policy, limit)
    if budget <= 0:
        return []
    if _session_cap_reached(cur, policy.subject_ref, surface):
        return []

    branch = router.policy_for(surface)

    # The memory read comes before the candidate read because the pool needs it:
    # cooldowns are the cheapest filter available and applying them in SQL is
    # what keeps the batching loop from fetching pages of products this viewer
    # has already been shown.
    state = exposure.load(cur, policy.subject_ref, user_id)
    # The cap is handed to the observer so it can alert when an *enforced* limit
    # did not hold. The check is nearly free and it is the only thing in
    # production that would notice the enforcement regressing — the symptom
    # otherwise is a feed that quietly gets more repetitive.
    _observe_repetition(
        state, surface=surface, subject_ref=policy.subject_ref, product_cap=branch.product_cap
    )

    # Read before the pool, not after. It used to be read after, which was fine
    # while it only fed the ranker — but ranking can only reorder rows retrieval
    # already returned, and retrieval mentioned the viewer exactly once, to exclude
    # their own listings. Measured 2026-09-27: a viewer whose only twenty clicks
    # were all on cameras, against a catalogue 10% cameras, was served 14.1%
    # cameras. The affinity bonus was firing; it had nothing to fire on. So the
    # profile now also chooses *which questions retrieval asks* — see
    # `pool._sources`.
    #
    # The `personalized` gate is load-bearing in both places. A viewer who opted
    # out must get the untargeted query, which means passing no interests here, not
    # merely declining to score them afterwards.
    profile = (
        _interest_profile(cur, user_id, subject_ref=policy.subject_ref)
        if policy.personalized
        else {}
    )

    # Read once and held, because it is needed twice: retrieval widens by it, and
    # `_select` takes it ahead of the scorer. Reading it twice would make those two
    # able to disagree — a product tagged between the two reads would be selected
    # ahead of a scorer that never retrieved it, which is a KeyError waiting to
    # happen rather than a subtle ranking bug.
    #
    # `content_type="post"` on every surface, including reels, because the id
    # arriving here is read from the posts table by the route on all of them
    # (`_content_post`). The table can hold reel, video and status attachments for
    # the composer's sake; only post attachments are resolvable from here, which is
    # why the composer attaches a reel's products to its mirror post as well — the
    # same thing `pulse_attach_music_to_content` does for a track. Passing a reel
    # id under `content_type="post"` would look up tags on whichever unrelated post
    # happened to share that number.
    #
    # Deliberately outside the `policy.personalized` gate that `profile` is behind.
    # A creator tag is not personalisation — it says nothing about the viewer and
    # would be identical for every person reading the post — so a viewer who opted
    # out of personalised commerce should still see the product the creator
    # attached. The opt-out that *does* suppress it is the surface-level one, and
    # that already returned above.
    tagged_listing_ids = tagging.tagged_listing_ids(
        cur, content_type="post", content_id=content_post_id,
    )

    built = pool.build(
        cur,
        viewer_user_id=user_id,
        policy=policy,
        exposure=state,
        parse_price=parse_price,
        surface=surface,
        product_cooldown=branch.product_cooldown_seconds,
        seller_cooldown=branch.seller_cooldown_seconds,
        product_cap=branch.product_cap,
        target=branch.pool_target,
        # The span is what lets the offset reach past row 540 of the candidate
        # ordering. Without it the offset space is four fixed values and 70.8% of
        # a 2000-listing catalogue can never be fetched by anybody; see
        # `exposure.rotation_offset` for the measurement. `catalogue_span` is
        # cached for a rotation period, so this is one COUNT per process per hour
        # rather than one per request.
        rotation_offset=exposure.rotation_offset(
            policy.subject_ref, span=pool.catalogue_span(cur)
        ),
        exclude_listing_ids=exclude_listing_ids,
        # `topics` (saved products) is deliberately not fed to retrieval. A saved
        # product is a strong interest signal and a weak *novelty* signal — the
        # viewer already found it — so widening retrieval by it would spend quota
        # fetching things they have already decided about. It still feeds the
        # ranker, where it belongs.
        interests=profile.get("viewed_categories", ()),
        followed_sellers=tuple(profile.get("followed_sellers", ()) or ()),
        tagged_listing_ids=tagged_listing_ids,
    )
    # Before the empty-pool return, not after: a pool that came back empty is
    # exactly when an operator most needs to know which questions were asked.
    metrics.observe_sources(built.sources, surface=surface)

    candidates = list(built.rows)
    if not candidates:
        LOGGER.debug(
            "COMMERCE_DISCOVERY_POOL_EMPTY surface=%s scanned=%d batches=%d dropped=%s sources=%s",
            surface, built.scanned, built.batches, built.dropped, built.sources,
        )
        return []

    stats = _listing_stats(cur, [row["id"] for row in candidates])

    # The context signal is the same computation on every surface; the *claim* it
    # justifies is not. Resolved here, where the surface is known, rather than
    # inside the ranker, which is deliberately surface-agnostic.
    context_reason = CONTEXT_CLAIM.get(surface)

    # Whether a contextual match is even *sayable* on this request, which is the
    # precondition `relationship.classify` needs and is narrower than "a context
    # arrived". Three things have to hold, and each drops out of a decision made
    # somewhere else:
    #
    # * `policy.personalized` — the `context=... if policy.personalized else None`
    #   below means a request can carry a context that policy forbids using.
    # * a non-empty context, matching `score_listing`'s own `has_context`.
    # * `context_reason in REASON_PRIORITY` — `choose_reason` requires this before
    #   it will claim relatedness out loud. `messenger` and `marketplace` have no
    #   entry in `CONTEXT_CLAIM` yet the route hands them a client-supplied
    #   context, so without this term a Messenger card would record a
    #   `contextual` provenance while its own label truthfully claimed nothing of
    #   the kind. That is the conflation `relationship.py` exists to remove,
    #   reappearing one layer down, and it is reachable from the wire today.
    context_claimable = bool(
        policy.personalized
        and context
        and context_reason in ranking.REASON_PRIORITY
    )
    # A product page's subject is a listing, so the same matched signal is
    # `similar` rather than `contextual`. Read off the map that already draws that
    # distinction instead of re-testing `surface == "product_detail"`, so a new
    # product-shaped surface cannot be added to `CONTEXT_CLAIM` and get the wrong
    # relationship here.
    subject_is_product = context_reason == ranking.REASON_SIMILAR_PRODUCT

    scored = []
    #: listing id -> §6 relationship. See the derivation below for why it is not
    #: a column on the row.
    relationships: dict[int, str] = {}
    now = subject.now_utc()
    weights = config.weights()
    for row in candidates:
        listing_id = int(row["id"])
        seller_id = int(row.get("seller_user_id") or 0)
        category = str(row.get("category") or "").strip().lower()
        verdict = ranking.score_listing(
            row,
            context=context if policy.personalized else None,
            interest_topics=profile.get("topics", ()),
            viewed_categories=profile.get("viewed_categories", ()),
            followed_sellers=profile.get("followed_sellers", frozenset()),
            stats=stats.get(listing_id),
            recent_impressions=state.product_seen(listing_id),
            recent_seller_impressions=state.seller_seen(seller_id),
            recent_category_impressions=state.category_seen(category),
            recent_total_impressions=state.total_impressions,
            recent_distinct_sellers=state.distinct_sellers,
            seconds_since_seen=state.seconds_since_product(listing_id),
            seconds_since_other_surface=state.seconds_since_other_surface(listing_id, surface),
            product_cooldown_seconds=branch.product_cooldown_seconds,
            cross_surface_cooldown_seconds=branch.cross_surface_cooldown_seconds,
            purchased=listing_id in state.purchased,
            in_cart=listing_id in state.in_cart,
            saved=state.is_saved(listing_id),
            hidden_strength=policy.hidden_strength(row),
            # Neutral on purpose, and not the whole story: diversity is a
            # property of the candidate *and the partial response*, which does
            # not exist yet. `_select` recomputes it per pick via
            # `ranking.rescore_diversity`. Reading a real value here would be
            # reading it from an empty response, which is what 1.0 means.
            diversity=1.0,
            parse_iso=subject.parse_iso,
            now=now,
            weights=weights,
            context_reason=context_reason,
        )
        # Derived here because this is the one place both inputs are in hand:
        # `candidate_source` is a property of *retrieval* that only `pool` knows
        # and only the row carries, and `signals` is a property of *ranking*.
        # `_select` may drop this row afterwards, so recomputing in `_persist`
        # would mean either threading the source through `_select` or re-reading it
        # from a row that has since been rescored.
        #
        # Kept in a side table rather than stamped onto the row, which was the
        # first attempt and the obvious one — `pool.build` sets
        # `row["candidate_source"]` exactly that way. It is wrong, and the reason
        # is in `_payload`: the product serializer is a *denylist*, so every key
        # this pipeline adds to a row ships to the buyer's device unless something
        # removes it. `candidate_source` is leaking today for precisely that
        # reason. Keyed by listing id, which `pool` guarantees is unique within a
        # pool because the exposure and dedup passes already depend on it.
        relationships[listing_id] = relationship.classify(
            candidate_source=row.get("candidate_source"),
            signals=verdict["signals"],
            subject_is_product=subject_is_product,
            context_offered=context_claimable,
        )
        scored.append((row, verdict))

    floor = branch.relevance_floor()
    selected = _select(scored, budget, floor, branch, preferred=tagged_listing_ids)
    if not selected:
        # The explicit form of "no placement is better than a bad placement":
        # the pool was non-empty and everything in it was below the bar.
        LOGGER.debug("COMMERCE_DISCOVERY_BELOW_FLOOR surface=%s floor=%.2f", surface, floor)
        return []

    return _persist(
        cur, selected, policy,
        surface=surface, session_id=session_id, klass=klass,
        parse_price=parse_price, serialize=serialize,
        relationships=relationships,
    )


# --- observability ----------------------------------------------------------
def _observe_repetition(state, *, surface: str, subject_ref: str, product_cap: int = 0) -> None:
    """Log how repetitive this viewer's recent window is. Never fails the request.

    Placed immediately after the exposure read, and deliberately *before* the
    pool is built, for two reasons. The state is the only input here, so nothing
    downstream can change the answer; and putting it before the two early returns
    below (empty pool, everything below the floor) means a surface that has gone
    quiet is still measured. A stuck feed and an empty one look identical from the
    outside, and the numbers are how you tell them apart — so the path that
    returns nothing is exactly the path that must not skip the measurement.

    Wrapped because this is instrumentation on a path whose job is something else.
    :func:`serve` already catches everything, but that fail-safe turns a bug here
    into an empty surface; catching locally turns it into a missing log line,
    which is the correct blast radius for a metric.
    """
    try:
        metrics.observe(
            metrics.from_state(state),
            surface=surface,
            subject_ref=subject_ref,
            product_cap=product_cap,
        )
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_REPETITION_OBSERVE_FAILED surface=%s", surface, exc_info=True)


# --- budget and session caps ------------------------------------------------
def _budget(surface: str, policy: ViewerPolicy, limit: Optional[int]) -> int:
    per_response, _ = config.surface_caps().get(surface, (0, 0))
    scaled = int(round(per_response * policy.budget_multiplier()))
    # A "low" user on a surface whose budget is 1 must still be able to get 1
    # rather than being rounded to zero by the multiplier — the switch for
    # "none at all" is marketplaceRecommendations, not the frequency dial.
    if per_response > 0:
        scaled = max(1, scaled)
    if limit is not None:
        scaled = min(scaled, max(0, int(limit)))
    return max(0, scaled)


def _session_cap_reached(cur, ref: str, surface: str) -> bool:
    """Has this viewer already had their whole per-session allowance here?

    Approximated over a rolling hour rather than a real session id, because the
    session id is client-supplied and a client that omits or rotates it would
    otherwise have no cap at all. An hour is long enough to bound a sitting and
    short enough not to punish someone returning in the evening.
    """
    _, per_session = config.surface_caps().get(surface, (0, 0))
    if per_session <= 0:
        return True
    try:
        cur.execute(
            "SELECT COUNT(*) AS n FROM commerce_discovery_impression_events "
            "WHERE subject_ref=? AND surface=? AND event_at>?",
            (ref, surface, subject.window_start_iso(3600)),
        )
        row = _rows(cur)
        count = int((row[0] if row else {}).get("n") or 0)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_SESSION_CAP_UNREADABLE", exc_info=True)
        return True  # fails closed
    return count >= per_session


# --- candidate retrieval ----------------------------------------------------
def _listing_stats(cur, listing_ids: Sequence[int]) -> dict[int, dict]:
    """Impressions, clicks, orders and refunds per listing — lifetime and recent.

    Two queries, not one join: the event tables and ``marketplace_orders`` have
    no useful join key beyond ``listing_id``, and a full outer join between two
    sparse aggregates would produce more rows than either.

    A failure here returns ``{}``, which scores every listing at the neutral
    prior. That is the right degradation — ranking gets less sharp, nothing
    breaks, and no listing is unfairly penalised for a query that did not run.

    Why both totals and a window
    ----------------------------

    Every count here used to be lifetime, and a lifetime count cannot express
    the one thing a commerce ranker most needs to know: whether interest is
    happening *now*. Worse, it cannot decay — a listing that earned ten clicks
    last year is indistinguishable from one earning ten a day, and the "trending"
    label it bought is permanent, because the counter only rises.

    So the window is collected alongside rather than instead of. Two reasons it is
    not a replacement. Production has very little engagement history, so a
    window-only rate would be a rate over single digits for nearly every listing,
    and the smoothing prior would flatten it to a constant. And the two answer
    different questions that both matter: lifetime says whether a listing has
    ever worked, the window says whether it is working. ``ranking`` decides which
    to trust per signal, and says so at each site.

    The recent counts are cheap. Both reads are covered by indexes that already
    exist and already lead with the columns being filtered —
    ``idx_cd_impr_listing`` and ``idx_cd_engage_listing (listing_id, action,
    event_at)`` — so adding ``event_at >`` narrows a range scan rather than
    forcing a new one.
    """
    if not listing_ids:
        return {}
    ids = [int(i) for i in listing_ids]
    marks = ",".join("?" for _ in ids)
    stats: dict[int, dict] = {i: {} for i in ids}
    trend_start = subject.window_start_iso(config.trend_window_seconds())

    try:
        cur.execute(
            "SELECT listing_id, COUNT(*) AS impressions, "
            "SUM(CASE WHEN event_at>? THEN 1 ELSE 0 END) AS recent_impressions "
            "FROM commerce_discovery_impression_events "
            f"WHERE visible=1 AND listing_id IN ({marks}) GROUP BY listing_id",
            [trend_start] + ids,
        )
        for row in _rows(cur):
            entry = stats.setdefault(int(row["listing_id"]), {})
            entry["impressions"] = int(row["impressions"] or 0)
            entry["recent_impressions"] = int(row.get("recent_impressions") or 0)

        cur.execute(
            "SELECT listing_id, COUNT(*) AS clicks, "
            "SUM(CASE WHEN event_at>? THEN 1 ELSE 0 END) AS recent_clicks "
            "FROM commerce_discovery_engagement_events "
            f"WHERE action='click' AND listing_id IN ({marks}) GROUP BY listing_id",
            [trend_start] + ids,
        )
        for row in _rows(cur):
            entry = stats.setdefault(int(row["listing_id"]), {})
            entry["clicks"] = int(row["clicks"] or 0)
            entry["recent_clicks"] = int(row.get("recent_clicks") or 0)
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_STATS_UNAVAILABLE", exc_info=True)

    try:
        cur.execute(
            "SELECT listing_id, "
            "SUM(CASE WHEN LOWER(COALESCE(status,'')) IN ('paid','fulfilled','completed') THEN 1 ELSE 0 END) AS orders, "
            "SUM(CASE WHEN LOWER(COALESCE(status,'')) IN ('refunded','returned') THEN 1 ELSE 0 END) AS refunds "
            f"FROM marketplace_orders WHERE listing_id IN ({marks}) GROUP BY listing_id",
            ids,
        )
        for row in _rows(cur):
            entry = stats.setdefault(int(row["listing_id"]), {})
            entry["orders"] = int(row.get("orders") or 0)
            entry["refunds"] = int(row.get("refunds") or 0)
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_ORDER_STATS_UNAVAILABLE", exc_info=True)

    return stats


def _interest_profile(cur, user_id: Any, *, subject_ref: str = "") -> dict:
    """Durable signals about what this viewer likes, each from the source it claims.

    Read from things the user did deliberately — saved a product, followed a
    seller, opened a product page — rather than from anything inferred about
    them. Every failure mode returns fewer signals, never wrong ones, and an
    empty profile scores at the neutral prior rather than against the user.

    On the three keys being three different reads
    ---------------------------------------------

    They were one read. ``marketplace_saved_products`` filled ``topics``,
    ``viewed_categories`` *and* ``followed_sellers``, which broke two separate
    things at once.

    It made the engine lie to the user. ``ranking.choose_reason`` is scrupulous
    about only claiming what fired, but it can only be as truthful as its inputs,
    and it was handed saved products in a parameter named ``viewed_categories``
    and again in one named ``followed_sellers``. So the card said "Because you
    viewed Rings" to someone who had never opened a ring, and "From sellers you
    follow" about a seller they had never followed. A reason code is a factual
    claim addressed to the person best placed to notice it is false.

    And it double-counted. ``predicted_interest`` takes
    ``max(topic_score, viewed_score * 1.1)`` — a deliberate choice to let the
    stronger purchase-intent signal dominate rather than average away. With both
    arguments derived from one table the comparison was between a value and
    itself, so the 1.1 made the viewed branch win unconditionally and the topic
    branch was unreachable. Two real sources make that ``max`` mean what it says.

    ``followed_sellers`` is read for the claim alone — it feeds no score term —
    so correcting its source cannot move a ranking. ``viewed_categories`` does
    feed a score, and now feeds it real view history.
    """
    profile: dict[str, Any] = {"topics": (), "viewed_categories": (), "followed_sellers": frozenset()}
    viewer = int(user_id or 0)

    # Saved products are an interest, not a view. This is the honest home for
    # them: `topics` is what the viewer has told us they care about.
    try:
        cur.execute(
            "SELECT DISTINCT l.category FROM marketplace_saved_products s "
            "JOIN marketplace_listings l ON l.id=s.listing_id "
            "WHERE s.user_id=? AND NULLIF(TRIM(COALESCE(l.category,'')),'') IS NOT NULL "
            "LIMIT 20",
            (viewer,),
        )
        profile["topics"] = tuple(
            str(row.get("category") or "") for row in _rows(cur) if row.get("category")
        )
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_SAVED_PRODUCTS_UNAVAILABLE", exc_info=True)

    # Actual views, from the engagement log. Keyed by `subject_ref` because that
    # is what the discovery tables hold — reaching for `user_id` here would mean
    # joining the pseudonymous event store back onto the account it exists to
    # keep separate from.
    #
    # `click` counts as a view: on every surface the card leads to the product
    # page, so a click is a viewer opening the product. `product_view` is the
    # explicit event, which the client does not emit on every path. Taking both
    # is the difference between a signal that works and one that is technically
    # purer and almost always empty.
    if subject_ref:
        try:
            cur.execute(
                "SELECT DISTINCT l.category FROM commerce_discovery_engagement_events e "
                "JOIN marketplace_listings l ON l.id=e.listing_id "
                "WHERE e.subject_ref=? AND e.action IN ('click','product_view') "
                "AND e.event_at>? "
                "AND NULLIF(TRIM(COALESCE(l.category,'')),'') IS NOT NULL "
                "LIMIT 20",
                (subject_ref, subject.window_start_iso(config.exposure_lookback_seconds())),
            )
            profile["viewed_categories"] = tuple(
                str(row.get("category") or "") for row in _rows(cur) if row.get("category")
            )
        except Exception:
            LOGGER.debug("COMMERCE_DISCOVERY_VIEW_HISTORY_UNAVAILABLE", exc_info=True)

    # Real follows. `pulse_follows` is the platform's own follow graph, so a
    # seller a viewer follows socially is a seller the shop may name.
    try:
        cur.execute(
            "SELECT DISTINCT followed_user_id FROM pulse_follows "
            "WHERE follower_user_id=? AND followed_user_id IS NOT NULL LIMIT 200",
            (viewer,),
        )
        profile["followed_sellers"] = frozenset(
            int(row["followed_user_id"]) for row in _rows(cur) if row.get("followed_user_id")
        )
    except Exception:
        # Fewer claims, never wrong ones: without this read no card says "from
        # sellers you follow", which is the correct answer when we cannot tell.
        LOGGER.debug("COMMERCE_DISCOVERY_FOLLOWS_UNAVAILABLE", exc_info=True)

    return profile


# --- selection --------------------------------------------------------------
def _select(
    scored: list[tuple[dict, dict]],
    budget: int,
    floor: float,
    branch: SurfacePolicy,
    preferred: Sequence[int] = (),
) -> list[tuple[dict, dict]]:
    """Greedy pick with live diversity re-scoring and a reserved explore slot.

    ``preferred`` is the creator's own ordering of the products they attached to
    this post (:mod:`tagging`), and it is taken **first, ahead of the floor and
    ahead of the caps**. That is three exemptions in one sentence, so each is
    argued separately below rather than left to be inferred — this is the only
    thing in the pipeline that outranks the scorer, and it should be hard to add
    a second one by accident.

    *Ahead of the score.* Every other row here was retrieved by an inference and
    ordered by a guess at what this viewer wants. A creator tag is a statement by
    the person who made the post about what the post is of. Ordering the statement
    by the guess's confidence in it gets the relationship between the two exactly
    backwards.

    *Ahead of the floor.* ``relevance_floor`` asks "is this a good answer for this
    person", and relevance to the content is precisely what the tag establishes by
    fiat. A tagged product that scored below the floor is not a bad answer; it is
    a product the scorer had no signal for — commonly a brand-new listing with no
    engagement history, which is the normal state of a product a creator is
    posting about for the first time.

    *Ahead of the caps.* The per-seller cap exists so one store cannot dominate a
    viewer's session. On a post whose creator tagged three of their own products
    that reasoning does not apply: every tagged row is from one seller *by
    construction*, because :func:`tagging.attach` refuses any other kind. Applying
    the cap here would silently truncate every creator's tags to two and look like
    a bug in the composer.

    What is *not* exempt, and deliberately: ``eligibility`` (the tag cannot show a
    delisted or unsafe product — it never reached the pool), ``promotion.assert_unpaid``,
    the surface budget, ``tagging.MAX_TAGGED_PER_CONTENT``, and the counts. Picks
    taken here feed ``seller_counts`` and the rest forward, so a two-slot surface
    filled by tags serves no inferred rows at all rather than serving tags *plus*
    a full quota of guesses.

    Greedy rather than optimal because the objective changes as items are
    chosen (diversity depends on the partial answer), and because the budget is
    one or two items — an exact solver over a forty-row pool to place two cards
    would be a great deal of machinery to reach the same two cards.

    The per-seller and per-category caps come from the surface's branch policy
    rather than from a module constant, which is the whole of what makes
    Messenger's strip refuse a second product from one store while the feed
    still allows a matching pair.

    The explore slot is taken from the *bottom* of the qualifying set, not from
    below the floor. Exploration means "surface something unproven", never
    "surface something bad": a listing that failed the relevance floor is not
    a discovery opportunity, it is a wrong answer.

    The floor is tested against the *pre-diversity* score and the ordering
    against the post-diversity one, and that split is the design rather than an
    accident of sequencing. The floor asks "is this a good answer for this
    person" — a relevance question, about the listing and the viewer. Diversity
    asks "does this belong in *this* response alongside what is already in it" —
    an exposure question, about composition. Letting a diversity penalty push a
    candidate below the relevance floor would let the second question veto on the
    first one's authority, and would mean the same listing was "irrelevant" or
    not depending on what happened to be picked before it.

    Why it is written this way now
    ------------------------------

    The previous implementation scored every candidate with ``diversity=1.0``,
    walked the statically-sorted list, and then — after the response was already
    decided — looped over the picks writing a computed ``diversity_bonus`` into
    each verdict's ``signals``. Three things followed, and all three were live:

    * ``score`` and ``contributions`` had already been computed from the constant
      in ``ranking.score_listing``, so the term contributed nothing to any
      ordering. Its weight of 0.07 was a constant added to every candidate.
    * The loop's comment said "then reorder". It did not reorder.
    * ``explain()`` reads ``contributions``, so the persisted
      ``score_breakdown_json`` disagreed with itself: ``signals`` recorded a term
      that had fired and ``contributions`` recorded one that had not.

    On the production catalogue this was the only diversity control capable of
    acting at all, because the cap arithmetic had quietly gone inert on both main
    surfaces: Feed's budget of 2 sits at its per-seller cap of 2 and below its
    per-category cap of 3, so neither could ever be reached, and Marketplace's
    per-category cap of 4 was counted on full leaf paths whose largest bucket held
    3. See ``router._SEGMENT_CAPS`` for the coarse cap that closes that half, and
    ``taxonomy`` for why leaf paths alone cannot see it.
    """
    # Split before the floor, not after: a preferred row is exempt from it, so
    # filtering first and rescuing afterwards would mean reconstructing the set
    # that was just discarded.
    wanted = [int(value or 0) for value in (preferred or ()) if int(value or 0) > 0]
    by_id = {int(pair[0]["id"]): pair for pair in scored}
    # In the creator's order, and only rows that survived retrieval — a tag whose
    # listing failed `eligibility` is simply not in `by_id`, which is the correct
    # outcome and needs no branch of its own.
    tagged_pairs = []
    for listing_id in wanted[:tagging.MAX_TAGGED_PER_CONTENT]:
        pair = by_id.get(listing_id)
        if pair is not None and pair not in tagged_pairs:
            tagged_pairs.append(pair)
    tagged_ids = {int(pair[0]["id"]) for pair in tagged_pairs}

    qualifying = [
        pair for pair in scored
        if pair[1]["score"] >= floor and int(pair[0]["id"]) not in tagged_ids
    ]
    if not qualifying and not tagged_pairs:
        return []

    qualifying.sort(key=lambda pair: pair[1]["score"], reverse=True)

    # Loosen the diversity caps to the diversity this catalogue actually holds.
    # On a many-seller catalogue this is a no-op; on a thin one it is the
    # difference between a surface that shows products and one that shows none.
    #
    # Measured over `scored` rather than `qualifying`: cooldowns are score
    # penalties, so the qualifying set is narrow exactly when the spacing rules
    # are working, and reading it would let a cooled-down field relax the cap
    # that the cooldown exists to hold.
    branch = router.fit_to_pool(
        branch,
        budget=budget,
        seller_ids=[row.get("seller_user_id") for row, _ in scored],
        # Folded keys, so a catalogue spelling one category two ways is not
        # counted as two kinds of diversity and used to relax the cap that its
        # duplicates would then fill.
        categories=[taxonomy.category_key(row.get("category")) for row, _ in scored],
        segments=[taxonomy.segment_root(row.get("category")) for row, _ in scored],
    )

    chosen: list[tuple[dict, dict]] = []
    #: The diversity keys of each placement already taken, in pick order. This is
    #: the "partial response" that `ranking.diversity_factor` measures against.
    taken: list[dict] = []
    seller_counts: dict[int, int] = {}
    category_counts: dict[str, int] = {}
    segment_counts: dict[str, int] = {}
    weights = config.weights()

    def keys_for(row: dict) -> dict:
        return {
            "seller_id": int(row.get("seller_user_id") or 0),
            "category_key": taxonomy.category_key(row.get("category")),
            "segment_root": taxonomy.segment_root(row.get("category")),
        }

    def admissible(keys: dict) -> bool:
        return router.admissible(
            branch,
            seller_id=keys["seller_id"],
            category=keys["category_key"],
            seller_counts=seller_counts,
            category_counts=category_counts,
            segment=keys["segment_root"],
            segment_counts=segment_counts,
        )

    def diversity_of(keys: dict) -> float:
        return ranking.diversity_factor(
            seller_id=keys["seller_id"],
            category_key=keys["category_key"],
            segment_root=keys["segment_root"],
            chosen=taken,
        )

    def take(pair: tuple[dict, dict], keys: dict, verdict: dict) -> None:
        """Commit one placement, with the verdict that justified choosing it.

        ``verdict`` is the diversity-adjusted one rather than ``pair[1]``, so the
        breakdown persisted against the placement is the arithmetic that actually
        selected it — including the case where this candidate won *because* the
        higher-scoring one ahead of it repeated something already taken.
        """
        seller_counts[keys["seller_id"]] = seller_counts.get(keys["seller_id"], 0) + 1
        if keys["category_key"]:
            category_counts[keys["category_key"]] = category_counts.get(keys["category_key"], 0) + 1
        if keys["segment_root"]:
            segment_counts[keys["segment_root"]] = segment_counts.get(keys["segment_root"], 0) + 1
        taken.append(keys)
        chosen.append((pair[0], verdict))

    def best_remaining(pool: list) -> Optional[tuple]:
        """The admissible candidate with the highest diversity-adjusted score.

        Re-measured against ``taken`` on every pass, which is what makes the
        diversity term an input to selection rather than an annotation on it.
        Cost is ``budget × len(pool)`` rescores — at most a few hundred
        multiply-adds over a dict, against a request that has already run several
        SQL queries.

        ``pool`` is sorted by base score descending and the comparison is strict
        ``>``, so a tie on the adjusted score resolves to the better base score
        and the outcome is deterministic. Two candidates identical on both are
        ordered by the pool's own order, which ``pool.sort`` has already made
        stable.
        """
        best: Optional[tuple] = None
        best_score = float("-inf")
        for pair in pool:
            keys = keys_for(pair[0])
            if not admissible(keys):
                continue
            adjusted = ranking.rescore_diversity(pair[1], diversity_of(keys), weights=weights)
            if adjusted["score"] > best_score:
                best = (pair, keys, adjusted)
                best_score = adjusted["score"]
        return best

    # The creator's picks, in the creator's order, before anything is guessed.
    # Rescored for diversity so the persisted breakdown is still the arithmetic of
    # the response they landed in — the value does not decide anything here, but a
    # breakdown that disagrees with the response is the defect `rescore_diversity`
    # was written to fix and it would be silly to reintroduce it one tier up.
    for pair in tagged_pairs:
        if len(chosen) >= budget:
            break
        keys = keys_for(pair[0])
        take(pair, keys, ranking.rescore_diversity(pair[1], diversity_of(keys), weights=weights))

    # Reserve at most one slot for exploration, and only when the budget can
    # actually spare it — a single-slot surface (Reels) gives its one slot to
    # the best answer, because a lone chip is the user's entire impression of
    # the feature.
    #
    # Not reserved once the creator's tags have already taken the budget: a slot
    # held back for exploration out of a budget that is already full would just
    # truncate the response by one.
    explore_slots = 1 if (budget >= 2 and config.exploration_rate() > 0) else 0
    if len(chosen) >= budget - explore_slots:
        explore_slots = 0

    remaining = list(qualifying)
    while len(chosen) < budget - explore_slots:
        pick = best_remaining(remaining)
        if pick is None:
            break
        pair, keys, verdict = pick
        take(pair, keys, verdict)
        remaining.remove(pair)

    if explore_slots and len(chosen) < budget:
        explorable = [
            pair for pair in remaining
            if pair[1]["signals"].get("exploration_bonus", 0.0) >= 0.7
            and admissible(keys_for(pair[0]))
        ]
        if explorable:
            # Chosen on exploration bonus alone, deliberately: the point of the
            # slot is to surface something unproven, and picking the *most*
            # unproven admissible candidate is the whole of that. Its verdict is
            # still rescored, so the breakdown records what the response cost in
            # diversity to spend a slot this way.
            pair = max(explorable, key=lambda pair: pair[1]["signals"]["exploration_bonus"])
            keys = keys_for(pair[0])
            take(pair, keys, ranking.rescore_diversity(pair[1], diversity_of(keys), weights=weights))
            remaining.remove(pair)
        else:
            # No unproven listing qualified: spend the slot on the next best
            # ordinary candidate rather than returning a shorter list.
            while len(chosen) < budget:
                pick = best_remaining(remaining)
                if pick is None:
                    break
                pair, keys, verdict = pick
                take(pair, keys, verdict)
                remaining.remove(pair)

    return chosen[:budget]


# --- placement persistence --------------------------------------------------
def _persist(
    cur,
    selected: list[tuple[dict, dict]],
    policy: ViewerPolicy,
    *,
    surface: str,
    session_id: str,
    klass: str,
    parse_price,
    serialize,
    relationships: Optional[Mapping[int, str]] = None,
) -> list[dict]:
    """Write the placement rows and build the client payloads.

    The placement row is written *before* the payload is returned so that the
    impression route has something authoritative to validate against. If the
    write fails the placement is dropped rather than served tokenless — a card
    whose impression can never be recorded is a card that would be re-served
    forever, because the frequency cap would never see it.
    """
    import json

    out: list[dict] = []
    ttl = config.placement_ttl_seconds()
    now = subject.now_iso()

    # One expiry for the whole response, computed once. Per-row expiries would
    # differ by microseconds and give the client nothing useful to reason about.
    expires_at = subject.expiry_iso(ttl)

    for slot, (row, verdict) in enumerate(selected):
        placement_id = subject.new_id("cd_pl")
        token = subject.make_token(placement_id)
        try:
            cur.execute(
                "INSERT INTO commerce_discovery_placements "
                "(placement_id, subject_ref, surface, slot, listing_id, seller_user_id, "
                " promotion_class, reason_code, relationship, score, score_breakdown_json, "
                " ranking_version, session_id, impression_token, expires_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    placement_id, policy.subject_ref, surface, slot,
                    int(row["id"]), int(row.get("seller_user_id") or 0),
                    klass, verdict["reason"],
                    # Next to `promotion_class` and `reason_code` deliberately:
                    # the three are one row's answers to three different questions
                    # (who funded it / what the buyer is told / how it got here),
                    # and reading them side by side is the audit §6 asks for.
                    #
                    # `assert_servable` rather than a bare read. Every value comes
                    # from `classify`, so this cannot fire today — which is the
                    # point of putting it on the write path rather than trusting
                    # the caller: the next writer of this column is a feature that
                    # does not exist yet (`creator_tagged`, `pulsedrop_curated`),
                    # and a fabricated provenance is worse than a missing one
                    # because it would be believed. It raises inside the `try`
                    # below, so the blast radius is one dropped placement.
                    relationship.assert_servable(
                        (relationships or {}).get(int(row["id"]))
                    ),
                    float(verdict["score"]),
                    json.dumps({
                        "signals": verdict["signals"],
                        "contributions": verdict["contributions"],
                    }, separators=(",", ":"), sort_keys=True),
                    verdict["ranking_version"], session_id or "",
                    token, expires_at, now,
                ),
            )
        except Exception:
            LOGGER.warning("COMMERCE_DISCOVERY_PLACEMENT_WRITE_FAILED listing=%s", row.get("id"), exc_info=True)
            continue

        out.append(_payload(
            row, verdict, placement_id, token,
            surface=surface, slot=slot, klass=klass,
            expires_at=expires_at,
            parse_price=parse_price, serialize=serialize,
        ))

    return out


#: Columns this pipeline puts on a pool row that must never reach a buyer's
#: device. See :func:`_payload` for why the downstream serializer cannot be
#: relied on to remove them.
#:
#: Ordered by how it got here rather than alphabetically, because that is the
#: question to ask of a new entry: did *we* add this column, or is it part of the
#: marketplace's own listing payload? Only the first kind belongs here.
PIPELINE_ONLY_FIELDS = frozenset({
    # `eligibility.py`'s SELECT list, for the ranker's seller-reliability signal.
    "seller_risk_score",
    # `pool.build` stamps this per row so the engine knows which retrieval
    # question produced it.
    "candidate_source",
    # Belt and braces. `_serve` deliberately keeps the relationship *off* the row
    # for exactly this reason, so this entry should be unreachable — it is here so
    # that a future writer who does reach for the row dict (the obvious thing to
    # do, and what `candidate_source` itself did) fails safe instead of publishing
    # the provenance of every card.
    "relationship",
})


def _buyer_safe(row: Mapping[str, Any]) -> dict:
    """``row`` without the columns in :data:`PIPELINE_ONLY_FIELDS`.

    A copy, not a mutation: the row is still being read after this — `_payload`
    itself reads ``cover_image_url`` and ``gallery_json`` from it through the
    price path, and `_select` may have more to do with it.
    """
    return {key: value for key, value in dict(row or {}).items()
            if key not in PIPELINE_ONLY_FIELDS}


def _payload(
    row: dict,
    verdict: dict,
    placement_id: str,
    token: str,
    *,
    surface: str,
    slot: int,
    klass: str,
    expires_at: str,
    parse_price,
    serialize,
) -> dict:
    """One placement, as the client receives it.

    The product half goes through ``pulse_marketplace_listing_payload`` rather
    than being hand-assembled. That serializer is what strips the
    reviewer-only columns (``safety_score``, ``moderation_*``), and this
    pipeline reads every one of them for ranking — hand-building the payload
    here is the one change that would leak them to a buyer's phone.

    That paragraph used to end there, and it was half true in the dangerous
    direction. ``pulse_marketplace_listing_payload`` is a **denylist**:
    ``{k: v for k, v in listing.items() if k not in
    MARKETPLACE_REVIEWER_ONLY_FIELDS}``. It removes the columns *it* knows about,
    and this package's pool row carries columns it has never heard of — so
    anything this pipeline invents ships to the device by default. Two were
    already shipping before this list existed, both confirmed against the real
    serializer rather than the fixture's ``serialize=None`` fallback (which is an
    allowlist, and therefore hid the whole problem):

    ``seller_risk_score``
        ``eligibility.py`` aliases ``ms.risk_score`` onto every row so the ranker
        can penalise unreliable sellers. It is an internal assessment of a named
        store, and ``ranking.EXPLAINABLE_FACTORS`` already refuses to publish the
        *reason* derived from it — publishing the raw number is strictly worse.

    ``candidate_source``
        Which retrieval question produced the row. Publishing it hands anyone a
        free readout of the retrieval strategy per card.

    :data:`PIPELINE_ONLY_FIELDS` is therefore a denylist of *our* additions,
    applied before the serializer's. It is deliberately not "fix the serializer's
    list instead": that list is shared by every marketplace endpoint, and
    widening it from inside this package would change payloads this mission never
    looked at. The columns below are ours, so the strip belongs here.

    ``score`` and the signal breakdown are **not** in the payload. The client
    needs the reason code (to render "Why am I seeing this?") and nothing else;
    shipping the score would publish a ranking oracle that anyone could probe
    by creating listings and reading back their own numbers.
    """
    product: dict
    if serialize is not None:
        try:
            product = dict(serialize(_buyer_safe(row)) or {})
        except Exception:
            LOGGER.warning("COMMERCE_DISCOVERY_SERIALIZE_FAILED listing=%s", row.get("id"), exc_info=True)
            product = {}
    else:
        product = {}

    if not product:
        # Minimal safe fallback. Every field here is one a buyer may see on the
        # public listing page anyway; nothing reviewer-only is reachable.
        product = {
            "id": int(row.get("id") or 0),
            "listing_id": int(row.get("id") or 0),
            "title": row.get("title") or "",
            "price_label": row.get("price_label") or "",
            "currency": row.get("currency") or "USD",
            "cover_image_url": row.get("cover_image_url") or "",
            "seller_user_id": int(row.get("seller_user_id") or 0),
            "seller_store_name": row.get("seller_store_name") or "",
            "category": row.get("category") or "",
        }

    price = eligibility.resolvable_price(row, parse_price) if parse_price else None

    return {
        "placement_id": placement_id,
        "impression_token": token,
        "surface": surface,
        "slot": slot,
        # The client needs this to stop reporting against a placement the server
        # will reject anyway. Without it a card left on screen past the TTL posts
        # impressions that fail, which reads in the logs as a client defect.
        "expires_at": expires_at,
        "promotion_class": klass,
        "label_key": promotion.label_key(klass),
        "reason": verdict["reason"],
        "ranking_version": verdict["ranking_version"],
        "product": product,
        "price_minor": price[0] if price else 0,
        "price_currency": price[1] if price else (row.get("currency") or "USD"),
    }
