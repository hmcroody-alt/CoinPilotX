"""Sentinel cross-domain incident engine (Stage 7).

Ingests observations from every domain (including the existing financial
incident engine — which it observes, never replaces). Idempotent by
incident_key; every transition is append-only recorded with actor + note.

Recovery is gated by SC4: an incident cannot reach RECOVERY_VERIFIED without
an independent verification record, and cannot RESOLVE without a note.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from services.sentinel import evidence, store
from services.sentinel.constitution import CONSTITUTION_VERSION

INCIDENT_TYPES = (
    "SECURITY_INTRUSION", "ACCOUNT_TAKEOVER", "ABUSE", "DATA_EXPOSURE",
    "FINANCIAL_DISCREPANCY", "PROVIDER_OUTAGE", "INVARIANT_VIOLATION",
    "AI_SAFETY", "OPERATIONAL_DEGRADATION", "COMPLIANCE",
)

# 11 canonical states.
STATES = (
    "NEW", "TRIAGED", "CORRELATING", "CONFIRMED", "CONTAINMENT_PROPOSED",
    "CONTAINMENT_APPROVED", "CONTAINED", "RECOVERY_PROPOSED",
    "RECOVERY_VERIFIED", "RESOLVED", "CLOSED",
)

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "NEW": ("TRIAGED", "CLOSED"),
    "TRIAGED": ("CORRELATING", "CONFIRMED", "CLOSED"),
    "CORRELATING": ("CONFIRMED", "TRIAGED", "CLOSED"),
    "CONFIRMED": ("CONTAINMENT_PROPOSED", "RECOVERY_PROPOSED"),
    "CONTAINMENT_PROPOSED": ("CONTAINMENT_APPROVED", "CONFIRMED"),
    "CONTAINMENT_APPROVED": ("CONTAINED",),
    "CONTAINED": ("RECOVERY_PROPOSED",),
    "RECOVERY_PROPOSED": ("RECOVERY_VERIFIED", "CONTAINED", "CONFIRMED"),
    "RECOVERY_VERIFIED": ("RESOLVED",),
    "RESOLVED": ("CLOSED",),
    "CLOSED": (),
}


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class IncidentRef:
    incident_key: str
    state: str
    created: bool


def open_incident(incident_key: str, incident_type: str, severity: str,
                  title: str, opened_by: str, detail: dict | None = None,
                  conn=None) -> IncidentRef:
    """Idempotent: re-opening an existing key returns the existing incident."""
    if incident_type not in INCIDENT_TYPES:
        raise ValueError(f"unknown incident type {incident_type!r} (SC15)")
    if not str(incident_key or "").strip():
        raise ValueError("incident_key is required")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT state FROM sentinel_incidents WHERE incident_key = ?", (incident_key,))
        row = cur.fetchone()
        if row:
            return IncidentRef(incident_key, str(row[0]), created=False)
        cur.execute(
            """INSERT INTO sentinel_incidents
               (incident_key, incident_type, severity, state, title, opened_by,
                deployment_sha, policy_version, detail_json)
               VALUES (?, ?, ?, 'NEW', ?, ?, ?, ?, ?)""",
            (incident_key, incident_type, severity, title, opened_by,
             store.deployment_sha(), CONSTITUTION_VERSION,
             json.dumps(detail or {}, default=str)))
        evidence.append("incident_opened", opened_by,
                        {"incident_key": incident_key, "incident_type": incident_type,
                         "severity": severity, "title": title}, conn=c)
    return IncidentRef(incident_key, "NEW", created=True)


def transition(incident_key: str, to_state: str, actor_id: str,
               note: str = "", *, verified_by: str | None = None,
               conn=None) -> IncidentRef:
    """Move an incident through its lifecycle. Guards:
    - transition must be in ALLOWED_TRANSITIONS,
    - RECOVERY_VERIFIED requires ``verified_by`` != the transitioning actor (SC4),
    - RESOLVED/CLOSED require a non-empty note.
    """
    if to_state not in STATES:
        raise TransitionError(f"unknown state {to_state!r} (SC15)")
    if to_state in ("RESOLVED", "CLOSED") and not str(note or "").strip():
        raise TransitionError(f"{to_state} requires a note")
    if to_state == "RECOVERY_VERIFIED":
        if not verified_by or verified_by == actor_id:
            raise TransitionError(
                "recovery verification must come from an independent verifier (SC4)")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT state FROM sentinel_incidents WHERE incident_key = ?", (incident_key,))
        row = cur.fetchone()
        if not row:
            raise TransitionError(f"unknown incident {incident_key!r}")
        from_state = str(row[0])
        if to_state not in ALLOWED_TRANSITIONS.get(from_state, ()):
            raise TransitionError(f"{from_state} -> {to_state} is not an allowed transition")
        cur.execute(
            "UPDATE sentinel_incidents SET state = ?, updated_at = datetime('now') "
            "WHERE incident_key = ?", (to_state, incident_key))
        cur.execute(
            """INSERT INTO sentinel_incident_transitions
               (incident_key, from_state, to_state, actor_id, note)
               VALUES (?, ?, ?, ?, ?)""",
            (incident_key, from_state, to_state, actor_id, note))
        evidence.append("incident_transition", actor_id,
                        {"incident_key": incident_key, "from": from_state,
                         "to": to_state, "note": note,
                         "verified_by": verified_by or ""}, conn=c)
    return IncidentRef(incident_key, to_state, created=False)


def get(incident_key: str, conn=None) -> dict | None:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT incident_key, incident_type, severity, state, title, opened_by, "
            "opened_at, updated_at, detail_json FROM sentinel_incidents WHERE incident_key = ?",
            (incident_key,))
        row = cur.fetchone()
    if not row:
        return None
    return {"incident_key": row[0], "incident_type": row[1], "severity": row[2],
            "state": row[3], "title": row[4], "opened_by": row[5],
            "opened_at": row[6], "updated_at": row[7],
            "detail": json.loads(row[8] or "{}")}


def list_open(conn=None, limit: int = 200) -> list[dict]:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT incident_key, incident_type, severity, state, title, opened_at "
            "FROM sentinel_incidents WHERE state NOT IN ('RESOLVED','CLOSED') "
            "ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),))
        rows = cur.fetchall()
    return [{"incident_key": r[0], "incident_type": r[1], "severity": r[2],
             "state": r[3], "title": r[4], "opened_at": r[5]} for r in rows]
