"""The controlled vocabulary of the advertising intelligence layer.

Everything in this subsystem that could otherwise drift into free text lives
here as a closed set: event names, no-fill reasons, campaign diagnostic codes,
interest categories, signal weights, and privacy classes.

The reason this is one module rather than constants scattered across the
services that use them is that these names end up in three places at once — a
stored row, an admin screen, and an UNDX explanation. A category invented at a
call site would be queryable but unnameable, and a weight buried in a ranking
function would be tunable only by someone willing to redeploy. Both failures are
easy to introduce and very hard to notice, because neither breaks a test.

Nothing here reads the database or has side effects.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Event taxonomy
# --------------------------------------------------------------------------- #

#: Bumped when the meaning of a stored field changes, never when a field is
#: added. Historical rows keep the version they were written under so a report
#: can exclude or translate them rather than silently mixing definitions.
EVENT_SCHEMA_VERSION = 1

OPPORTUNITY_EVENTS = ("ad_opportunity_created",)

#: Deliberately three distinct facts. "Served" is a server decision, "rendered"
#: is the client saying pixels exist, "viewable" is the client saying the
#: viewability contract below was met. Collapsing them is how a platform ends up
#: reporting a 100% viewability rate that means nothing.
DELIVERY_EVENTS = ("ad_served", "ad_rendered", "ad_viewable")

ENGAGEMENT_EVENTS = (
    "ad_click", "ad_expand", "ad_save", "ad_share", "ad_profile_visit",
    "ad_store_visit", "ad_product_view", "ad_video_start", "ad_video_25",
    "ad_video_50", "ad_video_75", "ad_video_complete", "ad_follow",
    "ad_message_action",
)

#: The signals that stop the system from optimising itself into a wall of ads
#: nobody wants. A platform that only learns from clicks learns that annoying
#: people works, because annoyance and attention are correlated.
NEGATIVE_EVENTS = (
    "ad_quick_skip", "ad_hide", "ad_not_interested", "ad_report",
    "ad_repeated_exposure_ignore", "ad_landing_bounce",
)

#: Negative signals split by who concluded the dislike, because the two deserve
#: different privacy treatment and collapsing them breaks one of them.
#:
#: An EXPLICIT negative is the person telling us, in words, using a control we
#: built for the purpose. Acting on it is honouring a request, not profiling —
#: and refusing to act on it means the button does nothing, which is worse than
#: not having the button. These are allowed to shape delivery.
#:
#: An INFERRED negative is the system guessing from behaviour. A fast scroll may
#: be boredom, a misfire, or a train going into a tunnel. That ambiguity is
#: exactly what `measurement_only` exists for: count it, report it, never let it
#: quietly decide what somebody sees.
#:
#: The safety property that makes the explicit set safe to act on is not the
#: class but the sign: every weight in `SIGNAL_WEIGHTS` for these events is
#: negative, and `interest.py` enforces that they may only ever decrease an
#: affinity. A complaint can therefore suppress a category but can never become
#: a positive profile attribute.
EXPLICIT_NEGATIVE_EVENTS = frozenset({
    "ad_hide", "ad_not_interested", "ad_report",
})
INFERRED_NEGATIVE_EVENTS = frozenset({
    "ad_quick_skip", "ad_repeated_exposure_ignore", "ad_landing_bounce",
})

#: Commerce outcomes. `ad_purchase_completed` is present in the taxonomy but is
#: NOT accepted from a client — see `CLIENT_FORBIDDEN_EVENTS`.
CONVERSION_EVENTS = (
    "ad_product_view_conversion", "ad_favorite", "ad_add_to_cart",
    "ad_checkout_started", "ad_purchase_completed", "ad_repeat_purchase",
)

ALL_EVENT_NAMES = frozenset(
    OPPORTUNITY_EVENTS + DELIVERY_EVENTS + ENGAGEMENT_EVENTS
    + NEGATIVE_EVENTS + CONVERSION_EVENTS
)

#: Events a client may never assert, because believing the client would let a
#: phone mint revenue. These are derived server-side from the canonical order
#: and payment records, or they do not exist. Ingest rejects them outright
#: rather than storing them as suspect, so there is no path by which one could
#: later be reclassified as valid.
CLIENT_FORBIDDEN_EVENTS = frozenset({
    "ad_purchase_completed", "ad_repeat_purchase",
})

#: Events that may be counted toward billing IF they also pass validity and the
#: canonical billing path authorises them. Membership here is necessary, never
#: sufficient — this module cannot bill anything.
BILLABLE_CANDIDATE_EVENTS = frozenset({"ad_viewable", "ad_click"})


# --------------------------------------------------------------------------- #
# Viewability contract
# --------------------------------------------------------------------------- #

#: A static ad is viewable at >=50% visible for >=1000ms; video at >=50% visible
#: for >=2000ms while actually playing. These mirror the thresholds the client
#: already enforces in SponsoredAdCard (72% / 1000ms), deliberately set no
#: stricter than the client so a client-side "viewable" is never rejected by the
#: server for a rule the client was never told about.
VIEWABILITY_STATIC_MIN_PERCENT = 50
VIEWABILITY_STATIC_MIN_MS = 1000
VIEWABILITY_VIDEO_MIN_PERCENT = 50
VIEWABILITY_VIDEO_MIN_MS = 2000


def viewability_met(percent_visible, duration_ms, *, is_video: bool = False,
                    foreground: bool = True) -> bool:
    """Whether a reported exposure satisfies the viewability contract.

    A backgrounded app never qualifies however long it claims to have been
    visible, which is the case that matters: an app suspended mid-scroll can
    otherwise accumulate arbitrary dwell time against an ad no human saw.
    """
    if not foreground:
        return False
    try:
        percent = float(percent_visible or 0)
        duration = float(duration_ms or 0)
    except (TypeError, ValueError):
        return False
    if is_video:
        return (percent >= VIEWABILITY_VIDEO_MIN_PERCENT
                and duration >= VIEWABILITY_VIDEO_MIN_MS)
    return (percent >= VIEWABILITY_STATIC_MIN_PERCENT
            and duration >= VIEWABILITY_STATIC_MIN_MS)


# --------------------------------------------------------------------------- #
# No-fill reasons
# --------------------------------------------------------------------------- #

#: Why an opportunity produced no ad. Recorded per decision so "we showed
#: nothing" stops being indistinguishable from "we were never asked".
NO_FILL_REASONS = (
    "NO_ELIGIBLE_CAMPAIGN", "ACCOUNT_UNVERIFIED", "CAMPAIGN_IN_REVIEW",
    "BUDGET_EXHAUSTED", "WALLET_EMPTY", "FREQUENCY_CAPPED",
    "AUDIENCE_MISMATCH", "PLACEMENT_UNSUPPORTED", "SCHEDULE_INACTIVE",
    "POLICY_BLOCKED", "CREATIVE_UNAVAILABLE", "INVENTORY_UNAVAILABLE",
    "AD_LOAD_LIMIT", "SYSTEM_DEGRADED",
)
NO_FILL_REASON_SET = frozenset(NO_FILL_REASONS)

#: Why a specific campaign is not delivering, shown to the advertiser. Same
#: vocabulary as the no-fill reasons where they overlap, so a support
#: conversation and a delivery log use one word for one thing.
CAMPAIGN_DIAGNOSTICS = (
    "ACCOUNT_UNVERIFIED", "ACCOUNT_SUSPENDED", "CAMPAIGN_IN_REVIEW",
    "CREATIVE_REJECTED", "WALLET_EMPTY", "BUDGET_EXHAUSTED", "UNDERPACING",
    "AUDIENCE_TOO_NARROW", "SCHEDULE_INACTIVE", "INVENTORY_UNAVAILABLE",
    "PLACEMENT_UNAVAILABLE", "FREQUENCY_LIMITED", "POLICY_BLOCKED",
    "DELIVERING",
)


# --------------------------------------------------------------------------- #
# Interest taxonomy
# --------------------------------------------------------------------------- #

#: A closed category list, not free text. Free-text segments would let an
#: advertiser (or an inference job) mint an unbounded number of ever-narrower
#: labels, which is both a targeting-precision problem and a privacy problem:
#: "interested in technology" is a product signal, an uncontrolled long tail is
#: a dossier.
INTEREST_CATEGORIES = (
    "technology", "fashion", "fitness", "music", "beauty", "gaming",
    "business", "home", "food", "travel", "automotive", "sports",
    "education", "pets", "art", "outdoors",
)
INTEREST_CATEGORY_SET = frozenset(INTEREST_CATEGORIES)

#: Interest windows in days. Short-term catches intent, long-term catches taste.
INTEREST_WINDOWS = (7, 30, 90)

#: Half-life in days per window: a signal contributes half as much once this
#: much time has passed. Decay is what stops a single interaction from
#: permanently classifying someone.
INTEREST_HALF_LIFE_DAYS = {7: 3.0, 30: 14.0, 90: 45.0}

#: How much each signal moves an affinity score. Negative weights are the point:
#: a hide has to be able to undo clicks, or "not interested" is decoration.
#: These live here rather than inside the scoring function so that retuning them
#: is a reviewable diff against a named table.
SIGNAL_WEIGHTS = {
    "ad_purchase_completed": 10.0,
    "ad_repeat_purchase": 12.0,
    "ad_add_to_cart": 7.0,
    "ad_checkout_started": 6.0,
    "ad_save": 5.0,
    "ad_favorite": 5.0,
    "ad_follow": 4.0,
    "ad_share": 4.0,
    "ad_store_visit": 3.0,
    "ad_product_view": 3.0,
    "ad_video_complete": 3.0,
    "ad_profile_visit": 2.5,
    "ad_video_75": 2.0,
    "ad_expand": 2.0,
    "ad_video_50": 1.5,
    "ad_click": 1.5,
    "ad_message_action": 2.0,
    "ad_video_25": 0.5,
    "ad_viewable": 0.2,
    "ad_rendered": 0.05,
    "ad_quick_skip": -1.0,
    "ad_repeated_exposure_ignore": -1.5,
    "ad_landing_bounce": -2.0,
    "ad_hide": -6.0,
    "ad_not_interested": -8.0,
    "ad_report": -15.0,
}

#: The affinity score is clamped so no single obsessive week can pin a category
#: at an unreachable value that later decay cannot bring back down.
AFFINITY_MIN = -50.0
AFFINITY_MAX = 100.0


# --------------------------------------------------------------------------- #
# Privacy control plane
# --------------------------------------------------------------------------- #

#: What a stored signal is allowed to be used for. Every event row carries one
#: of these, and the targeting reader filters on it, so restricting a signal is
#: a data change rather than an audit of every consumer.
PRIVACY_CLASSES = ("product_signal", "measurement_only", "security_only")

PRIVACY_CLASS_PERMISSIONS = {
    # Ordinary product behaviour: usable for delivery, measurement and fraud.
    "product_signal": {
        "analytics": True, "targeting": True, "fraud": True, "finance": True,
    },
    # Recorded so reports are correct, but never allowed to shape what a person
    # is shown. This is where anything ambiguous belongs.
    "measurement_only": {
        "analytics": True, "targeting": False, "fraud": True, "finance": True,
    },
    # Retained only to defend the platform. Never targeting, never analytics.
    "security_only": {
        "analytics": False, "targeting": False, "fraud": True, "finance": False,
    },
}

#: Sources that must never reach this subsystem at all. This is a denylist of
#: origins, not of categories, because the risk is a well-meaning caller piping
#: a whole surface's activity in — not someone typing "religion" into a field.
FORBIDDEN_SIGNAL_SOURCES = frozenset({
    "private_message", "direct_message", "message_body", "call_audio",
    "call_transcript", "password", "security_answer", "medical", "health",
    "sexual_orientation", "religion", "political_affiliation",
    "financial_hardship", "private_file", "biometric", "minor_behaviour",
})

#: Retention in days by record class. Financial and audit records outlive the
#: behavioural ones on purpose: one is a legal obligation, the other is a
#: liability that grows with age.
RETENTION_DAYS = {
    "raw_delivery_event": 180,
    "aggregated_performance": 730,
    "feature_aggregate": 180,
    "financial_record": 2555,
    "audit_record": 2555,
    "fraud_record": 730,
}


# --------------------------------------------------------------------------- #
# Validity
# --------------------------------------------------------------------------- #

#: Invalid traffic classification. `suspect` exists so a borderline signal can
#: be excluded from billing without being destroyed, which keeps the decision
#: reviewable instead of silently final.
VALIDITY_STATES = ("valid", "suspect", "invalid", "under_review")

INVALID_REASONS = (
    "DUPLICATE_EVENT", "UNKNOWN_DECISION", "DECISION_MISMATCH",
    "IMPLAUSIBLE_TIMESTAMP", "IMPOSSIBLE_SEQUENCE", "RAPID_REPEAT",
    "VELOCITY_ANOMALY", "CLIENT_ASSERTED_CONVERSION", "SCHEMA_INVALID",
    "UNKNOWN_CAMPAIGN", "FORBIDDEN_SOURCE",
)


# --------------------------------------------------------------------------- #
# Delivery and ad load
# --------------------------------------------------------------------------- #

PACING_STATES = ("UNDERPACING", "ON_TARGET", "OVERPACING", "LIMITED", "EXHAUSTED")

FREQUENCY_SCOPES = ("advertiser", "campaign", "creative")
FREQUENCY_WINDOWS = ("session", "day", "week")

#: Defaults, overridable per placement. The ceiling exists to protect the feed,
#: not the advertiser: without it, a well-funded campaign and an empty
#: competitive field produce a session that is mostly advertising.
DEFAULT_FREQUENCY_CAPS = {
    ("campaign", "session"): 2,
    ("campaign", "day"): 4,
    ("campaign", "week"): 12,
    ("creative", "session"): 1,
    ("creative", "day"): 3,
    ("creative", "week"): 8,
    ("advertiser", "session"): 3,
    ("advertiser", "day"): 8,
    ("advertiser", "week"): 25,
}

#: Platform-level ad load. These bound the product experience and are not
#: purchasable.
MAX_ADS_PER_SESSION = 12
MIN_ORGANIC_ITEMS_BETWEEN_ADS = 3
MAX_CONSECUTIVE_ADS = 1

#: Minimum audience size. Below this an audience is refused rather than
#: delivered narrowly, because a segment of nine people is a way of addressing
#: nine identifiable people.
MIN_AUDIENCE_SIZE = 1000


# --------------------------------------------------------------------------- #
# Exploration and versioning
# --------------------------------------------------------------------------- #

#: Share of opportunities reserved for under-sampled candidates. Without some
#: exploration a new creative can never accumulate the evidence that would let
#: it win; with too much, a small advertiser's budget is spent teaching the
#: platform rather than reaching people.
EXPLORATION_FRACTION = 0.10
EXPLORATION_MAX_BUDGET_SHARE = 0.20

#: Sample floors. Below these, a rate computed from the data is noise wearing a
#: percentage sign, and the code that consumes it must say "not enough data"
#: rather than render a number.
MIN_IMPRESSIONS_FOR_CTR = 500
MIN_CLICKS_FOR_CVR = 50
MIN_CONVERSIONS_FOR_OPTIMISATION = 30
MIN_IMPRESSIONS_FOR_FATIGUE = 1000
MIN_SAMPLE_FOR_EXPERIMENT = 200

RANKING_VERSION = 1
ATTRIBUTION_VERSION = 1
FEATURE_VERSION = 1
RECOMMENDATION_VERSION = 1
FRAUD_RULE_VERSION = 1
PROCESSING_VERSION = 1

#: Click-through attribution window. View-through is deliberately absent: the
#: product has no surface that would make a view-through conversion meaningful,
#: and adding one would inflate every conversion count without adding a fact.
CLICK_ATTRIBUTION_WINDOW_HOURS = 168
