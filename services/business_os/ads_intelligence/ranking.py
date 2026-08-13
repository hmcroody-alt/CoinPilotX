"""Explainable ranker v1 — a SelectionStrategy, not a second delivery engine.

The canonical delivery path already has the right shape. ``eligibility.evaluate``
applies every gate and ``selection.select_candidate`` then picks a winner through
a replaceable ``SelectionStrategy`` interface. Eligibility-before-ranking is
therefore already true, and the correct place for a better ranker is *inside*
that seam rather than beside it. This module implements the existing interface
and is injected; it owns no candidate enumeration, no gates, no delivery record
and no billing.

Today's default strategy is a stable hash rotation: deterministic and fair, but
blind. It cannot tell a creative people love from one they are reporting. This
ranker adds sight while keeping the properties that made rotation safe.

There is no bid, and none is invented
--------------------------------------
``pricing.py`` is a platform-published rate card: the platform sets cpm/cpc
within a guard band and advertisers accept it. Nobody bids. So a "value" or
"expected revenue" component would not be *bounding* pay-to-win, it would be
introducing it — building the mechanism whose absence is the reason the
question does not arise. Money is absent from this file entirely: no price, no
budget, no spend, no advertiser tier. A campaign cannot buy rank here because
there is nothing to buy it with.

Every score is a sum of named, bounded parts
---------------------------------------------
Each component is normalised to 0..1 and multiplied by a declared weight, and
the breakdown travels with the result. "This creative won" has to decompose
into "because context matched exactly, affinity was moderate, and quality was
unknown" — both because an advertiser will ask and because an unexplainable
ranker cannot be debugged when it starts behaving oddly.

Fatigue multiplies, it does not add
------------------------------------
An additive fatigue penalty can be outweighed by a strong score elsewhere. A
multiplier cannot. And a REJECTED creative — one whose negative feedback rate
is at or above threshold — is *removed* from the ranked set rather than scored
zero, because a zero-scored candidate still wins an auction with one entrant.
If that empties the set the request takes a no-fill: showing nothing is better
than showing something people are actively reporting.

It can never fail an ad request
--------------------------------
Every entry point is wrapped, and any failure falls back to the canonical
rotation strategy. A ranking layer that can 500 an ad request is worse than no
ranking layer. ``compare`` exists for shadow mode: it ranks without deciding,
so the ranker can be evaluated against live traffic while the legacy strategy
keeps serving.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from . import context as _context
from . import interest as _interest
from . import performance as _performance
from . import taxonomy

_LOG = logging.getLogger(__name__)

RANKING_MODE = "intelligence_v1"

#: Component weights, summing to 1.0. Declared here rather than inline so that
#: retuning the ranker is a reviewable diff against a named table instead of an
#: archaeology exercise across a scoring function.
#:
#: The exploration weight is not free. An unmeasured creative already collects
#: NEUTRAL quality, so exploration stacks on top of it, and if the two together
#: exceed what a perfect measured creative earns then the newest creative wins
#: forever and the ranker permanently prefers churn over quality. The bound is
#: therefore:
#:
#:     exploration <= quality * (1 - NEUTRAL)
#:
#: which keeps exploration able to beat a merely average performer — that is
#: what exploration is for — while never beating an excellent one. There is a
#: test asserting this relationship so a future retune cannot quietly break it.
WEIGHTS = {
    "context": 0.30,
    "affinity": 0.30,
    "quality": 0.30,
    "exploration": 0.10,
}

#: Fatigue is applied as a multiplier over the whole score. REJECTED is absent
#: on purpose — those candidates are dropped before scoring, not scaled to zero.
FATIGUE_MULTIPLIERS = {
    "HEALTHY": 1.0,
    "INSUFFICIENT_DATA": 1.0,
    "WEARING": 0.6,
    "FATIGUED": 0.25,
}

#: Neutral value for a component we genuinely cannot measure. Deliberately mid
#: rather than zero: an unmeasured creative that scores zero can never win, so
#: it never accumulates the data that would let it be measured. That is how a
#: ranker quietly freezes its own leaderboard.
NEUTRAL = 0.5

#: Affinity is clamped into 0..1 from the taxonomy's signed range. Only the
#: positive half maps upward; a suppressed category floors at zero rather than
#: going negative, because a negative component would let one disliked category
#: drag down a creative's unrelated strengths.
_AFFINITY_CEILING = 25.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _rotation_key(subject_ref: Any, creative_id: Any) -> str:
    """The legacy rotation hash, reused as a stable tiebreak.

    Keeping it means two creatives that genuinely score the same still spread
    across viewers instead of one always winning by identifier order.
    """
    raw = f"{subject_ref}:{creative_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #

def context_component(campaign_category: Any, described: dict) -> dict:
    """How well this campaign suits the surrounding content."""
    match = _context.match_score(campaign_category, described)
    return {"value": _clamp(match.get("score") or 0.0),
            "reason": match.get("reason"), "detail": match.get("match")}


def affinity_component(affinities: dict, campaign_category: Any) -> dict:
    """The viewer's own decayed affinity for this campaign's category.

    Neutral when the category is unknown or the viewer has no history, so a new
    viewer is not systematically shown worse ads than an established one.
    """
    category = _context.normalise_content_category(campaign_category)
    if not category:
        return {"value": NEUTRAL, "reason": "the campaign declares no category",
                "detail": "UNKNOWN_CATEGORY"}
    if not affinities:
        return {"value": NEUTRAL, "reason": "no interest history for this viewer",
                "detail": "NO_HISTORY"}
    raw = float(affinities.get(category) or 0.0)
    if raw <= 0:
        return {"value": 0.0,
                "reason": f"the viewer has shown no interest in {category}",
                "detail": "SUPPRESSED" if raw < 0 else "NO_SIGNAL"}
    return {"value": _clamp(raw / _AFFINITY_CEILING),
            "reason": f"the viewer has an affinity of {raw:.1f} for {category}",
            "detail": "AFFINITY"}


def quality_component(summary: Optional[dict]) -> dict:
    """Observed click quality, but only where the sample supports a claim.

    Uses ``ctr_on_viewable`` — clicks over impressions that could actually be
    seen. Below the sample floor this returns neutral rather than a number,
    which is the same rule ``performance.py`` enforces and for the same reason:
    ranking on eleven impressions is ranking on noise.
    """
    if not summary:
        return {"value": NEUTRAL, "reason": "no performance history yet",
                "detail": "NO_DATA"}
    ctr = summary.get("ctr_on_viewable")
    if ctr is None:
        return {"value": NEUTRAL,
                "reason": "not enough impressions to judge click quality",
                "detail": "INSUFFICIENT_DATA"}
    # A 2% click rate on viewable impressions is a strong result in-feed, so
    # that is the top of the scale rather than an unreachable 100%.
    return {"value": _clamp(float(ctr) / 0.02),
            "reason": f"click rate of {float(ctr):.2%} on viewable impressions",
            "detail": "MEASURED"}


def exploration_component(summary: Optional[dict]) -> dict:
    """A bounded head start for creatives that have not been measured yet.

    Without it a new creative can never accumulate the evidence that would let
    it win, so the ranker's first impression of the world becomes permanent.
    The weight caps how much of the platform's inventory this can consume.
    """
    seen = int((summary or {}).get("viewable") or 0)
    floor = taxonomy.MIN_IMPRESSIONS_FOR_CTR
    if seen >= floor:
        return {"value": 0.0, "reason": "this creative has enough data already",
                "detail": "MEASURED"}
    remaining = (floor - seen) / float(floor)
    return {"value": _clamp(remaining),
            "reason": f"only {seen} of {floor} impressions needed to judge it",
            "detail": "UNDER_SAMPLED"}


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def score_candidate(candidate: dict, *, described: dict, affinities: dict,
                    campaign_category: Any = None,
                    summary: Optional[dict] = None,
                    fatigue_state: str = "INSUFFICIENT_DATA") -> dict:
    """Score one eligible candidate, returning the full breakdown.

    Pure: every input is passed in, nothing is read here. That is what lets the
    scoring rules be tested exhaustively without a database, and what lets
    shadow mode replay a decision offline.
    """
    parts = {
        "context": context_component(campaign_category, described),
        "affinity": affinity_component(affinities, campaign_category),
        "quality": quality_component(summary),
        "exploration": exploration_component(summary),
    }
    base = sum(WEIGHTS[name] * part["value"] for name, part in parts.items())
    multiplier = FATIGUE_MULTIPLIERS.get(str(fatigue_state), 1.0)
    return {
        "creative_id": candidate.get("creative_id"),
        "campaign_id": candidate.get("campaign_id"),
        "score": _clamp(base * multiplier),
        "base_score": _clamp(base),
        "fatigue_state": fatigue_state,
        "fatigue_multiplier": multiplier,
        "components": {name: dict(part, weight=WEIGHTS[name])
                       for name, part in parts.items()},
        "ranking_version": taxonomy.RANKING_VERSION,
        "ranking_mode": RANKING_MODE,
    }


def explain_score(scored: dict) -> str:
    """One human sentence for why a creative scored what it did."""
    parts = scored.get("components") or {}
    ordered = sorted(parts.items(),
                     key=lambda kv: kv[1]["weight"] * kv[1]["value"],
                     reverse=True)
    leading = "; ".join(f"{name}: {part['reason']}" for name, part in ordered[:3])
    tail = ""
    if scored.get("fatigue_multiplier", 1.0) < 1.0:
        tail = (f" — then reduced to {scored['score']:.3f} because the creative "
                f"is {scored.get('fatigue_state')}")
    return f"scored {scored.get('base_score', 0.0):.3f} ({leading}){tail}"


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def _campaign_categories(conn, candidates: list) -> dict:
    out = {}
    for candidate in candidates:
        campaign_id = candidate.get("campaign_id")
        if campaign_id in out:
            continue
        try:
            out[campaign_id] = _interest.campaign_category(conn, campaign_id)
        except Exception:
            out[campaign_id] = None
    return out


def _creative_summaries(conn, candidates: list, *, days: int = 14) -> dict:
    """Recent performance per creative. Degrades to empty, never raises."""
    out = {}
    for candidate in candidates:
        creative_id = candidate.get("creative_id")
        if creative_id in out:
            continue
        try:
            trend = _performance.creative_trend(conn, creative_id, days=days)
            out[creative_id] = {
                "summary": trend.get("recent") or {},
                "fatigue_state": trend.get("state") or "INSUFFICIENT_DATA",
            }
        except Exception:
            out[creative_id] = {"summary": {},
                                "fatigue_state": "INSUFFICIENT_DATA"}
    return out


def rank(conn, candidates: list, *, subject_ref: Any, request_ctx: Optional[dict] = None,
         now=None) -> dict:
    """Rank eligible candidates. Writes nothing, decides nothing.

    Returns ``{ranked, dropped, described, ranking_mode}``. Safe to call in
    shadow mode against live traffic because it has no side effects at all.
    """
    eligible = [c for c in (candidates or []) if c.get("eligible")]
    described = _context.describe(request_ctx or {}, now=now)

    permitted = _context.ad_permitted(described)
    if not permitted["permitted"]:
        return {"ranked": [], "dropped": [
            {"creative_id": c.get("creative_id"),
             "reason": permitted["reason"], "detail": permitted["detail"]}
            for c in eligible],
            "described": described, "ranking_mode": RANKING_MODE}

    try:
        affinities = _interest.affinities_for(conn, subject_ref)
    except Exception:
        affinities = {}
    categories = _campaign_categories(conn, eligible)
    creatives = _creative_summaries(conn, eligible)

    ranked, dropped = [], []
    for candidate in eligible:
        creative = creatives.get(candidate.get("creative_id")) or {}
        state = creative.get("fatigue_state") or "INSUFFICIENT_DATA"
        if state == "REJECTED":
            # Dropped, not zero-scored: a zero still wins an auction of one.
            dropped.append({"creative_id": candidate.get("creative_id"),
                            "reason": "CREATIVE_REJECTED",
                            "detail": "negative feedback is at or above the "
                                      "rejection threshold"})
            continue
        scored = score_candidate(
            candidate, described=described, affinities=affinities,
            campaign_category=categories.get(candidate.get("campaign_id")),
            summary=creative.get("summary"), fatigue_state=state)
        scored["candidate"] = candidate
        scored["tiebreak"] = _rotation_key(subject_ref, candidate.get("creative_id"))
        ranked.append(scored)

    # Descending score; the legacy rotation hash breaks ties so equal creatives
    # still spread across viewers, and creative_id makes the order total.
    ranked.sort(key=lambda s: (-s["score"], s["tiebreak"], str(s["creative_id"])))
    return {"ranked": ranked, "dropped": dropped, "described": described,
            "ranking_mode": RANKING_MODE}


# --------------------------------------------------------------------------- #
# The strategy the canonical selector accepts
# --------------------------------------------------------------------------- #

def _legacy_strategy():
    from services.business_os.advertising import selection as _selection
    return _selection.DeterministicRotation()


class ExplainableRanker:
    """Ranker v1 as a drop-in ``SelectionStrategy``.

    Falls back to the canonical rotation on any failure. The fallback is the
    whole safety story: this class sits in the path of every ad request, so
    "the ranker broke" must degrade to the previous behaviour rather than to an
    error. It holds a connection because the interface does not pass one.
    """

    def __init__(self, conn, *, request_ctx: Optional[dict] = None):
        self._conn = conn
        self._request_ctx = request_ctx or {}
        self.last_result: Optional[dict] = None

    def select(self, candidates: list, *, subject_ref: str,
               placement: str) -> Optional[dict]:
        try:
            ctx = dict(self._request_ctx)
            ctx.setdefault("placement", placement)
            ctx.setdefault("surface", ctx.get("surface") or placement)
            result = rank(self._conn, candidates, subject_ref=subject_ref,
                          request_ctx=ctx)
            self.last_result = result
            ranked = result.get("ranked") or []
            if not ranked:
                # Either nothing was eligible, or everything eligible was
                # rejected. Both are a genuine no-fill, not a reason to fall
                # back — falling back here would serve the rejected creative.
                return None
            return ranked[0].get("candidate")
        except Exception:
            _LOG.warning("ADS_INTEL_RANKER_FAILED placement=%s", placement,
                         exc_info=True)
            self.last_result = None
            return _legacy_strategy().select(
                candidates, subject_ref=subject_ref, placement=placement)


def compare(conn, candidates: list, *, subject_ref: Any, placement: str,
            request_ctx: Optional[dict] = None) -> dict:
    """Shadow mode: what would the ranker have chosen, versus the live strategy?

    Decides nothing and writes nothing. This is how the ranker earns the right
    to be turned on — measured against real traffic, with the legacy strategy
    still serving every impression.
    """
    try:
        legacy = _legacy_strategy().select(
            candidates, subject_ref=str(subject_ref), placement=placement)
    except Exception:
        legacy = None
    try:
        result = rank(conn, candidates, subject_ref=subject_ref,
                      request_ctx=request_ctx)
        ranked = result.get("ranked") or []
        proposed = ranked[0] if ranked else None
    except Exception:
        _LOG.warning("ADS_INTEL_SHADOW_FAILED placement=%s", placement,
                     exc_info=True)
        return {"agreed": None, "error": True}

    legacy_id = (legacy or {}).get("creative_id")
    proposed_id = (proposed or {}).get("creative_id")
    return {
        "agreed": legacy_id == proposed_id,
        "legacy_creative_id": legacy_id,
        "proposed_creative_id": proposed_id,
        "proposed_score": (proposed or {}).get("score"),
        "explanation": explain_score(proposed) if proposed else None,
        "dropped": result.get("dropped") or [],
        "ranking_mode": RANKING_MODE,
        "ranking_version": taxonomy.RANKING_VERSION,
    }
