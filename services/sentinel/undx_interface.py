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
