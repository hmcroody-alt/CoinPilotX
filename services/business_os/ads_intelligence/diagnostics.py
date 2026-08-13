"""Campaign diagnosis — one ranked answer to "why isn't this working?".

An advertiser whose campaign is not delivering is currently told nothing, and
in the absence of a reason they do the thing that always feels productive:
raise the budget. If the actual cause was an unverified account or a creative
still in review, the extra budget buys nothing and the platform has taken money
for a problem it could have named.

So this module produces *findings*, and a finding is only worth having if it
has all four of these:

1. a **cause** stated in the advertiser's vocabulary, not the system's
2. the **evidence** it was drawn from, so the advertiser can check it
3. **who can fix it** — them, the platform, or nobody
4. an honest **confidence**, so a guess is not presented as a measurement

Ordering is the whole product
------------------------------
Three findings in the wrong order are worse than one finding. A campaign that
is both paused for review and has a narrow audience should hear about the
review first, because fixing the audience while the campaign cannot serve
changes nothing and teaches the advertiser that our advice does not work.
``SEVERITY`` encodes that: blocking causes outrank efficiency causes, always.

Thin data produces no diagnosis
--------------------------------
Below ``MIN_DECISIONS_FOR_DIAGNOSIS`` opportunities the honest finding is "this
has not run enough to say", and it is returned *as a finding* rather than as an
empty list. An empty list reads as "nothing is wrong", which is a diagnosis,
and it is one we have not earned.

This module reads. It has no write path, changes no setting, and moves no
money — see ``recommendations.py`` for why the acting half is kept separate.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import attribution, invalid_traffic, pacing, performance, taxonomy

_LOG = logging.getLogger(__name__)

#: Higher blocks more. A campaign that cannot serve at all has nothing to learn
#: from advice about its click rate.
SEVERITY = {"blocking": 3, "limiting": 2, "efficiency": 1, "informational": 0}

#: Who can act. Naming the platform where the platform is at fault matters: an
#: advertiser told to "improve your creative" when the real cause is a review
#: backlog will change a creative that was never the problem.
ACTOR_ADVERTISER = "advertiser"
ACTOR_PLATFORM = "platform"
ACTOR_NOBODY = "nobody"

#: Fewer opportunities than this and every rate is noise.
MIN_DECISIONS_FOR_DIAGNOSIS = 100

#: A campaign winning less than this share of the opportunities it was a
#: candidate for is being held back by something, not merely losing.
LOW_WIN_RATE = 0.05

#: Invalid traffic above this share is worth telling an advertiser about even
#: though they were never charged for it, because it changes how they read
#: their own numbers.
NOTABLE_INVALID_RATE = 0.05


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


def finding(code: str, *, severity: str, headline: str, detail: str,
            evidence: dict, actor: str, confidence: str = "measured") -> dict:
    """Build one finding. Every field is required for a reason — see the docstring.

    ``confidence`` is ``measured`` when the finding is read straight from
    recorded counts and ``inferred`` when it is the most likely of several
    explanations. Collapsing the two would let a guess inherit the authority of
    a count.
    """
    return {"code": code, "severity": severity,
            "severity_rank": SEVERITY.get(severity, 0), "headline": headline,
            "detail": detail, "evidence": evidence, "fixable_by": actor,
            "confidence": confidence}


# --------------------------------------------------------------------------- #
# Evidence gathering
# --------------------------------------------------------------------------- #

def _decision_evidence(conn, campaign_id: str) -> dict:
    """Opportunities this campaign won, and why others went unfilled.

    Opportunities the campaign *competed in and lost* are not recorded — the
    decision log stores the winner only. That limit is reported rather than
    papered over, because a win rate computed against a denominator we do not
    have would be a fabricated number in the one report that exists to stop
    advertisers guessing.
    """
    won = total = 0
    no_fill: dict = {}
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(filled), 0) "
            "FROM ads_intel_delivery_decisions WHERE campaign_id = ?",
            (campaign_id,)).fetchone()
        won = _int((row or [0, 0])[1])
        row = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_delivery_decisions").fetchone()
        total = _int((row or [0])[0])
        for reason, count in ((r[0], _int(r[1])) for r in _rows(conn.execute(
                "SELECT no_fill_reason, COUNT(*) FROM "
                "ads_intel_delivery_decisions WHERE filled = 0 "
                "AND no_fill_reason IS NOT NULL GROUP BY no_fill_reason"))):
            no_fill[reason] = count
    except Exception:
        _LOG.warning("ADS_INTEL_DIAG_DECISIONS_FAILED campaign=%s", campaign_id,
                     exc_info=True)
    return {"won": won, "platform_opportunities": total,
            "platform_no_fill": no_fill,
            "competed_in_is_not_recorded": True}


def _eligibility_evidence(conn, campaign_id: str) -> dict:
    """The canonical readiness verdict, asked of the canonical service.

    This module does not re-implement eligibility. A second opinion on whether
    a campaign may serve is a second answer, and delivery would keep using the
    first one while the advertiser read the second.
    """
    try:
        from services.business_os.advertising import readiness as _readiness
    except Exception:
        return {"available": False}
    for attempt in ("campaign_readiness", "get_campaign_readiness",
                    "evaluate_campaign", "readiness_for_campaign"):
        fn = getattr(_readiness, attempt, None)
        if not callable(fn):
            continue
        try:
            return {"available": True, "readiness": fn(campaign_id, conn=conn)}
        except TypeError:
            try:
                return {"available": True, "readiness": fn(campaign_id)}
            except Exception:
                continue
        except Exception:
            continue
    return {"available": False}


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

def _thin_data_finding(decisions: dict) -> Optional[dict]:
    won = _int(decisions.get("won"))
    if won >= MIN_DECISIONS_FOR_DIAGNOSIS:
        return None
    return finding(
        "NOT_ENOUGH_DELIVERY", severity="informational",
        headline="This campaign has not run enough to diagnose yet",
        detail=(f"It has been served {won} times. Below "
                f"{MIN_DECISIONS_FOR_DIAGNOSIS} we would be reading noise, and "
                f"a confident wrong answer is worse than waiting."),
        evidence={"served": won,
                  "needed": MIN_DECISIONS_FOR_DIAGNOSIS},
        actor=ACTOR_NOBODY, confidence="measured")


#: no-fill reason → (headline, who can actually do something about it). Kept at
#: module level rather than inside the function so the recommendation rules and
#: the tests read the same list; a second copy of this mapping would drift and
#: the drift would show up as a campaign told nothing about why it is dark.
NOT_DELIVERING_CAUSES = {
    "CAMPAIGN_IN_REVIEW": ("Your campaign is still in review",
                           ACTOR_PLATFORM),
    "ACCOUNT_UNVERIFIED": ("Your advertiser account is not verified yet",
                           ACTOR_ADVERTISER),
    "POLICY_BLOCKED": ("This campaign is blocked on policy grounds",
                       ACTOR_ADVERTISER),
    "BUDGET_EXHAUSTED": ("This campaign has spent its budget",
                         ACTOR_ADVERTISER),
    "WALLET_EMPTY": ("Your advertising balance is empty", ACTOR_ADVERTISER),
    "SCHEDULE_INACTIVE": ("This campaign is outside its schedule",
                          ACTOR_ADVERTISER),
    "CREATIVE_UNAVAILABLE": ("A creative could not be loaded",
                             ACTOR_ADVERTISER),
    "AUDIENCE_MISMATCH": ("Your audience did not match anybody in these "
                          "placements", ACTOR_ADVERTISER),
}


def _not_delivering_findings(decisions: dict) -> list:
    """A campaign serving nothing at all, with the platform's reasons attached."""
    if _int(decisions.get("won")) > 0:
        return []
    no_fill = decisions.get("platform_no_fill") or {}
    found = []
    for code, count in sorted(no_fill.items(), key=lambda kv: -_int(kv[1])):
        if code not in NOT_DELIVERING_CAUSES or not _int(count):
            continue
        headline, actor = NOT_DELIVERING_CAUSES[code]
        found.append(finding(
            f"NOT_DELIVERING_{code}", severity="blocking", headline=headline,
            detail=(f"This campaign has never been served. Across the platform "
                    f"{count} opportunities went unfilled for this reason, "
                    f"which is the most likely cause."),
            evidence={"served": 0, "platform_no_fill_reason": code,
                      "platform_no_fill_count": _int(count)},
            actor=actor,
            # Platform-wide no-fill counts are not per-campaign, so this names
            # the most likely cause rather than a proven one.
            confidence="inferred"))
    if not found:
        found.append(finding(
            "NOT_DELIVERING_UNKNOWN", severity="blocking",
            headline="This campaign is not being served",
            detail=("It has never won an opportunity and we do not yet have a "
                    "recorded reason. Support can look at this directly."),
            evidence={"served": 0}, actor=ACTOR_PLATFORM, confidence="measured"))
    return found[:2]


def _pacing_findings(pace: dict) -> list:
    state = str(pace.get("state") or "")
    if state == "EXHAUSTED":
        return [finding(
            "BUDGET_EXHAUSTED", severity="blocking",
            headline="Today's budget is spent",
            detail=("Delivery has stopped for the rest of the day. This is the "
                    "budget doing its job, not a fault."),
            evidence={"observed_cents": pace.get("observed_cents")},
            actor=ACTOR_ADVERTISER)]
    if state in ("OVERPACING", "LIMITED"):
        return [finding(
            "PACING_THROTTLED", severity="limiting",
            headline="Delivery is being slowed to make the budget last",
            detail=pacing.explain(pace),
            evidence={"state": state, "ratio": pace.get("ratio"),
                      "throttle": pace.get("throttle"),
                      "target_cents": pace.get("target_cents"),
                      "observed_cents": pace.get("observed_cents")},
            actor=ACTOR_ADVERTISER)]
    if state == "UNDERPACING":
        return [finding(
            "UNDERSPENDING", severity="efficiency",
            headline="This campaign is not spending its daily budget",
            detail=("Delivery is unrestricted, so the limit is how many "
                    "matching opportunities exist rather than your budget. A "
                    "wider audience or more placements would find more."),
            evidence={"ratio": pace.get("ratio"),
                      "target_cents": pace.get("target_cents"),
                      "observed_cents": pace.get("observed_cents")},
            actor=ACTOR_ADVERTISER)]
    return []


_FATIGUE_HEADLINES = {
    "REJECTED": "People are actively telling us they do not want this ad",
    "FATIGUED": "This creative has worn out with the people who see it",
    "WEARING": "This creative is starting to wear out",
}
_FATIGUE_SEVERITY = {"REJECTED": "blocking", "FATIGUED": "limiting",
                     "WEARING": "efficiency"}


def _fatigue_findings(trend: dict) -> list:
    """Fatigue as reported by the canonical assessment, not re-derived here.

    ``REJECTED`` is blocking rather than merely limiting. A creative people are
    hiding and reporting is not an efficiency problem to tune around — it is
    one the ranker already drops, so advice about anything else would be advice
    about a creative that is not being shown.
    """
    state = str(trend.get("state") or "")
    if state not in _FATIGUE_HEADLINES:
        return []
    return [finding(
        f"CREATIVE_{state}", severity=_FATIGUE_SEVERITY[state],
        headline=_FATIGUE_HEADLINES[state],
        detail=(trend.get("reason")
                or "Its click rate has fallen against its own earlier "
                   "performance, which is a statement about this audience "
                   "rather than about the creative's age."),
        evidence={k: trend.get(k) for k in
                  ("state", "ctr_on_viewable", "baseline_ctr", "drop",
                   "negative_rate", "viewable") if k in trend},
        actor=ACTOR_ADVERTISER)]


def _funnel_findings(funnel_result: dict) -> list:
    drop = funnel_result.get("biggest_drop")
    if not drop:
        return []
    rate = drop.get("survived_rate")
    if isinstance(rate, float) and rate >= 0.5 and drop["step"] != "click":
        # Half or more carried through: this is the largest step but not a
        # problem, and reporting it as one sends somebody to fix nothing.
        return []
    return [finding(
        f"FUNNEL_DROP_{str(drop['step']).upper()}", severity="efficiency",
        headline=f"Most people are lost between {drop['previous_step']} and "
                 f"{drop['step']}",
        detail=attribution.explain_funnel(funnel_result),
        evidence={"step": drop["step"], "previous_step": drop["previous_step"],
                  "lost": drop["lost"], "survived_rate": rate},
        actor=ACTOR_ADVERTISER)]


def _invalid_traffic_findings(summary: dict) -> list:
    rate = summary.get("invalid_rate")
    if not isinstance(rate, float) or rate < NOTABLE_INVALID_RATE:
        return []
    return [finding(
        "INVALID_TRAFFIC_EXCLUDED", severity="informational",
        headline="Some activity on this campaign was excluded from billing",
        detail=(f"{rate:.0%} of billable events were held back as invalid or "
                f"unverified. You were not charged for them, and they are not "
                f"in your rates — this is here so your own numbers and ours "
                f"agree."),
        evidence={"invalid_rate": rate, "excluded": summary.get("excluded"),
                  "total": summary.get("total"),
                  "by_reason": summary.get("by_reason")},
        actor=ACTOR_NOBODY)]


# --------------------------------------------------------------------------- #
# Diagnosis
# --------------------------------------------------------------------------- #

def diagnose(conn, campaign_id: Any, *, creative_id: Any = None,
             daily_budget_cents: Any = None, now=None) -> dict:
    """Every finding for one campaign, worst first.

    Each evidence source is gathered inside its own try/except: a campaign
    whose fatigue read fails should still be told that its account is
    unverified. A diagnosis that refuses to answer because one of six inputs
    was unavailable is the least useful possible behaviour for a report whose
    entire job is to answer when something is wrong.
    """
    campaign = str(campaign_id or "").strip()
    if not campaign:
        return {"campaign_id": None, "findings": [], "degraded": True,
                "version": taxonomy.PROCESSING_VERSION}

    degraded = []
    decisions = _decision_evidence(conn, campaign)

    def _safe(label, fn, default):
        try:
            return fn()
        except Exception:
            _LOG.warning("ADS_INTEL_DIAG_SOURCE_FAILED source=%s campaign=%s",
                         label, campaign, exc_info=True)
            degraded.append(label)
            return default

    pace = _safe("pacing", lambda: pacing.state_for(
        campaign, conn=conn, daily_budget_cents=daily_budget_cents, now=now),
        {})
    funnel_result = _safe("funnel",
                          lambda: attribution.funnel(conn, campaign), {})
    ivt = _safe("invalid_traffic",
                lambda: invalid_traffic.summarise(conn, campaign_id=campaign),
                {})
    trend = {}
    if creative_id:
        trend = _safe("fatigue", lambda: performance.creative_trend(
            conn, creative_id, now=now), {})

    findings: list = []
    if _int(decisions.get("won")) == 0:
        findings.extend(_not_delivering_findings(decisions))
    else:
        thin = _thin_data_finding(decisions)
        if thin:
            findings.append(thin)
        findings.extend(_pacing_findings(pace))
        findings.extend(_fatigue_findings(trend))
        findings.extend(_funnel_findings(funnel_result))
        findings.extend(_invalid_traffic_findings(ivt))

    findings.sort(key=lambda f: -f["severity_rank"])
    return {
        "campaign_id": campaign,
        "delivering": _int(decisions.get("won")) > 0,
        "served": _int(decisions.get("won")),
        "findings": findings,
        "primary": findings[0] if findings else None,
        "sources_unavailable": degraded,
        "degraded": bool(degraded),
        "version": taxonomy.PROCESSING_VERSION,
    }


def summary_sentence(result: dict) -> str:
    """The one line to put at the top of a campaign screen."""
    primary = result.get("primary")
    if not primary:
        return "Nothing is holding this campaign back that we can see."
    return f"{primary['headline']}. {primary['detail']}"
