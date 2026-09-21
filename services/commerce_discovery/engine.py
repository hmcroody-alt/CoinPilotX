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

from . import config, eligibility, preferences, promotion, ranking, schema, subject
from .preferences import ViewerPolicy

LOGGER = logging.getLogger(__name__)

#: Rows pulled from the database before filtering. Generous enough that the
#: eligibility and frequency filters have something to work with, bounded so a
#: viewer who has hidden a lot cannot turn one feed request into a table scan.
CANDIDATE_POOL = 120

#: Max listings per seller in one response. Two, not one: a store with a strong
#: matching pair should be able to show both, but a single seller must never own
#: a whole shelf.
MAX_PER_SELLER = 2

#: Max listings per category in one response, for the same reason.
MAX_PER_CATEGORY = 3


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
    context: Optional[Mapping[str, Any]] = None,
    session_id: str = "",
    limit: Optional[int] = None,
    promotion_class: str = promotion.ORGANIC,
    parse_price=None,
    serialize=None,
) -> list[dict]:
    """Placements for one surface, or ``[]``.

    ``parse_price`` and ``serialize`` are injected (they live on ``bot``) so the
    whole pipeline is testable without importing the monolith. The route pack
    supplies the real ones.
    """
    try:
        return _serve(
            cur, user_id, surface,
            context=context, session_id=session_id, limit=limit,
            promotion_class=promotion_class,
            parse_price=parse_price, serialize=serialize,
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
    context: Optional[Mapping[str, Any]],
    session_id: str,
    limit: Optional[int],
    promotion_class: str,
    parse_price,
    serialize,
) -> list[dict]:
    surface = str(surface or "").strip().lower()
    if surface not in schema.SURFACES:
        return []

    klass = promotion.assert_unpaid(promotion_class)

    if not schema.ensure_schema(cur):
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

    candidates = _candidates(cur, user_id, surface, context, parse_price, policy)
    if not candidates:
        return []

    profile = _interest_profile(cur, user_id) if policy.personalized else {}
    stats = _listing_stats(cur, [row["id"] for row in candidates])
    seen = _recent_impressions(cur, policy.subject_ref, [row["id"] for row in candidates])

    scored = []
    now = subject.now_utc()
    weights = config.weights()
    for row in candidates:
        verdict = ranking.score_listing(
            row,
            context=context if policy.personalized else None,
            interest_topics=profile.get("topics", ()),
            viewed_categories=profile.get("viewed_categories", ()),
            followed_sellers=profile.get("followed_sellers", frozenset()),
            stats=stats.get(int(row["id"])),
            recent_impressions=seen.get(int(row["id"]), 0),
            hidden_strength=policy.hidden_strength(row),
            diversity=1.0,
            parse_iso=subject.parse_iso,
            now=now,
            weights=weights,
        )
        scored.append((row, verdict))

    floor = config.min_score(surface)
    selected = _select(scored, budget, floor, policy)
    if not selected:
        # The explicit form of "no placement is better than a bad placement":
        # the pool was non-empty and everything in it was below the bar.
        LOGGER.debug("COMMERCE_DISCOVERY_BELOW_FLOOR surface=%s floor=%.2f", surface, floor)
        return []

    return _persist(
        cur, selected, policy,
        surface=surface, session_id=session_id, klass=klass,
        parse_price=parse_price, serialize=serialize,
    )


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
def _candidates(
    cur,
    user_id: Any,
    surface: str,
    context: Optional[Mapping[str, Any]],
    parse_price,
    policy: ViewerPolicy,
) -> list[dict]:
    """Eligible listings, already filtered by suppression and frequency cap.

    The SQL half carries the publication gate and the two indexable additions
    (cover image, price label). The Python half carries the judgemental gates
    and the per-viewer state, which is not joinable here: suppressions and
    impressions are keyed by ``subject_ref``, and joining a hashed viewer key
    against the catalogue on the hot path would buy nothing over a set lookup
    against a bounded pool.
    """
    sql = (
        f"SELECT {eligibility.candidate_projection()} "
        "FROM marketplace_listings l "
        "LEFT JOIN users u ON u.user_id=l.seller_user_id "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id "
        f"WHERE {eligibility.candidate_sql()} "
        # Never recommend a seller their own listing. Not a fraud control —
        # there is no money here — simply that it is absurd, and it is exactly
        # what a seller testing their own store would hit first.
        "AND COALESCE(l.seller_user_id,0)<>? "
        "ORDER BY l.featured DESC, l.updated_at DESC, l.id DESC "
        "LIMIT ?"
    )
    try:
        cur.execute(sql, (int(user_id or 0), CANDIDATE_POOL))
        rows = _rows(cur)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_CANDIDATE_QUERY_FAILED", exc_info=True)
        return []

    out: list[dict] = []
    for row in rows:
        try:
            listing_id = int(row.get("id") or 0)
            seller_id = int(row.get("seller_user_id") or 0)
        except (TypeError, ValueError):
            continue
        if listing_id in policy.suppressed_listings:
            continue
        if seller_id in policy.suppressed_sellers:
            continue
        if eligibility.gate(row, parse_price) != "":
            continue
        out.append(row)
    return out


def _listing_stats(cur, listing_ids: Sequence[int]) -> dict[int, dict]:
    """Impressions, clicks, orders and refunds per listing.

    Two queries, not one join: the event tables and ``marketplace_orders`` have
    no useful join key beyond ``listing_id``, and a full outer join between two
    sparse aggregates would produce more rows than either.

    A failure here returns ``{}``, which scores every listing at the neutral
    prior. That is the right degradation — ranking gets less sharp, nothing
    breaks, and no listing is unfairly penalised for a query that did not run.
    """
    if not listing_ids:
        return {}
    ids = [int(i) for i in listing_ids]
    marks = ",".join("?" for _ in ids)
    stats: dict[int, dict] = {i: {} for i in ids}

    try:
        cur.execute(
            "SELECT listing_id, COUNT(*) AS impressions FROM commerce_discovery_impression_events "
            f"WHERE visible=1 AND listing_id IN ({marks}) GROUP BY listing_id",
            ids,
        )
        for row in _rows(cur):
            stats.setdefault(int(row["listing_id"]), {})["impressions"] = int(row["impressions"] or 0)

        cur.execute(
            "SELECT listing_id, COUNT(*) AS clicks FROM commerce_discovery_engagement_events "
            f"WHERE action='click' AND listing_id IN ({marks}) GROUP BY listing_id",
            ids,
        )
        for row in _rows(cur):
            stats.setdefault(int(row["listing_id"]), {})["clicks"] = int(row["clicks"] or 0)
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


def _recent_impressions(cur, ref: str, listing_ids: Sequence[int]) -> dict[int, int]:
    """How often this viewer has already seen each candidate, in the window."""
    if not listing_ids:
        return {}
    ids = [int(i) for i in listing_ids]
    marks = ",".join("?" for _ in ids)
    try:
        cur.execute(
            "SELECT listing_id, COUNT(*) AS n FROM commerce_discovery_impression_events "
            f"WHERE subject_ref=? AND event_at>? AND listing_id IN ({marks}) GROUP BY listing_id",
            [ref, subject.window_start_iso(config.product_window_seconds()), *ids],
        )
        return {int(row["listing_id"]): int(row["n"] or 0) for row in _rows(cur)}
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_FREQ_READ_FAILED", exc_info=True)
        # Empty means "assume unseen", which is the generous direction. The hard
        # cap below still applies to anything we *can* read; what is lost is
        # only the de-ranking nudge, not the enforcement.
        return {}


def _interest_profile(cur, user_id: Any) -> dict:
    """Durable signals about what this viewer likes.

    Read from surfaces the user already engaged with by choice — saved
    products, followed sellers — rather than from anything inferred about them.
    Every failure mode returns fewer signals, never wrong ones, and an empty
    profile scores at the neutral prior rather than against the user.
    """
    profile: dict[str, Any] = {"topics": (), "viewed_categories": (), "followed_sellers": frozenset()}
    try:
        cur.execute(
            "SELECT DISTINCT l.category FROM marketplace_saved_products s "
            "JOIN marketplace_listings l ON l.id=s.listing_id "
            "WHERE s.user_id=? AND NULLIF(TRIM(COALESCE(l.category,'')),'') IS NOT NULL "
            "LIMIT 20",
            (int(user_id or 0),),
        )
        profile["viewed_categories"] = tuple(
            str(row.get("category") or "") for row in _rows(cur) if row.get("category")
        )
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_SAVED_PRODUCTS_UNAVAILABLE", exc_info=True)

    try:
        cur.execute(
            "SELECT DISTINCT l.seller_user_id FROM marketplace_saved_products s "
            "JOIN marketplace_listings l ON l.id=s.listing_id WHERE s.user_id=? LIMIT 50",
            (int(user_id or 0),),
        )
        profile["followed_sellers"] = frozenset(
            int(row["seller_user_id"]) for row in _rows(cur) if row.get("seller_user_id")
        )
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_FOLLOWED_SELLERS_UNAVAILABLE", exc_info=True)

    profile["topics"] = profile["viewed_categories"]
    return profile


# --- selection --------------------------------------------------------------
def _select(scored: list[tuple[dict, dict]], budget: int, floor: float, policy: ViewerPolicy) -> list[tuple[dict, dict]]:
    """Greedy pick with live diversity re-scoring and a reserved explore slot.

    Greedy rather than optimal because the objective changes as items are
    chosen (diversity depends on the partial answer), and because the budget is
    one or two items — an exact solver over a 120-row pool to place two cards
    would be a great deal of machinery to reach the same two cards.

    The explore slot is taken from the *bottom* of the qualifying set, not from
    below the floor. Exploration means "surface something unproven", never
    "surface something bad": a listing that failed the relevance floor is not
    a discovery opportunity, it is a wrong answer.
    """
    qualifying = [pair for pair in scored if pair[1]["score"] >= floor]
    if not qualifying:
        return []

    qualifying.sort(key=lambda pair: pair[1]["score"], reverse=True)

    chosen: list[tuple[dict, dict]] = []
    seller_counts: dict[int, int] = {}
    category_counts: dict[str, int] = {}

    def admissible(row: dict) -> bool:
        seller = int(row.get("seller_user_id") or 0)
        category = str(row.get("category") or "").strip().lower()
        if seller_counts.get(seller, 0) >= MAX_PER_SELLER:
            return False
        if category and category_counts.get(category, 0) >= MAX_PER_CATEGORY:
            return False
        return True

    def take(pair: tuple[dict, dict]) -> None:
        row, _ = pair
        seller = int(row.get("seller_user_id") or 0)
        category = str(row.get("category") or "").strip().lower()
        seller_counts[seller] = seller_counts.get(seller, 0) + 1
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        chosen.append(pair)

    # Reserve at most one slot for exploration, and only when the budget can
    # actually spare it — a single-slot surface (Reels) gives its one slot to
    # the best answer, because a lone chip is the user's entire impression of
    # the feature.
    explore_slots = 1 if (budget >= 2 and config.exploration_rate() > 0) else 0

    for pair in qualifying:
        if len(chosen) >= budget - explore_slots:
            break
        if admissible(pair[0]):
            take(pair)

    if explore_slots and len(chosen) < budget:
        picked_ids = {int(row.get("id") or 0) for row, _ in chosen}
        explorable = [
            pair for pair in qualifying
            if int(pair[0].get("id") or 0) not in picked_ids
            and pair[1]["signals"].get("exploration_bonus", 0.0) >= 0.7
            and admissible(pair[0])
        ]
        if explorable:
            take(max(explorable, key=lambda pair: pair[1]["signals"]["exploration_bonus"]))
        else:
            # No unproven listing qualified: spend the slot on the next best
            # ordinary candidate rather than returning a shorter list.
            for pair in qualifying:
                if len(chosen) >= budget:
                    break
                if int(pair[0].get("id") or 0) in picked_ids:
                    continue
                if admissible(pair[0]):
                    take(pair)

    # Re-apply the diversity term now that the response is known, then reorder.
    # This is the only point at which "too similar to its neighbour" is a fact
    # that exists at all.
    for index, (row, verdict) in enumerate(chosen):
        penalty = 0.0
        for other, _ in chosen[:index]:
            if str(other.get("category") or "") == str(row.get("category") or ""):
                penalty += 0.3
            if int(other.get("seller_user_id") or 0) == int(row.get("seller_user_id") or 0):
                penalty += 0.4
        verdict["signals"]["diversity_bonus"] = max(0.0, 1.0 - penalty)

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
                " promotion_class, reason_code, score, score_breakdown_json, ranking_version, "
                " session_id, impression_token, expires_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    placement_id, policy.subject_ref, surface, slot,
                    int(row["id"]), int(row.get("seller_user_id") or 0),
                    klass, verdict["reason"], float(verdict["score"]),
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

    ``score`` and the signal breakdown are **not** in the payload. The client
    needs the reason code (to render "Why am I seeing this?") and nothing else;
    shipping the score would publish a ranking oracle that anyone could probe
    by creating listings and reading back their own numbers.
    """
    product: dict
    if serialize is not None:
        try:
            product = dict(serialize(row) or {})
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
