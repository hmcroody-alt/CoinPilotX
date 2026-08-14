"""Sentinel Mission 5 — read-only financial event adapters (Stage 3).

Builds SentinelEventV1 envelopes for financial signals that EXIST in the
platform (per the Stage-1 forensic inventory). Adapters OBSERVE — they never
write to any financial table, never call a provider, never mutate state.

Every event type declares which source class produced it; confidence is the
minimum of the source-class ceiling (financial_sources) and the trust-grade
ceiling (source_trust), so a client claim can never masquerade as measured
truth. Dedupe keys are deterministic → replays are idempotent (Stage 38).
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple

from services.sentinel import events, financial_entities, financial_sources
from services.sentinel import source_trust as trust_mod

# event_type -> (category, default severity)
FINANCIAL_EVENT_TYPES: Dict[str, Tuple[str, str]] = {
    "ORDER_CREATED": ("PAYMENT", "info"),
    "ORDER_PAID": ("PAYMENT", "info"),
    "REFUND_RECORDED": ("PAYMENT", "info"),
    "PAYOUT_REQUESTED": ("PAYOUT", "info"),
    "PAYOUT_STATE_CHANGED": ("PAYOUT", "info"),
    "PAYOUT_DESTINATION_CHANGED": ("PAYOUT", "medium"),
    "PAYOUT_FAILED": ("PAYOUT", "low"),
    "PAYOUT_RETURNED": ("PAYOUT", "medium"),
    "SETTLEMENT_RECORDED": ("SETTLEMENT", "info"),
    "LEDGER_ENTRY_RECORDED": ("LEDGER", "info"),
    "AD_WALLET_FUNDED": ("ADVERTISING", "info"),
    "AD_WALLET_DEBITED": ("ADVERTISING", "info"),
    "AD_SPEND_BILLED": ("ADVERTISING", "info"),
    "CAMPAIGN_BUDGET_CHANGED": ("ADVERTISING", "info"),
    "FINANCIAL_WEBHOOK_RECEIVED": ("PAYMENT", "info"),
    "CLIENT_PAYMENT_CLAIM": ("PAYMENT", "low"),
    "DISPUTE_OPENED": ("PAYMENT", "medium"),
}

# Which event types are allowed to originate from CLIENT_REPORTED sources.
# Everything else must come from server-side sources; a client-sourced
# ORDER_PAID is rejected outright (Stage 36: client authority override).
_CLIENT_ALLOWED = frozenset({"CLIENT_PAYMENT_CLAIM"})


class FinancialEventRejected(events.EventRejected):
    """Raised when a financial adapter contract is violated."""


def _dedupe(event_type: str, subject_ref: str, source_event_id: str,
            occurred_at: str) -> str:
    basis = "|".join(("fin", event_type, subject_ref,
                      source_event_id or occurred_at))
    return hashlib.sha256(basis.encode()).hexdigest()


def build(event_type: str,
          subject_ref: str,
          source_id: str,
          payload: Optional[Dict[str, Any]] = None,
          *,
          actor_id: str = "service.sentinel.financial_adapter",
          source_event_id: str = "",
          occurred_at: Optional[str] = None,
          severity: Optional[str] = None,
          amount_cents: Optional[int] = None,
          currency: str = "usd",
          correlation_keys: Tuple[str, ...] = ()) -> events.Event:
    """Build (not ingest) a financial Event. Enforces the mission contract:

    - event_type must be registered;
    - subject_ref must be a valid financial entity ref;
    - source_id must be a known financial source (else UNKNOWN class);
    - payload must contain no forbidden financial fields;
    - CLIENT_REPORTED sources may only emit CLIENT_PAYMENT_CLAIM;
    - confidence = min(class ceiling, trust-grade ceiling) — never higher.
    """
    if event_type not in FINANCIAL_EVENT_TYPES:
        raise FinancialEventRejected(
            f"unknown financial event type {event_type!r}")
    financial_entities.parse_ref(subject_ref)  # raises on malformed

    src = financial_sources.get(source_id)
    src_class = src.source_class if src else "UNKNOWN"
    class_ceiling = (src.confidence_ceiling if src
                     else financial_sources.CLASS_CONFIDENCE_CEILING["UNKNOWN"])
    trust_grade = src.trust_grade if src else "UNKNOWN"

    if src_class == "CLIENT_REPORTED" and event_type not in _CLIENT_ALLOWED:
        raise FinancialEventRejected(
            f"CLIENT_REPORTED source {source_id!r} may not emit "
            f"{event_type!r} — client claims are questions, not authority")
    if event_type in _CLIENT_ALLOWED and src_class != "CLIENT_REPORTED":
        # keep the semantics honest in the other direction too
        raise FinancialEventRejected(
            "CLIENT_PAYMENT_CLAIM must come from a CLIENT_REPORTED source")

    payload = dict(payload or {})
    financial_entities.assert_payload_safe(payload)
    payload.setdefault("financial_source_id", source_id)
    payload.setdefault("financial_source_class", src_class)
    if amount_cents is not None:
        payload.setdefault("amount_cents", int(amount_cents))
        payload.setdefault("currency", currency)

    category, default_sev = FINANCIAL_EVENT_TYPES[event_type]
    subject_type, subject_id = financial_entities.parse_ref(subject_ref)

    confidence = min(class_ceiling, trust_mod.confidence_ceiling(trust_grade))

    kwargs: Dict[str, Any] = dict(
        category=category,
        event_type=event_type,
        severity=severity or default_sev,
        actor_id=actor_id,
        source=f"sentinel.financial.{source_id}",
        subject_type=subject_type,
        subject_id=subject_id,
        payload=payload,
        source_system="pulsesoc",
        source_component="financial_adapter",
        source_event_id=str(source_event_id or ""),
        source_trust=trust_grade,
        confidence=confidence,
        financial_impact="low",
        correlation_keys=tuple(correlation_keys) + (subject_ref,),
    )
    if occurred_at:
        kwargs["occurred_at"] = occurred_at

    evt = events.Event(**kwargs)
    # deterministic dedupe key (replay-safe, Stage 38)
    object.__setattr__(evt, "dedupe_key",
                       _dedupe(event_type, subject_ref, source_event_id,
                               evt.occurred_at))
    return evt


def observe(event_type: str, subject_ref: str, source_id: str,
            payload: Optional[Dict[str, Any]] = None, conn=None,
            **kwargs) -> bool:
    """Build + ingest. Returns False on duplicate or killed ingest."""
    evt = build(event_type, subject_ref, source_id, payload, **kwargs)
    return events.ingest(evt, conn=conn)


def recent_financial(conn=None, limit: int = 100) -> list:
    """Recent events across the financial categories (read-only)."""
    out = []
    for cat in ("PAYMENT", "LEDGER", "SETTLEMENT", "PAYOUT", "ADVERTISING"):
        out.extend(events.recent(category=cat, limit=limit, conn=conn))
    out.sort(key=lambda r: r.get("occurred_at") or "", reverse=True)
    return out[:limit]
