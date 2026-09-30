"""Ordering eligible products, transparently and on recorded signals only.

## Why a linear model and not something cleverer

There is no training data. PulseSoc has never published an automated commerce
post, so there are no impressions, no click-throughs and no conversions to learn
from; a model fitted today would be fitted to nothing. The honest first ranker is
therefore a weighted sum of facts, with the weights written down where they can
be argued about — and, critically, with every component returned alongside the
score so that when there *is* outcome data, the question "did the ranker prefer
what members preferred" is answerable from ``pulsedrop_publications`` rather than
from a replay.

## The extension point

:func:`rank` takes a ``scorer`` — any callable ``(listing, now) -> Score``. The
run log records :data:`RANKER_VERSION` on every publication, so a second scorer
can be introduced, A/B'd against this one and attributed, without this module
changing. That is the whole "extensible for future models" requirement; a plugin
registry would be more machinery than a single alternative implementation needs.

## Why the score is bounded per component

Each component saturates rather than growing without limit. One product with 400
lifetime saves would otherwise outrank everything else forever, which is not a
ranking but a pin — and the anti-repetition layer would then be the only thing
stopping PulseDrop from posting the same product for a year. Saturation keeps the
cooldowns as a safety net rather than as the mechanism.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Mapping, NamedTuple

from services.pulsedrop import editorial, eligibility

#: Stamped on every publication. Bump on any weight or component change, because
#: the column's purpose is to make a historical score interpretable, and a
#: version that silently means two different formulas is worse than no version.
RANKER_VERSION = "pulsedrop-linear-v1"


class Score(NamedTuple):
    total: float
    #: Component name -> contribution. Stored on the publication row so a
    #: decision can be explained after the counts that produced it have moved.
    components: dict[str, float]


#: Weights, summing to 1.0 so a total is readable as a fraction of the best
#: possible product rather than as an arbitrary magnitude.
WEIGHTS: dict[str, float] = {
    # What members have done with it lately. The strongest available evidence
    # that a product is worth someone else's attention.
    "recent_engagement": 0.32,
    # What members have done with it ever, orders weighted heaviest inside
    # ``editorial.lifetime_engagement``.
    "lifetime_engagement": 0.22,
    # Newness. Not because new is better, but because a discovery surface that
    # never surfaces new listings is a leaderboard.
    "freshness": 0.20,
    # Whether it will actually look good. A one-photo listing and an eight-photo
    # listing make very different posts, and the composer needs frames.
    "media": 0.18,
    # A human reviewer's flag. Small: it is a real signal and an old one.
    "featured": 0.08,
}

#: The count at which the engagement components reach ~0.63 of their weight.
#: Chosen against production's actual shape (tens of listings, single-digit
#: saves), not against a hypothetical catalog.
_RECENT_SCALE = 4.0
_LIFETIME_SCALE = 12.0

#: A listing older than this contributes no freshness. Thirty days: beyond it,
#: "how old" stops distinguishing products and starts ranking the import order
#: of a dropship batch.
_FRESHNESS_HORIZON_HOURS = 720.0

#: Enough images to build a Reel that does not repeat a frame.
_MEDIA_IDEAL_IMAGES = 4


def _saturate(value: float, scale: float) -> float:
    """0 at zero, asymptotic to 1. Concave, so the 1st save matters most."""
    if value <= 0 or scale <= 0:
        return 0.0
    return 1.0 - math.exp(-float(value) / float(scale))


def _hours_since(stamp: str, now: datetime) -> float:
    if not stamp:
        return _FRESHNESS_HORIZON_HOURS
    text = str(stamp).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return _FRESHNESS_HORIZON_HOURS
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    delta = (now - parsed).total_seconds() / 3600.0
    # A future timestamp is clock skew or a seller-set publish date, not a
    # product from tomorrow. Treat it as brand new rather than as invalid.
    return max(0.0, delta)


def _freshness(listing: Mapping[str, Any], now: datetime) -> float:
    hours = _hours_since(editorial.published_at(listing), now)
    if hours >= _FRESHNESS_HORIZON_HOURS:
        return 0.0
    return 1.0 - (hours / _FRESHNESS_HORIZON_HOURS)


def _media_quality(listing: Mapping[str, Any]) -> float:
    images = len(eligibility.image_urls(listing))
    if not images:
        return 0.0
    coverage = min(1.0, images / float(_MEDIA_IDEAL_IMAGES))
    # A seller-shot product video is the single best asset PulseDrop can be
    # handed: it needs no composition, it is the seller's own framing, and Path
    # A republishes it intact. It cannot on its own reach a full score, because
    # a video with no stills leaves the Signal surface with nothing to show.
    if eligibility.video_source(listing):
        coverage = min(1.0, coverage + 0.35)
    return coverage


def score(listing: Mapping[str, Any], now: datetime | None = None) -> Score:
    """The default scorer. Pure: no I/O, no clock of its own beyond ``now``."""
    now = now or datetime.utcnow()
    raw = {
        "recent_engagement": _saturate(editorial.recent_engagement(listing), _RECENT_SCALE),
        "lifetime_engagement": _saturate(editorial.lifetime_engagement(listing), _LIFETIME_SCALE),
        "freshness": _freshness(listing, now),
        "media": _media_quality(listing),
        "featured": 1.0 if int(listing.get("featured") or 0) > 0 else 0.0,
    }
    components = {key: round(WEIGHTS[key] * value, 6) for key, value in raw.items()}
    return Score(round(sum(components.values()), 6), components)


class Ranked(NamedTuple):
    listing: dict
    score: Score
    label: editorial.Label


def rank(
    listings,
    *,
    now: datetime | None = None,
    scorer: Callable[[Mapping[str, Any], datetime], Score] | None = None,
) -> list[Ranked]:
    """Eligible listings, best first, each carrying its score and its label.

    The tie-break is the listing id, descending. Not random: a curator whose
    ordering changes between two ticks that saw identical data is a curator
    whose decisions cannot be reproduced when one of them turns out to be wrong.
    """
    now = now or datetime.utcnow()
    scorer = scorer or score
    ranked = [
        Ranked(dict(listing), scorer(listing, now), editorial.classify(listing, now))
        for listing in listings or []
    ]
    ranked.sort(key=lambda item: (item.score.total, int(item.listing.get("id") or 0)), reverse=True)
    return ranked
