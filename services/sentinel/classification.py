"""Sentinel data classification (Stage 3).

Canonical classification levels and field-category rules used to decide what
may be stored in evidence, sent to UNDX, or shared with external vendors.

Pulse ID is private internal account data (HIGHLY_RESTRICTED for external
sharing purposes); public surfaces must use ``public_player_id``
(services/pulse_id_service.py) — Sentinel never weakens that boundary.
"""

from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    SENSITIVE = 3
    HIGHLY_RESTRICTED = 4


# Field categories → classification. Deny-by-default: unknown fields are
# treated as CONFIDENTIAL, never PUBLIC (constitution SC15).
CATEGORY_LEVELS: dict[str, Level] = {
    "public_profile": Level.PUBLIC,
    "content_public": Level.PUBLIC,
    "operational": Level.INTERNAL,
    "administrative": Level.CONFIDENTIAL,
    "identity": Level.SENSITIVE,
    "authentication": Level.HIGHLY_RESTRICTED,
    "payment": Level.HIGHLY_RESTRICTED,
    "financial": Level.SENSITIVE,
    "privacy": Level.SENSITIVE,
    "security": Level.CONFIDENTIAL,
    "secret": Level.HIGHLY_RESTRICTED,
    "provider": Level.CONFIDENTIAL,
    "customer_content": Level.SENSITIVE,
}

DEFAULT_LEVEL = Level.CONFIDENTIAL

# Field-name patterns that must never be persisted in Sentinel evidence or
# handed to UNDX / external adapters in raw form.
_FORBIDDEN_SUBSTRINGS = (
    "password", "passwd", "secret", "private_key", "api_key", "apikey",
    "token", "authorization", "cookie", "card_number", "pan", "cvv", "cvc",
    "ssn", "database_url",
)
# Identifiers that are internal-only (never in public payloads / vendor exports).
_INTERNAL_ID_FIELDS = ("pulse_id", "user_id", "internal_user_id", "email", "phone")

# Operational security metadata (Mission 3, Stage 24): fields that classify at
# INTERNAL so the UNDX advisory surface can actually reason about identity —
# a fully redacted context would hide contradicting evidence, which Stage 18
# forbids. Nothing here is a secret, a raw identifier, or user content:
# hashed refs, closed-vocabulary states, timestamps, and Sentinel's own
# deterministic explanations.
_OPERATIONAL_METADATA_FIELDS = frozenset({
    "event_id", "category", "event_type", "severity", "occurred_at",
    "received_at", "observed_at", "expires_at", "expired", "source",
    "source_trust", "confidence", "subject", "subject_ref", "session_ref",
    "device_ref", "network_ref", "trust_state", "risk_score", "reasons",
    "risk_reasons", "contradicting", "contradicting_evidence",
    "evidence_refs", "signal_quality_note", "note",
    "credential_risk", "recovery_risk", "session_risk", "device_risk",
    "network_risk", "admin_risk", "behavioral_risk",
    # Mission 4 (Stage 29): external-intelligence vocabulary. Closed-set
    # verdicts, digests, external indicator refs (CVEs, packages, hashes —
    # threat indicators, not user data), and Sentinel's own deterministic
    # explanations. No secrets, no internal identifiers, no user content.
    "provider_id", "provider_capability", "capability", "observation_id",
    "indicator_type", "indicator_ref", "indicator_digest", "finding_type",
    "verdict", "provider_labels", "provider_reasons", "negative_result",
    "staleness_note", "fetched_at", "disagreement", "providers",
    "external_score", "external_score_capped", "internal_corroboration_score",
    "risk_band", "enforcement", "authority_note", "finding_id",
    "vulnerability_id", "aliases", "package", "affected_version",
    "fixed_version", "ecosystem", "applicability", "priority",
    "known_exploited", "incident_key", "triage_reasons", "catalog_version",
})

REDACTED = "[REDACTED:sentinel]"


def classify_field(field_name: str, category: str | None = None) -> Level:
    name = (field_name or "").lower()
    if any(s in name for s in _FORBIDDEN_SUBSTRINGS):
        return Level.HIGHLY_RESTRICTED
    if name in _INTERNAL_ID_FIELDS:
        return Level.SENSITIVE
    if name in _OPERATIONAL_METADATA_FIELDS:
        return Level.INTERNAL
    if category:
        return CATEGORY_LEVELS.get(category, DEFAULT_LEVEL)
    return DEFAULT_LEVEL


def redact(payload: dict, max_level: Level = Level.CONFIDENTIAL) -> dict:
    """Return a copy of ``payload`` with every field above ``max_level``
    replaced by a redaction marker. Nested dicts are handled; lists of dicts
    too. Non-dict values are kept only if their field classifies at or below
    ``max_level``."""
    out: dict = {}
    for key, value in (payload or {}).items():
        level = classify_field(str(key))
        if isinstance(value, dict):
            out[key] = redact(value, max_level)
        elif isinstance(value, list):
            out[key] = [redact(v, max_level) if isinstance(v, dict) else
                        (REDACTED if level > max_level else v) for v in value]
        elif level > max_level:
            out[key] = REDACTED
        else:
            out[key] = value
    return out


def external_share_allowed(field_name: str) -> bool:
    """Stage 27 default: MINIMIZE. Only fields classifying at INTERNAL or
    PUBLIC may leave PulseSoc; everything else requires an explicit,
    documented policy exception."""
    return classify_field(field_name) <= Level.INTERNAL
