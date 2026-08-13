"""Third-party security-adapter contract (Stage 26) + external sharing policy
(Stage 27).

Contract for future vendor integrations (IP reputation, device intel, fraud
scores). Core doctrine: SIGNAL ≠ GUILT. An external signal:
- is stored as an ADVISORY event, severity-capped at "medium",
- is marked unverified until corroborated by an independent internal signal
  (SC8 — never trust a single high-risk signal),
- can never directly trigger enforcement of any kind.

Outbound direction defaults to MINIMIZE: only fields classifying at INTERNAL
or PUBLIC may leave the platform (classification.external_share_allowed);
everything else requires a documented policy exception.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.sentinel import classification, events
from services.sentinel.identity import Actor, TrustTier, register

SEVERITY_CAP = "medium"  # external signals can never enter as high/critical
_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True)
class AdapterSpec:
    """One external signal source."""
    adapter_id: str
    vendor: str
    description: str
    signal_types: tuple[str, ...]

    def actor(self) -> Actor:
        return register(Actor(f"adapter.{self.adapter_id}", "external",
                              TrustTier.ADVISORY, f"{self.vendor} adapter"))


def normalize_signal(spec: AdapterSpec, signal_type: str, subject_type: str,
                     subject_id: str, severity: str, payload: dict) -> events.Event:
    """Map a vendor signal into the canonical envelope with the doctrine
    applied: severity capped, provenance recorded, verification flag off."""
    if signal_type not in spec.signal_types:
        raise ValueError(f"adapter {spec.adapter_id!r} did not declare signal "
                         f"type {signal_type!r} (SC15)")
    sev = severity if severity in _SEVERITY_ORDER else "low"
    if _SEVERITY_ORDER[sev] > _SEVERITY_ORDER[SEVERITY_CAP]:
        sev = SEVERITY_CAP
    return events.Event(
        category="SECURITY", event_type=f"external_{signal_type}", severity=sev,
        actor_id=spec.actor().actor_id, source=f"adapter.{spec.adapter_id}",
        subject_type=subject_type, subject_id=subject_id,
        payload={**dict(payload or {}),
                 "provenance": spec.vendor, "verified": False,
                 "doctrine": "signal_is_not_guilt"})


def ingest_signal(spec: AdapterSpec, signal_type: str, subject_type: str,
                  subject_id: str, severity: str, payload: dict, conn=None) -> bool:
    return events.ingest(
        normalize_signal(spec, signal_type, subject_type, subject_id, severity, payload),
        conn=conn)


def outbound_filter(payload: dict) -> dict:
    """Stage 27 MINIMIZE: strip every field not explicitly shareable before
    anything leaves the platform. Deny-by-default via classification."""
    out = {}
    for key, value in dict(payload or {}).items():
        if classification.external_share_allowed(str(key)):
            out[key] = value
    return out
