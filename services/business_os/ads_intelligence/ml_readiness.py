"""Whether the data is good enough to learn from — checked before, not after.

Named ``ml_readiness`` rather than ``readiness`` because
``advertising/readiness.py`` already exists and answers a different question:
whether a *campaign* is ready to run. This one answers whether the *data* is
ready to train on.

The whole point is to be able to say no
----------------------------------------
Every gate below can fail, and a failing gate blocks training rather than
lowering a threshold. This is the module's only real function: a readiness check
that always passes is a rubber stamp, and the failure it exists to prevent is
the specific, expensive one where a model is trained on six weeks of data that
happened to contain a two-week logging outage, ships, performs worse than the
deterministic ranker it replaced, and takes a quarter to diagnose because
everybody assumed the data was fine.

What is actually checked
-------------------------
Volume, class balance, time coverage, the share of records that had to be
repaired, and label integrity. The last is the one most often skipped and most
often fatal: if clicks are attributed by a rule that changed halfway through the
window, half the labels mean something different from the other half, and the
model learns the rule change.

Nothing here trains anything
-----------------------------
There is no model, no fit, no predict. This module reports; a training pipeline
that does not exist yet is expected to refuse to start if ``ready`` is False.
Until that pipeline exists, the honest status of ML in this system is "not yet",
and this module is what makes that a measured statement rather than an opinion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: Minimums below which a model would be fitting noise. Deliberately blunt round
#: numbers: precision here would imply a confidence about sample size that
#: nobody has, and a threshold of 47,318 invites arguing about the threshold
#: instead of about the data.
MIN_LABELLED_EXAMPLES = 50_000
MIN_POSITIVE_EXAMPLES = 500
MIN_DISTINCT_DAYS = 28
MIN_DISTINCT_CAMPAIGNS = 20

#: A dataset more imbalanced than this needs handling a naive trainer will not
#: do, so it is reported as not ready rather than quietly passed on.
MIN_POSITIVE_RATE = 0.0005

#: Above this share of repaired or suspect records, the data describes our
#: ingest problems as much as it describes advertising.
MAX_REPAIRED_SHARE = 0.10

#: A gap this long inside the window means an outage, not a quiet weekend.
MAX_GAP_DAYS = 2

GATE_VOLUME = "volume"
GATE_POSITIVES = "positive_examples"
GATE_BALANCE = "class_balance"
GATE_COVERAGE = "time_coverage"
GATE_CONTINUITY = "continuity"
GATE_DIVERSITY = "campaign_diversity"
GATE_INTEGRITY = "data_integrity"
GATE_LABEL_STABILITY = "label_stability"

ALL_GATES = (GATE_VOLUME, GATE_POSITIVES, GATE_BALANCE, GATE_COVERAGE,
             GATE_CONTINUITY, GATE_DIVERSITY, GATE_INTEGRITY,
             GATE_LABEL_STABILITY)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def gate(name: str, *, passed: bool, detail: str, evidence: dict) -> dict:
    return {"gate": name, "passed": bool(passed), "detail": detail,
            "evidence": evidence}


def _dataset_stats(conn, start: str, end: str) -> dict:
    """One pass over the window. Only valid, billable-eligible delivery counts.

    Invalid traffic is excluded from training data for the same reason it is
    excluded from billing: a model trained to predict clicks that includes bot
    clicks learns to find bots.
    """
    row = conn.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN event_name = 'ad_click' THEN 1 ELSE 0 END), "
        "COUNT(DISTINCT SUBSTR(occurred_at, 1, 10)), "
        "COUNT(DISTINCT campaign_id), "
        "SUM(CASE WHEN quality_status != 'ok' THEN 1 ELSE 0 END) "
        "FROM ads_intel_events "
        "WHERE validity = 'valid' AND occurred_at >= ? AND occurred_at < ? "
        "AND event_name IN ('ad_viewable', 'ad_click')",
        (start, end)).fetchone() or (0, 0, 0, 0, 0)
    return {"examples": _int(row[0]), "positives": _int(row[1]),
            "distinct_days": _int(row[2]), "distinct_campaigns": _int(row[3]),
            "repaired": _int(row[4])}


def _largest_gap_days(conn, start: str, end: str) -> Optional[int]:
    """The longest run of consecutive days with no delivery at all.

    Returns None if the days cannot be read. A logging outage inside a training
    window is invisible in every aggregate statistic — totals still look
    healthy — which is exactly why it needs its own check.
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT SUBSTR(occurred_at, 1, 10) FROM ads_intel_events "
            "WHERE validity = 'valid' AND occurred_at >= ? AND occurred_at < ? "
            "ORDER BY 1", (start, end)).fetchall() or []
    except Exception:
        return None
    days = []
    for row in rows:
        try:
            days.append(datetime.strptime(row[0], "%Y-%m-%d"))
        except (TypeError, ValueError):
            continue
    if len(days) < 2:
        return None
    return max((days[i + 1] - days[i]).days - 1 for i in range(len(days) - 1))


def _label_stability(conn, start: str, end: str) -> dict:
    """Whether the labels mean the same thing across the whole window.

    Checked by looking at how many distinct processing versions produced the
    records. More than one means the rules changed mid-window, so the earlier
    and later labels are not the same quantity and a model will learn the
    change rather than the behaviour.
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT processing_version FROM ads_intel_events "
            "WHERE validity = 'valid' AND occurred_at >= ? AND occurred_at < ?",
            (start, end)).fetchall() or []
    except Exception:
        return {"versions": None, "degraded": True}
    return {"versions": sorted({_int(r[0]) for r in rows}), "degraded": False}


def assess(conn, *, window_days: int = 90,
           now: Optional[datetime] = None) -> dict:
    """Can we train on the last ``window_days``? ``{ready, gates, blocking}``.

    ``ready`` is True only when every gate passes. There is no partial credit
    and no override argument, because an override parameter is how a gate that
    is inconvenient once becomes a gate that is always overridden.
    """
    at = now or _now()
    start = _iso(at - timedelta(days=max(_int(window_days) or 90, 1)))
    end = _iso(at)

    try:
        stats = _dataset_stats(conn, start, end)
    except Exception:
        _LOG.warning("ADS_INTEL_READINESS_READ_FAILED", exc_info=True)
        return {"ready": False, "degraded": True, "gates": [],
                "blocking": ["dataset_unreadable"],
                "window_days": window_days,
                "reason": "The training dataset could not be read.",
                "version": taxonomy.RECOMMENDATION_VERSION}

    examples = stats["examples"]
    positives = stats["positives"]
    positive_rate = (positives / examples) if examples else 0.0
    repaired_share = (stats["repaired"] / examples) if examples else 0.0
    gap = _largest_gap_days(conn, start, end)
    labels = _label_stability(conn, start, end)
    versions = labels.get("versions")

    gates = [
        gate(GATE_VOLUME, passed=examples >= MIN_LABELLED_EXAMPLES,
             detail=(f"{examples:,} labelled examples "
                     f"(need {MIN_LABELLED_EXAMPLES:,})."),
             evidence={"examples": examples,
                       "required": MIN_LABELLED_EXAMPLES}),
        gate(GATE_POSITIVES, passed=positives >= MIN_POSITIVE_EXAMPLES,
             detail=(f"{positives:,} clicks to learn from "
                     f"(need {MIN_POSITIVE_EXAMPLES:,})."),
             evidence={"positives": positives,
                       "required": MIN_POSITIVE_EXAMPLES}),
        gate(GATE_BALANCE, passed=positive_rate >= MIN_POSITIVE_RATE,
             detail=(f"Positive rate {positive_rate:.4%}. Below "
                     f"{MIN_POSITIVE_RATE:.2%} a naive trainer learns to "
                     f"always predict 'no click'."),
             evidence={"positive_rate": round(positive_rate, 6),
                       "required": MIN_POSITIVE_RATE}),
        gate(GATE_COVERAGE, passed=stats["distinct_days"] >= MIN_DISTINCT_DAYS,
             detail=(f"{stats['distinct_days']} days with delivery "
                     f"(need {MIN_DISTINCT_DAYS}, so weekly seasonality is "
                     f"represented)."),
             evidence={"distinct_days": stats["distinct_days"],
                       "required": MIN_DISTINCT_DAYS}),
        gate(GATE_CONTINUITY, passed=(gap is None or gap <= MAX_GAP_DAYS),
             detail=("No delivery gap longer than "
                     f"{MAX_GAP_DAYS} days." if (gap is None or gap <= MAX_GAP_DAYS)
                     else f"A {gap}-day gap in the window suggests a logging "
                          f"outage rather than quiet trading."),
             evidence={"largest_gap_days": gap, "allowed": MAX_GAP_DAYS}),
        gate(GATE_DIVERSITY,
             passed=stats["distinct_campaigns"] >= MIN_DISTINCT_CAMPAIGNS,
             detail=(f"{stats['distinct_campaigns']} campaigns represented "
                     f"(need {MIN_DISTINCT_CAMPAIGNS}, or the model learns a "
                     f"handful of advertisers rather than advertising)."),
             evidence={"distinct_campaigns": stats["distinct_campaigns"],
                       "required": MIN_DISTINCT_CAMPAIGNS}),
        gate(GATE_INTEGRITY, passed=repaired_share <= MAX_REPAIRED_SHARE,
             detail=(f"{repaired_share:.1%} of records needed repair at ingest "
                     f"(allowed {MAX_REPAIRED_SHARE:.0%})."),
             evidence={"repaired_share": round(repaired_share, 4),
                       "allowed": MAX_REPAIRED_SHARE}),
        gate(GATE_LABEL_STABILITY,
             passed=bool(versions) and len(versions) == 1,
             detail=("Labels were produced by one processing version."
                     if versions and len(versions) == 1 else
                     f"Labels span processing versions {versions}: the rules "
                     f"changed mid-window, so early and late labels are not "
                     f"the same quantity."),
             evidence={"processing_versions": versions}),
    ]

    blocking = [g["gate"] for g in gates if not g["passed"]]
    return {
        "ready": not blocking,
        "degraded": False,
        "window_days": window_days,
        "gates": gates,
        "blocking": blocking,
        "reason": (
            "The dataset meets every gate for training."
            if not blocking else
            f"Not ready: {', '.join(blocking)}."),
        "trains_anything": False,
        "version": taxonomy.RECOMMENDATION_VERSION,
    }


def explain(result: dict) -> str:
    """What a human needs to read to know whether to press go."""
    if not result:
        return "Readiness has not been assessed."
    if result.get("degraded"):
        return ("We could not read the training dataset, so readiness is "
                "unknown — which is not the same as ready.")
    if result.get("ready"):
        return (f"The last {result.get('window_days')} days meet every gate "
                f"for training.")
    failed = [g for g in result.get("gates") or [] if not g["passed"]]
    lines = [f"Not ready to train on the last {result.get('window_days')} days:"]
    lines += [f"• {g['detail']}" for g in failed]
    return "\n".join(lines)
