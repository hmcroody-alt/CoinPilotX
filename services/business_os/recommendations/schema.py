"""Recommendations vertical schema — additive ``business_os_rec_*`` tables (Stage 6).

Follows the attribution / crypto / marketplace ``ensure_schema`` convention exactly:
idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev /
PostgreSQL prod), no ``bot.py`` import, and it NEVER mutates any legacy table. It
builds a new canonical recommendation surface beside whatever exists; nothing legacy
is read or written here.

Design invariants (informational-only ranking — NO money movement, NO actions):

* **Two append-only inputs are the truth.** ``business_os_rec_items`` is the
  catalog of recommendable objects; ``business_os_rec_interactions`` is the
  append-only implicit-feedback log (a user viewed/clicked/liked/purchased/
  dismissed an item, at a weight). Neither is ever updated in place.
* **Recommendations are a projection.** ``business_os_rec_recommendations`` holds
  the per-user ranked list computed by the engine under a named model. It is
  always rebuildable by replaying the two inputs, so it is never the authority.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on the
  interaction log makes a replayed feed event a no-op (NULL ``external_ref`` —
  manual entries — is exempt); UNIQUE ``(user_id, model, item_id)`` on the
  projection makes a recommendation row exactly-once so a recompute after a crash
  is deterministic and safe.

Text UUID primary keys everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_RECOMMENDATIONS"


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
    """Create the recommendation tables if absent. Idempotent; safe at startup and
    in tests. Owns its connection unless one is passed in (so callers can compose it
    into a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Catalog of recommendable items (append-only truth) ----------------
        # One row per item that can be recommended. tags_json holds a JSON array of
        # normalized string tags used by the content-based model; item_type +
        # category are coarse facets. Corrections are new items, not in-place edits
        # of the ranking inputs (an item's identity is item_id).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_rec_items (
                item_id TEXT PRIMARY KEY,
                item_type TEXT NOT NULL,
                title TEXT,
                category TEXT,
                tags_json TEXT,
                owner_ref TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rec_item_type "
            "ON business_os_rec_items (item_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rec_item_category "
            "ON business_os_rec_items (category)"
        )
        # Idempotent catalog ingest: a feed replaying the same item is a no-op.
        # NULL external_ref (manual entries) is exempt.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_rec_item_source_ref "
            "ON business_os_rec_items (source, external_ref)"
        )

        # --- Append-only implicit-feedback interaction log ---------------------
        # One row per (user, item) engagement. weight is a small positive integer
        # (a purchase counts for more than a view); interaction_type constrains the
        # enum. occurred_at is the authoritative ordering key. NEVER updated in
        # place — corrections are new rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_rec_interactions (
                interaction_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL CHECK (interaction_type IN
                    ('view','click','like','purchase','dismiss')),
                weight INTEGER NOT NULL DEFAULT 1,
                occurred_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rec_inter_user_time "
            "ON business_os_rec_interactions (user_id, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rec_inter_item "
            "ON business_os_rec_interactions (item_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_rec_inter_source_ref "
            "ON business_os_rec_interactions (source, external_ref)"
        )

        # --- Computed recommendation projection (rebuildable; never authority) --
        # One row per (user, model, item). score is a quantized decimal string (for
        # transparency); rank is the deterministic 1-based ordering (score desc,
        # item_id asc). reason is a short human string explaining the signal.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_rec_recommendations (
                rec_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                model TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_type TEXT,
                category TEXT,
                score TEXT NOT NULL,
                rank INTEGER NOT NULL,
                reason TEXT,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_rec_reco_user_model_item "
            "ON business_os_rec_recommendations (user_id, model, item_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rec_reco_user_model_rank "
            "ON business_os_rec_recommendations (user_id, model, rank)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_rec_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_rec_audit_subject "
            "ON business_os_rec_audit (subject_type, subject_ref)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
