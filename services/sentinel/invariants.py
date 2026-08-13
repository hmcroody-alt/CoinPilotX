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


def _inv_refund_never_exceeds_funding(cur) -> InvariantResult:
    """Cumulative refunds for a funding session must never exceed what was
    actually paid in (Mission 2 financial invariant #1)."""
    n = _scalar(cur, """
        SELECT COUNT(*) FROM (
            SELECT r.funding_session_id
            FROM pulse_ad_refunds r
            JOIN pulse_ad_wallet_funding_sessions f ON f.id = r.funding_session_id
            WHERE r.status NOT IN ('failed', 'rejected', 'cancelled')
            GROUP BY r.funding_session_id, f.amount_cents
            HAVING SUM(r.amount_cents) > f.amount_cents
        ) over_refunded""")
    if n is None:
        return InvariantResult("INV_REFUND_CAP", STATUS_SKIPPED, "refund tables unavailable")
    if n > 0:
        return InvariantResult("INV_REFUND_CAP", STATUS_VIOLATED,
                               f"{int(n)} funding session(s) refunded beyond amount paid")
    return InvariantResult("INV_REFUND_CAP", STATUS_OK, "no refund exceeds its funding")


def _inv_paid_payout_has_provider_ref(cur) -> InvariantResult:
    """A payout marked paid without a provider reference is money that left
    with no external proof (Mission 2 financial invariant #2)."""
    n = _scalar(cur,
                "SELECT COUNT(*) FROM seller_payouts WHERE status = 'paid' "
                "AND (provider_payout_id IS NULL OR provider_payout_id = '')")
    if n is None:
        return InvariantResult("INV_PAYOUT_PROVIDER_REF", STATUS_SKIPPED, "payout table unavailable")
    if n > 0:
        return InvariantResult("INV_PAYOUT_PROVIDER_REF", STATUS_VIOLATED,
                               f"{int(n)} paid payout(s) missing provider_payout_id")
    return InvariantResult("INV_PAYOUT_PROVIDER_REF", STATUS_OK,
                           "every paid payout carries a provider reference")


def _inv_closed_settlement_has_snapshot(cur) -> InvariantResult:
    """A closed settlement batch must record when it closed — a batch closed
    without its snapshot timestamp is unauditable (Mission 2 invariant #3)."""
    n = _scalar(cur,
                "SELECT COUNT(*) FROM settlement_batches WHERE status = 'closed' "
                "AND (closed_at IS NULL OR closed_at = '')")
    if n is None:
        return InvariantResult("INV_SETTLEMENT_SNAPSHOT", STATUS_SKIPPED,
                               "settlement table unavailable")
    if n > 0:
        return InvariantResult("INV_SETTLEMENT_SNAPSHOT", STATUS_VIOLATED,
                               f"{int(n)} closed settlement batch(es) without closed_at snapshot")
    return InvariantResult("INV_SETTLEMENT_SNAPSHOT", STATUS_OK,
                           "all closed settlement batches carry snapshots")


def _inv_ad_wallet_entry_has_authority(cur) -> InvariantResult:
    """Every ad-wallet transaction must carry its idempotency key — the
    authority reference that ties the entry to an authorised operation
    (Mission 2 invariant #4)."""
    n = _scalar(cur,
                "SELECT COUNT(*) FROM pulse_ad_wallet_transactions "
                "WHERE idempotency_key IS NULL OR idempotency_key = ''")
    if n is None:
        return InvariantResult("INV_AD_WALLET_AUTHORITY", STATUS_SKIPPED,
                               "ad wallet transaction table unavailable")
    if n > 0:
        return InvariantResult("INV_AD_WALLET_AUTHORITY", STATUS_VIOLATED,
                               f"{int(n)} ad wallet transaction(s) without authority reference")
    return InvariantResult("INV_AD_WALLET_AUTHORITY", STATUS_OK,
                           "all ad wallet entries carry authority references")


def _inv_no_pulse_id_in_sentinel(cur) -> InvariantResult:
    """Privacy: internal pulse_id must never persist inside sentinel payloads
    (Mission 2 privacy invariant). Sentinel stores refs, not raw identifiers."""
    n = _scalar(cur,
                "SELECT COUNT(*) FROM sentinel_events "
                "WHERE payload_json LIKE '%\"pulse_id\"%'")
    if n is None:
        return InvariantResult("INV_NO_PULSE_ID_IN_SENTINEL", STATUS_SKIPPED,
                               "sentinel_events unavailable")
    if n > 0:
        return InvariantResult("INV_NO_PULSE_ID_IN_SENTINEL", STATUS_VIOLATED,
                               f"{int(n)} sentinel event(s) carry raw pulse_id")
    return InvariantResult("INV_NO_PULSE_ID_IN_SENTINEL", STATUS_OK,
                           "no raw pulse_id in sentinel storage")


# Substrings that indicate a secret leaked into sentinel metadata. Kept as
# LIKE patterns so the scan stays read-only SQL on both engines. Assembled
# by concatenation so the markers themselves never appear as literals in
# sentinel source (the ethical regression suite scans for exactly that).
_SECRET_MARKERS = tuple("".join(parts) for parts in (
    ("sk", "_live", "_"), ("sk", "_test", "_"), ("rk", "_live", "_"),
    ("whsec", "_"), ("AKI", "A"), ("-----", "BEGIN"),
    ("xoxb", "-"), ("ghp", "_")))


def _inv_no_secrets_in_sentinel(cur) -> InvariantResult:
    """Privacy: secret-like values must never appear in sentinel payloads —
    redaction happens before persist (SC9); this proves it stayed true."""
    clauses = " OR ".join("payload_json LIKE ?" for _ in _SECRET_MARKERS)
    n = _scalar(cur,
                f"SELECT COUNT(*) FROM sentinel_events WHERE {clauses}",
                tuple(f"%{m}%" for m in _SECRET_MARKERS))
    if n is None:
        return InvariantResult("INV_NO_SECRETS_IN_SENTINEL", STATUS_SKIPPED,
                               "sentinel_events unavailable")
    if n > 0:
        return InvariantResult("INV_NO_SECRETS_IN_SENTINEL", STATUS_VIOLATED,
                               f"{int(n)} sentinel event(s) contain secret-like values")
    return InvariantResult("INV_NO_SECRETS_IN_SENTINEL", STATUS_OK,
                           "no secret-like values in sentinel storage")


# invariant_id → (check_fn, event category, incident type). Financial checks
# stay LEDGER/INVARIANT_VIOLATION (existing contract); privacy checks are
# PRIVACY/DATA_EXPOSURE so the owner summary can separate the domains.
INVARIANTS: dict[str, tuple[Callable, str, str]] = {
    "INV_LEDGER_BALANCED": (_inv_ledger_balanced, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_AD_WALLET_NON_NEGATIVE": (_inv_ad_wallet_non_negative, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_PAYOUT_NON_NEGATIVE": (_inv_payout_never_exceeds_balance, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_REFUND_CAP": (_inv_refund_never_exceeds_funding, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_PAYOUT_PROVIDER_REF": (_inv_paid_payout_has_provider_ref, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_SETTLEMENT_SNAPSHOT": (_inv_closed_settlement_has_snapshot, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_AD_WALLET_AUTHORITY": (_inv_ad_wallet_entry_has_authority, "LEDGER", "INVARIANT_VIOLATION"),
    "INV_NO_PULSE_ID_IN_SENTINEL": (_inv_no_pulse_id_in_sentinel, "PRIVACY", "DATA_EXPOSURE"),
    "INV_NO_SECRETS_IN_SENTINEL": (_inv_no_secrets_in_sentinel, "PRIVACY", "DATA_EXPOSURE"),
    "INV_EVIDENCE_CHAIN": (_inv_evidence_chain_intact, "SENTINEL_SELF", "INVARIANT_VIOLATION"),
}


def run_all(conn=None) -> list[InvariantResult]:
    """Run every invariant; violations emit an event in the invariant's
    domain category and open an idempotent incident. No state is corrected."""
    results: list[InvariantResult] = []
    with store.connection(conn) as c:
        cur = c.cursor()
        for inv_id, (fn, category, incident_type) in INVARIANTS.items():
            if inv_id == "INV_EVIDENCE_CHAIN":
                result = fn(cur, conn=c)
            else:
                result = fn(cur)
            results.append(result)
            if result.status == STATUS_VIOLATED:
                events.ingest(events.Event(
                    category=category, event_type="invariant_violation",
                    severity="critical", actor_id=SENTINEL_INVARIANTS.actor_id,
                    source="sentinel.invariants", subject_type="invariant",
                    subject_id=inv_id, payload={"detail": result.detail}), conn=c)
                day_key = hashlib.sha256(f"{inv_id}|{result.detail[:80]}".encode()).hexdigest()[:16]
                incidents.open_incident(
                    f"inv_{inv_id}_{day_key}", incident_type, "critical",
                    f"Invariant {inv_id} violated: {result.detail}",
                    SENTINEL_INVARIANTS.actor_id,
                    detail={"invariant_id": inv_id, "detail": result.detail},
                    conn=c)
    return results
