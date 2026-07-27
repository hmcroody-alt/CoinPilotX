"""Merchant automation schema — additive ``business_os_merchant_*`` tables (Stage 6).

Follows the attribution / recommendations / crypto ``ensure_schema`` convention
exactly: idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev /
PostgreSQL prod), no ``bot.py`` import, and it NEVER mutates any legacy table. It
builds a new canonical merchant-automation surface beside whatever exists; nothing
legacy is read or written here.

Design invariants (informational-only rule evaluation — NO money movement, NO actions):

* **Two append-only inputs are the truth.** ``business_os_merchant_rules`` is the
  catalog of rule definitions a merchant declared (a signal_type, a comparison
  operator, a numeric threshold, and the action_type to *suggest* when it matches);
  ``business_os_merchant_signals`` is the append-only fact log (a measured value for
  a (merchant, subject, signal_type) at a point in time). The **latest** signal per
  ``(merchant_id, subject_ref, signal_type)`` is the current state the engine reads.
  Neither table is ever updated in place — corrections are new rows.
* **Proposals are a projection.** ``business_os_merchant_proposals`` holds the
  proposed actions the engine emits by evaluating every active rule against the
  latest signals. It is always rebuildable by replaying the two inputs, so it is
  never the authority.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on the signal log
  makes a replayed feed event a no-op (NULL ``external_ref`` — manual entries — is
  exempt); UNIQUE ``(merchant_id, rule_id, subject_ref)`` on the projection makes a
  proposal row exactly-once so a recompute after a crash is deterministic and safe.

Text UUID primary keys everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_MERCHANT_AUTOMATION"


def new_id() -> str:
    """Opaque text UUID primary key (engine-agnostic)."""
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _existing_columns(conn, table: str) -> set:
    """Column names present on ``table`` (cross-engine). Empty set on any error."""
    try:
        if db.ENGINE_NAME == "sqlite":
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {r[1] for r in rows}
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def ensure_schema(conn=None) -> None:
    """Create the merchant-automation tables if absent. Idempotent; safe at startup
    and in tests. Owns its connection unless one is passed in (so callers can compose
    it into a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Rule definitions (append-only truth) ------------------------------
        # One row per rule a merchant declared. A rule says: for this signal_type,
        # when the latest measured value <operator> threshold, SUGGEST action_type.
        # operator is a small closed enum; action_type is a free-form label of the
        # *suggested* action (never executed). active toggles evaluation; priority
        # orders proposals (higher first). Corrections are new rules, not in-place
        # edits of an evaluated input (a rule's identity is rule_id).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_merchant_rules (
                rule_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL,
                name TEXT,
                signal_type TEXT NOT NULL,
                operator TEXT NOT NULL CHECK (operator IN
                    ('lt','lte','gt','gte','eq','ne')),
                threshold TEXT NOT NULL,
                action_type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_merchant_rule_merchant "
            "ON business_os_merchant_rules (merchant_id, active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_merchant_rule_signal "
            "ON business_os_merchant_rules (merchant_id, signal_type)"
        )
        # Idempotent rule ingest: a feed replaying the same rule is a no-op.
        # NULL external_ref (manual entries) is exempt.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_rule_source_ref "
            "ON business_os_merchant_rules (source, external_ref)"
        )

        # --- Append-only signal fact log ---------------------------------------
        # One row per measured value for a (merchant, subject, signal_type). value
        # is a decimal string (transparent, engine-portable). observed_at is the
        # authoritative ordering key; the latest row per key is the current state
        # the engine compares against. NEVER updated in place — corrections are new
        # rows (a later observed_at supersedes).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_merchant_signals (
                signal_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                value TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_merchant_signal_state "
            "ON business_os_merchant_signals "
            "(merchant_id, subject_ref, signal_type, observed_at)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_signal_source_ref "
            "ON business_os_merchant_signals (source, external_ref)"
        )

        # --- Computed proposal projection (rebuildable; never authority) --------
        # One row per (merchant, rule, subject) whose latest signal satisfies the
        # rule. action_type is the *suggested* action (never executed); observed
        # value + threshold + operator are recorded for transparency; rank is the
        # deterministic 1-based ordering (priority desc, rule_id asc, subject asc).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_merchant_proposals (
                proposal_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                action_type TEXT NOT NULL,
                operator TEXT NOT NULL,
                threshold TEXT NOT NULL,
                observed_value TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                rank INTEGER NOT NULL,
                reason TEXT,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_proposal_key "
            "ON business_os_merchant_proposals (merchant_id, rule_id, subject_ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_merchant_proposal_rank "
            "ON business_os_merchant_proposals (merchant_id, rank)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_merchant_audit (
                audit_id TEXT PRIMARY KEY,
                subject_type TEXT NOT NULL,
                subject_ref TEXT,
                action TEXT NOT NULL,
                actor TEXT,
                reason TEXT,
                before_json TEXT,
                after_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_merchant_audit_subject "
            "ON business_os_merchant_audit (subject_type, subject_ref)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
