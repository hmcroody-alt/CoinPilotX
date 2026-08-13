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

DEPLOYMENT_SHA_ENV = "RAILWAY_GIT_COMMIT"


def deployment_sha() -> str:
    return os.getenv(DEPLOYMENT_SHA_ENV, "").strip() or "unknown"


SCHEMA_STATEMENTS: tuple[str, ...] = (
    # Canonical events (Stage 2). dedupe_key UNIQUE = persist-before-process
    # idempotency, same DB-level pattern as webhook_inbox.
    """CREATE TABLE IF NOT EXISTS sentinel_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        dedupe_key TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        subject_type TEXT,
        subject_id TEXT,
        source TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
        deployment_sha TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_events_cat ON sentinel_events(category, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_sentinel_events_actor ON sentinel_events(actor_id, occurred_at)",

    # Incidents (Stage 7). Idempotent by incident_key, append-only history in
    # sentinel_incident_transitions.
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
        deployment_sha TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}'
    )""",
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

    # Self-metrics (Stage 28).
    """CREATE TABLE IF NOT EXISTS sentinel_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        metric TEXT NOT NULL,
        value REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
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
