"""Sentinel ↔ UNDX structured interface (Stage 18).

UNDX is intelligence, not root. This module is the ONLY surface UNDX gets:
read-only, structured, redacted. There is no freeform-execution entry point,
no mutation function, and nothing here accepts SQL, shell, or code.

Analyses submitted by UNDX are stored as ADVISORY events — they can inform a
human or a deterministic rule, but no code path treats them as authority
(SC2). Secrets never cross this boundary: everything returned is passed
through classification.redact at INTERNAL ceiling (SC9), stricter than the
CONFIDENTIAL ceiling used for internal evidence.
"""

from __future__ import annotations

from services.sentinel import classification, events, incidents, providers
from services.sentinel.classification import Level
from services.sentinel.identity import UNDX_MODEL

# Explicit allowlist of what UNDX may read. Anything not listed does not exist
# as far as UNDX is concerned (SC11, SC15).
READ_SURFACES = ("recent_events", "open_incidents", "provider_health")

_UNDX_REDACTION_CEILING = Level.INTERNAL


def _redact_rows(rows: list[dict]) -> list[dict]:
    return [classification.redact(row, _UNDX_REDACTION_CEILING) for row in rows]


def read(surface: str, *, category: str | None = None, limit: int = 50,
         conn=None) -> dict:
    """Structured read. Unknown surfaces fail closed."""
    limit = max(1, min(int(limit), 200))
    if surface == "recent_events":
        rows = events.recent(category=category, limit=limit, conn=conn)
    elif surface == "open_incidents":
        rows = incidents.list_open(conn=conn, limit=limit)
    elif surface == "provider_health":
        rows = providers.health_table(conn=conn)
    else:
        return {"ok": False, "error": f"unknown surface {surface!r} (SC15)",
                "surfaces": READ_SURFACES}
    return {"ok": True, "surface": surface, "rows": _redact_rows(rows),
            "authority_note": "ADVISORY READ — model output is never authority (SC2)"}


def submit_analysis(subject_type: str, subject_id: str, summary: str,
                    confidence: float, conn=None) -> dict:
    """UNDX submits an analysis as an ADVISORY observation.

    Stored as a canonical UNDX event with severity 'info' regardless of
    content — a model cannot self-assign severity, because severity feeds
    deterministic correlation and would otherwise be a text-to-authority
    path (SC2/SC8).
    """
    summary = str(summary or "").strip()
    if not summary:
        return {"ok": False, "error": "empty analysis"}
    try:
        confidence = max(0.0, min(float(confidence), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    stored = events.ingest(events.Event(
        category="UNDX", event_type="model_analysis", severity="info",
        actor_id=UNDX_MODEL.actor_id, source="undx.interface",
        subject_type=subject_type, subject_id=subject_id,
        payload={"summary": summary[:2000], "confidence": confidence,
                 "authority": "ADVISORY"}), conn=conn)
    return {"ok": True, "stored": stored, "authority": "ADVISORY",
            "note": "analysis recorded as intelligence; it authorizes nothing (SC2)"}


# --- Hypothesis output contract (Mission 2, Stage 19) -----------------------
#
# When UNDX reasons about an incident it must return THIS shape and nothing
# else. Every field is validated; the contract is deliberately explicit about
# what the model does NOT know (missing_evidence) and what it may NOT do
# (required_authority names a human gate, it never grants one).

HYPOTHESIS_FIELDS = (
    "hypothesis", "confidence", "supporting_evidence_ids",
    "contradicting_evidence_ids", "affected_domains", "estimated_impact",
    "recommended_next_step", "required_authority", "missing_evidence",
)

# The only authorities a hypothesis may name. None of them is self-granting:
# each maps to a human decision outside this interface.
REQUIRED_AUTHORITY_LEVELS = ("NONE", "OWNER_REVIEW", "OWNER_APPROVAL")

_IMPACT_LEVELS = ("none", "low", "medium", "high", "critical")

# A model opinion is DERIVED at best — its confidence may never claim more
# than the DERIVED trust ceiling (SC2/SC4).
_HYPOTHESIS_CONFIDENCE_CEILING = 0.8


def _str_tuple(value, cap: int = 50) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ValueError("expected a list of ids, got a bare string")
    return tuple(str(v)[:120] for v in list(value)[:cap])


def submit_hypothesis(incident_key: str, data: dict, conn=None) -> dict:
    """Validate and store a structured UNDX hypothesis for an incident.

    Fails closed on any malformed field. Stored as an ADVISORY UNDX event —
    a hypothesis is intelligence about an incident, never a verdict on it,
    and required_authority documents which human gate the recommended step
    would need. Nothing here executes that step (SC2, SC10).
    """
    if not isinstance(data, dict):
        return {"ok": False, "error": "hypothesis must be a structured dict"}
    unknown = sorted(set(data) - set(HYPOTHESIS_FIELDS))
    missing = sorted(set(HYPOTHESIS_FIELDS) - set(data))
    if unknown or missing:
        return {"ok": False, "error": f"contract mismatch: missing={missing} unknown={unknown}",
                "required_fields": HYPOTHESIS_FIELDS}

    incident = incidents.get(str(incident_key), conn=conn)
    if not incident:
        return {"ok": False, "error": f"unknown incident {incident_key!r} (SC15)"}

    hypothesis = str(data["hypothesis"] or "").strip()
    if not hypothesis:
        return {"ok": False, "error": "empty hypothesis"}
    try:
        confidence = float(data["confidence"])
    except (TypeError, ValueError):
        return {"ok": False, "error": "confidence must be numeric"}
    if not (0.0 <= confidence <= _HYPOTHESIS_CONFIDENCE_CEILING):
        return {"ok": False,
                "error": f"confidence must be within [0, {_HYPOTHESIS_CONFIDENCE_CEILING}] — "
                         "model output never exceeds the DERIVED ceiling (SC2)"}
    impact = str(data["estimated_impact"] or "").lower()
    if impact not in _IMPACT_LEVELS:
        return {"ok": False, "error": f"estimated_impact must be one of {_IMPACT_LEVELS}"}
    authority = str(data["required_authority"] or "").upper()
    if authority not in REQUIRED_AUTHORITY_LEVELS:
        return {"ok": False,
                "error": f"required_authority must be one of {REQUIRED_AUTHORITY_LEVELS}"}
    try:
        supporting = _str_tuple(data["supporting_evidence_ids"])
        contradicting = _str_tuple(data["contradicting_evidence_ids"])
        domains = _str_tuple(data["affected_domains"], cap=15)
        missing_ev = _str_tuple(data["missing_evidence"], cap=25)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    stored = events.ingest(events.Event(
        category="UNDX", event_type="model_hypothesis", severity="info",
        actor_id=UNDX_MODEL.actor_id, source="undx.interface",
        subject_type="incident", subject_id=str(incident_key),
        payload={
            "hypothesis": hypothesis[:2000],
            "confidence": confidence,
            "supporting_evidence_ids": list(supporting),
            "contradicting_evidence_ids": list(contradicting),
            "affected_domains": list(domains),
            "estimated_impact": impact,
            "recommended_next_step": str(data["recommended_next_step"] or "")[:500],
            "required_authority": authority,
            "missing_evidence": list(missing_ev),
            "authority": "ADVISORY",
        }), conn=conn)
    return {"ok": True, "stored": stored, "authority": "ADVISORY",
            "required_authority": authority,
            "note": "hypothesis recorded; required_authority names a human "
                    "gate — it grants nothing (SC2/SC10)"}
