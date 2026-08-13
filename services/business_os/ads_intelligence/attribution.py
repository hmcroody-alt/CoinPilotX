"""Attribution and the delivery funnel — credit only where it can be shown.

Attribution is where advertising reporting usually stops being measurement and
starts being marketing. The temptations are well known and all of them inflate
the same number:

**View-through attribution** credits a conversion to an ad the person saw and
did not click. It is absent here, and its absence is a design decision rather
than a missing feature. A view-through conversion is indistinguishable from a
person who was going to buy anyway and happened to scroll past an ad, and on a
feed product almost everybody scrolls past almost every ad. Adding it would
roughly multiply reported conversions without adding a single new fact about
whether advertising caused anything.

**Modelled conversions** fill gaps with a statistical estimate. An estimate
presented in the same column as a measured count, in the same font, is not
reporting. If a conversion cannot be observed it is reported as unattributed.

**Cross-device** and **fingerprinting** are absent because the identity graph
needed to support them is exactly the thing the privacy model refuses to build.

What remains is last-click within a window: a conversion is credited to the
most recent valid click by the same subject on the same campaign inside
``CLICK_ATTRIBUTION_WINDOW_HOURS``. It undercounts. Undercounting is the
correct direction to be wrong in, because the alternative asks an advertiser to
spend real money against a number nobody can verify.

Value is the advertiser's, and stays labelled as theirs
--------------------------------------------------------
``value_cents`` on a conversion is a figure the advertiser's own system
reported. This module carries it through and names it ``reported_value_cents``
everywhere so it cannot be mistaken for a platform-verified amount, and it does
not divide it by spend. A ROAS computed from self-reported revenue and platform
spend looks authoritative and is not, and the mission is explicit that a fake
ROAS is worse than no ROAS.

One conversion, one click
--------------------------
Each conversion attributes to at most one click. Counting a conversion once per
matching click is how a campaign that a person clicked four times reports four
purchases from one order.

The funnel measures where people leave
---------------------------------------
``funnel`` walks opportunity → served → rendered → viewable → click →
conversion and names the largest proportional drop. That single named step is
the entire diagnostic value: "0.4% CTR" prompts nothing, while "68% of your
served ads were never viewable" points straight at a placement problem.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: The model, stated as data so a report can print it beside the numbers.
ATTRIBUTION_MODEL = "last_click"
VIEW_THROUGH_SUPPORTED = False

#: Funnel steps in order. Each entry is (label, event names that satisfy it).
FUNNEL_STEPS = (
    ("opportunity", ("ad_opportunity_created",)),
    ("served", ("ad_served",)),
    ("rendered", ("ad_rendered",)),
    ("viewable", ("ad_viewable",)),
    ("click", ("ad_click",)),
    ("conversion", tuple(taxonomy.CONVERSION_EVENTS)),
)

#: What a drop at each step usually means. Deliberately phrased as the most
#: likely cause rather than a certainty — a diagnosis that overstates its
#: confidence sends somebody to rebuild a creative when their budget was the
#: problem.
STEP_DIAGNOSIS = {
    "served": "opportunities existed but this campaign rarely won them — "
              "usually targeting, budget pacing or a frequency cap",
    "rendered": "the ad was chosen but the client did not draw it — usually a "
                "media loading problem",
    "viewable": "the ad was drawn but scrolled past before it counted as seen "
                "— usually placement or creative position",
    "click": "people saw the ad and did not act on it — usually the creative "
             "or the match between ad and audience",
    "conversion": "people clicked and did not complete — usually the landing "
                  "destination rather than the ad",
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


def _rows(cursor) -> list:
    try:
        return list(cursor.fetchall() or [])
    except Exception:
        return []


def window_start(occurred_at: Any, *, hours: Optional[int] = None) -> Optional[str]:
    """The earliest click timestamp that could still claim this conversion."""
    moment = _parse(occurred_at)
    if moment is None:
        return None
    span = taxonomy.CLICK_ATTRIBUTION_WINDOW_HOURS if hours is None else hours
    return _iso(moment - timedelta(hours=max(_int(span), 0)))


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

def attribute(conn, *, subject_ref: Any, campaign_id: Any, occurred_at: Any,
              hours: Optional[int] = None) -> dict:
    """Find the click a conversion belongs to, if there is one.

    Returns ``{attributed, click_event_id, clicked_at, model, reason}``. An
    unattributed conversion is a normal, reportable outcome — it means the
    conversion happened and this campaign cannot be shown to have caused it,
    which is a different and more honest statement than zero conversions.

    Only ``valid`` clicks qualify. A conversion credited to a click the invalid
    traffic engine rejected would let fraudulent clicks manufacture the very
    outcome an advertiser optimises toward.
    """
    subject = str(subject_ref or "").strip()
    campaign = str(campaign_id or "").strip()
    if not subject or not campaign:
        return {"attributed": False, "click_event_id": None,
                "clicked_at": None, "model": ATTRIBUTION_MODEL,
                "reason": "NO_SUBJECT_OR_CAMPAIGN"}

    at = _parse(occurred_at)
    start = window_start(at, hours=hours)
    if at is None or start is None:
        return {"attributed": False, "click_event_id": None,
                "clicked_at": None, "model": ATTRIBUTION_MODEL,
                "reason": "UNPARSEABLE_TIMESTAMP"}

    try:
        row = conn.execute(
            "SELECT event_id, occurred_at FROM ads_intel_events "
            "WHERE event_name = 'ad_click' AND validity = 'valid' "
            "AND subject_ref = ? AND campaign_id = ? "
            "AND occurred_at >= ? AND occurred_at <= ? "
            "ORDER BY occurred_at DESC LIMIT 1",
            (subject, campaign, start, _iso(at))).fetchone()
    except Exception:
        _LOG.warning("ADS_INTEL_ATTRIBUTION_READ_FAILED campaign=%s", campaign,
                     exc_info=True)
        return {"attributed": False, "click_event_id": None,
                "clicked_at": None, "model": ATTRIBUTION_MODEL,
                "reason": "READ_FAILED", "degraded": True}

    if not row:
        return {"attributed": False, "click_event_id": None,
                "clicked_at": None, "model": ATTRIBUTION_MODEL,
                "reason": "NO_CLICK_IN_WINDOW",
                "window_hours": taxonomy.CLICK_ATTRIBUTION_WINDOW_HOURS
                if hours is None else _int(hours)}

    return {"attributed": True, "click_event_id": row[0],
            "clicked_at": row[1], "model": ATTRIBUTION_MODEL,
            "reason": None,
            "attribution_version": taxonomy.ATTRIBUTION_VERSION}


def campaign_conversions(conn, campaign_id: Any, *, since: Any = None,
                         until: Any = None, hours: Optional[int] = None) -> dict:
    """Attributed conversions for one campaign, with the unattributed shown too.

    The unattributed count is reported rather than dropped. Hiding it makes an
    attribution rate of 12% look like a conversion count of 12%, and an
    advertiser deciding whether to keep spending deserves to know which one
    they are looking at.

    Each click may carry at most one conversion of a given kind, so a person who
    clicked four times and bought once contributes one purchase.
    """
    campaign = str(campaign_id or "").strip()
    if not campaign:
        return _empty_conversions()

    clauses = ["campaign_id = ?", "event_name IN ({})".format(
        ", ".join("?" for _ in taxonomy.CONVERSION_EVENTS))]
    params: list = [campaign, *taxonomy.CONVERSION_EVENTS]
    if since:
        clauses.append("occurred_at >= ?")
        params.append(str(since))
    if until:
        clauses.append("occurred_at < ?")
        params.append(str(until))
    # Invalid conversions are excluded from the numerator here for the same
    # reason invalid clicks are excluded from attribution.
    clauses.append("validity = 'valid'")

    try:
        rows = _rows(conn.execute(
            "SELECT event_id, event_name, subject_ref, occurred_at, value_cents "
            "FROM ads_intel_events WHERE " + " AND ".join(clauses) +
            " ORDER BY occurred_at ASC", tuple(params)))
    except Exception:
        _LOG.warning("ADS_INTEL_CONVERSIONS_READ_FAILED campaign=%s", campaign,
                     exc_info=True)
        result = _empty_conversions()
        result["degraded"] = True
        return result

    attributed, unattributed, value = 0, 0, 0
    by_event: dict = {}
    claimed = set()
    for event_id, name, subject, occurred, value_cents in rows:
        verdict = attribute(conn, subject_ref=subject, campaign_id=campaign,
                            occurred_at=occurred, hours=hours)
        if not verdict.get("attributed"):
            unattributed += 1
            continue
        # One conversion of each kind per click. A second purchase on the same
        # click is the same order being reported twice far more often than it is
        # a person buying twice from one visit.
        key = (verdict.get("click_event_id"), name)
        if key in claimed:
            continue
        claimed.add(key)
        attributed += 1
        value += _int(value_cents)
        by_event[name] = by_event.get(name, 0) + 1

    total = attributed + unattributed
    return {
        "campaign_id": campaign,
        "attributed_conversions": attributed,
        "unattributed_conversions": unattributed,
        "total_conversions_observed": total,
        "attribution_rate": (attributed / float(total)) if total else None,
        "reported_value_cents": value,
        "value_is_advertiser_reported": True,
        "by_event": by_event,
        "model": ATTRIBUTION_MODEL,
        "view_through": VIEW_THROUGH_SUPPORTED,
        "window_hours": taxonomy.CLICK_ATTRIBUTION_WINDOW_HOURS
        if hours is None else _int(hours),
        "attribution_version": taxonomy.ATTRIBUTION_VERSION,
        "degraded": False,
    }


def _empty_conversions() -> dict:
    return {"campaign_id": None, "attributed_conversions": 0,
            "unattributed_conversions": 0, "total_conversions_observed": 0,
            "attribution_rate": None, "reported_value_cents": 0,
            "value_is_advertiser_reported": True, "by_event": {},
            "model": ATTRIBUTION_MODEL, "view_through": VIEW_THROUGH_SUPPORTED,
            "window_hours": taxonomy.CLICK_ATTRIBUTION_WINDOW_HOURS,
            "attribution_version": taxonomy.ATTRIBUTION_VERSION,
            "degraded": False}


# --------------------------------------------------------------------------- #
# Funnel
# --------------------------------------------------------------------------- #

def funnel(conn, campaign_id: Any, *, since: Any = None,
           until: Any = None) -> dict:
    """Step counts and the largest proportional drop.

    Opportunities come from the decision log rather than the event log, because
    an opportunity this campaign lost produces no event yet is exactly the
    number an advertiser who is not delivering needs to see.
    """
    campaign = str(campaign_id or "").strip()
    if not campaign:
        return {"campaign_id": None, "steps": [], "biggest_drop": None,
                "degraded": True}

    counts = {label: 0 for label, _ in FUNNEL_STEPS}
    clauses, params = ["campaign_id = ?", "validity = 'valid'"], [campaign]
    if since:
        clauses.append("occurred_at >= ?")
        params.append(str(since))
    if until:
        clauses.append("occurred_at < ?")
        params.append(str(until))
    try:
        for name, count in ((r[0], _int(r[1])) for r in _rows(conn.execute(
                "SELECT event_name, COUNT(*) FROM ads_intel_events "
                "WHERE " + " AND ".join(clauses) + " GROUP BY event_name",
                tuple(params)))):
            for label, names in FUNNEL_STEPS:
                if name in names:
                    counts[label] += count
    except Exception:
        _LOG.warning("ADS_INTEL_FUNNEL_READ_FAILED campaign=%s", campaign,
                     exc_info=True)
        return {"campaign_id": campaign, "steps": [], "biggest_drop": None,
                "degraded": True}

    try:
        dec_clauses, dec_params = ["campaign_id = ?"], [campaign]
        if since:
            dec_clauses.append("occurred_at >= ?")
            dec_params.append(str(since))
        if until:
            dec_clauses.append("occurred_at < ?")
            dec_params.append(str(until))
        row = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_delivery_decisions WHERE "
            + " AND ".join(dec_clauses), tuple(dec_params)).fetchone()
        counts["opportunity"] = max(counts["opportunity"], _int((row or [0])[0]))
    except Exception:
        _LOG.warning("ADS_INTEL_FUNNEL_DECISIONS_FAILED campaign=%s", campaign,
                     exc_info=True)

    steps, previous, previous_label = [], None, None
    for label, _names in FUNNEL_STEPS:
        count = counts[label]
        rate = None if not previous else count / float(previous)
        steps.append({"step": label, "count": count,
                      "from_previous_rate": rate,
                      "previous_step": previous_label,
                      "dropped": None if previous is None
                      else max(previous - count, 0)})
        previous, previous_label = count, label

    measurable = [s for s in steps
                  if s["from_previous_rate"] is not None and s["dropped"]]
    biggest = None
    if measurable:
        worst = max(measurable, key=lambda s: s["dropped"])
        biggest = {
            "step": worst["step"],
            "previous_step": worst["previous_step"],
            "lost": worst["dropped"],
            "survived_rate": worst["from_previous_rate"],
            "likely_cause": STEP_DIAGNOSIS.get(worst["step"],
                                               "no diagnosis for this step"),
        }
    return {"campaign_id": campaign, "steps": steps, "biggest_drop": biggest,
            "degraded": False}


def explain_funnel(result: dict) -> str:
    """One sentence naming where a campaign is losing people."""
    drop = result.get("biggest_drop")
    if not drop:
        return ("There is not enough delivery yet to show where this campaign "
                "loses people.")
    rate = drop.get("survived_rate")
    share = f"{rate:.0%}" if isinstance(rate, float) else "an unknown share"
    return (f"The largest fall is between {drop['previous_step']} and "
            f"{drop['step']}: {share} carried through, losing "
            f"{drop['lost']}. Most often this is {drop['likely_cause']}.")


def explain_attribution(result: dict) -> str:
    """A sentence that states the model rather than implying certainty."""
    attributed = _int(result.get("attributed_conversions"))
    unattributed = _int(result.get("unattributed_conversions"))
    if not attributed and not unattributed:
        return "No conversions have been recorded for this campaign yet."
    window = _int(result.get("window_hours")) // 24
    base = (f"{attributed} conversions are attributed to a click on this "
            f"campaign within {window} days.")
    if unattributed:
        base += (f" A further {unattributed} conversions happened without a "
                 f"click we can trace to this campaign, so they are not "
                 f"credited to it.")
    return base + (" Conversion values are as reported by your own systems; we "
                   "do not verify them or divide them by your spend.")
