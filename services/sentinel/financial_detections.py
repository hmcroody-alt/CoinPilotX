"""Sentinel Mission 5 — financial detections (Stages 11–15).

Refund-abuse patterns, payout threat signals, coordinated-abuse candidates,
and the shared-infrastructure false-positive defense. Every function here
returns OBSERVATIONS and opens SUSPICION incidents at most — nothing blocks,
holds, refunds, or bans. REFUND != ABUSE. RELATIONSHIP != COLLUSION.
DEVICE/NETWORK SHARING != FRAUD.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.sentinel import incidents, killswitches

# --- Stage 15: shared-infrastructure false-positive defense -------------------
# Contexts where device/network sharing is EXPECTED. Each maps to a damping
# factor applied to coordination-flavored signals: the more legitimate the
# sharing context, the harder it is to call the same signals "coordination".

SHARED_CONTEXT_FACTORS = {
    "family_household": 0.3,
    "office_network": 0.4,
    "cgnat_carrier": 0.5,      # carrier-grade NAT: thousands share one IP
    "qa_test_account": 0.2,
    "shared_public_device": 0.4,
    "none_known": 1.0,
}


def shared_infrastructure_factor(contexts: List[str]) -> float:
    """Most-forgiving context wins: one legitimate explanation is enough to
    damp the signal (innocent-first bias, Stage 15)."""
    factors = [SHARED_CONTEXT_FACTORS.get(str(c), 1.0) for c in (contexts or [])]
    return min(factors) if factors else 1.0


# --- Stage 11: refund pattern analysis ----------------------------------------

# Refund reasons that are LEGITIMATE by construction and never count toward
# abuse scoring (customer-service approved, partial refunds, platform fault).
LEGIT_REFUND_REASONS = frozenset({
    "cs_approved", "customer_service", "partial_refund", "item_not_received",
    "platform_error", "seller_fault", "duplicate_charge", "goodwill",
})


def refund_pattern(refunds: List[Dict[str, Any]],
                   *, window_days: int = 30) -> Dict[str, Any]:
    """Score a refund pattern from refund records (facts supplied by the
    caller; Sentinel reads, it does not fetch from payment providers).

    Each refund dict: {reason, amount_cents, order_amount_cents,
    buyer_initiated(bool), same_seller(bool)}.
    Returns a signal in [0,1] + reasons. REFUND != ABUSE: legitimate reasons
    are excluded before anything is counted.
    """
    if window_days <= 0 or window_days > 90:
        raise ValueError("refund window must be bounded (0, 90] days")
    total = len(refunds or [])
    legit, countable = [], []
    for r in refunds or []:
        reason = str(r.get("reason") or "").strip().lower()
        if reason in LEGIT_REFUND_REASONS:
            legit.append(r)
        else:
            countable.append(r)
    reasons: List[str] = []
    signal = 0.0
    if countable:
        # Frequency component: saturates at 10 non-legit refunds / window.
        freq = min(1.0, len(countable) / 10.0)
        signal += 0.4 * freq
        if freq > 0.3:
            reasons.append(
                f"{len(countable)} non-exempt refunds in {window_days}d")
        # Full-refund concentration: repeatedly refunding 100 % of orders.
        fulls = [r for r in countable
                 if r.get("amount_cents") is not None
                 and r.get("order_amount_cents")
                 and int(r["amount_cents"]) >= int(r["order_amount_cents"])]
        if len(fulls) >= 3:
            signal += 0.3
            reasons.append(f"{len(fulls)} full-amount refunds")
        # Same-seller concentration (possible collusion refund cycling).
        same_seller = [r for r in countable if r.get("same_seller")]
        if len(same_seller) >= 3:
            signal += 0.3
            reasons.append(
                f"{len(same_seller)} refunds concentrated on one seller")
    return {
        "signal": round(min(1.0, signal), 4),
        "reasons": reasons,
        "total_refunds": total,
        "exempt_legitimate": len(legit),
        "counted": len(countable),
        "note": "REFUND != ABUSE — legitimate reasons excluded before scoring",
    }


# --- Stage 12: payout threat signals -------------------------------------------

PAYOUT_SIGNAL_NAMES = (
    "destination_changed_recently",   # payout destination changed < 72h ago
    "identity_risk_active",           # Mission 3 elevated identity risk
    "velocity_spike",                 # payout request rate anomaly
    "amount_outlier",                 # far above the seller's own baseline
    "first_payout_after_dormancy",    # long-dormant account suddenly paying out
)

PAYOUT_MIN_SIGNALS = 2   # a single signal is never enough (Stage 12)


def payout_threat(signals: Dict[str, bool],
                  *, seasonal_context: bool = False) -> Dict[str, Any]:
    """Combine named payout threat signals. Requires ≥2 concurrent signals
    to flag at all; a seasonal/holiday context damps the velocity/amount
    signals (Stage 37: seasonal payout spike is a false-positive case).
    NEVER executes, retries, cancels, or holds a payout."""
    unknown = set(signals or {}) - set(PAYOUT_SIGNAL_NAMES)
    if unknown:
        raise ValueError(f"unknown payout signals: {sorted(unknown)}")
    active = [name for name in PAYOUT_SIGNAL_NAMES if (signals or {}).get(name)]
    if seasonal_context:
        active = [s for s in active
                  if s not in ("velocity_spike", "amount_outlier")]
        damped = True
    else:
        damped = False
    flagged = len(active) >= PAYOUT_MIN_SIGNALS
    signal = min(1.0, len(active) / len(PAYOUT_SIGNAL_NAMES) + (0.2 if flagged else 0.0))
    return {
        "flagged": flagged,
        "signal": round(signal if flagged else min(signal, 0.4), 4),
        "active_signals": active,
        "seasonal_damping_applied": damped,
        "note": ("suspicion only — Sentinel cannot execute, retry, cancel, "
                 "or hold payouts"),
    }


# --- Stage 14: coordinated abuse candidates -------------------------------------

COORDINATION_MIN_ACCOUNTS = 3
COORDINATION_MIN_DIMENSIONS = 2
LINKING_DIMENSIONS = ("shared_device", "shared_network", "shared_payment_method_ref",
                      "shared_payout_destination_ref", "temporal_lockstep")


def coordination_candidate(accounts: List[str],
                           linking: Dict[str, List[str]],
                           economic_anomaly: str,
                           *, shared_contexts: Optional[List[str]] = None
                           ) -> Dict[str, Any]:
    """Evaluate a POSSIBLE coordinated financial abuse candidate.

    Requirements (all three, Stage 14):
      1. ≥ 3 distinct accounts,
      2. ≥ 2 DISTINCT linking dimensions (device+network alone is one
         household — sharing is not fraud),
      3. a named economic anomaly (money must actually look wrong).

    The verdict vocabulary is closed: POSSIBLE_COORDINATED_FINANCIAL_ABUSE
    or NOT_ESTABLISHED. 'CONFIRMED_FRAUD_RING' does not exist here.
    """
    accounts = sorted(set(str(a) for a in (accounts or [])))
    unknown = set(linking or {}) - set(LINKING_DIMENSIONS)
    if unknown:
        raise ValueError(f"unknown linking dimensions: {sorted(unknown)}")
    active_dims = [d for d, members in (linking or {}).items()
                   if members and len(set(members)) >= 2]
    anomaly = str(economic_anomaly or "").strip()
    factor = shared_infrastructure_factor(shared_contexts or [])

    failures = []
    if len(accounts) < COORDINATION_MIN_ACCOUNTS:
        failures.append(f"only {len(accounts)} accounts (< {COORDINATION_MIN_ACCOUNTS})")
    if len(active_dims) < COORDINATION_MIN_DIMENSIONS:
        failures.append(
            f"only {len(active_dims)} linking dimension(s) (< {COORDINATION_MIN_DIMENSIONS})"
            " — RELATIONSHIP != COLLUSION")
    if not anomaly:
        failures.append("no economic anomaly named — sharing alone is not fraud")
    if factor <= 0.5 and len(active_dims) <= 2:
        failures.append(
            f"legitimate shared-infrastructure context (factor={factor}) "
            "explains the linkage")

    established = not failures
    return {
        "verdict": ("POSSIBLE_COORDINATED_FINANCIAL_ABUSE" if established
                    else "NOT_ESTABLISHED"),
        "accounts": accounts,
        "active_dimensions": active_dims,
        "economic_anomaly": anomaly,
        "shared_infrastructure_factor": factor,
        "failed_requirements": failures,
        "note": "POSSIBLE, never CONFIRMED — confirmation is human work",
    }


def open_coordination_incident(candidate: Dict[str, Any], conn=None,
                               actor_id: str = "service.sentinel.financial_detections"):
    """Open COORDINATED_FINANCIAL_ABUSE for an established candidate only.
    Gated by the marketplace-risk kill switch."""
    if candidate.get("verdict") != "POSSIBLE_COORDINATED_FINANCIAL_ABUSE":
        return None
    if not killswitches.marketplace_risk_enabled():
        return None
    accounts = candidate.get("accounts") or []
    key = incidents.dedupe_key("fin-coordination", *sorted(accounts)[:10])
    detail = {
        "subject_ref": accounts[0] if accounts else "",
        "accounts": accounts,
        "active_dimensions": candidate.get("active_dimensions", []),
        "economic_anomaly": candidate.get("economic_anomaly", ""),
        "shared_infrastructure_factor": candidate.get(
            "shared_infrastructure_factor", 1.0),
        "authority_note": ("possible coordination — evidence for human "
                           "review, never a confirmed fraud ring"),
    }
    return incidents.open_incident(
        key, "COORDINATED_FINANCIAL_ABUSE", "high",
        f"Possible coordinated financial abuse across {len(accounts)} accounts",
        actor_id, detail, conn=conn, owner_action_required=True)
