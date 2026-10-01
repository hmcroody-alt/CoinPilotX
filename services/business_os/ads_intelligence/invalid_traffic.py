"""Invalid traffic — a one-way ratchet on billability, never a money authority.

Every ad platform has traffic that should not be charged for: a click the
viewer did not make, an impression rendered off-screen, a script hammering a
creative, an event replayed by a retry loop. The question is not whether to
detect it — it is what the detector is allowed to *do*.

The ratchet
-----------
This module can move an event from ``valid`` toward ``suspect`` or ``invalid``,
and it can clear ``billable``. It cannot do the reverse. Not "does not" — the
UPDATE statement writes ``billable = 0`` as a literal and guards on the current
validity, so there is no argument that makes it grant billability.

That direction matters more than the accuracy of any individual rule. A
detector that can flip an event back to valid is a detector that can be talked
into billing for fraud, either by a bug or by somebody with database access and
a quota. A detector that can only ever subtract is safe to run automatically,
which in turn means it can run often, which is what makes it useful.

It never touches the money
--------------------------
The canonical billing path owns ``business_os_ad_impression_events``. This
module does not write to that table, does not issue credits, and does not
reverse a charge. It does two things instead:

* ``screen`` is available *before* an event is stored, so the cheap rules run
  in time to matter.
* ``credit_candidates`` lists events the canonical path already billed which
  this layer later ruled invalid. It produces a list for reconciliation to act
  on; issuing the credit is a financial operation and belongs to the financial
  system, which has the ledger, the audit trail and the authority.

Silently reversing charges from an analytics module would be the single most
dangerous thing in this codebase: it would put an automated heuristic in
control of an advertiser's balance with no ledger entry explaining why.

Suspect is a real state, not a soft invalid
-------------------------------------------
``suspect`` means "excluded from billing and from every computed rate, but the
event is intact and the ruling is reviewable". Rules with genuine false
positives — velocity, rapid repeat — land here rather than on ``invalid``,
because a burst of clicks is sometimes a person on a train with bad signal
retrying, and destroying that distinction to make a dashboard tidier is how a
legitimate advertiser gets told their traffic was fraudulent.

Rules are deterministic and versioned
-------------------------------------
No model, no score, no threshold that drifts. Every ruling is reproducible from
the stored events and ``FRAUD_RULE_VERSION``, so "why was this ruled invalid in
March" has an answer that does not depend on what the system has learned since.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: More than this many billable-candidate events from one subject on one
#: creative inside the window is not a person looking at an ad.
RAPID_REPEAT_WINDOW_SECONDS = 60
RAPID_REPEAT_LIMIT = 3

#: A whole-subject ceiling across every campaign. Catches a script that spreads
#: itself thinly enough to stay under the per-creative limit.
VELOCITY_WINDOW_SECONDS = 3600
VELOCITY_LIMIT = 120

#: A click this soon after the impression it belongs to is faster than a person
#: can see an ad and decide to act on it.
MIN_PLAUSIBLE_CLICK_DELAY_MS = 300

#: Ordered from most to least severe. A row is only ever moved *up* this list.
_SEVERITY = {"valid": 0, "suspect": 1, "under_review": 1, "invalid": 2}

#: Which rules produce which state. Rules that can be wrong about a real person
#: mark suspect; rules that describe a structural impossibility mark invalid.
RULE_STATES = {
    "RAPID_REPEAT": "suspect",
    "VELOCITY_ANOMALY": "suspect",
    "IMPOSSIBLE_SEQUENCE": "invalid",
    "UNKNOWN_DECISION": "invalid",
    "DECISION_MISMATCH": "invalid",
    "IMPLAUSIBLE_TIMESTAMP": "invalid",
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


def is_more_severe(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a downgrade relative to ``current``.

    The whole ratchet reduces to this predicate, so it is a single small
    function that can be tested exhaustively rather than a condition inlined in
    three places that drift apart.
    """
    return _SEVERITY.get(candidate, 0) > _SEVERITY.get(current, 0)


def state_for_reason(reason: str) -> str:
    """The validity state a rule produces. Unknown rules mark suspect.

    Defaulting an unrecognised reason to ``suspect`` rather than ``invalid``
    means a rule added without a state entry withholds billing (safe) instead
    of declaring fraud on evidence nobody classified (not safe).
    """
    return RULE_STATES.get(str(reason or "").strip().upper(), "suspect")


# --------------------------------------------------------------------------- #
# Synchronous screening
# --------------------------------------------------------------------------- #

def screen(conn, payload: dict, *, now: Optional[datetime] = None) -> dict:
    """Rule on one event before it is stored. Returns ``{validity, reason}``.

    Cheap enough to run inline at ingest. Returns ``valid`` on any failure: a
    screening error must not reject real traffic, and the post-hoc sweep will
    catch anything missed here on its next pass.
    """
    if not isinstance(payload, dict):
        return {"validity": "valid", "reason": None}
    name = str(payload.get("event_name") or "").strip()
    subject = str(payload.get("subject_ref") or "").strip()
    creative = str(payload.get("creative_id") or "").strip()

    if name not in taxonomy.BILLABLE_CANDIDATE_EVENTS:
        # Nothing that cannot be billed is worth screening synchronously. It is
        # still swept later, where the cost of a scan is not on the hot path.
        return {"validity": "valid", "reason": None}

    delay = payload.get("since_impression_ms")
    if delay is not None and 0 <= _int(delay) < MIN_PLAUSIBLE_CLICK_DELAY_MS:
        return {"validity": "invalid", "reason": "IMPOSSIBLE_SEQUENCE",
                "detail": f"acted {_int(delay)}ms after the impression"}

    if not conn or not subject or not creative:
        return {"validity": "valid", "reason": None}

    since = _iso(_now(now) - timedelta(seconds=RAPID_REPEAT_WINDOW_SECONDS))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events "
            "WHERE subject_ref = ? AND creative_id = ? AND event_name = ? "
            "AND occurred_at >= ? AND validity = 'valid'",
            (subject, creative, name, since)).fetchone()
    except Exception:
        _LOG.warning("ADS_INTEL_IVT_SCREEN_FAILED event=%s", name, exc_info=True)
        return {"validity": "valid", "reason": None}

    recent = _int((row or [0])[0])
    if recent >= RAPID_REPEAT_LIMIT:
        return {"validity": "suspect", "reason": "RAPID_REPEAT",
                "detail": (f"{recent} of the same event in "
                           f"{RAPID_REPEAT_WINDOW_SECONDS}s")}
    return {"validity": "valid", "reason": None}


# --------------------------------------------------------------------------- #
# Post-hoc sweep
# --------------------------------------------------------------------------- #

def _apply(conn, event_id: str, validity: str, reason: str,
           detail: str = "") -> bool:
    """Downgrade one event. The only write in this module.

    Three separate guards, because this is the statement that decides whether
    somebody is charged:

    * ``billable = 0`` is a literal, so no code path here can grant billing.
    * ``validity`` is only assigned inside the caller's severity check.
    * The ``WHERE`` clause repeats the severity check in SQL, so two sweeps
      racing cannot walk a row back up.

    The rule version and the evidence are *appended* to ``quality_notes`` rather
    than replacing it, so a row that was already flagged at ingest keeps both
    findings. Without the version stored per row, re-reading an old ruling means
    guessing which thresholds were in force at the time.
    """
    allowed = [s for s, rank in _SEVERITY.items()
               if rank < _SEVERITY.get(validity, 0)]
    if not allowed:
        return False
    placeholders = ", ".join("?" for _ in allowed)
    note = f"ivt v{taxonomy.FRAUD_RULE_VERSION} {reason}"
    if detail:
        note = f"{note}: {detail}"
    try:
        conn.execute(
            "UPDATE ads_intel_events SET validity = ?, invalid_reason = ?, "
            "billable = 0, "
            "quality_notes = COALESCE(quality_notes || ' | ', '') || ? "
            f"WHERE event_id = ? AND validity IN ({placeholders})",
            (validity, reason, note, event_id, *allowed))
        return True
    except Exception:
        _LOG.warning("ADS_INTEL_IVT_WRITE_FAILED event=%s", event_id,
                     exc_info=True)
        return False


def _sequence_violations(conn, since: str) -> list:
    """Billable events whose decision does not exist or does not match.

    A click naming a decision that was never recorded cannot be joined to
    anything, so it is not merely unverifiable — it is a claim about a delivery
    the server has no memory of making.
    """
    found = []
    rows = _rows(conn.execute(
        "SELECT e.event_id, e.decision_id, e.campaign_id, e.creative_id, "
        "       d.decision_id, d.campaign_id, d.creative_id "
        "FROM ads_intel_events e "
        "LEFT JOIN ads_intel_delivery_decisions d "
        "       ON d.decision_id = e.decision_id "
        "WHERE e.occurred_at >= ? AND e.validity = 'valid' "
        "AND e.event_name IN ('ad_viewable', 'ad_click')", (since,)))
    for row in rows:
        event_id, decision_id, camp, creative, d_id, d_camp, d_creative = row[:7]
        if not decision_id or d_id is None:
            found.append((event_id, "UNKNOWN_DECISION",
                          f"decision {decision_id!r} was never recorded"))
            continue
        if camp and d_camp and str(camp) != str(d_camp):
            found.append((event_id, "DECISION_MISMATCH",
                          f"event says campaign {camp}, decision says {d_camp}"))
            continue
        if creative and d_creative and str(creative) != str(d_creative):
            found.append((event_id, "DECISION_MISMATCH",
                          f"event says creative {creative}, decision says "
                          f"{d_creative}"))
    return found


def _clicks_without_an_impression(conn, since: str) -> list:
    """Clicks on a decision that produced no delivery event.

    Somebody clicked an ad that was never shown. This is the shape of a replayed
    or forged click, and it is structurally impossible for a real viewer.
    """
    rows = _rows(conn.execute(
        "SELECT c.event_id, c.decision_id FROM ads_intel_events c "
        "WHERE c.event_name = 'ad_click' AND c.occurred_at >= ? "
        "AND c.validity = 'valid' AND c.decision_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM ads_intel_events s "
        "                WHERE s.decision_id = c.decision_id "
        "                AND s.event_name IN ('ad_served', 'ad_rendered', "
        "                                     'ad_viewable'))", (since,)))
    return [(r[0], "IMPOSSIBLE_SEQUENCE",
             f"click on decision {r[1]} which was never displayed")
            for r in rows]


def _velocity_outliers(conn, since: str) -> list:
    """Subjects generating more billable events per hour than a person can.

    Marked suspect rather than invalid. The threshold is a judgement about
    plausibility, and a judgement about plausibility should not be the thing
    that publicly labels an account fraudulent.
    """
    rows = _rows(conn.execute(
        "SELECT subject_ref, COUNT(*) FROM ads_intel_events "
        "WHERE occurred_at >= ? AND validity = 'valid' "
        "AND event_name IN ('ad_viewable', 'ad_click') "
        "AND subject_ref IS NOT NULL "
        "GROUP BY subject_ref HAVING COUNT(*) > ?", (since, VELOCITY_LIMIT)))
    found = []
    for subject, count in ((r[0], _int(r[1])) for r in rows):
        detail = (f"{count} billable events in "
                  f"{VELOCITY_WINDOW_SECONDS // 3600}h")
        for event in _rows(conn.execute(
                "SELECT event_id FROM ads_intel_events "
                "WHERE subject_ref = ? AND occurred_at >= ? "
                "AND validity = 'valid' "
                "AND event_name IN ('ad_viewable', 'ad_click')",
                (subject, since))):
            found.append((event[0], "VELOCITY_ANOMALY", detail))
    return found


def sweep(conn, *, window_seconds: int = VELOCITY_WINDOW_SECONDS,
          now: Optional[datetime] = None) -> dict:
    """Run every post-hoc rule over a recent window.

    Idempotent: each rule only selects rows that are still ``valid``, so a
    second sweep over the same window finds nothing left to do and reports zero
    rather than re-counting its own previous work.
    """
    since = _iso(_now(now) - timedelta(seconds=max(_int(window_seconds), 60)))
    findings, by_reason = [], {}
    for rule in (_sequence_violations, _clicks_without_an_impression,
                 _velocity_outliers):
        try:
            findings.extend(rule(conn, since))
        except Exception:
            _LOG.warning("ADS_INTEL_IVT_RULE_FAILED rule=%s",
                         getattr(rule, "__name__", "?"), exc_info=True)

    applied, seen = 0, set()
    for event_id, reason, detail in findings:
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        if _apply(conn, str(event_id), state_for_reason(reason), reason, detail):
            applied += 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
    try:
        conn.commit()
    except Exception:
        _LOG.warning("ADS_INTEL_IVT_COMMIT_FAILED", exc_info=True)
        return {"scanned_since": since, "reclassified": 0, "by_reason": {},
                "rule_version": taxonomy.FRAUD_RULE_VERSION, "committed": False}
    return {"scanned_since": since, "reclassified": applied,
            "by_reason": by_reason,
            "rule_version": taxonomy.FRAUD_RULE_VERSION, "committed": True}


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def credit_candidates(conn, *, campaign_id: Any = None, since: Any = None,
                      limit: int = 500) -> list:
    """Events the canonical path billed which this layer later ruled invalid.

    Returns a list for a human or the financial system to act on. It issues no
    credit, writes no ledger entry, and touches no balance — see the module
    docstring on why an analytics heuristic must not be able to move money.
    """
    clauses = ["e.validity <> 'valid'",
               "e.event_name IN ('ad_viewable', 'ad_click')"]
    params: list = []
    if campaign_id:
        clauses.append("e.campaign_id = ?")
        params.append(str(campaign_id))
    if since:
        clauses.append("e.occurred_at >= ?")
        params.append(str(since))
    try:
        rows = _rows(conn.execute(
            "SELECT e.event_id, e.campaign_id, e.creative_id, e.event_name, "
            "       e.occurred_at, e.validity, e.invalid_reason "
            "FROM ads_intel_events e WHERE " + " AND ".join(clauses) +
            " ORDER BY e.occurred_at DESC LIMIT ?",
            (*params, max(_int(limit), 1))))
    except Exception:
        _LOG.warning("ADS_INTEL_IVT_CREDIT_READ_FAILED", exc_info=True)
        return []
    return [{"event_id": r[0], "campaign_id": r[1], "creative_id": r[2],
             "event_name": r[3], "occurred_at": r[4], "validity": r[5],
             "reason": r[6], "action": "review_for_credit"} for r in rows]


def summarise(conn, *, campaign_id: Any = None, since: Any = None) -> dict:
    """Invalid-traffic counts for one campaign or the whole platform.

    Reports the rate alongside the counts, because "418 invalid events" means
    nothing without knowing whether that is out of five hundred or five million.
    """
    clauses = ["event_name IN ('ad_viewable', 'ad_click')"]
    params: list = []
    if campaign_id:
        clauses.append("campaign_id = ?")
        params.append(str(campaign_id))
    if since:
        clauses.append("occurred_at >= ?")
        params.append(str(since))
    try:
        rows = _rows(conn.execute(
            "SELECT validity, invalid_reason, COUNT(*) FROM ads_intel_events "
            "WHERE " + " AND ".join(clauses) +
            " GROUP BY validity, invalid_reason", tuple(params)))
    except Exception:
        _LOG.warning("ADS_INTEL_IVT_SUMMARY_FAILED", exc_info=True)
        return {"total": 0, "valid": 0, "excluded": 0, "invalid_rate": None,
                "by_reason": {}, "degraded": True}

    total = valid = excluded = 0
    by_reason: dict = {}
    for validity, reason, count in ((r[0], r[1], _int(r[2])) for r in rows):
        total += count
        if validity == "valid":
            valid += count
            continue
        excluded += count
        key = reason or "UNSPECIFIED"
        by_reason[key] = by_reason.get(key, 0) + count
    rate = (excluded / float(total)) if total else None
    return {"total": total, "valid": valid, "excluded": excluded,
            "invalid_rate": rate, "by_reason": by_reason,
            "rule_version": taxonomy.FRAUD_RULE_VERSION, "degraded": False}


def explain(finding: dict) -> str:
    """One sentence for an advertiser looking at their invalid-traffic figure."""
    reason = str(finding.get("reason") or "").upper()
    sentences = {
        "RAPID_REPEAT": ("The same viewer produced several identical events "
                         "within a minute, which we do not charge for."),
        "VELOCITY_ANOMALY": ("A viewer produced far more ad events in an hour "
                             "than a person plausibly could, so their activity "
                             "was held back from billing pending review."),
        "IMPOSSIBLE_SEQUENCE": ("A click was recorded for an ad that was never "
                                "displayed, so it cannot be a real click."),
        "UNKNOWN_DECISION": ("An event referenced a delivery we have no record "
                             "of making, so it could not be verified."),
        "DECISION_MISMATCH": ("An event disagreed with the delivery it claimed "
                              "to belong to, so it was not counted."),
    }
    return sentences.get(reason,
                         "This activity was excluded from billing because it "
                         "could not be verified as genuine.")
