"""Sentinel canonical event envelope — SentinelEventV1 (Stage 2, Mission 2).

One event shape for every domain. Payloads are redacted BEFORE persistence
(classification Stage 3) so secrets can never enter evidence (SC5/SC9).
Ingestion is idempotent at the database level via UNIQUE dedupe_key —
the webhook-inbox pattern, not an in-memory cache.

Mission 2 additions: source provenance (system/component/source event id),
source trust grades (CONFIGURED is never silently truth), actor types (no
"everything is SYSTEM"), typed entity refs, freshness (received_at /
expires_at), impact dimensions, correlation keys, and policy context.
Every new field has a safe default so the envelope stays one shape.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.sentinel import classification, entities, killswitches, source_trust as trust_mod, store
from services.sentinel.constitution import CONSTITUTION_VERSION

CATEGORIES = (
    "AUTH", "SESSION", "ADMIN", "PRIVACY", "SECURITY",
    "PAYMENT", "LEDGER", "SETTLEMENT", "PAYOUT", "ADVERTISING",
    "PROVIDER", "DEPLOYMENT", "WORKER", "UNDX", "SENTINEL_SELF",
)

SEVERITIES = ("info", "low", "medium", "high", "critical")

IMPACT_LEVELS = ("none", "low", "medium", "high", "critical")

EVENT_VERSION = "1"


class EventRejected(ValueError):
    """Raised when an event violates the envelope contract (fail closed)."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _default_actor_type(actor_id: str) -> str:
    """Deterministic default from well-known actor-id prefixes. Adapters
    should set actor_type explicitly; this keeps internal emitters honest
    without forcing every call site to repeat itself."""
    aid = str(actor_id or "")
    if aid.startswith("undx."):
        return "UNDX_AGENT"
    if aid.startswith(("sentinel.", "service.")):
        return "SERVICE"
    if aid.startswith("worker."):
        return "WORKER"
    if aid.startswith("runbook."):
        return "RUNBOOK"
    return "SYSTEM"


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
    # --- SentinelEventV1 (Mission 2) ---
    event_version: str = EVENT_VERSION
    source_system: str = ""          # e.g. "pulsesoc", "stripe", "railway"
    source_component: str = ""       # e.g. "security_center_bridge"
    source_event_id: str = ""        # id in the source system, for tracebacks
    source_trust: str = "UNKNOWN"    # see source_trust.SOURCE_TRUST
    environment: str = ""            # "production" / "development" / ""
    actor_type: str = ""             # derived from actor_id when omitted
    resource_type: str | None = None
    resource_ref: str | None = None
    session_ref: str | None = None
    device_ref: str | None = None
    network_ref: str | None = None
    expires_at: str | None = None    # after this, the observation is STALE
    confidence: float | None = None  # defaults to the trust ceiling
    operational_impact: str = "none"
    security_impact: str = "none"
    financial_impact: str = "none"
    privacy_impact: str = "none"
    compliance_impact: str = "none"
    correlation_keys: tuple = ()
    evidence_refs: tuple = ()
    policy_context: dict = field(default_factory=dict)

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
        if not isinstance(self.policy_context, dict):
            raise EventRejected("policy_context must be a dict")

        # Source trust: closed vocabulary, fail closed.
        try:
            trust_mod.validate(self.source_trust)
        except trust_mod.SourceTrustError as exc:
            raise EventRejected(str(exc)) from exc

        # Actor type: explicit value must be valid; omitted → deterministic
        # default from the actor id (never a silent free-for-all).
        if self.actor_type:
            try:
                entities.validate_actor_type(self.actor_type)
            except entities.EntityRefError as exc:
                raise EventRejected(str(exc)) from exc
        else:
            object.__setattr__(self, "actor_type", _default_actor_type(self.actor_id))

        # Impact dimensions: closed vocabulary.
        for name in ("operational_impact", "security_impact", "financial_impact",
                     "privacy_impact", "compliance_impact"):
            if getattr(self, name) not in IMPACT_LEVELS:
                raise EventRejected(f"unknown {name} {getattr(self, name)!r} (SC15)")

        # Confidence is capped by what the trust grade can support. An event
        # may not claim more certainty than its provenance allows (SC4).
        ceiling = trust_mod.confidence_ceiling(self.source_trust)
        if self.confidence is None:
            object.__setattr__(self, "confidence", ceiling)
        else:
            conf = float(self.confidence)
            if not (0.0 <= conf <= 1.0):
                raise EventRejected("confidence must be within [0, 1]")
            if conf > ceiling + 1e-9:
                raise EventRejected(
                    f"confidence {conf} exceeds ceiling {ceiling} for "
                    f"source_trust {self.source_trust} (SC4)")

        # Normalise sequence fields to tuples of strings.
        object.__setattr__(self, "correlation_keys",
                           tuple(str(k) for k in (self.correlation_keys or ())))
        object.__setattr__(self, "evidence_refs",
                           tuple(str(k) for k in (self.evidence_refs or ())))

        # Typed refs, when supplied, must parse (SC15).
        for name in ("resource_ref", "session_ref", "device_ref", "network_ref"):
            value = getattr(self, name)
            if value and ":" in str(value) and not entities.is_valid_ref(str(value)):
                raise EventRejected(f"malformed {name} {value!r} (SC15)")

        if not self.dedupe_key:
            # Deterministic default: same source+type+subject+time = same event.
            basis = "|".join((self.source, self.category, self.event_type,
                              str(self.subject_type), str(self.subject_id), self.occurred_at))
            object.__setattr__(self, "dedupe_key", hashlib.sha256(basis.encode()).hexdigest())


def ingest(event: Event, conn=None) -> bool:
    """Persist one event. Returns True if newly stored, False if it was a
    duplicate (dedupe) or ingest is killed. Payload is redacted to
    CONFIDENTIAL ceiling before it touches disk. received_at is stamped
    here — the moment Sentinel took custody, distinct from occurred_at."""
    if not killswitches.ingest_enabled():
        return False
    safe_payload = classification.redact(event.payload, classification.Level.CONFIDENTIAL)
    safe_policy = classification.redact(event.policy_context, classification.Level.CONFIDENTIAL)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT 1 FROM sentinel_events WHERE dedupe_key = ?", (event.dedupe_key,))
        if cur.fetchone():
            return False
        cur.execute(
            """INSERT INTO sentinel_events
               (event_id, event_version, dedupe_key, category, event_type, severity,
                confidence, actor_type, actor_id, subject_type, subject_id,
                resource_type, resource_ref, session_ref, device_ref, network_ref,
                source, source_system, source_component, source_event_id,
                source_trust, environment, occurred_at, received_at, expires_at,
                operational_impact, security_impact, financial_impact,
                privacy_impact, compliance_impact, correlation_keys_json,
                evidence_refs_json, policy_context_json, deployment_sha,
                policy_version, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event.event_id, event.event_version, event.dedupe_key, event.category,
             event.event_type, event.severity, float(event.confidence),
             event.actor_type, event.actor_id, event.subject_type, event.subject_id,
             event.resource_type, event.resource_ref, event.session_ref,
             event.device_ref, event.network_ref, event.source,
             event.source_system or event.source, event.source_component,
             event.source_event_id, event.source_trust, event.environment,
             event.occurred_at, _utcnow(), event.expires_at,
             event.operational_impact, event.security_impact,
             event.financial_impact, event.privacy_impact, event.compliance_impact,
             json.dumps(list(event.correlation_keys)),
             json.dumps(list(event.evidence_refs)),
             json.dumps(safe_policy, default=str),
             store.deployment_sha(), CONSTITUTION_VERSION,
             json.dumps(safe_payload, default=str)))
        return True


def recent(category: str | None = None, limit: int = 100, conn=None) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with store.connection(conn) as c:
        cur = c.cursor()
        base = ("SELECT event_id, category, event_type, severity, actor_id, subject_type, "
                "subject_id, source, occurred_at, payload_json, actor_type, source_trust, "
                "source_system, confidence, correlation_keys_json FROM sentinel_events ")
        if category:
            cur.execute(base + "WHERE category = ? ORDER BY id DESC LIMIT ?", (category, limit))
        else:
            cur.execute(base + "ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "event_id": r[0], "category": r[1], "event_type": r[2], "severity": r[3],
            "actor_id": r[4], "subject_type": r[5], "subject_id": r[6], "source": r[7],
            "occurred_at": r[8], "payload": json.loads(r[9] or "{}"),
            "actor_type": r[10], "source_trust": r[11], "source_system": r[12],
            "confidence": r[13], "correlation_keys": json.loads(r[14] or "[]"),
        })
    return out
