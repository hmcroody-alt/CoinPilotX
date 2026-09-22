"""The score, and the reason the score can be read out loud.

``commerce_score`` is a weighted sum of seventeen signals, each normalised to
``[0, 1]`` before weighting. Positive terms earn a slot; negative terms spend
one. Weights live in ``config.DEFAULT_WEIGHTS`` and are overridable as a whole
vector, never one leaked constant at a time.

Five of the seventeen have nothing to do with how good a product is. ``novelty``,
the three repetition penalties and ``owned_penalty`` all describe the *sequence*
the viewer is being shown rather than the item — and they exist because a model
built only from item quality is deterministic, and a deterministic ranker over a
stable catalogue returns the same winners every single request. That is not a
tuning problem that better relevance would fix. It is what "keeps showing me the
same products" actually is, and the only cure is for the score to know what came
before it.

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


#: The only score terms that may be named to a user.
#:
#: "Why am I seeing this?" answers with the terms that *lifted* this product, and
#: the eight below are exactly the terms carrying a client i18n key
#: (``commerce:discovery.factor.<name>``). The four penalties are absent for two
#: independent reasons, either sufficient on its own:
#:
#: * ``refund_risk`` and ``seller_risk`` are judgements about a seller. Naming
#:   them in a shopper-facing sheet publishes an internal risk assessment of a
#:   named store to that store's potential customers.
#: * none of the four has a translation, so rendering one puts a raw key string
#:   on screen in every locale.
#:
#: Selecting by ``contribution > 0`` is *not* the same guard. Weights are
#: operator-overridable through ``COMMERCE_DISCOVERY_WEIGHTS``, so a single sign
#: typo in a JSON env var turns a penalty positive and walks it into the sheet.
#: This allowlist is the vocabulary; the sign test only ranks within it.
EXPLAINABLE_FACTORS = frozenset(
    {
        "relevance",
        "quality",
        "predicted_interest",
        "conversion_probability",
        "seller_reliability",
        "freshness",
        "exploration_bonus",
        "diversity_bonus",
    }
)
#: ``novelty`` is deliberately **not** in that set, and the reason is not i18n.
#: It is a statement about the *sequence* rather than about the product: "you
#: have not seen this recently" is not a reason anyone would want something. Put
#: in front of a shopper it would also sit beside ``freshness`` ("new arrival")
#: and ``exploration_bonus`` ("new to marketplace") as a third, differently-
#: meaning "new", which is how an explanation feature starts reading as noise.
#: The four repetition and ownership penalties are excluded for the reason the
#: original four were: naming them publishes an internal assessment of a named
#: store, or of what this person has already bought.


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


def novelty(seconds_since_seen: float, cooldown_seconds: int) -> float:
    """How fresh this product is *to this person*, as distinct from how new it is.

    ``freshness`` measures the listing's age; this measures the gap since the
    viewer last saw it. The two are independent — a listing published a year ago
    that this person has never encountered is maximally novel and minimally
    fresh — and collapsing them would mean a catalogue that stopped growing
    could never produce a novel recommendation again.

    Measured in time rather than in count, which is what separates it from
    :func:`repetition_penalty`. The count answers "how much of this person's
    weekly allowance has this product used"; this answers "has it had time to
    stop being the thing they just scrolled past". A product can be well under
    its cap and still be the wrong thing to show twice in twenty seconds.

    Ramps linearly to 1.0 at the cooldown boundary. Never-seen returns 1.0 —
    that is what ``exposure.NEVER`` is for, not a special case here.
    """
    gap = max(0.0, float(seconds_since_seen))
    window = max(1, int(cooldown_seconds))
    return _clamp(gap / float(window))


def seller_repetition_penalty(
    recent_seller_impressions: int,
    *,
    recent_impressions: int = 0,
    distinct_sellers: int = 0,
) -> float:
    """How over-represented this seller is in what the viewer has recently seen.

    The per-response seller cap cannot see this. A scrolling feed is many
    requests, and a seller with fifty eligible listings satisfies a
    two-per-response cap twenty-five times running without ever breaking it —
    which is exactly how inventory size turns into placement share. This term is
    the only thing in the model that looks across requests at a seller.

    Measured as a *share* rather than as a count against a cap, because a count
    saturates and a saturated term is an inert one. Six impressions used to mean
    full penalty; past that, a seller with sixty placements and a seller with
    seven scored identically here, so the only surviving discriminator between
    them was how much inventory each had — the precise failure this term exists
    to prevent, arriving a few seconds into any real session.

    The reference point is an even split across the sellers this viewer has
    actually been shown, not across the catalogue, because the catalogue's seller
    count is not knowable from a scored row and would be the wrong number anyway:
    a viewer whose eligible inventory comes from three stores is not being
    treated badly when each supplies a third.

    Below ``seller_cap`` impressions there is no share worth computing — one
    placement out of one is a 100% share and means nothing — so the original
    count ramp still governs the opening of a session.
    """
    cap = max(1, config.seller_cap())
    count = max(0, int(recent_seller_impressions))
    total = max(0, int(recent_impressions))
    if total < cap:
        return _clamp(float(count) / float(cap))

    fair = 1.0 / float(max(2, int(distinct_sellers)))
    share = float(count) / float(total)
    return _clamp((share - fair) / (1.0 - fair))


def category_repetition_penalty(recent_category_impressions: int) -> float:
    """How concentrated this viewer's recent commerce has been in one category.

    Deliberately weaker than the seller term. Category concentration is often
    *correct* — someone shopping for a coat should be shown coats — so this
    nudges against a shirt/shirt/shirt/shirt run without overriding a genuine
    interest signal, which ``relevance`` and ``predicted_interest`` both carry at
    higher weight.
    """
    cap = max(1, config.category_cap())
    return _clamp(float(recent_category_impressions) / float(cap))


def cross_surface_penalty(seconds_since_other_surface: float, cooldown_seconds: int) -> float:
    """Whether this product just appeared somewhere *else* in the app.

    The failure this prevents has a specific feel to it: the same pair of shoes
    in the feed, then over a reel, then above the chat list, inside a minute.
    Each placement is individually defensible and the sequence reads as a broken
    system — the user concludes the app is following them rather than helping
    them.

    Steep rather than binary. A hard block would make the product unavailable
    even when it is the only thing left in the pool, and the brief forbids the
    blank more firmly than it forbids the repeat; a steep ramp means it loses to
    anything else that qualifies and wins only against nothing.
    """
    window = max(1, int(cooldown_seconds))
    gap = max(0.0, float(seconds_since_other_surface))
    if gap >= window:
        return 0.0
    return _clamp(1.0 - (gap / float(window)))


def owned_penalty(*, purchased: bool = False, in_cart: bool = False, saved: bool = False) -> float:
    """How much of this product the viewer already has.

    Three states, three strengths, because they mean three different things:

    * **purchased** — they own it. Recommending it again is the most obviously
      broken output this engine can produce. Full penalty, though in practice
      ``pool`` has already removed it; this is the backstop for the request
      where the purchase read degraded.
    * **in cart** — they are mid-decision. Re-advertising is not broken, but it
      spends a discovery slot telling someone something they already know, and
      the brief is explicit that discovery must not interfere with checkout.
    * **saved** — softest. A saved product does not need discovery to be found
      again; the user has a list. It should still be able to resurface on a
      genuinely strong match, so this is a nudge rather than a bar.
    """
    if purchased:
        return 1.0
    if in_cart:
        return 0.8
    if saved:
        return 0.35
    return 0.0


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
    recent_seller_impressions: int = 0,
    recent_category_impressions: int = 0,
    #: Denominators for the seller share. Defaulted rather than required so a
    #: caller that knows nothing about the viewer's wider history still gets the
    #: opening-of-session count ramp rather than a division by zero.
    recent_total_impressions: int = 0,
    recent_distinct_sellers: int = 0,
    seconds_since_seen: float = 1e9,
    seconds_since_other_surface: float = 1e9,
    product_cooldown_seconds: Optional[int] = None,
    cross_surface_cooldown_seconds: Optional[int] = None,
    purchased: bool = False,
    in_cart: bool = False,
    saved: bool = False,
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

    product_gap = config.product_cooldown_seconds() if product_cooldown_seconds is None else product_cooldown_seconds
    cross_gap = (
        config.cross_surface_cooldown_seconds()
        if cross_surface_cooldown_seconds is None
        else cross_surface_cooldown_seconds
    )

    signals = {
        "relevance": relevance(listing, context),
        "quality": quality(listing),
        "predicted_interest": predicted_interest(listing, interest_topics, viewed_categories),
        "conversion_probability": conversion_probability(stats),
        "seller_reliability": seller_reliability(listing),
        "freshness": freshness(listing, parse_iso, now) if parse_iso and now else NEUTRAL,
        "exploration_bonus": exploration_bonus(stats),
        "diversity_bonus": _clamp(diversity),
        "novelty": novelty(seconds_since_seen, product_gap),
        "repetition_penalty": repetition_penalty(recent_impressions),
        "seller_repetition_penalty": seller_repetition_penalty(
            recent_seller_impressions,
            recent_impressions=recent_total_impressions,
            distinct_sellers=recent_distinct_sellers,
        ),
        "category_repetition_penalty": category_repetition_penalty(recent_category_impressions),
        "cross_surface_penalty": cross_surface_penalty(seconds_since_other_surface, cross_gap),
        "owned_penalty": owned_penalty(purchased=purchased, in_cart=in_cart, saved=saved),
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
