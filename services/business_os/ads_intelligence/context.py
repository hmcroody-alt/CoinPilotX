"""Contextual intelligence — relevance from the slot, not from the person.

The interest graph in ``interest.py`` answers "what does this person like".
This module answers a different question: "what is this moment". A fitness ad
next to fitness content is relevant without anybody having been profiled, which
makes context the cheapest privacy win available to an ad system — it is the
only relevance signal that costs the viewer nothing.

Three structural properties, each enforced rather than documented.

It cannot become a profile
--------------------------
Every function here is pure and takes a request-scoped dict. There is no
``subject_ref`` parameter anywhere in the module, no database handle, and no
write path. That is deliberate: the failure mode for contextual targeting is
not that it is used, it is that somebody starts *accumulating* it — "this
person was next to fitness content 40 times" is a behavioural profile that was
assembled without ever calling it one. Because the module cannot see a subject
and cannot write, that accumulation is impossible here rather than merely
discouraged, and a test asserts the signature stays that way.

Sensitive context refuses the ad rather than scoring it low
-----------------------------------------------------------
Some adjacencies must not happen at any price: an ad beside a death
announcement, a crisis post, or a medical disclosure. A low score is not
sufficient because a low score still wins when nothing else is eligible.
``ad_permitted`` returns a hard refusal, and the refusal is checked before the
match score is ever computed, so no bid can outrank it.

Private surfaces produce no signal at all
------------------------------------------
Messages, calls and live audio never yield a context signal, not even a
downgraded one. This is a denylist of *origins* rather than of categories,
because the realistic failure is a well-meaning caller piping a whole surface's
activity in, not somebody typing a sensitive word into a field.

Unknown is a first-class answer throughout. A category that is not in the
closed taxonomy resolves to ``None`` rather than to a guess, because a guessed
context is indistinguishable from a measured one once it is stored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from . import taxonomy

#: Coarse on purpose. "Evening" is a scheduling signal; a timestamp accurate to
#: the second is a location and routine signal for anyone holding two of them.
TIME_BUCKETS = ("early_morning", "morning", "afternoon", "evening", "night")

#: Surfaces that may carry advertising. Anything absent is refused by default,
#: which is the correct direction for a list like this: a new surface has to be
#: added deliberately rather than inheriting ad load because nobody updated a
#: denylist.
AD_SUPPORTED_SURFACES = frozenset({"feed", "reels", "explore", "search"})

#: Origins that must never produce a contextual signal. Mirrors the taxonomy's
#: forbidden sources, plus the real-time surfaces, so a refactor of either list
#: cannot quietly narrow this one.
PRIVATE_SURFACES = frozenset({
    "messages", "direct_message", "dm", "chat", "inbox", "call", "audio_call",
    "video_call", "live_audio", "live_call", "voice", "voice_note",
}) | taxonomy.FORBIDDEN_SIGNAL_SOURCES

#: Content adjacencies where advertising is refused outright. These are not
#: "low quality" — they are contexts where the presence of an ad is itself the
#: harm, regardless of what the ad is.
SENSITIVE_CONTEXT_CATEGORIES = frozenset({
    "death", "memorial", "funeral", "obituary", "grief",
    "crisis", "self_harm", "suicide", "emergency", "disaster",
    "medical", "health_condition", "diagnosis", "mental_health",
    "violence", "accident", "tragedy", "conflict", "war",
    "minor", "child_safety",
    "religion", "political_affiliation", "sexual_orientation",
    "financial_hardship", "legal_trouble",
})

#: Refusal codes. Closed, because these surface in "why am I seeing this ad"
#: and in admin diagnostics, and free-text refusals are unaggregatable.
REFUSAL_REASONS = (
    "SURFACE_NOT_SUPPORTED", "PRIVATE_SURFACE", "SENSITIVE_CONTEXT",
    "CONTEXT_UNAVAILABLE",
)

#: Match strengths. Deterministic and few: an explainable ranker cannot use a
#: continuous context score that nobody can justify a specific value of.
MATCH_EXACT = 1.0
MATCH_RELATED = 0.5
MATCH_NEUTRAL = 0.25
MATCH_NONE = 0.0

#: Categories that genuinely inform each other. Kept small and symmetric on
#: purpose — a large adjacency map is a taxonomy that wants to be a graph, and
#: every extra edge weakens the claim that a match means anything.
#: Symmetry is a tested invariant, not a convention: an asymmetric edge makes
#: the score depend on which side of the pair you ask from, so the same ad in
#: the same slot would score differently depending on which value the caller
#: happened to pass first.
_RELATED_CATEGORIES = {
    "fitness": {"sports", "outdoors", "food"},
    "sports": {"fitness", "outdoors"},
    "outdoors": {"fitness", "sports", "travel", "automotive"},
    "travel": {"outdoors", "food"},
    "food": {"fitness", "travel", "home"},
    "home": {"food", "art", "pets"},
    "art": {"home", "fashion", "music"},
    "fashion": {"beauty", "art"},
    "beauty": {"fashion"},
    "technology": {"gaming", "business", "education", "automotive"},
    "gaming": {"technology", "music"},
    "music": {"gaming", "art"},
    "business": {"technology", "education"},
    "education": {"technology", "business"},
    "automotive": {"technology", "outdoors"},
    "pets": {"home"},
}


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def normalise_surface(raw: Any) -> Optional[str]:
    """A known surface name, or ``None``. Never a guess."""
    value = _clean(raw)
    return value or None


def normalise_content_category(raw: Any) -> Optional[str]:
    """Resolve adjacent-content category against the closed taxonomy.

    Anything outside the taxonomy is ``None``, not a nearest match. A fuzzy
    resolution here would let an unbounded long tail of labels back in through
    the side door the closed list exists to shut.
    """
    value = _clean(raw)
    if value in taxonomy.INTEREST_CATEGORY_SET:
        return value
    return None


def is_sensitive_context(raw: Any) -> bool:
    """Whether an adjacency is one where an ad must not appear."""
    return _clean(raw) in SENSITIVE_CONTEXT_CATEGORIES


def derive_time_bucket(when: Optional[datetime] = None) -> str:
    """Coarse time-of-day bucket in UTC."""
    moment = when or datetime.now(timezone.utc)
    hour = moment.hour
    if hour < 6:
        return "night"
    if hour < 9:
        return "early_morning"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "night"


def describe(ctx: Optional[dict] = None, *,
             now: Optional[datetime] = None) -> dict:
    """Normalise a request context into the closed vocabulary.

    Returns a dict that is safe to log and to show back to a person: it holds
    only the surface, the placement, a coarse time bucket, and a taxonomy
    category. Nothing identifying survives normalisation, because nothing
    identifying is copied through in the first place.

    ``sensitive`` is carried as a flag rather than as a category so that the
    refusal path never has to log what the sensitive content actually was.
    """
    raw = ctx or {}
    surface = normalise_surface(raw.get("surface"))
    placement = normalise_surface(raw.get("placement_key") or raw.get("placement"))
    raw_category = raw.get("content_category") or raw.get("category")
    sensitive = is_sensitive_context(raw_category)
    private = bool(
        (surface and surface in PRIVATE_SURFACES)
        or (placement and placement in PRIVATE_SURFACES)
    )
    return {
        "surface": surface,
        "placement": placement,
        "platform": normalise_surface(raw.get("platform")),
        "time_bucket": derive_time_bucket(now),
        # A sensitive adjacency yields no category at all. Recording it would
        # create exactly the sensitive-inference record the refusal exists to
        # prevent — the refusal and the log have to agree.
        "content_category": None if sensitive
        else normalise_content_category(raw_category),
        "sensitive": sensitive,
        "private": private,
    }


def ad_permitted(context: dict) -> dict:
    """Whether an ad may be shown here at all, with a closed reason code.

    Checked *before* any scoring. A sensitive or private context is not a
    low-scoring slot that a big enough bid can win; it is a slot that does not
    exist. Returning a score here instead of a refusal is how "we only show
    those ads when nothing else is available" becomes true by accident.
    """
    if context.get("private"):
        return {"permitted": False, "reason": "PRIVATE_SURFACE",
                "detail": "private surfaces never carry advertising"}
    if context.get("sensitive"):
        return {"permitted": False, "reason": "SENSITIVE_CONTEXT",
                "detail": "adjacent content is in a category where an ad is "
                          "itself the harm"}
    surface = context.get("surface") or context.get("placement")
    if not surface:
        return {"permitted": False, "reason": "CONTEXT_UNAVAILABLE",
                "detail": "no surface was supplied"}
    if surface not in AD_SUPPORTED_SURFACES:
        return {"permitted": False, "reason": "SURFACE_NOT_SUPPORTED",
                "detail": f"{surface} is not an advertising surface"}
    return {"permitted": True, "reason": None, "detail": "surface carries ads"}


def match_score(campaign_category: Any, context: dict) -> dict:
    """How well a campaign fits this moment, with the reason attached.

    Neutral rather than zero when the context has no category: an unknown
    context is not evidence of a bad fit, and scoring it zero would hand every
    impression on uncategorised content to whoever bid most, which is the
    opposite of what a contextual system is for.
    """
    permitted = ad_permitted(context)
    if not permitted["permitted"]:
        return {"score": MATCH_NONE, "match": "REFUSED",
                "reason": permitted["detail"]}

    campaign = normalise_content_category(campaign_category)
    content = context.get("content_category")
    if not content:
        return {"score": MATCH_NEUTRAL, "match": "UNKNOWN_CONTEXT",
                "reason": "the surrounding content has no known category"}
    if not campaign:
        return {"score": MATCH_NEUTRAL, "match": "UNKNOWN_CAMPAIGN",
                "reason": "the campaign declares no category"}
    if campaign == content:
        return {"score": MATCH_EXACT, "match": "EXACT",
                "reason": f"the campaign and the surrounding content are both "
                          f"{content}"}
    if content in _RELATED_CATEGORIES.get(campaign, ()):  # noqa: SIM118
        return {"score": MATCH_RELATED, "match": "RELATED",
                "reason": f"{campaign} and {content} are related categories"}
    return {"score": MATCH_NONE, "match": "NONE",
            "reason": f"{campaign} does not relate to {content}"}


def explain(context: dict, campaign_category: Any = None) -> dict:
    """The contextual half of "why am I seeing this ad", in plain terms.

    Only ever describes the slot. The interest half is explained separately by
    ``interest.explain_affinity`` — keeping them apart means this explanation
    stays true even for a viewer with no profile at all, which is precisely the
    case where an unexplained ad is most alarming.
    """
    permitted = ad_permitted(context)
    if not permitted["permitted"]:
        return {"shown": False, "reason": permitted["detail"],
                "code": permitted["reason"]}
    match = match_score(campaign_category, context)
    if match["match"] == "EXACT":
        why = (f"this ad is about {context.get('content_category')}, and so is "
               f"the content around it")
    elif match["match"] == "RELATED":
        why = (f"this ad relates to {context.get('content_category')}, which is "
               f"what the content around it is about")
    else:
        why = "this ad was not chosen because of the content around it"
    return {"shown": True, "reason": why, "code": match["match"],
            "surface": context.get("surface"),
            "used_content_category": context.get("content_category")}
