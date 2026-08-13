"""Sentinel cross-domain incident engine (Stage 7, Mission 2 states).

Ingests observations from every domain (including the existing financial
incident engine — which it observes, never replaces). Idempotent by
incident_key — a deterministic dedupe key built from explicit components,
never from model output. Every transition is append-only recorded with
actor + note.

Mission 2 lifecycle: DETECTED → INVESTIGATING → CONFIRMED → CONTAINING →
RECOVERING → VERIFYING → MONITORING → RESOLVED, with ESCALATED, SUPPRESSED
and FALSE_POSITIVE as human-judgment branches. Recurrence after RESOLVED
reopens the same incident (observation_count grows; history is preserved).
Suppression requires a reason and an expiry — suppressed incidents still
exist and still count observations.

SC4 holds: leaving VERIFYING requires an independent verifier.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.sentinel import evidence, store
from services.sentinel.constitution import CONSTITUTION_VERSION

INCIDENT_TYPES = (
    "SECURITY_INTRUSION", "ACCOUNT_TAKEOVER", "ABUSE", "DATA_EXPOSURE",
    "FINANCIAL_DISCREPANCY", "PROVIDER_OUTAGE", "INVARIANT_VIOLATION",
    "AI_SAFETY", "OPERATIONAL_DEGRADATION", "COMPLIANCE",
    # Mission 3 identity incident types. Detection ≠ guilt: each of these
    # names a *suspicion* backed by evidence refs, never a verdict.
    "CREDENTIAL_STUFFING", "RECOVERY_ABUSE", "ACCOUNT_TAKEOVER_SUSPECTED",
    "SESSION_ANOMALY", "DEVICE_ANOMALY", "NETWORK_ANOMALY",
    "ADMIN_IDENTITY_ANOMALY", "COORDINATED_IDENTITY_ABUSE",
)

# Canonical states (Mission 2).
STATES = (
    "DETECTED", "INVESTIGATING", "CONFIRMED", "CONTAINING", "RECOVERING",
    "VERIFYING", "MONITORING", "RESOLVED", "ESCALATED", "SUPPRESSED",
    "FALSE_POSITIVE",
)

# States that count as "open" for the operator surface. SUPPRESSED is not
# listed — but suppressed incidents are never deleted and stay queryable.
OPEN_STATES = ("DETECTED", "INVESTIGATING", "CONFIRMED", "CONTAINING",
               "RECOVERING", "VERIFYING", "MONITORING", "ESCALATED")

_CLOSED_FOR_LISTING = ("RESOLVED", "FALSE_POSITIVE", "SUPPRESSED")

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DETECTED": ("INVESTIGATING", "CONFIRMED", "ESCALATED", "SUPPRESSED", "FALSE_POSITIVE"),
    "INVESTIGATING": ("CONFIRMED", "MONITORING", "ESCALATED", "SUPPRESSED", "FALSE_POSITIVE"),
    "CONFIRMED": ("CONTAINING", "RECOVERING", "ESCALATED", "SUPPRESSED"),
    "CONTAINING": ("RECOVERING", "ESCALATED"),
    "RECOVERING": ("VERIFYING", "ESCALATED"),
    "VERIFYING": ("MONITORING", "RECOVERING", "RESOLVED"),
    "MONITORING": ("RESOLVED", "INVESTIGATING"),
    "ESCALATED": ("INVESTIGATING", "CONFIRMED", "CONTAINING"),
    "SUPPRESSED": ("INVESTIGATING", "FALSE_POSITIVE"),
    "RESOLVED": ("INVESTIGATING",),   # reopen on recurrence
    "FALSE_POSITIVE": (),
}

# Transitions asserting "the fix worked" — they need an independent verifier.
_VERIFICATION_EXITS = (("VERIFYING", "MONITORING"), ("VERIFYING", "RESOLVED"))

# A recurrence within this many minutes of resolution is counted but does not
# reopen (residue of the same episode, not a new one).
REOPEN_COOLDOWN_MINUTES = 10

_TS = "%Y-%m-%d %H:%M:%S"


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class IncidentRef:
    incident_key: str
    state: str
    created: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


def _parse(ts) -> datetime | None:
    try:
        return datetime.strptime(str(ts)[:19], _TS).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def dedupe_key(*components) -> str:
    """Deterministic incident key from explicit components (rule id, subject
    ref, time bucket, …). Components must be scalar and non-empty — free-form
    blobs (and therefore model output) cannot slip in unnoticed (SC2)."""
    if not components:
        raise ValueError("dedupe_key requires at least one component")
    parts = []
    for comp in components:
        if not isinstance(comp, (str, int)):
            raise ValueError(f"dedupe components must be scalar, got {type(comp).__name__}")
        text = str(comp).strip()
        if not text:
            raise ValueError("dedupe components must be non-empty")
        parts.append(text)
    return "inc_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def open_incident(incident_key: str, incident_type: str, severity: str,
                  title: str, opened_by: str, detail: dict | None = None,
                  conn=None, *, event_ids: tuple = (),
                  owner_action_required: bool = False) -> IncidentRef:
    """Idempotent by key. Re-opening an existing key records an observation
    (count + last_seen bump; reopen if it recurred after resolution)."""
    if incident_type not in INCIDENT_TYPES:
        raise ValueError(f"unknown incident type {incident_type!r} (SC15)")
    if not str(incident_key or "").strip():
        raise ValueError("incident_key is required")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT state FROM sentinel_incidents WHERE incident_key = ?", (incident_key,))
        row = cur.fetchone()
        if row:
            result = record_observation(incident_key, opened_by,
                                        note="recurrence observed",
                                        event_ids=event_ids, conn=c)
            return IncidentRef(incident_key, result["state"], created=False)
        now = _fmt(_utcnow())
        detail = dict(detail or {})
        if event_ids:
            detail.setdefault("related_event_ids", [str(e) for e in event_ids])
        cur.execute(
            """INSERT INTO sentinel_incidents
               (incident_key, incident_type, severity, state, title, opened_by,
                first_seen_at, last_seen_at, observation_count,
                owner_action_required, deployment_sha, policy_version, detail_json)
               VALUES (?, ?, ?, 'DETECTED', ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (incident_key, incident_type, severity, title, opened_by,
             now, now, 1 if owner_action_required else 0,
             store.deployment_sha(), CONSTITUTION_VERSION,
             json.dumps(detail, default=str)))
        evidence.append("incident_opened", opened_by,
                        {"incident_key": incident_key, "incident_type": incident_type,
                         "severity": severity, "title": title,
                         "event_ids": [str(e) for e in event_ids]}, conn=c)
    return IncidentRef(incident_key, "DETECTED", created=True)


def record_observation(incident_key: str, actor_id: str, note: str = "",
                       event_ids: tuple = (), conn=None,
                       *, now: datetime | None = None) -> dict:
    """Count a recurrence against an existing incident. Reopens RESOLVED
    incidents (outside the cooldown) and un-suppresses expired suppressions.
    Never deletes, never resets history."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT state, updated_at, suppressed_until, observation_count "
            "FROM sentinel_incidents WHERE incident_key = ?", (incident_key,))
        row = cur.fetchone()
        if not row:
            raise TransitionError(f"unknown incident {incident_key!r}")
        state, updated_at, suppressed_until = str(row[0]), row[1], row[2]
        cur.execute(
            "UPDATE sentinel_incidents SET observation_count = observation_count + 1, "
            "last_seen_at = ?, updated_at = ? WHERE incident_key = ?",
            (_fmt(now), _fmt(now), incident_key))
        reopened = False
        if state == "RESOLVED":
            resolved_at = _parse(updated_at)
            outside_cooldown = (resolved_at is None or
                                now - resolved_at >= timedelta(minutes=REOPEN_COOLDOWN_MINUTES))
            if outside_cooldown:
                _apply_transition(c, incident_key, "RESOLVED", "INVESTIGATING",
                                  actor_id, note or "recurrence after resolution")
                state, reopened = "INVESTIGATING", True
        elif state == "SUPPRESSED":
            until = _parse(suppressed_until)
            if until is not None and now >= until:
                _apply_transition(c, incident_key, "SUPPRESSED", "INVESTIGATING",
                                  actor_id, note or "suppression expired on recurrence")
                state, reopened = "INVESTIGATING", True
        if event_ids:
            evidence.append("incident_observation", actor_id,
                            {"incident_key": incident_key,
                             "event_ids": [str(e) for e in event_ids],
                             "reopened": reopened}, conn=c)
    return {"incident_key": incident_key, "state": state, "reopened": reopened}


def _apply_transition(c, incident_key: str, from_state: str, to_state: str,
                      actor_id: str, note: str) -> None:
    cur = c.cursor()
    cur.execute(
        "UPDATE sentinel_incidents SET state = ?, updated_at = ? WHERE incident_key = ?",
        (to_state, _fmt(_utcnow()), incident_key))
    if to_state != "SUPPRESSED":
        # Leaving suppression (or any normal move) clears the suppression fields.
        cur.execute(
            "UPDATE sentinel_incidents SET suppressed_reason = '', suppressed_until = NULL "
            "WHERE incident_key = ? AND state != 'SUPPRESSED'", (incident_key,))
    cur.execute(
        """INSERT INTO sentinel_incident_transitions
           (incident_key, from_state, to_state, actor_id, note)
           VALUES (?, ?, ?, ?, ?)""",
        (incident_key, from_state, to_state, actor_id, note))
    evidence.append("incident_transition", actor_id,
                    {"incident_key": incident_key, "from": from_state,
                     "to": to_state, "note": note}, conn=c)


def transition(incident_key: str, to_state: str, actor_id: str,
               note: str = "", *, verified_by: str | None = None,
               resolution_code: str = "", conn=None) -> IncidentRef:
    """Move an incident through its lifecycle. Guards:
    - transition must be in ALLOWED_TRANSITIONS,
    - leaving VERIFYING (to MONITORING/RESOLVED) requires ``verified_by``
      different from the transitioning actor (SC4),
    - RESOLVED / FALSE_POSITIVE require a non-empty note,
    - SUPPRESSED must go through ``suppress()`` (reason + expiry are mandatory).
    """
    if to_state not in STATES:
        raise TransitionError(f"unknown state {to_state!r} (SC15)")
    if to_state == "SUPPRESSED":
        raise TransitionError("use suppress(): suppression requires a reason and an expiry")
    if to_state in ("RESOLVED", "FALSE_POSITIVE") and not str(note or "").strip():
        raise TransitionError(f"{to_state} requires a note")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT state FROM sentinel_incidents WHERE incident_key = ?", (incident_key,))
        row = cur.fetchone()
        if not row:
            raise TransitionError(f"unknown incident {incident_key!r}")
        from_state = str(row[0])
        if to_state not in ALLOWED_TRANSITIONS.get(from_state, ()):
            raise TransitionError(f"{from_state} -> {to_state} is not an allowed transition")
        if (from_state, to_state) in _VERIFICATION_EXITS:
            if not verified_by or verified_by == actor_id:
                raise TransitionError(
                    "verification exit requires an independent verifier (SC4)")
        _apply_transition(c, incident_key, from_state, to_state, actor_id,
                          note if not verified_by else f"{note} [verified_by={verified_by}]")
        if to_state == "RESOLVED" and resolution_code:
            cur.execute(
                "UPDATE sentinel_incidents SET resolution_code = ? WHERE incident_key = ?",
                (str(resolution_code)[:100], incident_key))
    return IncidentRef(incident_key, to_state, created=False)


def suppress(incident_key: str, actor_id: str, reason: str,
             until_minutes: int, conn=None) -> IncidentRef:
    """Suppress with a mandatory reason and expiry. The incident still exists,
    still counts observations, and auto-returns to INVESTIGATING when a
    recurrence lands after the expiry."""
    if not str(reason or "").strip():
        raise TransitionError("suppression requires a reason")
    minutes = int(until_minutes)
    if minutes <= 0 or minutes > 60 * 24 * 30:
        raise TransitionError("suppression expiry must be bounded (SC14)")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT state FROM sentinel_incidents WHERE incident_key = ?", (incident_key,))
        row = cur.fetchone()
        if not row:
            raise TransitionError(f"unknown incident {incident_key!r}")
        from_state = str(row[0])
        if "SUPPRESSED" not in ALLOWED_TRANSITIONS.get(from_state, ()):
            raise TransitionError(f"{from_state} -> SUPPRESSED is not an allowed transition")
        until = _fmt(_utcnow() + timedelta(minutes=minutes))
        cur.execute(
            "UPDATE sentinel_incidents SET suppressed_reason = ?, suppressed_until = ? "
            "WHERE incident_key = ?", (reason[:500], until, incident_key))
        _apply_transition(c, incident_key, from_state, "SUPPRESSED", actor_id,
                          f"suppressed: {reason[:200]} (until {until})")
    return IncidentRef(incident_key, "SUPPRESSED", created=False)


def get(incident_key: str, conn=None) -> dict | None:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT incident_key, incident_type, severity, state, title, opened_by, "
            "opened_at, updated_at, first_seen_at, last_seen_at, observation_count, "
            "owner_action_required, resolution_code, suppressed_reason, suppressed_until, "
            "detail_json FROM sentinel_incidents WHERE incident_key = ?",
            (incident_key,))
        row = cur.fetchone()
    if not row:
        return None
    return {"incident_key": row[0], "incident_type": row[1], "severity": row[2],
            "state": row[3], "title": row[4], "opened_by": row[5],
            "opened_at": row[6], "updated_at": row[7],
            "first_seen_at": row[8], "last_seen_at": row[9],
            "observation_count": int(row[10] or 1),
            "owner_action_required": bool(row[11]),
            "resolution_code": row[12] or "",
            "suppressed_reason": row[13] or "", "suppressed_until": row[14],
            "detail": json.loads(row[15] or "{}")}


def list_open(conn=None, limit: int = 200, *, include_suppressed: bool = False) -> list[dict]:
    closed = ("RESOLVED", "FALSE_POSITIVE") if include_suppressed else _CLOSED_FOR_LISTING
    placeholders = ",".join("?" for _ in closed)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            f"SELECT incident_key, incident_type, severity, state, title, opened_at, "
            f"observation_count, last_seen_at, owner_action_required "
            f"FROM sentinel_incidents WHERE state NOT IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT ?", (*closed, max(1, min(int(limit), 500))))
        rows = cur.fetchall()
    return [{"incident_key": r[0], "incident_type": r[1], "severity": r[2],
             "state": r[3], "title": r[4], "opened_at": r[5],
             "observation_count": int(r[6] or 1), "last_seen_at": r[7],
             "owner_action_required": bool(r[8])} for r in rows]
