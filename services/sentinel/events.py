"""Sentinel canonical event envelope (Stage 2).

One event shape for every domain. Payloads are redacted BEFORE persistence
(classification Stage 3) so secrets can never enter evidence (SC5/SC9).
Ingestion is idempotent at the database level via UNIQUE dedupe_key —
the webhook-inbox pattern, not an in-memory cache.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.sentinel import classification, killswitches, store
from services.sentinel.constitution import CONSTITUTION_VERSION

CATEGORIES = (
    "AUTH", "SESSION", "ADMIN", "PRIVACY", "SECURITY",
    "PAYMENT", "LEDGER", "SETTLEMENT", "PAYOUT", "ADVERTISING",
    "PROVIDER", "DEPLOYMENT", "WORKER", "UNDX", "SENTINEL_SELF",
)

SEVERITIES = ("info", "low", "medium", "high", "critical")


class EventRejected(ValueError):
    """Raised when an event violates the envelope contract (fail closed)."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class Event:
    category: str
    event_type: str
    severity: str
    actor_id: str
    source: str
    occurred_at: str = field(default_factory=_utcnow)
    subject_type: str | None = None
    subject_id: str | None = None
    payload: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    dedupe_key: str = ""

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise EventRejected(f"unknown category {self.category!r} (SC15)")
        if self.severity not in SEVERITIES:
            raise EventRejected(f"unknown severity {self.severity!r} (SC15)")
        if not str(self.event_type or "").strip():
            raise EventRejected("event_type is required")
        if not str(self.actor_id or "").strip():
            raise EventRejected("actor_id is required (SC12)")
        if not str(self.source or "").strip():
            raise EventRejected("source is required")
        if not isinstance(self.payload, dict):
            raise EventRejected("payload must be a dict")
        if not self.dedupe_key:
            # Deterministic default: same source+type+subject+time = same event.
            basis = "|".join((self.source, self.category, self.event_type,
                              str(self.subject_type), str(self.subject_id), self.occurred_at))
            object.__setattr__(self, "dedupe_key", hashlib.sha256(basis.encode()).hexdigest())


def ingest(event: Event, conn=None) -> bool:
    """Persist one event. Returns True if newly stored, False if it was a
    duplicate (dedupe) or ingest is killed. Payload is redacted to
    CONFIDENTIAL ceiling before it touches disk."""
    if not killswitches.ingest_enabled():
        return False
    safe_payload = classification.redact(event.payload, classification.Level.CONFIDENTIAL)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT 1 FROM sentinel_events WHERE dedupe_key = ?", (event.dedupe_key,))
        if cur.fetchone():
            return False
        cur.execute(
            """INSERT INTO sentinel_events
               (event_id, dedupe_key, category, event_type, severity, actor_id,
                subject_type, subject_id, source, occurred_at, deployment_sha,
                policy_version, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event.event_id, event.dedupe_key, event.category, event.event_type,
             event.severity, event.actor_id, event.subject_type, event.subject_id,
             event.source, event.occurred_at, store.deployment_sha(),
             CONSTITUTION_VERSION, json.dumps(safe_payload, default=str)))
        return True


def recent(category: str | None = None, limit: int = 100, conn=None) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with store.connection(conn) as c:
        cur = c.cursor()
        if category:
            cur.execute(
                "SELECT event_id, category, event_type, severity, actor_id, subject_type, "
                "subject_id, source, occurred_at, payload_json FROM sentinel_events "
                "WHERE category = ? ORDER BY id DESC LIMIT ?", (category, limit))
        else:
            cur.execute(
                "SELECT event_id, category, event_type, severity, actor_id, subject_type, "
                "subject_id, source, occurred_at, payload_json FROM sentinel_events "
                "ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "event_id": r[0], "category": r[1], "event_type": r[2], "severity": r[3],
            "actor_id": r[4], "subject_type": r[5], "subject_id": r[6], "source": r[7],
            "occurred_at": r[8], "payload": json.loads(r[9] or "{}"),
        })
    return out
