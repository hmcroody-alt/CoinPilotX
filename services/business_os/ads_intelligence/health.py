"""Anomaly detection and advertiser health — advisory, and only advisory.

This module can raise an alarm. It cannot act on one. Stopping delivery is the
guardrail module's job in the canonical advertising package, under audit and
with an operator behind it; the separation exists because an anomaly detector
that can halt accounts will eventually halt a legitimate one at 3am over a
holiday traffic spike, and nobody will be able to explain why.

Anomalies are measured against the account's own past
-----------------------------------------------------
Not against a global threshold. "Spent more than $500 today" is not an anomaly
for an advertiser who spends $500 every day, and it is an emergency for one who
has never spent more than $20. Every detector here compares a recent window to
the same account's earlier baseline and reports the ratio, so the number that
triggered the alarm is the number an operator sees.

The consequence is that a brand-new account cannot have an anomaly, because it
has no baseline. That is correct and deliberate: the alternative is inventing a
baseline from other advertisers, which reports "unusual for someone else".

Health is a set of named factors, never a single opaque number
---------------------------------------------------------------
``account_health`` returns the factors and their states. It does compute a
score, because a list of eleven factors does not sort a queue — but the score is
derived from the factors in the open, every factor is returned alongside it, and
nothing in this codebase gates delivery on it. A number that decides whether
somebody may advertise, and which nobody can decompose, is a credit score with
no regulator and no appeals process.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: A recent window is compared against a baseline this many times longer. Long
#: enough that one busy day does not become "normal", short enough that a
#: genuine change of behaviour is reflected within a week.
RECENT_HOURS = 24
BASELINE_MULTIPLE = 7

#: Ratios past which a change stops being noise. Asymmetric on purpose: a
#: collapse to a third of normal delivery is worth a look, while spend has to
#: triple before it is remarkable, because spend rises for legitimate reasons
#: (a new campaign, a raised budget) far more often than delivery collapses for
#: legitimate ones.
SPEND_SPIKE_RATIO = 3.0
DELIVERY_COLLAPSE_RATIO = 0.33
CTR_COLLAPSE_RATIO = 0.40

#: Below this, a window is too thin to compare and no anomaly is reported.
MIN_BASELINE_EVENTS = 200
MIN_RECENT_EVENTS = 50

#: Health factor states, worst first. Used for ordering, not arithmetic.
STATE_CRITICAL = "critical"
STATE_WARNING = "warning"
STATE_OK = "ok"
STATE_UNKNOWN = "unknown"

_STATE_RANK = {STATE_CRITICAL: 0, STATE_WARNING: 1, STATE_OK: 2,
               STATE_UNKNOWN: 3}

#: Points removed from a starting 100 for each factor in each state. Published
#: as data so the score is reproducible by hand from the factors returned.
_PENALTY = {
    STATE_CRITICAL: 30,
    STATE_WARNING: 10,
    STATE_OK: 0,
    STATE_UNKNOWN: 0,   # not knowing is not a fault of the advertiser's
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ratio(recent: float, baseline: float) -> Optional[float]:
    """Recent rate over baseline rate, or None when there is no baseline.

    Returns None rather than infinity when the baseline is zero. "Infinitely
    more than nothing" is not a finding an operator can act on, and it sorts to
    the top of every list forever.
    """
    if baseline <= 0:
        return None
    return recent / baseline


def anomaly(code: str, *, severity: str, headline: str, detail: str,
            evidence: dict) -> dict:
    """One alarm. ``action_taken`` is always none — this module cannot act."""
    return {
        "code": code,
        "severity": severity,
        "headline": headline,
        "detail": detail,
        "evidence": evidence,
        "action_taken": "none",
        "requires_human": True,
        "detected_at": _iso(_now()),
        "version": taxonomy.RECOMMENDATION_VERSION,
    }


def _windows(now: Optional[datetime] = None) -> tuple:
    """(recent_start, baseline_start, at) as ISO strings."""
    at = now or _now()
    recent_start = at - timedelta(hours=RECENT_HOURS)
    baseline_start = at - timedelta(hours=RECENT_HOURS * (BASELINE_MULTIPLE + 1))
    return _iso(recent_start), _iso(baseline_start), _iso(at)


def _counts(conn, advertiser_user_id: str, start: str, end: str) -> dict:
    """Delivery counts for one account over one window, from the event log."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN e.event_name = 'ad_viewable' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN e.event_name = 'ad_click' THEN 1 ELSE 0 END), "
        "COUNT(*) "
        "FROM ads_intel_events e "
        "JOIN business_os_ad_campaigns c ON c.campaign_id = e.campaign_id "
        "WHERE c.advertiser_user_id = ? AND e.validity = 'valid' "
        "AND e.occurred_at >= ? AND e.occurred_at < ?",
        (advertiser_user_id, start, end)).fetchone() or (0, 0, 0)
    return {"viewable": _int(row[0]), "clicks": _int(row[1]),
            "events": _int(row[2])}


def _spend_cents(conn, advertiser_user_id: str, start: str, end: str) -> int:
    """Cents charged in a window, from the canonical billing record."""
    row = conn.execute(
        "SELECT COALESCE(SUM(total_amount_cents), 0) "
        "FROM business_os_ad_billing_events "
        "WHERE advertiser_user_id = ? AND created_at >= ? AND created_at < ? "
        "AND billing_status != 'failed'",
        (advertiser_user_id, start, end)).fetchone()
    return _int((row or [0])[0])


def detect(conn, advertiser_user_id: Any, *, now: Optional[datetime] = None) -> dict:
    """Anomalies for one account, each against that account's own baseline.

    Returns ``{anomalies, comparable, ...}``. ``comparable`` is False when there
    is not enough history to say anything, and in that case the anomaly list is
    empty rather than speculative.
    """
    advertiser = str(advertiser_user_id or "").strip()
    result = {"advertiser_user_id": advertiser, "anomalies": [],
              "comparable": False, "degraded": False}
    if not advertiser:
        return result

    recent_start, baseline_start, at = _windows(now)
    try:
        recent = _counts(conn, advertiser, recent_start, at)
        baseline = _counts(conn, advertiser, baseline_start, recent_start)
        recent_spend = _spend_cents(conn, advertiser, recent_start, at)
        baseline_spend = _spend_cents(conn, advertiser, baseline_start,
                                      recent_start)
    except Exception:
        _LOG.warning("ADS_INTEL_ANOMALY_READ_FAILED", exc_info=True)
        result["degraded"] = True
        return result

    # Per-day rates, so the two windows are comparable despite different lengths.
    baseline_days = float(BASELINE_MULTIPLE)
    base_rate = {k: v / baseline_days for k, v in baseline.items()}
    base_spend_rate = baseline_spend / baseline_days

    if baseline["events"] < MIN_BASELINE_EVENTS:
        # No baseline means no anomaly. Comparing against other advertisers
        # would report what is unusual for somebody else.
        result["evidence"] = {"baseline_events": baseline["events"],
                              "needed": MIN_BASELINE_EVENTS}
        return result
    result["comparable"] = True

    found = []

    spend_ratio = _ratio(float(recent_spend), base_spend_rate)
    if spend_ratio is not None and spend_ratio >= SPEND_SPIKE_RATIO:
        found.append(anomaly(
            "SPEND_SPIKE", severity="warning",
            headline="This account is spending much faster than usual",
            detail=(f"Spend in the last {RECENT_HOURS} hours is "
                    f"{spend_ratio:.1f}x its recent daily average. This is "
                    f"often a deliberate change; it is flagged so it is never "
                    f"a surprise."),
            evidence={"recent_spend_cents": recent_spend,
                      "baseline_daily_spend_cents": round(base_spend_rate),
                      "ratio": round(spend_ratio, 2)}))

    delivery_ratio = _ratio(float(recent["viewable"]), base_rate["viewable"])
    if delivery_ratio is not None and delivery_ratio <= DELIVERY_COLLAPSE_RATIO:
        found.append(anomaly(
            "DELIVERY_COLLAPSE", severity="critical",
            headline="This account's delivery has dropped sharply",
            detail=(f"Viewable impressions in the last {RECENT_HOURS} hours "
                    f"are {delivery_ratio:.0%} of the recent daily average. "
                    f"This is usually a budget, policy or creative problem "
                    f"rather than a change in demand."),
            evidence={"recent_viewable": recent["viewable"],
                      "baseline_daily_viewable": round(base_rate["viewable"]),
                      "ratio": round(delivery_ratio, 2)}))

    # CTR is only compared when both windows delivered enough to have one.
    if (recent["viewable"] >= MIN_RECENT_EVENTS
            and baseline["viewable"] >= MIN_BASELINE_EVENTS):
        recent_ctr = recent["clicks"] / recent["viewable"]
        baseline_ctr = baseline["clicks"] / baseline["viewable"]
        ctr_ratio = _ratio(recent_ctr, baseline_ctr)
        if ctr_ratio is not None and ctr_ratio <= CTR_COLLAPSE_RATIO:
            found.append(anomaly(
                "ENGAGEMENT_COLLAPSE", severity="warning",
                headline="People are engaging with these ads much less",
                detail=(f"Click rate on viewable impressions is "
                        f"{ctr_ratio:.0%} of its recent level. Creative "
                        f"fatigue is the most common cause."),
                evidence={"recent_ctr": round(recent_ctr, 5),
                          "baseline_ctr": round(baseline_ctr, 5),
                          "ratio": round(ctr_ratio, 2)}))

    result["anomalies"] = found
    return result


# --------------------------------------------------------------------------- #
# Account health
# --------------------------------------------------------------------------- #

def factor(name: str, *, state: str, detail: str, evidence: dict) -> dict:
    return {"factor": name, "state": state, "detail": detail,
            "evidence": evidence}


def account_health(conn, advertiser_user_id: Any, *,
                   now: Optional[datetime] = None) -> dict:
    """Named factors, their states, and a score derived from them in the open.

    Nothing in this codebase gates delivery on the score. It exists to sort an
    operator's queue, and it is returned alongside the factors that produced it
    so a support conversation can be about a specific factor rather than about
    a number nobody can decompose.
    """
    advertiser = str(advertiser_user_id or "").strip()
    factors = []

    detection = detect(conn, advertiser, now=now)
    if detection.get("degraded"):
        factors.append(factor(
            "delivery_stability", state=STATE_UNKNOWN,
            detail="We could not read this account's recent delivery.",
            evidence={"degraded": True}))
    elif not detection.get("comparable"):
        factors.append(factor(
            "delivery_stability", state=STATE_UNKNOWN,
            detail="Not enough history yet to say whether delivery is stable.",
            evidence=detection.get("evidence") or {}))
    else:
        critical = [a for a in detection["anomalies"]
                    if a["severity"] == "critical"]
        warnings = [a for a in detection["anomalies"]
                    if a["severity"] == "warning"]
        if critical:
            state, detail = STATE_CRITICAL, critical[0]["headline"]
        elif warnings:
            state, detail = STATE_WARNING, warnings[0]["headline"]
        else:
            state, detail = STATE_OK, "Delivery is behaving normally."
        factors.append(factor("delivery_stability", state=state, detail=detail,
                              evidence={"anomalies": [a["code"] for a
                                                      in detection["anomalies"]]}))

    factors.append(_invalid_traffic_factor(conn, advertiser, now=now))
    factors.append(_guardrail_factor(advertiser))

    known = [f for f in factors if f["state"] != STATE_UNKNOWN]
    score = max(0, 100 - sum(_PENALTY[f["state"]] for f in known))
    factors.sort(key=lambda f: _STATE_RANK[f["state"]])

    return {
        "advertiser_user_id": advertiser,
        "score": score if known else None,
        "score_is_advisory": True,
        "factors": factors,
        "worst_state": factors[0]["state"] if factors else STATE_UNKNOWN,
        "computed_at": _iso(now or _now()),
        "version": taxonomy.RECOMMENDATION_VERSION,
    }


def _invalid_traffic_factor(conn, advertiser: str, *, now=None) -> dict:
    """How much of this account's traffic we refused to bill for.

    A high rate is not an accusation against the advertiser — most invalid
    traffic is bots nobody invited — so this reads as information, and the
    detail says explicitly that they were not charged.
    """
    recent_start, _baseline, at = _windows(now)
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN e.validity != 'valid' THEN 1 "
            "ELSE 0 END) FROM ads_intel_events e "
            "JOIN business_os_ad_campaigns c ON c.campaign_id = e.campaign_id "
            "WHERE c.advertiser_user_id = ? AND e.occurred_at >= ? "
            "AND e.occurred_at < ?",
            (advertiser, recent_start, at)).fetchone() or (0, 0)
    except Exception:
        _LOG.warning("ADS_INTEL_HEALTH_IVT_READ_FAILED", exc_info=True)
        return factor("traffic_quality", state=STATE_UNKNOWN,
                      detail="We could not read traffic quality.",
                      evidence={"degraded": True})

    total, invalid = _int(row[0]), _int(row[1])
    if total < MIN_RECENT_EVENTS:
        return factor("traffic_quality", state=STATE_UNKNOWN,
                      detail="Not enough recent activity to assess.",
                      evidence={"events": total})
    rate = invalid / total
    state = (STATE_WARNING if rate >= 0.20
             else STATE_OK)
    return factor(
        "traffic_quality", state=state,
        detail=(f"{rate:.0%} of recent activity was excluded as invalid. You "
                f"were not charged for any of it."),
        evidence={"events": total, "invalid": invalid, "rate": round(rate, 4)})


def _guardrail_factor(advertiser: str) -> dict:
    """Whether the account is currently stopped or at its ceiling.

    Reads the canonical guardrail rather than keeping a second idea of account
    standing. Imported locally so this module has no hard dependency on the
    advertising package's import order.
    """
    try:
        from services.business_os.advertising import guardrails
        state = guardrails.check(advertiser)
    except Exception:
        return factor("account_standing", state=STATE_UNKNOWN,
                      detail="We could not read this account's standing.",
                      evidence={"degraded": True})

    if state.get("halted"):
        return factor("account_standing", state=STATE_CRITICAL,
                      detail="Delivery on this account is stopped.",
                      evidence={"reason": state.get("reason")})
    if not state.get("allowed", True):
        return factor("account_standing", state=STATE_WARNING,
                      detail=guardrails.explain(state),
                      evidence={"reason": state.get("reason")})
    return factor("account_standing", state=STATE_OK,
                  detail="This account is able to deliver.", evidence={})


def explain(result: dict) -> str:
    """The health summary, in one line, naming the worst factor rather than the
    score — because the factor is the thing somebody can act on."""
    factors = (result or {}).get("factors") or []
    if not factors:
        return "We do not have enough information about this account yet."
    worst = factors[0]
    if worst["state"] == STATE_OK:
        return "This account looks healthy."
    if worst["state"] == STATE_UNKNOWN:
        return "We do not have enough information about this account yet."
    return f"{worst['detail']} ({worst['factor'].replace('_', ' ')})"
