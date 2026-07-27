"""Creator commerce schema — additive ``business_os_creator_*`` tables (Stage 6).

Follows the attribution / recommendations / merchant-automation ``ensure_schema``
convention exactly: idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db``
(SQLite dev / PostgreSQL prod), no ``bot.py`` import, and it NEVER mutates any legacy
table. It builds a new canonical creator-commerce surface beside whatever exists;
nothing legacy is read or written here.

Design invariants (informational-only earnings/tier projection — NO money movement):

* **Two append-only inputs are the truth.** ``business_os_creator_offerings`` is the
  catalog of support options a creator declared; ``business_os_creator_contributions``
  is the append-only fact log (a supporter contributed an amount toward an offering at
  a point in time). Neither is ever updated in place — corrections are new rows.
* **The supporter/tier ranking is a projection.** ``business_os_creator_supporters``
  holds the per-(creator, supporter) rollup the engine computes: summed support,
  contribution count, a deterministic tier label by cumulative-support threshold, and
  a rank. It is always rebuildable by replaying the two inputs, so it is never the
  authority.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on both input logs
  makes a replayed feed event a no-op (NULL ``external_ref`` — manual entries — is
  exempt); UNIQUE ``(creator_id, supporter_id)`` on the projection makes a supporter
  row exactly-once so a recompute after a crash is deterministic and safe.

Amounts are decimal strings (transparent, engine-portable). Text UUID primary keys
everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_CREATOR_COMMERCE"


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
    """Create the creator-commerce tables if absent. Idempotent; safe at startup and
    in tests. Owns its connection unless one is passed in (so callers can compose it
    into a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Offerings (append-only truth) -------------------------------------
        # One row per support option a creator declared. offering_type constrains the
        # enum; unit_amount is an optional transparent list price (decimal string).
        # Corrections are new offerings, not in-place edits (identity is offering_id).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_creator_offerings (
                offering_id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL,
                name TEXT,
                offering_type TEXT NOT NULL CHECK (offering_type IN
                    ('membership','subscription','tip','product')),
                unit_amount TEXT,
                currency TEXT NOT NULL DEFAULT 'USD',
                active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_offering_creator "
            "ON business_os_creator_offerings (creator_id, active)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_creator_offering_source_ref "
            "ON business_os_creator_offerings (source, external_ref)"
        )

        # --- Append-only contribution fact log ---------------------------------
        # One row per supporter contribution toward an offering. amount is a decimal
        # string; occurred_at is the authoritative ordering key. NEVER updated in
        # place — corrections are new rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_creator_contributions (
                contribution_id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL,
                offering_id TEXT,
                supporter_id TEXT NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                occurred_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_contrib_creator_time "
            "ON business_os_creator_contributions (creator_id, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_contrib_offering "
            "ON business_os_creator_contributions (offering_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_creator_contrib_source_ref "
            "ON business_os_creator_contributions (source, external_ref)"
        )

        # --- Computed supporter/tier projection (rebuildable; never authority) --
        # One row per (creator, supporter). total_amount is the summed contribution
        # (decimal string); tier is a deterministic label by cumulative-support
        # threshold; rank is the deterministic 1-based ordering (total desc, then
        # supporter_id asc).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_creator_supporters (
                row_id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL,
                supporter_id TEXT NOT NULL,
                total_amount TEXT NOT NULL,
                contribution_count INTEGER NOT NULL,
                tier TEXT NOT NULL,
                rank INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_creator_supporter_key "
            "ON business_os_creator_supporters (creator_id, supporter_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_supporter_rank "
            "ON business_os_creator_supporters (creator_id, rank)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_creator_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_creator_audit_subject "
            "ON business_os_creator_audit (subject_type, subject_ref)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
