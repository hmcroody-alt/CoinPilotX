"""Delivery decision and no-fill recording.

The single biggest observability gap in the legacy path: ``select_ads`` returns
an empty list and no record of why. A campaign that never delivers is therefore
indistinguishable from a campaign nobody ever asked for — which is precisely the
question an advertiser asks first, and the one support cannot currently answer.

This module records one row per *opportunity*: every time a surface asked for an
ad, whether or not it got one, along with the funnel that produced the outcome.
The funnel counters are the actionable part:

    candidates=0                 -> nothing was even in scope (targeting/setup)
    candidates>0, eligible=0     -> a named filter removed them all
    eligible>0,   filled=0       -> selection or ad-load declined to fill

Stage 1 contract
----------------
**Recording must not change delivery.** This module is an observer. It never
selects, never filters, never vetoes. It is called *after* the existing
selection has already decided, and it is wrapped by :func:`record_safely` so
that a bug or a slow write here degrades to "we lost a log line" rather than
"the feed stopped serving ads". That property is worth more than any metric it
collects, so it is enforced here rather than left to each call site to remember.

The reason vocabulary
---------------------
``eligibility.evaluate`` emits implementation-shaped reasons
(``advertiser_ineligible:...``, ``campaign_schedule_inactive``). Those are the
right level of detail for a developer and the wrong one for an advertiser-facing
screen, so :func:`map_exclusion_reason` translates them into the closed
``NO_FILL_REASONS`` taxonomy. Both are kept: the taxonomy drives reporting, and
the raw counts are preserved in ``exclusion_counts_json`` for debugging.
"""

from __future__ import annotations

import json
import logging

from services import db

from . import taxonomy
from .schema import ensure_schema, new_id, utc_now_iso

#: Legacy eligibility reason -> closed no-fill taxonomy. Prefix match, longest
#: first, so `advertiser_ineligible:unverified` resolves before `advertiser_`.
_REASON_MAP = (
    ("hierarchy:", "CAMPAIGN_IN_REVIEW"),
    ("advertiser_ineligible:unverified", "ACCOUNT_UNVERIFIED"),
    ("advertiser_ineligible:suspended", "POLICY_BLOCKED"),
    ("advertiser_ineligible:", "ACCOUNT_UNVERIFIED"),
    ("placement_incompatible", "PLACEMENT_UNSUPPORTED"),
    ("placement_not_selected", "PLACEMENT_UNSUPPORTED"),
    ("audience_mismatch", "AUDIENCE_MISMATCH"),
    ("campaign_schedule_inactive", "SCHEDULE_INACTIVE"),
    ("ad_set_schedule_inactive", "SCHEDULE_INACTIVE"),
    ("frequency_cap_reached", "FREQUENCY_CAPPED"),
    ("media_unavailable", "CREATIVE_UNAVAILABLE"),
    ("destination_invalid", "CREATIVE_UNAVAILABLE"),
    ("budget", "BUDGET_EXHAUSTED"),
    ("wallet", "WALLET_EMPTY"),
    ("policy", "POLICY_BLOCKED"),
)


def map_exclusion_reason(reason) -> str:
    """Translate one eligibility reason into the closed no-fill taxonomy.

    Unrecognised reasons collapse to ``NO_ELIGIBLE_CAMPAIGN`` rather than being
    passed through. Letting an unmapped string through would quietly grow the
    vocabulary that admin screens and UNDX explanations read from, which is the
    exact drift the taxonomy module exists to prevent.
    """
    text = str(reason or "").strip().lower()
    for prefix, mapped in _REASON_MAP:
        if text.startswith(prefix):
            return mapped
    return "NO_ELIGIBLE_CAMPAIGN"


def summarise_exclusions(decisions) -> dict:
    """Count why candidates were excluded, keyed by the raw reason.

    Raw rather than mapped, because this field is for debugging a delivery
    problem; the mapped taxonomy is what reporting consumes.
    """
    counts: dict[str, int] = {}
    for decision in decisions or []:
        if decision.get("eligible"):
            continue
        for reason in decision.get("reasons") or []:
            key = str(reason)
            counts[key] = counts.get(key, 0) + 1
    return counts


def infer_no_fill_reason(decisions, *, candidate_count: int) -> str:
    """The single most representative reason an opportunity went unfilled.

    Picks the most common exclusion rather than the first: with a hundred
    candidates, the modal reason is the one worth acting on, while the first is
    an artefact of enumeration order.
    """
    if not candidate_count:
        return "NO_ELIGIBLE_CAMPAIGN"
    mapped: dict[str, int] = {}
    for decision in decisions or []:
        if decision.get("eligible"):
            continue
        for reason in decision.get("reasons") or []:
            key = map_exclusion_reason(reason)
            mapped[key] = mapped.get(key, 0) + 1
    if not mapped:
        # Candidates existed and all were eligible, yet nothing was served.
        # Selection or ad load declined; that is inventory, not targeting.
        return "INVENTORY_UNAVAILABLE"
    return max(mapped.items(), key=lambda kv: (kv[1], kv[0]))[0]


def record_decision(*, placement_key=None, surface=None, platform=None,
                    subject_ref=None, session_ref=None, selection=None,
                    winner=None, latency_ms=None, ranking_mode="legacy",
                    experiment_key=None, experiment_variant=None,
                    exploration=False, score=None, score_breakdown=None,
                    opportunity_id=None, forced_no_fill_reason=None,
                    conn=None) -> str | None:
    """Record one ad opportunity and its outcome. Returns the decision id.

    ``selection`` is the dict returned by
    ``advertising.selection.select_candidate`` — passing it whole keeps this
    call site cheap and means new funnel fields there arrive here for free.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        selection = selection or {}
        decisions = selection.get("decisions") or []
        candidate_count = int(selection.get("candidate_count")
                              or len(decisions) or 0)
        eligible_count = int(selection.get("eligible_count") or 0)

        winner = winner if winner is not None else selection.get("winner")
        filled = bool(winner)
        no_fill_reason = None
        if not filled:
            # A caller that already knows the cause (e.g. a winner whose
            # creative row could not be loaded) overrides inference, so a real
            # data-integrity fault is not filed as a generic targeting miss.
            no_fill_reason = forced_no_fill_reason or infer_no_fill_reason(
                decisions, candidate_count=candidate_count)
            if no_fill_reason not in taxonomy.NO_FILL_REASON_SET:
                no_fill_reason = "NO_ELIGIBLE_CAMPAIGN"

        decision_id = new_id()
        now_iso = utc_now_iso()
        conn.execute(
            """
            INSERT INTO ads_intel_delivery_decisions (
                decision_id, opportunity_id, occurred_at, subject_ref,
                session_ref, placement_key, surface, platform, filled,
                no_fill_reason, candidate_count, eligible_count, ranked_count,
                campaign_id, creative_id, score, score_breakdown_json,
                exclusion_counts_json, ranking_version, ranking_mode,
                experiment_key, experiment_variant, exploration, latency_ms,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                str(opportunity_id or decision_id),
                now_iso,
                subject_ref,
                session_ref,
                placement_key,
                surface,
                platform,
                1 if filled else 0,
                no_fill_reason,
                candidate_count,
                eligible_count,
                eligible_count,
                (str((winner or {}).get("campaign_id"))
                 if (winner or {}).get("campaign_id") else None),
                (str((winner or {}).get("creative_id"))
                 if (winner or {}).get("creative_id") else None),
                score,
                json.dumps(score_breakdown) if score_breakdown else None,
                json.dumps(summarise_exclusions(decisions)) or None,
                taxonomy.RANKING_VERSION,
                str(ranking_mode or "legacy"),
                experiment_key,
                experiment_variant,
                1 if exploration else 0,
                int(latency_ms) if latency_ms is not None else None,
                now_iso,
            ),
        )
        if owned:
            conn.commit()
        return decision_id
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def record_safely(**kwargs) -> str | None:
    """:func:`record_decision`, but a failure can never break delivery.

    Stage 1's whole premise is that turning measurement on does not risk the ad
    system. Every production call site uses this wrapper; the raw function
    exists for tests that need the exception.
    """
    try:
        return record_decision(**kwargs)
    except Exception:
        logging.exception("ADS_INTEL_DECISION_RECORD_FAILED")
        return None


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        try:
            return {k: row[k] for k in row.keys()}
        except Exception:
            return None


def load_decision(conn, decision_id: str) -> dict | None:
    return _row_to_dict(conn.execute(
        "SELECT * FROM ads_intel_delivery_decisions WHERE decision_id = ?",
        (str(decision_id),)).fetchone())


def no_fill_breakdown(conn, *, since: str | None = None,
                      placement_key: str | None = None) -> dict:
    """Why opportunities went unfilled, most common first.

    The report that makes "we showed nothing" answerable. Returns totals plus
    the per-reason counts.
    """
    sql = ("SELECT no_fill_reason, COUNT(*) FROM ads_intel_delivery_decisions "
           "WHERE filled = 0")
    params: list = []
    if since:
        sql += " AND occurred_at >= ?"
        params.append(str(since))
    if placement_key:
        sql += " AND placement_key = ?"
        params.append(str(placement_key))
    sql += " GROUP BY no_fill_reason"

    rows = conn.execute(sql, tuple(params)).fetchall() or []
    counts = {str(r[0] or "UNKNOWN"): int(r[1] or 0) for r in rows}

    total_sql = "SELECT COUNT(*), SUM(filled) FROM ads_intel_delivery_decisions"
    total_params: list = []
    clauses = []
    if since:
        clauses.append("occurred_at >= ?")
        total_params.append(str(since))
    if placement_key:
        clauses.append("placement_key = ?")
        total_params.append(str(placement_key))
    if clauses:
        total_sql += " WHERE " + " AND ".join(clauses)
    total_row = conn.execute(total_sql, tuple(total_params)).fetchone()
    opportunities = int((total_row or [0])[0] or 0)
    filled = int((total_row or [0, 0])[1] or 0)

    return {
        "opportunities": opportunities,
        "filled": filled,
        "unfilled": opportunities - filled,
        "fill_rate": (filled / opportunities) if opportunities else None,
        "reasons": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }
