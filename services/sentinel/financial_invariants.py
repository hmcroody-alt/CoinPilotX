"""Sentinel Mission 5 — financial security invariants FIN-001…FIN-015 (Stage 6).

Pure functions over explicit `facts` dicts. Each check returns one of:
  HOLDS          — the invariant is satisfied by the supplied facts
  VIOLATED       — the facts demonstrate a violation (evidence in detail)
  NOT_EVALUATED  — required facts are absent; honesty over guessing

Violations are DETECTED, RECORDED, and ESCALATED as incidents. They are
NEVER repaired here: Sentinel does not modify financial state, ever.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, Optional, Tuple

from services.sentinel import incidents

HOLDS = "HOLDS"
VIOLATED = "VIOLATED"
NOT_EVALUATED = "NOT_EVALUATED"

# Live commercial policy constants (Stage 47): these are the values that must
# NOT change. merchant 10%, teacher 15%; proposed standard 5% must be inactive.
LIVE_FEE_BPS = {"merchant": 1000, "teacher": 1500}
PROPOSED_STANDARD_FEE_BPS = 500

# Legal payout state machine (seller_payouts.py:64-76).
PAYOUT_TRANSITIONS = {
    "pending": {"payout_created", "failed", "canceled"},
    "payout_created": {"in_transit", "paid", "failed", "canceled"},
    "in_transit": {"paid", "failed", "returned", "canceled"},
    "paid": set(), "failed": set(), "canceled": set(), "returned": set(),
}


@dataclass(frozen=True)
class InvariantResult:
    invariant_id: str
    status: str
    detail: str = ""
    evidence: Dict[str, Any] = dc_field(default_factory=dict)


def _need(facts: Dict[str, Any], *keys: str) -> Optional[str]:
    missing = [k for k in keys if facts.get(k) is None]
    return ", ".join(missing) if missing else None


def _res(iid: str, status: str, detail: str = "", **evidence) -> InvariantResult:
    return InvariantResult(iid, status, detail, dict(evidence))


# --- individual invariants ---------------------------------------------------

def fin_001(facts):
    """Cumulative refunds must never exceed the captured amount."""
    m = _need(facts, "captured_cents", "refunded_total_cents")
    if m:
        return _res("FIN-001", NOT_EVALUATED, f"missing: {m}")
    cap, ref = int(facts["captured_cents"]), int(facts["refunded_total_cents"])
    if ref > cap:
        return _res("FIN-001", VIOLATED,
                    f"refunded {ref}c exceeds captured {cap}c",
                    captured_cents=cap, refunded_total_cents=ref)
    return _res("FIN-001", HOLDS)


def fin_002(facts):
    """A payout in 'paid' state must carry a provider payout reference."""
    m = _need(facts, "payout_state")
    if m:
        return _res("FIN-002", NOT_EVALUATED, f"missing: {m}")
    if str(facts["payout_state"]) != "paid":
        return _res("FIN-002", HOLDS, "not in paid state")
    provider_ref = str(facts.get("provider_payout_ref") or "").strip()
    if not provider_ref:
        return _res("FIN-002", VIOLATED,
                    "payout marked paid without provider reference",
                    payout_state="paid")
    return _res("FIN-002", HOLDS)


def fin_003(facts):
    """A replayed provider event must not create a second economic effect."""
    m = _need(facts, "provider_event_id", "economic_effect_count")
    if m:
        return _res("FIN-003", NOT_EVALUATED, f"missing: {m}")
    count = int(facts["economic_effect_count"])
    if count > 1:
        return _res("FIN-003", VIOLATED,
                    f"provider event produced {count} economic effects",
                    provider_event_id=str(facts["provider_event_id"]),
                    economic_effect_count=count)
    return _res("FIN-003", HOLDS)


def fin_004(facts):
    """Refunds must use the immutable fee snapshot, never current policy."""
    m = _need(facts, "refund_fee_bps", "snapshot_fee_bps")
    if m:
        return _res("FIN-004", NOT_EVALUATED, f"missing: {m}")
    used, snap = int(facts["refund_fee_bps"]), int(facts["snapshot_fee_bps"])
    if used != snap:
        return _res("FIN-004", VIOLATED,
                    f"refund used {used}bps but snapshot says {snap}bps",
                    refund_fee_bps=used, snapshot_fee_bps=snap)
    return _res("FIN-004", HOLDS)


def fin_005(facts):
    """Live commercial policy unchanged: merchant 1000bps, teacher 1500bps,
    proposed 500bps standard INACTIVE (Stage 47)."""
    m = _need(facts, "fee_rules")
    if m:
        return _res("FIN-005", NOT_EVALUATED, f"missing: {m}")
    rules = dict(facts["fee_rules"])
    for role, expected in LIVE_FEE_BPS.items():
        got = rules.get(role)
        if got is not None and int(got) != expected:
            return _res("FIN-005", VIOLATED,
                        f"live fee for {role} is {got}bps, expected {expected}bps",
                        role=role, observed_bps=int(got), expected_bps=expected)
    if bool(facts.get("proposed_standard_active")):
        return _res("FIN-005", VIOLATED,
                    "proposed 500bps standard reported ACTIVE — it must stay "
                    "gated OFF", proposed_standard_active=True)
    return _res("FIN-005", HOLDS)


def fin_006(facts):
    """Ad wallet credits must originate from an authoritative funding op."""
    m = _need(facts, "credit_source_class")
    if m:
        return _res("FIN-006", NOT_EVALUATED, f"missing: {m}")
    cls = str(facts["credit_source_class"])
    if cls != "AUTHORITATIVE":
        return _res("FIN-006", VIOLATED,
                    f"wallet credit from non-authoritative source class {cls}",
                    credit_source_class=cls)
    return _res("FIN-006", HOLDS)


def fin_007(facts):
    """Ad wallet debits must reference a billing event or funding op."""
    m = _need(facts, "debit_has_billing_reference")
    if m:
        return _res("FIN-007", NOT_EVALUATED, f"missing: {m}")
    if not bool(facts["debit_has_billing_reference"]):
        return _res("FIN-007", VIOLATED,
                    "wallet debit without billing/funding reference")
    return _res("FIN-007", HOLDS)


def fin_008(facts):
    """No client-derived payment state: an order marked paid must trace to a
    server-verified source, never a client claim (mission absolute)."""
    m = _need(facts, "paid_state_source_class")
    if m:
        return _res("FIN-008", NOT_EVALUATED, f"missing: {m}")
    cls = str(facts["paid_state_source_class"])
    if cls in ("CLIENT_REPORTED", "UNKNOWN"):
        return _res("FIN-008", VIOLATED,
                    f"order paid-state derived from {cls} source — client "
                    "claims can never be canonical authority",
                    paid_state_source_class=cls)
    return _res("FIN-008", HOLDS)


def fin_009(facts):
    """Order/payment consistency: a paid order needs a matching payment ref
    and amount agreement."""
    m = _need(facts, "order_status")
    if m:
        return _res("FIN-009", NOT_EVALUATED, f"missing: {m}")
    if str(facts["order_status"]) not in ("paid", "checkout_completed"):
        return _res("FIN-009", HOLDS, "order not in a paid state")
    if not str(facts.get("payment_ref") or "").strip():
        return _res("FIN-009", VIOLATED, "paid order without payment reference")
    oc, pc = facts.get("order_amount_cents"), facts.get("payment_amount_cents")
    if oc is not None and pc is not None and int(oc) != int(pc):
        return _res("FIN-009", VIOLATED,
                    f"order amount {oc}c != payment amount {pc}c",
                    order_amount_cents=int(oc), payment_amount_cents=int(pc))
    return _res("FIN-009", HOLDS)


def fin_010(facts):
    """Refund authority: only admin/system actors may record refunds."""
    m = _need(facts, "refund_actor_type")
    if m:
        return _res("FIN-010", NOT_EVALUATED, f"missing: {m}")
    actor = str(facts["refund_actor_type"]).lower()
    if actor not in ("admin", "system", "service"):
        return _res("FIN-010", VIOLATED,
                    f"refund recorded by unauthorized actor type {actor!r}",
                    refund_actor_type=actor)
    return _res("FIN-010", HOLDS)


def fin_011(facts):
    """Settlement idempotency: one settlement per order."""
    m = _need(facts, "settlement_count_for_order")
    if m:
        return _res("FIN-011", NOT_EVALUATED, f"missing: {m}")
    count = int(facts["settlement_count_for_order"])
    if count > 1:
        return _res("FIN-011", VIOLATED,
                    f"{count} settlements recorded for one order",
                    settlement_count_for_order=count)
    return _res("FIN-011", HOLDS)


def fin_012(facts):
    """Payout state transitions must follow the legal state graph."""
    m = _need(facts, "payout_from_state", "payout_to_state")
    if m:
        return _res("FIN-012", NOT_EVALUATED, f"missing: {m}")
    frm, to = str(facts["payout_from_state"]), str(facts["payout_to_state"])
    allowed = PAYOUT_TRANSITIONS.get(frm)
    if allowed is None:
        return _res("FIN-012", VIOLATED, f"unknown payout state {frm!r}",
                    payout_from_state=frm)
    if to not in allowed:
        return _res("FIN-012", VIOLATED,
                    f"illegal payout transition {frm} -> {to}",
                    payout_from_state=frm, payout_to_state=to)
    return _res("FIN-012", HOLDS)


def fin_013(facts):
    """Balance delta must equal the sum of its ledger entries."""
    m = _need(facts, "balance_delta_cents", "ledger_entries_sum_cents")
    if m:
        return _res("FIN-013", NOT_EVALUATED, f"missing: {m}")
    bal, ent = int(facts["balance_delta_cents"]), int(facts["ledger_entries_sum_cents"])
    if bal != ent:
        return _res("FIN-013", VIOLATED,
                    f"balance delta {bal}c != ledger entries sum {ent}c",
                    balance_delta_cents=bal, ledger_entries_sum_cents=ent)
    return _res("FIN-013", HOLDS)


def fin_014(facts):
    """Webhook idempotency honored: duplicate provider event ids must be
    skipped by the inbox, not reprocessed."""
    m = _need(facts, "duplicate_deliveries", "processed_count")
    if m:
        return _res("FIN-014", NOT_EVALUATED, f"missing: {m}")
    dup, proc = int(facts["duplicate_deliveries"]), int(facts["processed_count"])
    if dup > 0 and proc > 1:
        return _res("FIN-014", VIOLATED,
                    f"{dup} duplicate deliveries led to {proc} processings",
                    duplicate_deliveries=dup, processed_count=proc)
    return _res("FIN-014", HOLDS)


def fin_015(facts):
    """Exposure honesty: potential exposure must never be reported as
    confirmed (Stage 20)."""
    m = _need(facts, "confirmed_cents", "confirmed_basis_count")
    if m:
        return _res("FIN-015", NOT_EVALUATED, f"missing: {m}")
    confirmed = int(facts["confirmed_cents"])
    basis = int(facts["confirmed_basis_count"])
    if confirmed > 0 and basis <= 0:
        return _res("FIN-015", VIOLATED,
                    f"confirmed exposure {confirmed}c reported without any "
                    "confirmed-loss basis items",
                    confirmed_cents=confirmed, confirmed_basis_count=basis)
    return _res("FIN-015", HOLDS)


INVARIANTS: Dict[str, Tuple[Callable[[Dict[str, Any]], InvariantResult], str, str]] = {
    # id -> (check, incident_type on violation, severity)
    "FIN-001": (fin_001, "REFUND_ABUSE_SUSPECTED", "high"),
    "FIN-002": (fin_002, "PAYOUT_ABUSE_SUSPECTED", "high"),
    "FIN-003": (fin_003, "DUPLICATE_ECONOMIC_EFFECT_RISK", "high"),
    "FIN-004": (fin_004, "FINANCIAL_LEDGER_MISMATCH", "medium"),
    "FIN-005": (fin_005, "INVARIANT_VIOLATION", "critical"),
    "FIN-006": (fin_006, "AD_WALLET_INTEGRITY_ANOMALY", "high"),
    "FIN-007": (fin_007, "AD_WALLET_INTEGRITY_ANOMALY", "medium"),
    "FIN-008": (fin_008, "PAYMENT_ABUSE_SUSPECTED", "critical"),
    "FIN-009": (fin_009, "FINANCIAL_PROVIDER_INCONSISTENCY", "medium"),
    "FIN-010": (fin_010, "REFUND_ABUSE_SUSPECTED", "high"),
    "FIN-011": (fin_011, "DUPLICATE_ECONOMIC_EFFECT_RISK", "high"),
    "FIN-012": (fin_012, "PAYOUT_ABUSE_SUSPECTED", "medium"),
    "FIN-013": (fin_013, "FINANCIAL_LEDGER_MISMATCH", "high"),
    "FIN-014": (fin_014, "FINANCIAL_WEBHOOK_REPLAY", "high"),
    "FIN-015": (fin_015, "INVARIANT_VIOLATION", "medium"),
}


def check(invariant_id: str, facts: Dict[str, Any]) -> InvariantResult:
    entry = INVARIANTS.get(invariant_id)
    if entry is None:
        raise ValueError(f"unknown invariant {invariant_id!r}")
    return entry[0](dict(facts or {}))


def check_all(facts: Dict[str, Any]) -> Dict[str, InvariantResult]:
    return {iid: entry[0](dict(facts or {}))
            for iid, entry in INVARIANTS.items()}


def escalate(result: InvariantResult, subject_ref: str, conn=None,
             actor_id: str = "service.sentinel.financial_invariants"):
    """Open (or observe) an incident for a VIOLATED result. Detect + record +
    escalate — never repair. Returns IncidentRef or None."""
    if result.status != VIOLATED:
        return None
    entry = INVARIANTS.get(result.invariant_id)
    incident_type = entry[1] if entry else "INVARIANT_VIOLATION"
    if incident_type not in incidents.INCIDENT_TYPES:
        incident_type = "INVARIANT_VIOLATION"
    severity = entry[2] if entry else "high"
    key = incidents.dedupe_key("fin-invariant", result.invariant_id, subject_ref)
    detail = {"subject_ref": subject_ref, "invariant_id": result.invariant_id,
              "detail": result.detail, "evidence": result.evidence,
              "authority_note": ("violation detected and recorded; Sentinel "
                                 "cannot and will not modify financial state")}
    return incidents.open_incident(
        key, incident_type, severity,
        f"Financial invariant {result.invariant_id} violated: {result.detail[:120]}",
        actor_id, detail, conn=conn, owner_action_required=True)
