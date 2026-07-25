"""Governed UNDX business actions schema — additive ``business_os_undx_*`` tables
(Stage 6).

Follows the attribution / recommendations / merchant-automation / creator-commerce
``ensure_schema`` convention exactly: idempotent ``CREATE TABLE IF NOT EXISTS`` via
``services.db`` (SQLite dev / PostgreSQL prod), no ``bot.py`` import, and it NEVER
mutates any legacy table. It builds a new canonical governed-actions surface beside
whatever exists; nothing legacy is read or written here.

Design invariants (informational-only governance projection — NO action executes):

* **Two append-only inputs are the truth.** ``business_os_undx_policies`` is the catalog
  of governance policies an org declared (an ``action_type`` — or the ``*`` wildcard —
  maps to an ``effect`` of allow/deny/require_approval, with an optional ``max_risk``
  ceiling and a ``priority``). ``business_os_undx_action_requests`` is the append-only
  fact log (an actor proposed an action of some type against a subject, at a declared
  risk). Neither is ever updated in place — corrections are new rows.
* **The decision list is a projection.** ``business_os_undx_decisions`` holds the
  per-(org, request) governance decision the engine computes: the resolved effect, the
  matched policy (if any), and a deterministic rank. It is always rebuildable by
  replaying the two inputs, so it is never the authority.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on both input logs
  makes a replayed feed event a no-op (NULL ``external_ref`` — manual entries — is
  exempt); UNIQUE ``(request_id)`` on the projection makes a decision row exactly-once
  so a recompute after a crash is deterministic and safe.

Risk is a transparent ordered enum (``read_only`` < ``low`` < ``medium`` < ``high``).
Text UUID primary keys everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_UNDX_ACTIONS"


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
    """Create the governed-actions tables if absent. Idempotent; safe at startup and in
    tests. Owns its connection unless one is passed in (so callers can compose it into a
    larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Governance policies (append-only truth) ---------------------------
        # One row per declared policy. action_type is the action name it governs (or
        # '*' for a default policy); effect constrains the enum; max_risk is an optional
        # transparent risk ceiling; priority orders competing policies (highest wins).
        # Corrections are new policies, not in-place edits (identity is policy_id).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_policies (
                policy_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                name TEXT,
                action_type TEXT NOT NULL,
                effect TEXT NOT NULL CHECK (effect IN
                    ('allow','deny','require_approval')),
                max_risk TEXT,
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
            "CREATE INDEX IF NOT EXISTS idx_undx_policy_org "
            "ON business_os_undx_policies (org_id, active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_policy_action "
            "ON business_os_undx_policies (org_id, action_type)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_undx_policy_source_ref "
            "ON business_os_undx_policies (source, external_ref)"
        )

        # --- Append-only action-request fact log -------------------------------
        # One row per proposed action. risk is a transparent ordered enum; params_json
        # is opaque payload the engine never executes. NEVER updated in place —
        # corrections are new rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_action_requests (
                request_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action_type TEXT NOT NULL,
                subject_ref TEXT,
                risk TEXT NOT NULL DEFAULT 'low',
                params_json TEXT,
                requested_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_request_org_time "
            "ON business_os_undx_action_requests (org_id, requested_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_request_action "
            "ON business_os_undx_action_requests (org_id, action_type)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_undx_request_source_ref "
            "ON business_os_undx_action_requests (source, external_ref)"
        )

        # --- Computed decision projection (rebuildable; never authority) -------
        # One row per (org, request). effect is the resolved governance label;
        # matched_policy_id is the policy that decided it (NULL = default); rank is the
        # deterministic 1-based ordering (deny first, then require_approval, then allow;
        # then action_type asc, then request_id asc).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_decisions (
                row_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                risk TEXT NOT NULL,
                effect TEXT NOT NULL,
                matched_policy_id TEXT,
                reason TEXT,
                rank INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_undx_decision_request "
            "ON business_os_undx_decisions (request_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_decision_rank "
            "ON business_os_undx_decisions (org_id, rank)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_undx_audit_subject "
            "ON business_os_undx_audit (subject_type, subject_ref)"
        )

        # --- Tool registry ----------------------------------------------------
        # Canonical catalog of UNDX-callable tools. This is descriptive, not an
        # executor: tool-specific services such as Marketplace remain the source of
        # truth for mutations and verification.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_tool_registry (
                tool_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT 'v1',
                product_area TEXT,
                action_type TEXT NOT NULL,
                risk TEXT NOT NULL DEFAULT 'low',
                confirmation_required INTEGER NOT NULL DEFAULT 0,
                feature_flag TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_modes_json TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_undx_tool_name_version "
            "ON business_os_undx_tool_registry (tool_name, version)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_tool_area "
            "ON business_os_undx_tool_registry (product_area, enabled)"
        )

        # --- Actor permissions ------------------------------------------------
        # Actor-scoped grants/denials layered above org policies. These are
        # deterministic inputs to the governance projection; they do not execute.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_permissions (
                permission_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action_type TEXT NOT NULL,
                effect TEXT NOT NULL CHECK (effect IN
                    ('allow','deny','require_approval')),
                scope_ref TEXT,
                max_risk TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                expires_at TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_permission_actor "
            "ON business_os_undx_permissions (org_id, actor, active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_permission_action "
            "ON business_os_undx_permissions (org_id, action_type, active)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_undx_permission_source_ref "
            "ON business_os_undx_permissions (source, external_ref)"
        )

        # --- Explicit confirmations ------------------------------------------
        # Durable proof that a human confirmed a specific request/payload before
        # a product service may execute a consequential action.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_confirmations (
                confirmation_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN
                    ('pending','confirmed','expired','cancelled')),
                payload_hash TEXT NOT NULL,
                expires_at TEXT,
                confirmed_at TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_confirmation_request "
            "ON business_os_undx_confirmations (request_id, status)"
        )

        # --- Action receipts --------------------------------------------------
        # Append-only receipts for canonical services after they execute and
        # verify state. A receipt does not replace the product service audit log.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_action_receipts (
                receipt_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                request_id TEXT,
                action_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN
                    ('verified','failed','cancelled','blocked')),
                canonical_ref TEXT,
                verification_json TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_receipt_org_time "
            "ON business_os_undx_action_receipts (org_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_receipt_request "
            "ON business_os_undx_action_receipts (request_id)"
        )

        # --- Emergency stop ---------------------------------------------------
        # Operator kill-switch for a whole org or action family. Active stops
        # override policies and actor permissions in the decision projection.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_undx_emergency_stops (
                stop_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                action_type TEXT NOT NULL DEFAULT '*',
                active INTEGER NOT NULL DEFAULT 1,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                cleared_at TEXT,
                meta_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undx_stop_org_action "
            "ON business_os_undx_emergency_stops (org_id, action_type, active)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
