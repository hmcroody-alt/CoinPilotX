"""Which format, if any — the decision that stops PulseDrop being two bots.

## Why this is a module and not an ``if reels_enabled`` at the publish site

The brief's requirement is one sentence: *never mechanically both*. That sounds
like a guard clause and is not. "Both" has to be a decision with its own
evidence, because the cheap implementation — publish a Signal, then publish a
Reel of the same product because nothing forbade it — produces a timeline where
every product appears twice and neither appearance means anything. Meanwhile the
expensive format has to be earned: a Reel costs CPU, storage and a member's
attention for eight seconds, and a product with one flat catalog photo does not
repay that.

So the decision is: what is the *best single way* to show this product, and is
there a specific reason to use two? The reason exists exactly once — a product
strong enough to lead the feed, with a seller's own video and enough stills to
carry a Signal too — and the cross-format cooldown must permit it, which by
default it does not.

## Two outcomes that look like failure and are not

``DEFER`` means the product is good and this is the wrong moment: a cooldown, a
pacing floor, a daily cap. It will be reconsidered unchanged on a later tick, so
nothing is lost and nothing needs logging as an error.

``SKIP`` means this product cannot be published in any enabled format right now
for a structural reason: no usable media, both surfaces switched off, no render
capability on the box. Also not an error — but it will keep being true until
something about the listing or the deployment changes, which is why the two are
counted separately.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from services.pulsedrop import config, diversity, eligibility

log = logging.getLogger(__name__)

SIGNAL_ONLY = "SIGNAL_ONLY"
REEL_ONLY = "REEL_ONLY"
SIGNAL_AND_REEL = "SIGNAL_AND_REEL"
DEFER = "DEFER"
SKIP = "SKIP"

#: Path A: republish the seller's own product video through the Reels pipeline.
SOURCE_SELLER_VIDEO = "seller_video"
#: Path B: compose a Reel from the product's stills.
SOURCE_COMPOSED_IMAGES = "composed_images"

#: A product must score at least this well before it is worth two posts. Set
#: against the scorer's own scale (weights sum to 1.0), so this reads as "in the
#: top fifth of what a product can be" rather than as a magic number.
DUAL_FORMAT_MIN_SCORE = 0.62


class Decision(NamedTuple):
    outcome: str
    #: Which Reel path is viable, or ``""`` when none is. Carried through so the
    #: renderer is not asked to re-derive it from the media a second time.
    reel_source: str
    #: One short code naming what drove the outcome. Stored on the run row.
    reason: str

    @property
    def publishes_signal(self) -> bool:
        return self.outcome in (SIGNAL_ONLY, SIGNAL_AND_REEL)

    @property
    def publishes_reel(self) -> bool:
        return self.outcome in (REEL_ONLY, SIGNAL_AND_REEL)


def reel_source_for(listing) -> str:
    """The best Reel path this listing supports, or ``""``.

    Path A wins whenever it is available. A seller's own footage is their
    framing, their lighting and their product in motion; a composed slideshow of
    the same product is strictly less information. The only reasons to fall back
    are length — a four-minute unboxing is a legitimate product video and a bad
    Reel, and truncating someone else's footage at an arbitrary point is not
    ours to do — and absence.
    """
    if eligibility.video_source(listing):
        return SOURCE_SELLER_VIDEO
    images = eligibility.image_urls(listing)
    if len(images) >= config.reel_min_images():
        return SOURCE_COMPOSED_IMAGES
    return ""


def _render_capable() -> bool:
    """Whether this container can produce a Reel at all.

    ffmpeg is a declared deploy dependency, so this is False on a developer's
    laptop and true in production. It is checked here rather than at render time
    so a box without it reports SKIP with a reason instead of enqueueing work
    that will fail three times and then give up.
    """
    try:
        from services import media_covers

        return media_covers.ffmpeg_available()
    except Exception:
        log.debug("pulsedrop_ffmpeg_probe_failed", exc_info=True)
        return False


def decide(ranked_item, history: diversity.History) -> Decision:
    """Pick the format for one ranked candidate.

    Pure with respect to the database: everything it needs is on the candidate
    or in the pre-loaded history, so the whole matrix is testable without one.
    """
    listing = ranked_item.listing
    signals_on = config.signals_enabled()
    reels_on = config.reels_enabled()
    if not signals_on and not reels_on:
        return Decision(SKIP, "", "both_surfaces_disabled")

    has_image = bool(eligibility.image_urls(listing))
    reel_source = reel_source_for(listing) if reels_on else ""
    if reel_source == SOURCE_COMPOSED_IMAGES and not _render_capable():
        # Path A needs no encoder of ours — the Reels pipeline transcodes the
        # seller's file. Path B is the one that needs ffmpeg locally.
        reel_source = ""

    signal_possible = signals_on and has_image
    reel_possible = bool(reel_source)
    if not signal_possible and not reel_possible:
        return Decision(SKIP, "", "no_publishable_format")

    # Pacing is checked before anti-repetition because it is a property of the
    # account, not of the product: when PulseDrop is inside its posting floor,
    # no candidate is publishable and evaluating cooldowns per product is work
    # whose answer cannot matter.
    if history.min_interval_blocked():
        return Decision(DEFER, reel_source, "min_publish_interval")
    if history.daily_cap_reached():
        return Decision(DEFER, reel_source, "daily_cap")

    signal_blocker = (
        history.blocker(listing, diversity.SIGNAL) if signal_possible else "surface_unavailable"
    )
    reel_blocker = ""
    if reel_possible:
        if history.reel_interval_blocked():
            reel_blocker = "reel_min_interval"
        elif history.daily_cap_reached(diversity.REEL):
            reel_blocker = "reel_daily_cap"
        else:
            reel_blocker = history.blocker(listing, diversity.REEL)
    else:
        reel_blocker = "surface_unavailable"

    signal_ok = signal_possible and not signal_blocker
    reel_ok = reel_possible and not reel_blocker

    if signal_ok and reel_ok and _earns_both(ranked_item, reel_source):
        return Decision(SIGNAL_AND_REEL, reel_source, "earned_both_formats")
    if reel_ok and _prefers_reel(ranked_item, reel_source):
        return Decision(REEL_ONLY, reel_source, "seller_video_leads")
    if signal_ok:
        return Decision(SIGNAL_ONLY, "", "signal_is_the_right_format")
    if reel_ok:
        return Decision(REEL_ONLY, reel_source, "signal_unavailable")

    # Both blocked. A cooldown or a cap will lift; a disabled surface or missing
    # media will not, and the caller counts those differently.
    blockers = {signal_blocker, reel_blocker} - {""}
    if blockers <= {"surface_unavailable"}:
        return Decision(SKIP, reel_source, "no_enabled_format")
    return Decision(DEFER, reel_source, diversity.most_specific(blockers - {"surface_unavailable"}))


def _earns_both(ranked_item, reel_source: str) -> bool:
    """The narrow case where two posts of one product is a decision.

    All three conditions are required, and the third is the important one: the
    cross-format cooldown having been set to zero is an operator saying, on
    purpose, that same-tick dual publication is wanted. Left at its default it
    is 48 hours, ``history.blocker`` returns ``cross_format_cooldown`` for the
    Reel, and this branch is unreachable — which is the intended resting state.
    """
    return (
        reel_source == SOURCE_SELLER_VIDEO
        and ranked_item.score.total >= DUAL_FORMAT_MIN_SCORE
        and config.cross_format_cooldown_hours() == 0
    )


def _prefers_reel(ranked_item, reel_source: str) -> bool:
    """Reel over Signal when the seller filmed the product themselves.

    A composed Reel does not clear this bar. Given stills and nothing else, the
    Signal is the honest format — it shows exactly the photographs the seller
    took, at the size they were taken, with no motion invented on top. Composing
    is worth doing when a Reel is what the surface needs, not as a default
    dressing-up of a photo.
    """
    return reel_source == SOURCE_SELLER_VIDEO
