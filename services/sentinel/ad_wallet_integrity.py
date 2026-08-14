"""Sentinel Mission 5 — ad wallet integrity monitoring (Stages 16–17).

Observes the three independent ad-spend sources the platform already keeps
(billing events sum, spend accumulator, escrow ledger balance) and checks
they agree. Disagreement is recorded and escalated — NEVER repaired, and no
balance is ever mutated from here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.sentinel import (financial_reconciliation, incidents,
                               killswitches)

SPEND_SOURCES = ("billing_events_sum_cents", "accumulator_cents",
                 "escrow_delta_cents")

# Sub-cent accrual means the accumulator may legitimately trail by < 1 cent.
AGREEMENT_TOLERANCE_CENTS = 1


def spend_agreement(campaign_ref: str,
                    figures: Dict[str, Optional[int]],
                    *, stale: bool = False,
                    conn=None) -> Dict[str, Any]:
    """Three-source spend agreement check for one campaign.

    `figures` maps each SPEND_SOURCES key to cents or None (unavailable).
    Missing sources degrade the answer to PARTIAL/UNKNOWN — they never
    silently pass. Records the result via the reconciliation engine.
    """
    unknown = set(figures or {}) - set(SPEND_SOURCES)
    if unknown:
        raise ValueError(f"unknown spend sources: {sorted(unknown)}")
    present = {k: int(v) for k, v in (figures or {}).items() if v is not None}

    if stale:
        status, detail = "STALE", "spend figures are stale"
    elif len(present) == 0:
        status, detail = "UNKNOWN", "no spend source available"
    elif len(present) < len(SPEND_SOURCES):
        missing = sorted(set(SPEND_SOURCES) - set(present))
        vals = list(present.values())
        if max(vals) - min(vals) <= AGREEMENT_TOLERANCE_CENTS:
            status = "PARTIAL"
            detail = f"available sources agree; missing: {', '.join(missing)}"
        else:
            status = "MISMATCH"
            detail = (f"available sources disagree by "
                      f"{max(vals) - min(vals)}c; missing: {', '.join(missing)}")
    else:
        vals = list(present.values())
        spread = max(vals) - min(vals)
        if spread <= AGREEMENT_TOLERANCE_CENTS:
            status, detail = "MATCH", ""
        else:
            status, detail = "MISMATCH", f"three-source spread {spread}c"

    expected = present.get("billing_events_sum_cents")
    observed = present.get("escrow_delta_cents",
                           present.get("accumulator_cents"))
    result = financial_reconciliation.reconcile(
        "ad_spend_agreement", campaign_ref, expected, observed,
        components=dict(figures or {}), stale=stale,
        partial=(status == "PARTIAL"))
    # reconcile() classifies independently; carry our richer status/detail.
    result["status"], result["detail"] = status, detail or result["detail"]
    financial_reconciliation.record(result, conn=conn)

    if status == "MISMATCH" and killswitches.ad_wallet_risk_enabled():
        key = incidents.dedupe_key("ad-wallet-integrity", campaign_ref)
        incidents.open_incident(
            key, "AD_WALLET_INTEGRITY_ANOMALY", "high",
            f"Ad spend sources disagree for {campaign_ref}: {detail}",
            "service.sentinel.ad_wallet_integrity",
            {"subject_ref": campaign_ref, "figures": dict(figures or {}),
             "authority_note": ("disagreement recorded; NO balance mutation "
                                "— repair is an owner decision")},
            conn=conn, owner_action_required=True)
    return {"campaign_ref": campaign_ref, "status": status, "detail": detail,
            "figures": dict(figures or {}),
            "tolerance_cents": AGREEMENT_TOLERANCE_CENTS,
            "note": "observation only — no balance is ever mutated from here"}


def assess_advertiser(advertiser_ref: str,
                      facts: Dict[str, Any]) -> Dict[str, Any]:
    """Advisory advertiser financial-risk signals (Stage 17). Explainable
    baselines, no ML; returns dimension scores for financial_risk.assess.

    facts: {funding_ops_7d, funding_failures_7d, spend_vs_budget_ratio,
    campaign_creates_7d, campaign_cancels_7d}.
    """
    reasons: List[str] = []
    funding_ops = int(facts.get("funding_ops_7d") or 0)
    failures = int(facts.get("funding_failures_7d") or 0)
    ratio = float(facts.get("spend_vs_budget_ratio") or 0.0)
    creates = int(facts.get("campaign_creates_7d") or 0)
    cancels = int(facts.get("campaign_cancels_7d") or 0)

    funding = 0.0
    if funding_ops > 0 and failures / max(1, funding_ops) > 0.5:
        funding = min(1.0, failures / max(1, funding_ops))
        reasons.append(f"{failures}/{funding_ops} funding ops failed in 7d")
    spend = 0.0
    if ratio > 1.0:
        spend = min(1.0, (ratio - 1.0))
        reasons.append(f"spend at {ratio:.2f}x budget")
    churn = 0.0
    if creates >= 5 and cancels / max(1, creates) > 0.6:
        churn = min(1.0, cancels / max(1, creates))
        reasons.append(f"{cancels}/{creates} campaigns canceled in 7d")

    return {"advertiser_ref": advertiser_ref,
            "dimensions": {"wallet_funding_pattern": round(funding, 4),
                           "spend_anomaly": round(spend, 4),
                           "campaign_churn": round(churn, 4)},
            "reasons": reasons,
            "note": "advisory only — explainable baselines, no ML, no verdict"}
