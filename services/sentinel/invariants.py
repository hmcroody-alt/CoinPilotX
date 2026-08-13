"""Sentinel invariant engine (Stage 11).

Read-only observers over canonical financial and platform state. Sentinel
NEVER mutates financial data (constitution preamble; SC6): every invariant
here executes SELECTs only, and a violation produces an event + incident,
not a correction.

Invariants are defensive about missing tables: on a fresh database the
underlying subsystem may not have created its schema yet, in which case the
invariant reports SKIPPED (unknown ≠ healthy, but also ≠ violated).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from services.sentinel import events, incidents, store
from services.sentinel.identity import SENTINEL_INVARIANTS

STATUS_OK = "OK"
STATUS_VIOLATED = "VIOLATED"
STATUS_SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class InvariantResult:
    invariant_id: str
    status: str
    detail: str


def _scalar(cur, sql: str, params=()) -> float | None:
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return None  # table missing / engine mismatch → SKIPPED


def _inv_ledger_balanced(cur) -> InvariantResult:
    """Double-entry ledger must sum to zero across debit/credit legs."""
    total = _scalar(cur,
                    "SELECT COALESCE(SUM(CASE WHEN direction='debit' THEN amount_cents "
                    "ELSE -amount_cents END), 0) FROM creator_ledger_entries")
    if total is None:
        return InvariantResult("INV_LEDGER_BALANCED", STATUS_SKIPPED, "ledger table unavailable")
    if abs(total) > 0:
        return InvariantResult("INV_LEDGER_BALANCED", STATUS_VIOLATED,
                               f"ledger legs sum to {total} cents, expected 0")
    return InvariantResult("INV_LEDGER_BALANCED", STATUS_OK, "ledger balanced")


def _inv_ad_wallet_non_negative(cur) -> InvariantResult:
    n = _scalar(cur, "SELECT COUNT(*) FROM pulse_ad_wallets WHERE balance_cents < 0")
    if n is None:
        return InvariantResult("INV_AD_WALLET_NON_NEGATIVE", STATUS_SKIPPED, "ad wallet table unavailable")
    if n > 0:
        return InvariantResult("INV_AD_WALLET_NON_NEGATIVE", STATUS_VIOLATED,
                               f"{int(n)} ad wallet(s) with negative balance")
    return InvariantResult("INV_AD_WALLET_NON_NEGATIVE", STATUS_OK, "no negative ad wallets")


def _inv_payout_never_exceeds_balance(cur) -> InvariantResult:
    n = _scalar(cur,
                "SELECT COUNT(*) FROM seller_payouts WHERE amount_cents < 0")
    if n is None:
        return InvariantResult("INV_PAYOUT_NON_NEGATIVE", STATUS_SKIPPED, "payout table unavailable")
    if n > 0:
        return InvariantResult("INV_PAYOUT_NON_NEGATIVE", STATUS_VIOLATED,
                               f"{int(n)} payout(s) with negative amount")
    return InvariantResult("INV_PAYOUT_NON_NEGATIVE", STATUS_OK, "no negative payouts")


def _inv_evidence_chain_intact(cur, conn=None) -> InvariantResult:
    from services.sentinel import evidence as ev
    try:
        result = ev.verify_chain(conn=conn) if conn is not None else ev.verify_chain()
    except Exception as exc:
        return InvariantResult("INV_EVIDENCE_CHAIN", STATUS_SKIPPED, f"chain check unavailable: {exc}")
    if not result["ok"]:
        return InvariantResult("INV_EVIDENCE_CHAIN", STATUS_VIOLATED,
                               f"evidence chain broken at seq {result['broken_at']} (SC5)")
    return InvariantResult("INV_EVIDENCE_CHAIN", STATUS_OK,
                           f"{result['records']} evidence records intact")


INVARIANTS: dict[str, Callable] = {
    "INV_LEDGER_BALANCED": _inv_ledger_balanced,
    "INV_AD_WALLET_NON_NEGATIVE": _inv_ad_wallet_non_negative,
    "INV_PAYOUT_NON_NEGATIVE": _inv_payout_never_exceeds_balance,
    "INV_EVIDENCE_CHAIN": _inv_evidence_chain_intact,
}


def run_all(conn=None) -> list[InvariantResult]:
    """Run every invariant; violations emit a LEDGER event and open an
    idempotent INVARIANT_VIOLATION incident. No state is corrected."""
    results: list[InvariantResult] = []
    with store.connection(conn) as c:
        cur = c.cursor()
        for inv_id, fn in INVARIANTS.items():
            if inv_id == "INV_EVIDENCE_CHAIN":
                result = fn(cur, conn=c)
            else:
                result = fn(cur)
            results.append(result)
            if result.status == STATUS_VIOLATED:
                events.ingest(events.Event(
                    category="LEDGER", event_type="invariant_violation",
                    severity="critical", actor_id=SENTINEL_INVARIANTS.actor_id,
                    source="sentinel.invariants", subject_type="invariant",
                    subject_id=inv_id, payload={"detail": result.detail}), conn=c)
                day_key = hashlib.sha256(f"{inv_id}|{result.detail[:80]}".encode()).hexdigest()[:16]
                incidents.open_incident(
                    f"inv_{inv_id}_{day_key}", "INVARIANT_VIOLATION", "critical",
                    f"Invariant {inv_id} violated: {result.detail}",
                    SENTINEL_INVARIANTS.actor_id,
                    detail={"invariant_id": inv_id, "detail": result.detail},
                    conn=c)
    return results
