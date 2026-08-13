"""Sentinel storage layer (Stage 25 decision: existing DB, no new infra).

Built on ``services.db.connect()`` — SQLite locally, PostgreSQL in prod,
identical SQL via the compat layer. Schema creation is idempotent
(CREATE TABLE IF NOT EXISTS), mirroring the platform's imperative-schema
convention; there is no migration framework to hook into.

All Sentinel tables are namespaced ``sentinel_``. Sentinel NEVER writes to
any non-sentinel table.

Functions take an optional ``conn`` so tests can supply an in-memory SQLite
connection; when omitted a fresh platform connection is used and committed.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from services import db as platform_db

# Same precedence list bot.py's health surface uses — the two must agree or
# the "deployment mismatch" rule would fire against ourselves.
DEPLOYMENT_SHA_ENVS = (
    "RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT", "SOURCE_VERSION", "COMMIT_SHA",
    "RAILWAY_GIT_COMMIT",
)


def deployment_sha() -> str:
    for env in DEPLOYMENT_SHA_ENVS:
        value = os.getenv(env, "").strip()
        if value:
            return value
    return "unknown"


SCHEMA_STATEMENTS: tuple[str, ...] = (
    # Canonical events — SentinelEventV1 envelope (Mission 2). dedupe_key
    # UNIQUE = persist-before-process idempotency, same DB-level pattern as
    # webhook_inbox. V1 columns land in the initial CREATE (nothing deployed
    # yet), so this remains purely additive to the platform.
    """CREATE TABLE IF NOT EXISTS sentinel_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        event_version TEXT NOT NULL DEFAULT '1',
        dedupe_key TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        actor_type TEXT NOT NULL DEFAULT 'SYSTEM',
        actor_id TEXT NOT NULL,
        subject_type TEXT,
        subject_id TEXT,
        resource_type TEXT,
        resource_ref TEXT,
        session_ref TEXT,
        device_ref TEXT,
        network_ref TEXT,
        source TEXT NOT NULL,
        source_system TEXT NOT NULL DEFAULT '',
        source_component TEXT NOT NULL DEFAULT '',
        source_event_id TEXT NOT NULL DEFAULT '',
        source_trust TEXT NOT NULL DEFAULT 'UNKNOWN',
        environment TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
        received_at TEXT NOT NULL DEFAULT '',
        expires_at TEXT,
        operational_impact TEXT NOT NULL DEFAULT 'none',
        security_impact TEXT NOT NULL DEFAULT 'none',
        financial_impact TEXT NOT NULL DEFAULT 'none',
        privacy_impact TEXT NOT NULL DEFAULT 'none',
        compliance_impact TEXT NOT NULL DEFAULT 'none',
        correlation_keys_json TEXT NOT NULL DEFAULT '[]',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        policy_context_json TEXT NOT NULL DEFAULT '{}',
        deployment_sha TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_events_cat ON sentinel_events(category, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_events_actor ON sentinel_events(actor_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_events_type ON sentinel_events(event_type, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_events_source ON sentinel_events(source_system, source_event_id)",

    # Incidents (Stage 7, extended Mission 2). Idempotent by incident_key
    # (the deterministic dedupe key), append-only history in
    # sentinel_incident_transitions. Recurrence bumps observation_count and
    # last_seen_at instead of duplicating. Suppression carries a reason and
    # an expiry — suppressed incidents still exist.
    """CREATE TABLE IF NOT EXISTS sentinel_incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_key TEXT NOT NULL UNIQUE,
        incident_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        state TEXT NOT NULL,
        title TEXT NOT NULL,
        opened_by TEXT NOT NULL,
        opened_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        observation_count INTEGER NOT NULL DEFAULT 1,
        owner_action_required INTEGER NOT NULL DEFAULT 0,
        resolution_code TEXT NOT NULL DEFAULT '',
        suppressed_reason TEXT NOT NULL DEFAULT '',
        suppressed_until TEXT,
        deployment_sha TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_incidents_state ON sentinel_incidents(state, updated_at)",
    """CREATE TABLE IF NOT EXISTS sentinel_incident_transitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_key TEXT NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # Evidence chain (Stage 17): append-only, hash-linked.
    """CREATE TABLE IF NOT EXISTS sentinel_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seq INTEGER NOT NULL UNIQUE,
        record_hash TEXT NOT NULL UNIQUE,
        prev_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deployment_sha TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        body_json TEXT NOT NULL
    )""",

    # Relational edges (Stage 9).
    """CREATE TABLE IF NOT EXISTS sentinel_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        src_type TEXT NOT NULL,
        src_id TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        dst_type TEXT NOT NULL,
        dst_id TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        first_seen TEXT NOT NULL DEFAULT (datetime('now')),
        last_seen TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(src_type, src_id, edge_type, dst_type, dst_id)
    )""",

    # Provider capability health (Stage 12).
    """CREATE TABLE IF NOT EXISTS sentinel_provider_capabilities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        capability TEXT NOT NULL,
        status TEXT NOT NULL,
        observed_at TEXT NOT NULL DEFAULT (datetime('now')),
        detail TEXT NOT NULL DEFAULT '',
        UNIQUE(provider, capability)
    )""",

    # Runbook executions (Stage 14/16) — execution and verification are two
    # separate records with separate actors.
    """CREATE TABLE IF NOT EXISTS sentinel_runbook_executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL UNIQUE,
        runbook TEXT NOT NULL,
        executor_id TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at TEXT,
        verified INTEGER NOT NULL DEFAULT 0,
        verifier_id TEXT,
        verification_note TEXT NOT NULL DEFAULT '',
        result_json TEXT NOT NULL DEFAULT '{}'
    )""",

    # Health snapshots (Mission 2, Stage 7): freshness-aware health with the
    # trust of the observation attached. Append-only; "current" health is the
    # newest unexpired row per component, never a mutable flag.
    """CREATE TABLE IF NOT EXISTS sentinel_health_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component TEXT NOT NULL,
        status TEXT NOT NULL,
        source_trust TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        measurement TEXT NOT NULL DEFAULT '',
        threshold TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.0,
        deployment_sha TEXT NOT NULL,
        evidence_ref TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_health_component ON sentinel_health_snapshots(component, id)",

    # Self-metrics (Stage 28).
    """CREATE TABLE IF NOT EXISTS sentinel_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        metric TEXT NOT NULL,
        value REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # --- Mission 3: identity risk observations -----------------------------
    # Append-only risk observations per subject (session/user/device/network/
    # admin). Each row is a point-in-time assessment with an explicit expiry:
    # stale high risk must never remain active (Stage 16). Dimensions, reasons
    # and contradicting evidence are stored structurally — a risk score
    # without reasons is invalid (Stage 15/18 invariants).
    """CREATE TABLE IF NOT EXISTS sentinel_identity_risk (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_ref TEXT NOT NULL,
        trust_state TEXT NOT NULL,
        risk_score REAL NOT NULL,
        dimensions_json TEXT NOT NULL DEFAULT '{}',
        reasons_json TEXT NOT NULL DEFAULT '[]',
        contradicting_json TEXT NOT NULL DEFAULT '[]',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        source_trust TEXT NOT NULL DEFAULT 'DERIVED',
        confidence REAL NOT NULL DEFAULT 0.0,
        observed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        deployment_sha TEXT NOT NULL,
        policy_version TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_idrisk_subject ON sentinel_identity_risk(subject_ref, id)",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_idrisk_expiry ON sentinel_identity_risk(expires_at)",

    # Mission 3: explicit, versioned, time-bounded detection exclusions
    # (Stage 27). There are no silent code exceptions — every exclusion is a
    # row with an owner-visible reason, an author, and a mandatory expiry.
    """CREATE TABLE IF NOT EXISTS sentinel_detection_exclusions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id TEXT NOT NULL,
        subject_ref TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        UNIQUE(rule_id, subject_ref)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_exclusions_rule ON sentinel_detection_exclusions(rule_id, expires_at)",

    # Mission 3: sequence-engine dedupe/cooldown ledger (Stage 6). One row per
    # (sequence_id, subject) firing; cooldown_until prevents alert storms.
    """CREATE TABLE IF NOT EXISTS sentinel_sequence_firings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sequence_id TEXT NOT NULL,
        subject_ref TEXT NOT NULL,
        fired_at TEXT NOT NULL DEFAULT (datetime('now')),
        cooldown_until TEXT NOT NULL,
        completeness TEXT NOT NULL DEFAULT 'FULL',
        matched_event_ids_json TEXT NOT NULL DEFAULT '[]',
        UNIQUE(sequence_id, subject_ref, fired_at)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_seqfire_subject ON sentinel_sequence_firings(sequence_id, subject_ref, cooldown_until)",
)


def ensure_schema(conn=None) -> int:
    """Create all Sentinel tables. Idempotent; safe to call at every boot.
    Returns the number of statements executed."""
    own = conn is None
    if own:
        conn = platform_db.connect()
    try:
        cur = conn.cursor()
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        if own:
            conn.commit()
        return len(SCHEMA_STATEMENTS)
    finally:
        if own:
            conn.close()


@contextmanager
def connection(conn=None):
    """Yield a usable connection; commit+close only if we opened it."""
    if conn is not None:
        yield conn
        return
    owned = platform_db.connect()
    try:
        yield owned
        owned.commit()
    finally:
        owned.close()
