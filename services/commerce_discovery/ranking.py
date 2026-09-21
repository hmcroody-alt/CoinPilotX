"""The score, and the reason the score can be read out loud.

``commerce_score`` is a weighted sum of twelve signals, each normalised to
``[0, 1]`` before weighting. Positive terms earn a slot; negative terms spend
one. Weights live in ``config.DEFAULT_WEIGHTS`` and are overridable as a whole
vector, never one leaked constant at a time.

Three properties are load-bearing, and each one cost something to get.

**Every signal is normalised before it is weighted.** Otherwise the weight
vector is uninterpretable: an operator reading ``freshness: 0.08`` would have no
way to know whether that is small or enormous without also knowing that
freshness happens to be measured in days-since-epoch. Normalising first makes
the weights the *only* place magnitude lives, which is the only reason
retuning by env var is a safe operation.

**Every score carries its breakdown.** :func:`score_listing` returns the
component contributions alongside the total, they are persisted on the placement
row, and "Why am I seeing this?" is rendered from them. A recommender that
cannot explain itself is one nobody can debug and no seller can trust — and
under the brief the explanation is a *user-facing feature*, not a diagnostic.

**A missing signal scores neutral, never zero.** This marketplace has almost no
engagement history: most listings have never been clicked, and scoring "never
clicked" as 0.0 conversion probability would rank them below a listing with one
click out of a thousand impressions. Absence of evidence is scored as the prior
(:data:`NEUTRAL`), and the exploration term — not the conversion term — is what
gets an unproven listing in front of someone.

Deliberately not in v1
----------------------

No learned model, no embedding similarity, no collaborative filtering. With
roughly two digits of engagement data in production, a learned model would fit
noise and would be impossible to explain to the first seller who asks why their
product stopped appearing. This is a hand-specified model on purpose, and
``config.RANKING_VERSION`` exists so that when it is replaced the old numbers
are not silently pooled with the new ones.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import config

#: The score a signal takes when nothing is known about it. Not 0.0 — see above.
NEUTRAL = 0.5

#: Reason codes. These are the vocabulary of "Why am I seeing this?" and of the
#: Marketplace module headings, so they are stable wire values with i18n keys on
#: the client, never prose.
REASON_MATCHES_INTERESTS = "matches_your_interests"
REASON_BECAUSE_YOU_VIEWED = "because_you_viewed"
REASON_CONTEXT = "related_to_this_post"
REASON_SELLER_FOLLOWED = "from_sellers_you_follow"
REASON_TRENDING = "trending"
REASON_NEW_ARRIVAL = "new_arrival"
REASON_POPULAR = "popular"
REASON_EXPLORE = "new_to_marketplace"

#: Order in which reasons are claimed when several apply. The most *specific*
#: true statement wins: "because you viewed X" is more informative than
#: "popular", and a vague reason on a well-targeted card reads as evasion.
REASON_PRIORITY = (
    REASON_BECAUSE_YOU_VIEWED,
    REASON_CONTEXT,
    REASON_SELLER_FOLLOWED,
    REASON_MATCHES_INTERESTS,
    REASON_NEW_ARRIVAL,
    REASON_TRENDING,
    REASON_EXPLORE,
    REASON_POPULAR,
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _tokens(*values: Any) -> set[str]:
    """Lowercased word set from arbitrary text/JSON-list fields.

    Tolerates the three shapes ``tags_json`` actually takes in this table:
    a JSON array, a comma-joined string, and NULL.
    """
    out: set[str] = set()
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple, set)):
            items: Iterable[Any] = value
        else:
            text = str(value).strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    items = parsed if isinstance(parsed, list) else [text]
                except Exception:
                    items = text.replace(",", " ").split()
            else:
                items = text.replace(",", " ").split()
        for item in items:
            word = str(item or "").strip().lower()
            # Two-character words are almost all stopwords and they match far
            # too eagerly across unrelated categories.
            if len(word) > 2:
                out.add(word)
    return out


def _overlap(left: set[str], right: set[str]) -> float:
    """Jaccard-ish overlap, biased toward the smaller set.

    Plain Jaccard punishes a listing with rich tags for matching a post with
    two: the union grows and the score falls even though the match got no worse.
    Dividing by the smaller set asks the question actually being asked — "how
    much of the narrower thing is covered" — which is what context matching
    wants on both the post side and the listing side.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / float(min(len(left), len(right)))


# --- individual signals -----------------------------------------------------
def relevance(listing: Mapping[str, Any], context: Optional[Mapping[str, Any]]) -> float:
    """Match against the thing the user is looking at right now.

    ``NEUTRAL`` with no context, which is the honest answer on Marketplace's own
    shelves and on the messenger strip: there is no surrounding content to be
    relevant *to*, so relevance should neither help nor hurt. Scoring 0.0 there
    would make every marketplace module fight a headwind the surface cannot
    remove.
    """
    if not context:
        return NEUTRAL
    listing_tokens = _tokens(
        listing.get("category"), listing.get("subcategory"),
        listing.get("title"), listing.get("tags_json"),
    )
    context_tokens = _tokens(
        context.get("category"), context.get("tags"),
        context.get("topic"), context.get("query"),
    )
    if not context_tokens:
        return NEUTRAL
    direct = _overlap(listing_tokens, context_tokens)
    # An exact category equality is a much stronger statement than token
    # overlap and deserves to saturate the term on its own.
    if _tokens(listing.get("category")) & _tokens(context.get("category")):
        direct = max(direct, 0.85)
    return _clamp(direct)


def quality(listing: Mapping[str, Any]) -> float:
    """How well-made the listing is, as a merchandising judgement.

    Everything here is observable from the row and none of it is engagement —
    that separation is what lets a brand-new listing score well on quality and
    so be worth exploring, rather than being indistinguishable from a bad one
    until it has accumulated a click history.
    """
    score = 0.0
    if str(listing.get("cover_image_url") or "").strip():
        score += 0.35
    gallery = listing.get("gallery_json")
    gallery_count = 0
    if gallery:
        try:
            parsed = json.loads(gallery) if isinstance(gallery, str) else gallery
            gallery_count = len(parsed) if isinstance(parsed, list) else 0
        except Exception:
            gallery_count = 0
    score += _clamp(gallery_count / 4.0) * 0.15
    if str(listing.get("video_url") or "").strip():
        score += 0.10
    description = str(listing.get("description") or listing.get("short_description") or "")
    score += _clamp(len(description) / 400.0) * 0.20
    title = str(listing.get("title") or "").strip()
    # Long enough to say what it is, short enough not to be keyword soup.
    if 8 <= len(title) <= 90:
        score += 0.10
    if str(listing.get("category") or "").strip():
        score += 0.10
    return _clamp(score)


def predicted_interest(
    listing: Mapping[str, Any],
    interest_topics: Sequence[str],
    viewed_categories: Sequence[str],
) -> float:
    """Match against the viewer's own durable profile, not this moment.

    ``NEUTRAL`` for a viewer we know nothing about — a new account should get
    recommendations ranked on quality and freshness, not be punished for having
    no history.
    """
    if not interest_topics and not viewed_categories:
        return NEUTRAL
    listing_tokens = _tokens(
        listing.get("category"), listing.get("subcategory"),
        listing.get("title"), listing.get("tags_json"),
    )
    topic_score = _overlap(listing_tokens, _tokens(*interest_topics))
    viewed_score = _overlap(_tokens(listing.get("category")), _tokens(*viewed_categories))
    # Viewing a category is a far stronger signal of purchase intent than
    # posting about a topic, so it dominates rather than averages.
    return _clamp(max(topic_score, viewed_score * 1.1))


def conversion_probability(stats: Optional[Mapping[str, Any]]) -> float:
    """Smoothed click-through, as a stand-in for purchase likelihood.

    Laplace-smoothed against a weak prior so a listing with one click from one
    impression does not outrank one with four hundred from a thousand. The prior
    strength (``PRIOR_N``) is what makes early data cheap to overrule and late
    data expensive to, which is the correct direction when the whole table has
    three figures of history.
    """
    if not stats:
        return NEUTRAL
    impressions = float(stats.get("impressions") or 0)
    clicks = float(stats.get("clicks") or 0)
    if impressions <= 0:
        return NEUTRAL
    PRIOR_N = 50.0
    PRIOR_RATE = 0.04
    smoothed = (clicks + PRIOR_N * PRIOR_RATE) / (impressions + PRIOR_N)
    # A 20% CTR is exceptional; normalising against it keeps the term from
    # sitting permanently at the bottom of its range.
    return _clamp(smoothed / 0.20)


def seller_reliability(listing: Mapping[str, Any]) -> float:
    verification = str(listing.get("seller_verification_status") or "unverified").lower()
    score = {"verified": 0.9, "business_verified": 1.0, "pending": 0.5}.get(verification, 0.45)
    fulfilled = listing.get("seller_fulfilled_orders")
    if fulfilled not in (None, ""):
        try:
            # log10 so the first ten orders matter a great deal and the
            # thousandth barely registers — reliability saturates.
            score = max(score, _clamp(math.log10(float(fulfilled) + 1.0) / 2.5))
        except (TypeError, ValueError):
            pass
    return _clamp(score)


def freshness(listing: Mapping[str, Any], parse_iso, now) -> float:
    """Half-life decay on the newer of created/updated, 30-day half-life.

    Exponential rather than linear because the interesting difference is
    between today and last week, not between last year and the year before —
    a linear ramp spends most of its range distinguishing things nobody cares
    about the ordering of.
    """
    newest = None
    for key in ("updated_at", "created_at"):
        parsed = parse_iso(listing.get(key))
        if parsed and (newest is None or parsed > newest):
            newest = parsed
    if newest is None:
        return NEUTRAL
    age_days = max(0.0, (now - newest).total_seconds() / 86400.0)
    return _clamp(0.5 ** (age_days / 30.0))


def exploration_bonus(stats: Optional[Mapping[str, Any]]) -> float:
    """Full marks for never having been shown, decaying to nothing by ~200.

    This is the anti-rich-get-richer term and the only reason a new seller's
    first listing is ever surfaced. It is not fairness for its own sake: a
    catalogue where nothing new can be discovered stops being a marketplace.
    """
    impressions = float((stats or {}).get("impressions") or 0)
    return _clamp(1.0 - (impressions / 200.0))


def repetition_penalty(recent_impressions: int) -> float:
    """How much this viewer has already seen *this listing*.

    Saturates at the configured cap, at which point the frequency filter in
    ``engine.py`` has removed the listing anyway — so the penalty's job is only
    to de-rank on the way there, not to be the enforcement.
    """
    cap = max(1, config.product_cap())
    return _clamp(float(recent_impressions) / float(cap))


def hide_penalty(hidden_strength: float) -> float:
    """Weight of the viewer's own negative signals against this listing/seller.

    A hard "hide this seller" is not represented here at all — it is a
    suppression and removes the candidate outright. This term carries the softer
    "see fewer like this", which should bias without banning.
    """
    return _clamp(hidden_strength)


def refund_risk(stats: Optional[Mapping[str, Any]]) -> float:
    if not stats:
        return 0.0
    orders = float(stats.get("orders") or 0)
    refunds = float(stats.get("refunds") or 0)
    if orders <= 0:
        return 0.0
    # A 25% refund rate saturates the term.
    return _clamp((refunds / orders) / 0.25)


def seller_risk(listing: Mapping[str, Any]) -> float:
    try:
        return _clamp(float(listing.get("seller_risk_score") or 0) / 100.0)
    except (TypeError, ValueError):
        return 0.0


# --- composition ------------------------------------------------------------
def score_listing(
    listing: Mapping[str, Any],
    *,
    context: Optional[Mapping[str, Any]] = None,
    interest_topics: Sequence[str] = (),
    viewed_categories: Sequence[str] = (),
    followed_sellers: frozenset = frozenset(),
    stats: Optional[Mapping[str, Any]] = None,
    recent_impressions: int = 0,
    hidden_strength: float = 0.0,
    diversity: float = 1.0,
    parse_iso=None,
    now=None,
    weights: Optional[Mapping[str, float]] = None,
) -> dict:
    """``{score, reason, signals, contributions}`` for one listing.

    ``score`` is normalised to ``[0, 1]`` by dividing by the sum of the positive
    weights, so the relevance floors in ``config.min_score`` mean the same thing
    before and after a retune. Without that division, raising one weight would
    silently move every threshold in the system.
    """
    resolved = dict(config.DEFAULT_WEIGHTS)
    resolved.update(weights or config.weights())

    signals = {
        "relevance": relevance(listing, context),
        "quality": quality(listing),
        "predicted_interest": predicted_interest(listing, interest_topics, viewed_categories),
        "conversion_probability": conversion_probability(stats),
        "seller_reliability": seller_reliability(listing),
        "freshness": freshness(listing, parse_iso, now) if parse_iso and now else NEUTRAL,
        "exploration_bonus": exploration_bonus(stats),
        "diversity_bonus": _clamp(diversity),
        "repetition_penalty": repetition_penalty(recent_impressions),
        "hide_penalty": hide_penalty(hidden_strength),
        "refund_risk": refund_risk(stats),
        "seller_risk": seller_risk(listing),
    }

    contributions = {key: resolved.get(key, 0.0) * value for key, value in signals.items()}
    positive_mass = sum(w for w in resolved.values() if w > 0) or 1.0
    total = sum(contributions.values()) / positive_mass

    return {
        "score": _clamp(total),
        "reason": choose_reason(
            listing, signals,
            followed_sellers=followed_sellers,
            stats=stats,
            has_context=bool(context),
            viewed_categories=viewed_categories,
        ),
        "signals": {k: round(v, 4) for k, v in signals.items()},
        "contributions": {k: round(v, 4) for k, v in contributions.items()},
        "ranking_version": config.RANKING_VERSION,
    }


def choose_reason(
    listing: Mapping[str, Any],
    signals: Mapping[str, float],
    *,
    followed_sellers: frozenset = frozenset(),
    stats: Optional[Mapping[str, Any]] = None,
    has_context: bool = False,
    viewed_categories: Sequence[str] = (),
) -> str:
    """The most specific *true* explanation, never the most flattering one.

    Each candidate reason is admitted only if the signal that would justify it
    actually fired. This is the difference between an explanation and a caption:
    a card labelled "Because you viewed Shoes" for someone who never viewed
    shoes is worse than no label, because it is a claim about the user's own
    history that they can check.
    """
    claims: set[str] = set()

    if viewed_categories and _tokens(listing.get("category")) & _tokens(*viewed_categories):
        claims.add(REASON_BECAUSE_YOU_VIEWED)
    if has_context and signals.get("relevance", 0.0) >= 0.6:
        claims.add(REASON_CONTEXT)
    try:
        if int(listing.get("seller_user_id") or 0) in followed_sellers:
            claims.add(REASON_SELLER_FOLLOWED)
    except (TypeError, ValueError):
        pass
    if signals.get("predicted_interest", 0.0) >= 0.6:
        claims.add(REASON_MATCHES_INTERESTS)
    if signals.get("freshness", 0.0) >= 0.8:
        claims.add(REASON_NEW_ARRIVAL)
    if stats and float(stats.get("clicks") or 0) >= 10:
        claims.add(REASON_TRENDING)
    if signals.get("exploration_bonus", 0.0) >= 0.9:
        claims.add(REASON_EXPLORE)

    for reason in REASON_PRIORITY:
        if reason in claims:
            return reason
    return REASON_POPULAR
