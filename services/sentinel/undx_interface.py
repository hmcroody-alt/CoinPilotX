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
READ_SURFACES = ("recent_events", "open_incidents", "provider_health",
                 "identity_context", "external_threat_context")

_UNDX_REDACTION_CEILING = Level.INTERNAL


def _redact_rows(rows: list[dict]) -> list[dict]:
    return [classification.redact(row, _UNDX_REDACTION_CEILING) for row in rows]


def read(surface: str, *, category: str | None = None, limit: int = 50,
         subject: str | None = None, conn=None) -> dict:
    """Structured read. Unknown surfaces fail closed."""
    limit = max(1, min(int(limit), 200))
    if surface == "recent_events":
        rows = events.recent(category=category, limit=limit, conn=conn)
    elif surface == "open_incidents":
        rows = incidents.list_open(conn=conn, limit=limit)
    elif surface == "provider_health":
        rows = providers.health_table(conn=conn)
    elif surface == "identity_context":
        return identity_context(subject, limit=limit, conn=conn)
    elif surface == "external_threat_context":
        return external_threat_context(subject, limit=limit, conn=conn)
    else:
        return {"ok": False, "error": f"unknown surface {surface!r} (SC15)",
                "surfaces": READ_SURFACES}
    return {"ok": True, "surface": surface, "rows": _redact_rows(rows),
            "authority_note": "ADVISORY READ — model output is never authority (SC2)"}


def identity_context(subject: str | None, *, limit: int = 50, conn=None) -> dict:
    """Mission 3 (Stage 24): the identity-analyst context for one subject.

    Read-only and redacted like every other surface. Deliberately includes
    CONTRADICTING evidence alongside the risk reasons (Stage 18) — a model
    reasoning about identity must see what argues AGAINST the hypothesis,
    not only what supports it. Exposes no mutation, no raw tokens, no raw
    network identifiers (refs are already hashed at ingest)."""
    subject = str(subject or "").strip()
    if not subject:
        return {"ok": False, "error": "identity_context requires subject= (SC15)"}
    from services.sentinel import graph, identity_trust, store

    limit = max(1, min(int(limit), 200))
    with store.connection(conn) as c:
        cur = c.cursor()
        # Security-relevant timeline for the subject (bounded).
        cur.execute(
            "SELECT event_id, category, event_type, severity, occurred_at, "
            "source_trust, confidence, device_ref, network_ref "
            "FROM sentinel_events WHERE subject_id = ? AND category IN "
            "('AUTH','SESSION','SECURITY','ADMIN') ORDER BY id DESC LIMIT ?",
            (subject.partition(":")[2] or subject, limit))
        timeline = [{"event_id": r[0], "category": r[1], "event_type": r[2],
                     "severity": r[3], "occurred_at": r[4], "source_trust": r[5],
                     "confidence": r[6], "device_ref": r[7], "network_ref": r[8]}
                    for r in cur.fetchall()]
        risk = identity_trust.latest(subject, conn=c)
        try:
            etype, eid = subject.split(":", 1)
            edges = graph.neighbors(etype, eid, limit=50, conn=c)
        except ValueError:
            edges = []
    payload = {
        "subject": subject,
        "timeline": timeline,
        "session_trust": risk,
        "risk_reasons": (risk or {}).get("reasons", []),
        "contradicting_evidence": (risk or {}).get("contradicting", []),
        "relationships": edges,
        "signal_quality_note": (
            "device identity is CLIENT_REPORTED (salted hash of a "
            "client-generated id + user agent) — forgeable, not fingerprinting; "
            "network refs are internal hashed observations, no external "
            "reputation data"),
    }
    return {"ok": True, "surface": "identity_context",
            "rows": _redact_rows([payload]),
            "authority_note": "ADVISORY READ — model output is never authority "
                              "(SC2); expected response shape is the "
                              "submit_hypothesis contract"}


def external_threat_context(subject: str | None, *, limit: int = 50,
                            conn=None) -> dict:
    """Mission 4 (Stage 29): read-only external-intelligence context for one
    indicator, ``subject`` = "INDICATOR_TYPE:ref" (e.g. "CVE:CVE-2026-1234",
    "PACKAGE_VERSION:PyPI:flask:2.0.0").

    UNDX gets: per-provider verdicts side by side (disagreement preserved),
    fused risk with reasons, and related supply-chain findings. UNDX may NOT:
    dismiss or modify findings, upgrade dependencies, block indicators,
    revoke sessions, hold funds, upload files, or call providers — none of
    those operations exist on this surface, and analyses it submits back are
    ADVISORY (SC2)."""
    subject = str(subject or "").strip()
    itype, _, ref = subject.partition(":")
    from services.sentinel import external_fusion, external_observations, store

    if itype not in external_observations.INDICATOR_TYPES or not ref:
        return {"ok": False,
                "error": "external_threat_context requires subject="
                         "'INDICATOR_TYPE:ref' with a known indicator type (SC15)",
                "indicator_types": external_observations.INDICATOR_TYPES}
    limit = max(1, min(int(limit), 200))
    observations = external_observations.for_indicator(itype, ref, limit=limit,
                                                      conn=conn)
    fused = external_fusion.fuse(itype, ref, conn=conn)
    findings = []
    with store.connection(conn) as c:
        cur = c.cursor()
        parts = ref.split(":")
        package = parts[1] if len(parts) == 3 else ref  # "eco:pkg:ver" → pkg
        cur.execute(
            "SELECT finding_id, vulnerability_id, package, affected_version, "
            "applicability, priority, known_exploited, incident_key "
            "FROM sentinel_vulnerability_findings "
            "WHERE vulnerability_id = ? OR package = ? ORDER BY id DESC LIMIT ?",
            (ref, package, limit))
        findings = [{"finding_id": r[0], "vulnerability_id": r[1],
                     "package": r[2], "affected_version": r[3],
                     "applicability": r[4], "priority": r[5],
                     "known_exploited": bool(r[6]), "incident_key": r[7]}
                    for r in cur.fetchall()]
    payload = {
        "subject": subject,
        "observations": observations,
        "fusion": fused,
        "related_findings": findings,
        "signal_quality_note": (
            "external verdicts are evidence about an indicator, not verdicts "
            "on users or the platform; expired rows are already degraded to "
            "UNKNOWN; disagreement is preserved verbatim"),
    }
    return {"ok": True, "surface": "external_threat_context",
            "rows": _redact_rows([payload]),
            "authority_note": "ADVISORY READ — UNDX cannot dismiss, upgrade, "
                              "block, revoke, hold, upload, or call providers "
                              "from this surface (SC2/SC10, Stage 29)"}


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
