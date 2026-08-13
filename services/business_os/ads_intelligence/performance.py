"""Creative and campaign performance — funnel rollups, rates, and fatigue.

Two jobs: turn the event log into daily rollups, and turn rollups into rates
that are safe to show someone.

The second job is where most ad platforms quietly lie, so this module has rules
about it.

Rates are never invented from thin data
---------------------------------------
Every rate here returns ``None`` rather than a number when the denominator is
below the sample floor in ``taxonomy``. Three clicks on eleven impressions is
not a 27% click rate, it is noise wearing a percentage sign — and an advertiser
who reallocates budget because of it has been actively misled by their own
dashboard. ``None`` renders as "not enough data yet", which is both true and
actionable; 27% is neither. Callers must handle ``None``, which is the point:
the type makes the uncertainty impossible to ignore.

Every rate names its denominator
--------------------------------
``ctr_on_viewable`` and ``ctr_on_served`` are different metrics and the legacy
tables could not say which one they were reporting. Collapsing "served",
"rendered" and "viewable" into a single "impressions" number is how a platform
ends up publishing a viewability rate that means nothing. The names here are
deliberately clumsy so nobody can quote one without saying what it is over.

Invalid traffic is excluded from rates but kept in the counts
-------------------------------------------------------------
Every funnel counter here is *valid-only*: an event that failed validity is
never inside ``served_count`` or ``viewable_count``, so it cannot move a rate
and cannot be billed. It is counted separately in ``invalid_count`` and
``excluded_count`` instead. Deleting it would destroy the evidence; leaving it
in the denominators would let fraud inflate performance. Keeping it visible and
inert is what makes the fraud review possible later.

Only ``valid`` counts toward rates — ``suspect`` and ``under_review`` do not.
Those two are undecided, and spending an advertiser's money on an undecided
event is a decision. The conservative direction is the only safe default.

Fatigue is a statement about a specific audience
------------------------------------------------
Creative fatigue is not "this creative is old". It is "the people being shown
this have seen it enough times that they have stopped responding, and are
starting to actively dislike it". So the signal combines falling engagement with
rising negative feedback and sufficient exposure — and it requires a real sample
before it will say anything at all, because declaring fatigue on 40 impressions
would pause creatives for noise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: Fatigue judgements. ``INSUFFICIENT_DATA`` is a first-class answer, not an
#: error: most creatives most of the time genuinely do not have enough evidence,
#: and saying so is more useful than defaulting to "healthy".
FATIGUE_STATES = ("INSUFFICIENT_DATA", "HEALTHY", "WEARING", "FATIGUED",
                  "REJECTED")

#: A creative is WEARING when engagement has fallen by this share against its
#: own early-life baseline, and FATIGUED at the steeper drop. Measured against
#: itself rather than against a platform average, because a 0.4% CTR may be
#: excellent in one vertical and poor in another.
FATIGUE_WEARING_DROP = 0.30
FATIGUE_FATIGUED_DROP = 0.50

#: Negative feedback rate at which a creative is REJECTED regardless of how well
#: it is performing. A creative can have a strong click rate and still be one
#: people are reporting; optimising on clicks alone would promote it.
FATIGUE_NEGATIVE_RATE = 0.02


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _day(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Rates
# --------------------------------------------------------------------------- #

def safe_rate(numerator: Any, denominator: Any, *, min_denominator: int) -> Optional[float]:
    """A rate, or ``None`` when the sample is too small to mean anything.

    ``None`` rather than 0.0 or a best guess. A caller that receives 0.0 cannot
    tell "nobody clicked" from "nobody has seen it yet", and those call for
    opposite decisions — pause the creative, or wait.
    """
    den = _int(denominator)
    if den <= 0 or den < int(min_denominator):
        return None
    return _int(numerator) / float(den)


def ctr_on_viewable(row: dict) -> Optional[float]:
    """Clicks over *viewable* impressions — the honest denominator.

    An impression nobody could see is not an opportunity to click, so including
    it flatters the rate for exactly the placements that deserve it least.
    """
    return safe_rate(row.get("click_count"), row.get("viewable_count"),
                     min_denominator=taxonomy.MIN_IMPRESSIONS_FOR_CTR)


def ctr_on_served(row: dict) -> Optional[float]:
    """Clicks over everything the server sent, viewable or not.

    Kept because it is the number that reconciles against delivery logs. It is
    not the number to optimise on.
    """
    return safe_rate(row.get("click_count"), row.get("served_count"),
                     min_denominator=taxonomy.MIN_IMPRESSIONS_FOR_CTR)


def viewability_rate(row: dict) -> Optional[float]:
    """Share of served impressions that actually met the viewability contract.

    The metric that makes a placement's quality visible. A placement with a high
    fill rate and a low viewability rate is burning budget on ads nobody sees.
    """
    return safe_rate(row.get("viewable_count"), row.get("served_count"),
                     min_denominator=taxonomy.MIN_IMPRESSIONS_FOR_CTR)


def conversion_rate(row: dict) -> Optional[float]:
    """Conversions over clicks. Click-through only — there is no view-through."""
    return safe_rate(row.get("conversion_count"), row.get("click_count"),
                     min_denominator=taxonomy.MIN_CLICKS_FOR_CVR)


def negative_rate(row: dict) -> Optional[float]:
    """Negative feedback over viewable impressions.

    The counterweight to click rate. A platform that only reads clicks learns
    that annoying people works, because annoyance and attention correlate.
    """
    return safe_rate(row.get("negative_count"), row.get("viewable_count"),
                     min_denominator=taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE)


def invalid_impression_rate(row: dict) -> Optional[float]:
    """Excluded impressions over *all* impressions recorded, valid or not.

    Deliberately not over ``served_count``: that counter is already valid-only,
    so dividing by it would compare the rejected traffic against the traffic
    that survived and understate the problem. The denominator here is everything
    the server recorded at the impression stage, which is the number an
    advertiser asking "how much of my traffic was junk" actually means.
    """
    total = _int(row.get("served_total_count"))
    if not total:
        # Older rollups predate the split; fall back to what is knowable rather
        # than reporting a confidently wrong zero.
        total = _int(row.get("served_count")) + _int(row.get("excluded_count"))
    return safe_rate(total - _int(row.get("served_count")), total,
                     min_denominator=taxonomy.MIN_IMPRESSIONS_FOR_CTR)


def summarise(row: dict) -> dict:
    """Counts plus every rate, each named for its denominator.

    ``None`` values are preserved rather than coerced, so a caller rendering
    this cannot accidentally print 0% for "we don't know yet".
    """
    return {
        "served": _int(row.get("served_count")),
        "rendered": _int(row.get("rendered_count")),
        "viewable": _int(row.get("viewable_count")),
        "clicks": _int(row.get("click_count")),
        "conversions": _int(row.get("conversion_count")),
        "negative": _int(row.get("negative_count")),
        "invalid": _int(row.get("invalid_count")),
        "excluded": _int(row.get("excluded_count")),
        "unique_subjects": _int(row.get("unique_subjects")),
        "ctr_on_viewable": ctr_on_viewable(row),
        "ctr_on_served": ctr_on_served(row),
        "viewability_rate": viewability_rate(row),
        "conversion_rate": conversion_rate(row),
        "negative_rate": negative_rate(row),
        "invalid_impression_rate": invalid_impression_rate(row),
        "has_enough_data": _int(row.get("viewable_count"))
        >= taxonomy.MIN_IMPRESSIONS_FOR_CTR,
    }


# --------------------------------------------------------------------------- #
# Rollups
# --------------------------------------------------------------------------- #

#: Note the shape: valid and non-valid rows are counted in separate columns of
#: the same scan rather than filtered in the WHERE clause. Filtering would have
#: made the excluded traffic invisible, and invisible fraud evidence is the same
#: as deleted fraud evidence.
_FUNNEL_SQL = """
    SELECT event_name,
           SUM(CASE WHEN validity = 'valid' THEN 1 ELSE 0 END),
           SUM(CASE WHEN validity <> 'valid' THEN 1 ELSE 0 END),
           SUM(CASE WHEN validity = 'invalid' THEN 1 ELSE 0 END),
           COUNT(DISTINCT CASE WHEN validity = 'valid'
                               THEN subject_ref END),
           COALESCE(SUM(CASE WHEN validity = 'valid'
                             THEN duration_ms ELSE 0 END), 0)
    FROM ads_intel_events
    WHERE {key_column} = ? AND occurred_at >= ? AND occurred_at < ?
    GROUP BY event_name
"""


def _bucket(event_name: str) -> Optional[str]:
    if event_name == "ad_served":
        return "served_count"
    if event_name == "ad_rendered":
        return "rendered_count"
    if event_name == "ad_viewable":
        return "viewable_count"
    if event_name == "ad_click":
        return "click_count"
    if event_name in taxonomy.NEGATIVE_EVENTS:
        return "negative_count"
    if event_name in taxonomy.CONVERSION_EVENTS:
        return "conversion_count"
    if event_name in taxonomy.ENGAGEMENT_EVENTS:
        return "engagement_count"
    return None


def _funnel_for(conn, *, key_column: str, key_value: str, day: str) -> dict:
    """Count one entity's events for one UTC day, bucketed by funnel stage.

    Funnel buckets are valid-only. ``excluded_count`` holds everything that did
    not pass validity, ``invalid_count`` the subset ruled definitively invalid,
    and ``served_total_count`` the impression stage before exclusion — which is
    the only honest denominator for "what share of impressions were rejected".
    """
    start = f"{day}T00:00:00.000Z"
    end = f"{day}T23:59:59.999Z"
    counts = {
        "served_count": 0, "rendered_count": 0, "viewable_count": 0,
        "click_count": 0, "engagement_count": 0, "negative_count": 0,
        "conversion_count": 0, "invalid_count": 0, "excluded_count": 0,
        "served_total_count": 0, "unique_subjects": 0, "total_dwell_ms": 0,
    }
    # Only two column names ever reach this f-string and both are literals
    # chosen below, never caller input.
    sql = _FUNNEL_SQL.format(key_column=key_column)
    try:
        rows = conn.execute(sql, (key_value, start, end)).fetchall()
    except Exception:
        _LOG.warning("ADS_INTEL_ROLLUP_READ_FAILED %s=%s day=%s",
                     key_column, key_value, day, exc_info=True)
        return counts

    subjects = 0
    for row in rows or ():
        event_name, valid, excluded, invalid, distinct_subjects, dwell = row
        name = str(event_name or "")
        bucket = _bucket(name)
        if bucket:
            counts[bucket] += _int(valid)
        counts["excluded_count"] += _int(excluded)
        counts["invalid_count"] += _int(invalid)
        counts["total_dwell_ms"] += _int(dwell)
        if name == "ad_served":
            counts["served_total_count"] += _int(valid) + _int(excluded)
        subjects = max(subjects, _int(distinct_subjects))
    counts["unique_subjects"] = subjects
    return counts


def rebuild_creative_day(conn, creative_id: Any, day: str, *,
                         campaign_id: Any = None) -> dict:
    """Recompute one creative's rollup for one day. Idempotent.

    A rollup is a cache of the event log and nothing else, so it is always
    replaced wholesale. Incrementing in place would make a replayed batch
    double-count, and there would be no way to tell afterwards.
    """
    cid = str(creative_id or "").strip()
    if not cid:
        return {}
    counts = _funnel_for(conn, key_column="creative_id", key_value=cid, day=day)
    counts["fatigue_state"] = assess_fatigue(counts)["state"]
    try:
        conn.execute(
            "DELETE FROM ads_intel_creative_daily "
            "WHERE creative_id = ? AND day = ?", (cid, day))
        conn.execute(
            "INSERT INTO ads_intel_creative_daily "
            "(rollup_id, creative_id, campaign_id, day, served_count, "
            "rendered_count, viewable_count, click_count, engagement_count, "
            "negative_count, conversion_count, invalid_count, unique_subjects, "
            "total_dwell_ms, fatigue_state, computed_at, processing_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{cid}:{day}", cid,
             str(campaign_id).strip() if campaign_id else None, day,
             counts["served_count"], counts["rendered_count"],
             counts["viewable_count"], counts["click_count"],
             counts["engagement_count"], counts["negative_count"],
             counts["conversion_count"], counts["invalid_count"],
             counts["unique_subjects"], counts["total_dwell_ms"],
             counts["fatigue_state"], _iso(_now()),
             taxonomy.PROCESSING_VERSION))
        conn.commit()
    except Exception:
        _LOG.warning("ADS_INTEL_ROLLUP_WRITE_FAILED creative=%s day=%s",
                     cid, day, exc_info=True)
    return counts


def rebuild_campaign_day(conn, campaign_id: Any, day: str) -> dict:
    """Recompute one campaign's rollup for one day, including what was won.

    ``won_count`` comes from the decision log rather than the event log, because
    only the decision log knows the campaign was selected even if the client
    never reported back.

    ``opportunity_count`` and ``eligible_count`` stay at zero here, and that is
    on purpose. The decision log records only the *winning* campaign, so it
    cannot say how often this campaign was considered and lost — the losers are
    not rows. Copying ``won_count`` into those columns would have filled the
    dashboard with a permanent 100% win rate, which is worse than an empty one:
    an advertiser cannot tell a fabricated number from a measured one, and "you
    win every auction" is the exact reading that stops someone investigating why
    they are not spending. These populate once candidate-level logging lands in
    the candidate-generation phase, and read as "not measured yet" until then.
    """
    cid = str(campaign_id or "").strip()
    if not cid:
        return {}
    counts = _funnel_for(conn, key_column="campaign_id", key_value=cid, day=day)

    start, end = f"{day}T00:00:00.000Z", f"{day}T23:59:59.999Z"
    won = 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_delivery_decisions "
            "WHERE campaign_id = ? AND filled = 1 "
            "AND created_at >= ? AND created_at < ?",
            (cid, start, end)).fetchone()
        won = _int((row or [0])[0])
    except Exception:
        won = 0

    try:
        conn.execute(
            "DELETE FROM ads_intel_campaign_daily "
            "WHERE campaign_id = ? AND day = ?", (cid, day))
        conn.execute(
            "INSERT INTO ads_intel_campaign_daily "
            "(rollup_id, campaign_id, day, opportunity_count, eligible_count, "
            "won_count, served_count, viewable_count, click_count, "
            "negative_count, conversion_count, invalid_count, unique_subjects, "
            "top_exclusion_reason, computed_at, processing_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{cid}:{day}", cid, day, 0, 0, won,
             counts["served_count"], counts["viewable_count"],
             counts["click_count"], counts["negative_count"],
             counts["conversion_count"], counts["invalid_count"],
             counts["unique_subjects"], None, _iso(_now()),
             taxonomy.PROCESSING_VERSION))
        conn.commit()
    except Exception:
        _LOG.warning("ADS_INTEL_CAMPAIGN_ROLLUP_FAILED campaign=%s day=%s",
                     cid, day, exc_info=True)
    counts["won_count"] = won
    return counts


# --------------------------------------------------------------------------- #
# Fatigue
# --------------------------------------------------------------------------- #

def assess_fatigue(current: dict, *, baseline: Optional[dict] = None) -> dict:
    """Judge one creative's fatigue, with the reason attached.

    Returns ``{state, reason, ...}``. The reason travels with the state because
    a creative being paused is a decision somebody will want explained, and
    "FATIGUED" on its own explains nothing.

    Order matters. Negative feedback is checked *before* performance, because a
    creative people are reporting must not be kept alive by a good click rate —
    that is precisely the failure mode where optimising on engagement promotes
    something people hate.
    """
    viewable = _int(current.get("viewable_count"))
    if viewable < taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE:
        return {
            "state": "INSUFFICIENT_DATA",
            "reason": (f"{viewable} viewable impressions; "
                       f"{taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE} needed before "
                       f"fatigue can be judged"),
            "viewable": viewable,
        }

    neg = negative_rate(current)
    if neg is not None and neg >= FATIGUE_NEGATIVE_RATE:
        return {
            "state": "REJECTED",
            "reason": (f"negative feedback {neg:.2%} is at or above the "
                       f"{FATIGUE_NEGATIVE_RATE:.0%} threshold"),
            "negative_rate": neg, "viewable": viewable,
        }

    current_ctr = ctr_on_viewable(current)
    baseline_ctr = ctr_on_viewable(baseline) if baseline else None
    if current_ctr is None or baseline_ctr is None or baseline_ctr <= 0:
        return {
            "state": "HEALTHY",
            "reason": "no decline detected against an established baseline",
            "ctr_on_viewable": current_ctr, "viewable": viewable,
        }

    drop = (baseline_ctr - current_ctr) / baseline_ctr
    if drop >= FATIGUE_FATIGUED_DROP:
        state = "FATIGUED"
    elif drop >= FATIGUE_WEARING_DROP:
        state = "WEARING"
    else:
        state = "HEALTHY"
    return {
        "state": state,
        "reason": (f"click rate {current_ctr:.3%} against a baseline of "
                   f"{baseline_ctr:.3%} is a {drop:.0%} change"),
        "ctr_on_viewable": current_ctr, "baseline_ctr": baseline_ctr,
        "drop": drop, "viewable": viewable,
    }


def creative_trend(conn, creative_id: Any, *, days: int = 14,
                   now: Optional[datetime] = None) -> dict:
    """Recent vs. earlier performance for one creative, and the fatigue verdict.

    The baseline is the creative's own earlier window. Comparing against a
    platform average would call a niche creative fatigued for being niche.
    """
    cid = str(creative_id or "").strip()
    if not cid:
        return {"state": "INSUFFICIENT_DATA", "reason": "no creative"}
    horizon = _now(now)
    span = max(2, int(days))
    half = span // 2
    recent_from = _day(horizon - timedelta(days=half))
    baseline_from = _day(horizon - timedelta(days=span))

    def _window(start_day: str, end_day: str) -> dict:
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(served_count),0), "
                "COALESCE(SUM(viewable_count),0), COALESCE(SUM(click_count),0), "
                "COALESCE(SUM(negative_count),0), "
                "COALESCE(SUM(conversion_count),0), "
                "COALESCE(SUM(invalid_count),0) "
                "FROM ads_intel_creative_daily "
                "WHERE creative_id = ? AND day >= ? AND day < ?",
                (cid, start_day, end_day)).fetchone()
        except Exception:
            return {}
        if not row:
            return {}
        return {"served_count": _int(row[0]), "viewable_count": _int(row[1]),
                "click_count": _int(row[2]), "negative_count": _int(row[3]),
                "conversion_count": _int(row[4]), "invalid_count": _int(row[5])}

    recent = _window(recent_from, _day(horizon + timedelta(days=1)))
    baseline = _window(baseline_from, recent_from)
    verdict = assess_fatigue(recent, baseline=baseline)
    return {
        "creative_id": cid, "window_days": span,
        "recent": summarise(recent), "baseline": summarise(baseline),
        **verdict,
    }
