"""Frequency caps and ad load — the two limits that protect the person.

These are separate ideas and it matters that they stay separate.

A **frequency cap** limits how often one viewer sees one advertiser, campaign or
creative. It protects the viewer from repetition and the advertiser from wasting
budget on somebody who has already decided.

**Ad load** limits how much advertising a session contains in total, regardless
of who is paying. It protects the product. Frequency caps alone cannot do this:
twelve different advertisers each respecting a cap of two still produce a feed
that is a quarter ads. Ad load is deliberately not purchasable, so it lives here
as a constant rather than as a campaign setting.

Counts are derived, not stored
-------------------------------
Every count comes from ``business_os_ad_impression_events``, the immutable
delivery log, exactly as the canonical ``advertising.frequency`` service already
does. There is no counter table to drift out of sync, no reconciliation job, and
a replayed impression cannot inflate a cap because duplicates collide on
``dedup_key`` upstream.

This module extends that service rather than replacing it. The canonical one
answers a single question — campaign scope, one rolling window — and remains in
the eligibility path. This adds the other two scopes and the other two windows
from the taxonomy, which is what turns "n impressions ever" into "n per day".

The legacy ``pulse_ad_frequency_caps`` table counts LIFETIME impressions with no
window at all. That cap can only ever tighten, so a long-lived account
eventually becomes ineligible for every campaign permanently and the only fix is
a manual counter reset. Windowed counts age out on their own.

Windows are honest about what they can measure
-----------------------------------------------
The impression log has no session column, so "session" here is a short rolling
window rather than a true session boundary. That is a real approximation and it
is named as one, because a cap documented as per-session but implemented as
per-30-minutes will eventually be debugged by somebody who believed the label.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: Window lengths in seconds. "session" is an approximation — see the module
#: docstring. Thirty minutes is long enough to cover a normal browsing sitting
#: and short enough that a cap of one genuinely reads as "once per sitting".
WINDOW_SECONDS = {
    "session": 30 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
}

#: Which impression-log column identifies each scope.
SCOPE_COLUMNS = {
    "advertiser": "advertiser_user_id",
    "campaign": "campaign_id",
    "creative": "creative_id",
}


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cap_for(scope: str, window: str) -> Optional[int]:
    """The configured cap, or ``None`` when the pair is not capped."""
    return taxonomy.DEFAULT_FREQUENCY_CAPS.get((scope, window))


def exposure_count(conn, *, scope: str, scope_ref: Any, subject_ref: Any,
                   window: str, now: Optional[datetime] = None) -> int:
    """Impressions for this viewer and scope inside the window.

    Derived live from the immutable impression log. Returns 0 on any failure —
    a frequency read that raises must not fail the ad request, and the
    canonical campaign-scope cap in the eligibility path is unaffected by this
    module, so a degraded read here loses a refinement rather than a control.
    """
    column = SCOPE_COLUMNS.get(scope)
    seconds = WINDOW_SECONDS.get(window)
    if not column or not seconds or not scope_ref or not subject_ref:
        return 0
    start = _iso(_now(now) - timedelta(seconds=seconds))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM business_os_ad_impression_events "
            f"WHERE {column} = ? AND subject_ref = ? AND event_at >= ?",
            (str(scope_ref), str(subject_ref), start)).fetchone()
    except Exception:
        _LOG.warning("ADS_INTEL_FREQUENCY_READ_FAILED scope=%s window=%s",
                     scope, window, exc_info=True)
        return 0
    return _int((row or [0])[0])


def check(conn, *, subject_ref: Any, advertiser_user_id: Any = None,
          campaign_id: Any = None, creative_id: Any = None,
          now: Optional[datetime] = None,
          caps: Optional[dict] = None) -> dict:
    """Evaluate every scope/window pair for one candidate.

    Returns ``{capped, reason, detail, checks}``. Every pair is evaluated even
    after one has failed, because the diagnostic value of "which caps is this
    campaign hitting" is the whole reason an advertiser can be told why their
    reach has stalled instead of guessing.
    """
    refs = {
        "advertiser": advertiser_user_id,
        "campaign": campaign_id,
        "creative": creative_id,
    }
    table = caps if caps is not None else taxonomy.DEFAULT_FREQUENCY_CAPS
    checks, breached = [], []
    for scope in taxonomy.FREQUENCY_SCOPES:
        ref = refs.get(scope)
        if not ref:
            continue
        for window in taxonomy.FREQUENCY_WINDOWS:
            cap = table.get((scope, window))
            if not cap or _int(cap) <= 0:
                continue
            count = exposure_count(conn, scope=scope, scope_ref=ref,
                                   subject_ref=subject_ref, window=window,
                                   now=now)
            hit = count >= _int(cap)
            entry = {"scope": scope, "window": window, "count": count,
                     "cap": _int(cap), "capped": hit,
                     "remaining": max(_int(cap) - count, 0)}
            checks.append(entry)
            if hit:
                breached.append(entry)

    if not breached:
        return {"capped": False, "reason": None, "checks": checks,
                "detail": "within every frequency cap"}
    # Report the tightest breach: the one with the fewest permitted exposures
    # is the one an advertiser has to change something about.
    worst = min(breached, key=lambda e: e["cap"])
    return {
        "capped": True,
        "reason": "FREQUENCY_LIMITED",
        "detail": (f"this viewer has seen this {worst['scope']} "
                   f"{worst['count']} times in the last {worst['window']}, "
                   f"at a cap of {worst['cap']}"),
        "breached": breached,
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# Ad load
# --------------------------------------------------------------------------- #

def ad_load_permits(*, ads_this_session: Any, items_since_last_ad: Any,
                    consecutive_ads: Any = 0) -> dict:
    """Whether the product can afford another ad in this session.

    Pure, and deliberately not parameterised by campaign: ad load is a property
    of the session, and if an advertiser could raise it then the ceiling would
    belong to whoever paid the most rather than to the product.

    ``items_since_last_ad`` being large is the normal case; the check exists
    for the boundary where two ads would otherwise land close together.
    """
    shown = _int(ads_this_session)
    gap = _int(items_since_last_ad)
    run = _int(consecutive_ads)

    if shown >= taxonomy.MAX_ADS_PER_SESSION:
        return {"permitted": False, "reason": "SESSION_AD_LIMIT",
                "detail": (f"{shown} ads already shown this session, at a "
                           f"ceiling of {taxonomy.MAX_ADS_PER_SESSION}")}
    if run >= taxonomy.MAX_CONSECUTIVE_ADS:
        return {"permitted": False, "reason": "CONSECUTIVE_ADS",
                "detail": (f"{run} ads in a row, at a ceiling of "
                           f"{taxonomy.MAX_CONSECUTIVE_ADS}")}
    if shown > 0 and gap < taxonomy.MIN_ORGANIC_ITEMS_BETWEEN_ADS:
        return {"permitted": False, "reason": "AD_SPACING",
                "detail": (f"only {gap} organic items since the last ad, "
                           f"needing {taxonomy.MIN_ORGANIC_ITEMS_BETWEEN_ADS}")}
    return {"permitted": True, "reason": None,
            "detail": f"{shown} ads shown this session"}


def explain_cap(result: dict) -> str:
    """A viewer-facing sentence. Never names the advertiser back to the viewer."""
    if not result.get("capped"):
        return "You have not reached a limit on how often you see this ad."
    return ("You have seen this ad enough times recently that we have stopped "
            "showing it to you for a while.")


def explain_for_advertiser(result: dict) -> str:
    """The same fact, framed for the person whose campaign stopped reaching."""
    if not result.get("capped"):
        return "Frequency caps are not limiting this campaign's delivery."
    return (f"Delivery is limited by a frequency cap: {result.get('detail')}. "
            f"This protects your budget from repeatedly paying to reach "
            f"someone who has already seen the ad.")
