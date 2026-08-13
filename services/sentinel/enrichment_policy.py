"""Sentinel enrichment request policy (Mission 4, Stages 5–7, 25–26).

Every external query passes ONE gate: ``evaluate()``. It checks the kill
switches, provider configuration, indicator support, purpose, budget, cache
freshness and circuit state, and returns a structured decision with reasons.
A denied decision is final — there is no bypass parameter.

The data-minimization gate (Stage 6) strips internal identifiers, secrets
and user content from every outbound payload and reports what it stripped.
Every allowed request lands in the append-only external data-sharing audit
(Stage 25). File upload is disabled at the vocabulary level (Stage 6): there
is no purpose, capability, or code path that transmits file content.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import json

from services.sentinel import store
from services.sentinel.constitution import CONSTITUTION_VERSION
from services.sentinel import external_observations, external_providers

POLICY_VERSION = "external-enrichment-v1"

ALLOWED_PURPOSES = (
    "THREAT_TRIAGE", "VULNERABILITY_TRIAGE", "SUPPLY_CHAIN_REVIEW",
    "SECURITY_INCIDENT_ENRICHMENT", "PROVIDER_HEALTH_PROBE",
)

DISALLOWED_PURPOSES = (
    "AD_TARGETING", "MARKETING", "USER_PROFILING", "GENERAL_ANALYTICS",
)

# Stage 6: field names that must never leave PulseSoc in an enrichment
# request. Substring match, case-insensitive.
FORBIDDEN_REQUEST_FIELDS = (
    "pulse_id", "user_id", "internal_user_id", "email", "phone",
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "session", "card_number", "pan", "cvv", "cvc", "ssn", "iban",
    "bank", "tax", "payment", "message", "media", "document", "profile",
    "private_key", "api_key", "apikey",
)

# Stage 6 file policy: no capability may carry file content. Hash lookup only.
FORBIDDEN_CAPABILITIES = ("file_upload", "sample_submission", "media_upload")

_TS = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    provider_id: str
    capability: str
    purpose: str
    reasons: tuple[str, ...] = ()
    cached: dict | None = None
    request_id: str = ""


class MinimizationError(ValueError):
    pass


def minimize(payload: dict) -> tuple[dict, list[str]]:
    """Strip every forbidden field from an outbound payload (recursively).
    Returns (safe_payload, stripped_field_paths). The caller sends ONLY the
    safe payload; stripped paths go into the audit row."""
    stripped: list[str] = []

    def walk(d: dict, path: str = "") -> dict:
        out = {}
        for k, v in d.items():
            name = str(k).lower()
            full = path + str(k)
            if any(s in name for s in FORBIDDEN_REQUEST_FIELDS):
                stripped.append(full)
                continue
            if isinstance(v, dict):
                out[k] = walk(v, full + ".")
            elif isinstance(v, list):
                out[k] = [walk(i, full + ".") if isinstance(i, dict) else i
                          for i in v]
            else:
                out[k] = v
        return out

    return walk(dict(payload or {})), stripped


def evaluate(provider_id: str, capability: str, indicator_type: str,
             indicator_ref: str, purpose: str, *, incident_ref: str = "",
             conn=None) -> PolicyDecision:
    """The single gate before any external query. Deny-by-default: every
    check must pass, and each failure is named in reasons."""
    reasons: list[str] = []
    spec = external_providers.PROVIDERS.get(provider_id)

    if capability in FORBIDDEN_CAPABILITIES:
        return PolicyDecision(False, provider_id, capability, purpose,
                              ("file/sample upload is disabled in Mission 4 "
                               "(Stage 6 file policy)",))
    if spec is None:
        return PolicyDecision(False, provider_id, capability, purpose,
                              (f"unknown provider {provider_id!r} (SC15)",))
    if purpose not in ALLOWED_PURPOSES:
        return PolicyDecision(False, provider_id, capability, purpose,
                              (f"purpose {purpose!r} is not an allowed "
                               f"enrichment purpose",))
    if not external_providers.master_enabled():
        reasons.append(f"{external_providers.MASTER_SWITCH} is off (default)")
    elif not external_providers.provider_enabled(provider_id):
        reasons.append(f"kill switch {spec.kill_switch} is off (default)")
    if not spec.configured():
        reasons.append("provider not configured (credentials absent); "
                       "CONFIGURED=false is not FAILED")
    if capability not in spec.capabilities:
        reasons.append(f"capability {capability!r} not supported by {provider_id}")
    if indicator_type not in external_observations.INDICATOR_TYPES:
        reasons.append(f"unknown indicator_type {indicator_type!r} (SC15)")
    if reasons:
        return PolicyDecision(False, provider_id, capability, purpose,
                              tuple(reasons))

    # Cache first (Stage 7): a fresh cached answer means NO external call.
    cached = external_observations.cache_lookup(
        provider_id, capability, indicator_type, indicator_ref, conn=conn)
    if cached is not None:
        return PolicyDecision(False, provider_id, capability, purpose,
                              ("fresh cached observation exists; external "
                               "call suppressed (Stage 7)",), cached=cached)

    # Circuit (Stage 8).
    state = external_providers.circuit_state(provider_id, capability, conn=conn)
    if state == "open":
        breaker = external_providers.load_circuit(provider_id, capability, conn=conn)
        if not breaker.allow_request():
            return PolicyDecision(False, provider_id, capability, purpose,
                                  ("circuit open for this capability; "
                                   "intelligence is UNKNOWN, not SAFE (Stage 8)",))

    # Budget (Stage 9).
    ok, note = external_providers.budget_available(provider_id, conn=conn)
    if not ok:
        return PolicyDecision(False, provider_id, capability, purpose, (note,))

    # Single-flight (Stage 7): an identical in-flight request blocks a second.
    digest = external_observations.indicator_digest(indicator_type, indicator_ref)
    now = datetime.now(timezone.utc)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT request_id FROM sentinel_enrichment_requests "
            "WHERE provider_id=? AND capability=? AND indicator_type=? "
            "AND indicator_digest=? AND status='pending' AND lease_until > ?",
            (provider_id, capability, indicator_type, digest,
             now.strftime(_TS)))
        inflight = cur.fetchone()
        if inflight:
            return PolicyDecision(False, provider_id, capability, purpose,
                                  (f"identical request {inflight[0]} in flight "
                                   f"(single-flight, Stage 7)",))
        request_id = "enrich_" + uuid.uuid4().hex[:16]
        lease = (now + timedelta(seconds=max(30.0, spec.budget.timeout_seconds * 3))
                 ).strftime(_TS)
        cur.execute(
            """INSERT INTO sentinel_enrichment_requests
               (request_id, provider_id, capability, indicator_type,
                indicator_digest, purpose, status, lease_until,
                policy_version, incident_ref)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (request_id, provider_id, capability, indicator_type, digest,
             purpose, lease, POLICY_VERSION, str(incident_ref)[:100]))
    return PolicyDecision(True, provider_id, capability, purpose,
                          ("all policy checks passed",), request_id=request_id)


def complete_request(request_id: str, *, status: str = "completed",
                     conn=None) -> None:
    with store.connection(conn) as c:
        c.cursor().execute(
            "UPDATE sentinel_enrichment_requests SET status=?, "
            "completed_at=datetime('now') WHERE request_id=?",
            (str(status)[:50], request_id))


def record_share_audit(*, provider_id: str, capability: str, purpose: str,
                       indicator_type: str, indicator_ref: str,
                       data_classes_sent: list | None = None,
                       stripped_fields: list | None = None,
                       response_status: str = "", retention_class: str = "standard",
                       incident_ref: str = "", conn=None) -> str:
    """Append-only Stage 25 audit row. Digest only — the raw indicator is not
    copied here; credentials never are."""
    audit_id = "extaudit_" + uuid.uuid4().hex[:16]
    digest = external_observations.indicator_digest(indicator_type, indicator_ref)
    with store.connection(conn) as c:
        c.cursor().execute(
            """INSERT INTO sentinel_external_data_audit
               (audit_id, provider_id, capability, purpose, indicator_type,
                indicator_digest, data_classes_sent_json, stripped_fields_json,
                policy_version, response_status, retention_class, incident_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (audit_id, provider_id, capability, purpose, indicator_type,
             digest,
             json.dumps([str(x)[:50] for x in (data_classes_sent or [])[:20]]),
             json.dumps([str(x)[:200] for x in (stripped_fields or [])[:100]]),
             f"{POLICY_VERSION}/{CONSTITUTION_VERSION}",
             str(response_status)[:50], str(retention_class)[:50],
             str(incident_ref)[:100]))
    return audit_id


def audit_rows(provider_id: str | None = None, *, limit: int = 100,
               conn=None) -> list[dict]:
    with store.connection(conn) as c:
        cur = c.cursor()
        if provider_id:
            cur.execute(
                "SELECT audit_id, provider_id, capability, purpose, "
                "indicator_type, indicator_digest, data_classes_sent_json, "
                "stripped_fields_json, policy_version, requested_at, "
                "response_status FROM sentinel_external_data_audit "
                "WHERE provider_id=? ORDER BY id DESC LIMIT ?",
                (provider_id, max(1, min(int(limit), 500))))
        else:
            cur.execute(
                "SELECT audit_id, provider_id, capability, purpose, "
                "indicator_type, indicator_digest, data_classes_sent_json, "
                "stripped_fields_json, policy_version, requested_at, "
                "response_status FROM sentinel_external_data_audit "
                "ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),))
        rows = cur.fetchall()
    return [{"audit_id": r[0], "provider_id": r[1], "capability": r[2],
             "purpose": r[3], "indicator_type": r[4],
             "indicator_digest": r[5],
             "data_classes_sent": json.loads(r[6] or "[]"),
             "stripped_fields": json.loads(r[7] or "[]"),
             "policy_version": r[8], "requested_at": r[9],
             "response_status": r[10]} for r in rows]


# --- Deletion / privacy request design (Stage 26) ---------------------------
# Mission 4 designs, it does not fake. Deletion capability per provider lives
# in the registry (deletion_capability: none|vendor_api|vendor_ticket|unknown).
# There is deliberately NO function here that marks provider-side data
# "deleted": Sentinel cannot verify vendor-side deletion, so claiming it
# would be a false status. The documented flow (external_retention_and_
# deletion.md) is: owner files the vendor request → owner records the vendor
# confirmation as evidence → the local observation rows expire on TTL and are
# prunable. Local expiry is real and automatic; vendor deletion is tracked as
# evidence, never asserted by code.
