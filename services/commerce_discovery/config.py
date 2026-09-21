"""Every tunable the discovery engine has, in one place, with its default.

Two rules this module exists to enforce.

**Nothing downstream reads ``os.environ``.** A knob that is read where it is
used is a knob nobody can enumerate, and this engine's whole risk profile is
"how often does a buyer see commerce" — a question an operator has to be able to
answer by reading one file. It is also a protection-suite matter:
``tests/protection/test_environment_contract.py`` requires every ``os.getenv``
name to appear in ``.env.example``, and keeping the reads in one module keeps
that list auditable instead of scattered across nine.

**Every default is the conservative one.** Unset means *less* commerce, never
more: the engine ships showing a feed unit once per eight posts, one reels chip
per session, and nothing at all below the relevance floor. An operator turning
knobs up is a decision someone made; an operator forgetting to turn them down
should not be.

Weights are a single JSON override rather than one variable per term. Twelve
env vars would be twelve ``.env.example`` lines, twelve chances to set one and
forget the other eleven, and no way to see the vector as a vector. One
``COMMERCE_DISCOVERY_WEIGHTS`` document is diffable and partial — unnamed terms
keep their defaults, so an operator can retune ``freshness`` without restating
the model.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    """Env int, clamped at ``minimum``, falling back on anything unparseable.

    A malformed value falls back rather than raising. A typo'd tuning knob must
    not be able to stop the app from booting — the engine is an enhancement, and
    the fail-safe rule says a discovery fault may never take down a surface.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        LOGGER.warning("COMMERCE_DISCOVERY_BAD_INT name=%s using_default=%s", name, default)
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return min(maximum, max(minimum, float(raw)))
    except (TypeError, ValueError):
        LOGGER.warning("COMMERCE_DISCOVERY_BAD_FLOAT name=%s using_default=%s", name, default)
        return default


# --- master switch ----------------------------------------------------------
# Off returns an empty placement list from every surface, which every client
# already renders as "no commerce unit". This is the kill switch: it needs to
# work without a client release, so it is checked at request time, not import.
def enabled() -> bool:
    return _flag("COMMERCE_DISCOVERY_ENABLED", True)


def personalization_enabled() -> bool:
    """Operator-level off switch for the *personalized* half of ranking.

    Distinct from the user's own preference. With this off the engine still
    serves discovery, but scores on listing quality and freshness alone and
    reports ``reason="popular"`` — the same degraded mode a user gets when they
    turn personalization off for themselves, so there is one code path to test.
    """
    return _flag("COMMERCE_DISCOVERY_PERSONALIZATION", True)


# --- secrets ----------------------------------------------------------------
# Deliberately NOT shared with advertising's salt. Sharing would make one hash
# join an organic viewer to a paid viewer across the two event stores, which is
# precisely the cross-class linkage the promotion wall is there to prevent.
SUBJECT_SALT_ENV = "COMMERCE_DISCOVERY_SUBJECT_SALT"
TOKEN_SECRET_ENV = "COMMERCE_DISCOVERY_TOKEN_SECRET"
_DEFAULT_SUBJECT_SALT = "pulsesoc-commerce-subject-v1"
_DEFAULT_TOKEN_SECRET = "pulsesoc-commerce-token-v1"


def subject_salt() -> str:
    return os.environ.get(SUBJECT_SALT_ENV) or _DEFAULT_SUBJECT_SALT


def token_secret() -> bytes:
    return (os.environ.get(TOKEN_SECRET_ENV) or _DEFAULT_TOKEN_SECRET).encode("utf-8")


# --- placement lifetime -----------------------------------------------------
def placement_ttl_seconds() -> int:
    """How long a served placement may still report an impression or click.

    Longer than an ad delivery TTL on purpose: a discovery card can sit in a
    scrolled-past feed page for a while before the user scrolls back up to it,
    and there is no budget being spent, so the cost of a generous window is only
    a slightly stale analytics row.
    """
    return _env_int("COMMERCE_DISCOVERY_PLACEMENT_TTL", 3600, minimum=60)


# --- per-surface cadence ----------------------------------------------------
def feed_lead_in() -> int:
    """Organic posts that must render before the first commerce unit."""
    return _env_int("COMMERCE_DISCOVERY_FEED_LEAD_IN", 6, minimum=1)


def feed_interval() -> int:
    """Organic posts between commerce units after the first.

    Default 8 sits inside the mission's 6–10 band and, just as importantly,
    is coprime with neither the ad cadence (5) nor the discovery-row cadence
    (7) in a way that pins the three to the same scroll depths every session.
    """
    return _env_int("COMMERCE_DISCOVERY_FEED_INTERVAL", 8, minimum=1)


def feed_max_per_page() -> int:
    return _env_int("COMMERCE_DISCOVERY_FEED_MAX_PER_PAGE", 2, minimum=0)


def reels_max_per_session() -> int:
    """Reels is the most intrusive surface, so it gets the smallest budget."""
    return _env_int("COMMERCE_DISCOVERY_REELS_MAX_PER_SESSION", 1, minimum=0)


def reels_lead_in() -> int:
    """Reels watched before a chip may appear at all."""
    return _env_int("COMMERCE_DISCOVERY_REELS_LEAD_IN", 4, minimum=1)


def reels_interval() -> int:
    """Reels between chips, once the lead-in is spent.

    Only reachable when ``COMMERCE_DISCOVERY_REELS_MAX_PER_SESSION`` is raised
    above its default of 1, but it has to exist for that knob to be safe to
    turn: without it a budget of 2 puts the second chip on the very next reel.
    """
    return _env_int("COMMERCE_DISCOVERY_REELS_INTERVAL", 10, minimum=1)


def cadence(surface: str) -> dict:
    """The placement rhythm for one surface, as plain numbers.

    This exists because the rhythm has two owners and only one of them can be
    authoritative. The client cannot fetch it separately — it needs the cadence
    on the same frame it needs the placements, or the first page places rows at
    one rhythm and re-places them at another. But when the client is the *only*
    owner, ``COMMERCE_DISCOVERY_FEED_INTERVAL`` becomes a variable an operator
    can set, restart for, and watch do nothing, which is worse than not having
    the knob at all.

    Sending it alongside the placements settles it: cadence can only change on a
    response that changes the placements too, so a retune never re-flows rows
    that are already on screen.
    """
    if surface == "reels":
        return {
            "lead_in": reels_lead_in(),
            "interval": reels_interval(),
            "max_per_page": surface_caps()["reels"][0],
        }
    if surface == "messenger":
        return {"lead_in": 0, "interval": 1, "max_per_page": surface_caps()["messenger"][0]}
    if surface == "marketplace":
        return {"lead_in": 0, "interval": 1, "max_per_page": marketplace_module_limit()}
    return {
        "lead_in": feed_lead_in(),
        "interval": feed_interval(),
        "max_per_page": feed_max_per_page(),
    }


def messenger_max_per_session() -> int:
    return _env_int("COMMERCE_DISCOVERY_MESSENGER_MAX_PER_SESSION", 1, minimum=0)


def marketplace_module_limit() -> int:
    """Marketplace is the surface the user came to for commerce: densest."""
    return _env_int("COMMERCE_DISCOVERY_MARKETPLACE_MODULES", 8, minimum=0)


#: Per-surface caps, resolved together so a caller cannot read one and miss the
#: other. Values are ``(max_placements_per_response, max_per_session)``.
def surface_caps() -> dict[str, tuple[int, int]]:
    return {
        "feed": (feed_max_per_page(), _env_int("COMMERCE_DISCOVERY_FEED_MAX_PER_SESSION", 6, minimum=0)),
        "reels": (1, reels_max_per_session()),
        "messenger": (1, messenger_max_per_session()),
        "marketplace": (marketplace_module_limit(), 1000),
    }


# --- relevance floor --------------------------------------------------------
def min_score(surface: str) -> float:
    """The score below which a surface shows **nothing**.

    "No placement is better than a bad placement" is a product rule, so it is
    enforced as a number rather than as a comment. Reels carries the highest
    floor because it is the only surface where a placement shares the frame with
    content the user is actively watching.
    """
    base = _env_float("COMMERCE_DISCOVERY_MIN_SCORE", 0.35, minimum=0.0, maximum=1.0)
    lift = {"reels": 0.20, "messenger": 0.10, "feed": 0.0, "marketplace": -0.15}
    return max(0.0, min(1.0, base + lift.get(surface, 0.0)))


# --- frequency caps ---------------------------------------------------------
def product_cap() -> int:
    """Times one listing may be shown to one viewer inside the window."""
    return _env_int("COMMERCE_DISCOVERY_PRODUCT_CAP", 3, minimum=1)


def product_window_seconds() -> int:
    return _env_int("COMMERCE_DISCOVERY_PRODUCT_WINDOW", 604800, minimum=60)


def seller_cap() -> int:
    return _env_int("COMMERCE_DISCOVERY_SELLER_CAP", 6, minimum=1)


def seller_window_seconds() -> int:
    return _env_int("COMMERCE_DISCOVERY_SELLER_WINDOW", 86400, minimum=60)


def hide_days() -> int:
    """Length of the "hide suggestions for 30 days" snooze."""
    return _env_int("COMMERCE_DISCOVERY_HIDE_DAYS", 30, minimum=1)


def request_rate_max() -> int:
    return _env_int("COMMERCE_DISCOVERY_RATE_MAX", 120, minimum=1)


def request_rate_window_seconds() -> int:
    return _env_int("COMMERCE_DISCOVERY_RATE_WINDOW", 60, minimum=1)


# --- exploration ------------------------------------------------------------
def exploration_rate() -> float:
    """Share of slots reserved for listings the ranker is unsure about.

    Without this the engine is a rich-get-richer machine: a listing with no
    impressions has no engagement signal, scores low, and therefore never earns
    the impressions that would let it score. A small reserved share is the whole
    mechanism by which a new seller's first product is ever seen. It is
    deliberately small — fairness that costs relevance stops being fairness once
    the buyer disengages from the unit entirely.
    """
    return _env_float("COMMERCE_DISCOVERY_EXPLORATION_RATE", 0.15, minimum=0.0, maximum=0.5)


# --- ranking weights --------------------------------------------------------
#: The scoring model, as a vector. Positive terms earn a slot, negative terms
#: spend one. Magnitudes are intentionally blunt for v1 — a hand-tuned model
#: with eleven significant figures would be overfitted to a marketplace that has
#: almost no engagement history yet, and it would be impossible to explain to a
#: seller asking why their product does not appear.
DEFAULT_WEIGHTS: dict[str, float] = {
    "relevance": 0.28,
    "quality": 0.16,
    "predicted_interest": 0.14,
    "conversion_probability": 0.10,
    "seller_reliability": 0.10,
    "freshness": 0.08,
    "exploration_bonus": 0.06,
    "diversity_bonus": 0.08,
    "repetition_penalty": -0.20,
    "hide_penalty": -0.60,
    "refund_risk": -0.15,
    "seller_risk": -0.25,
}


def weights() -> dict[str, float]:
    """Ranking weights, with a partial JSON override applied.

    An override naming a term the model does not have is ignored with a log line
    rather than accepted: silently carrying an unknown key would let a typo
    (``freshnes``) read as a successful retune while the real term stayed at its
    default.
    """
    resolved = dict(DEFAULT_WEIGHTS)
    raw = (os.environ.get("COMMERCE_DISCOVERY_WEIGHTS") or "").strip()
    if not raw:
        return resolved
    try:
        override = json.loads(raw)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_WEIGHTS_UNPARSEABLE using_defaults=1")
        return resolved
    if not isinstance(override, dict):
        LOGGER.warning("COMMERCE_DISCOVERY_WEIGHTS_NOT_AN_OBJECT using_defaults=1")
        return resolved
    for key, value in override.items():
        if key not in DEFAULT_WEIGHTS:
            LOGGER.warning("COMMERCE_DISCOVERY_WEIGHTS_UNKNOWN_TERM term=%s ignored=1", key)
            continue
        try:
            resolved[key] = float(value)
        except (TypeError, ValueError):
            LOGGER.warning("COMMERCE_DISCOVERY_WEIGHTS_BAD_VALUE term=%s ignored=1", key)
    return resolved


#: Bumped whenever the model changes shape (a term added, removed, or
#: redefined). Stamped onto every event row so a metric can be read against the
#: model that produced it instead of being silently pooled across two.
RANKING_VERSION = "commerce-discovery-v1"


#: Viewability, in PulseSoc's existing vocabulary rather than the spec's.
#: ``DiscoveryRowView`` already counts a card seen at 60% and Reels at 72%; the
#: brief asks for 50%/1s but explicitly defers to established conventions, and
#: two different definitions of "seen" in one feed would make organic and paid
#: reach non-comparable for reasons that have nothing to do with either.
VISIBLE_PERCENT_THRESHOLD = 60
VISIBLE_DWELL_MS = 1000
