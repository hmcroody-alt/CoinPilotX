"""Attribution vertical schema — additive ``business_os_attr_*`` tables (Stage 6).

Follows the crypto / marketplace / advertising ``ensure_schema`` convention exactly:
idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev /
PostgreSQL prod), no ``bot.py`` import, and it NEVER mutates any legacy table. It
builds a new canonical analytics surface beside whatever exists; nothing legacy is
read or written here.

Design invariants (informational-only credit accounting — NO money movement):

* **Two append-only logs are the truth.** ``business_os_attr_touchpoints`` records
  each exposure/click; ``business_os_attr_conversions`` records each conversion with
  its integer-cent value and lookback window. Neither is ever updated in place.
* **Credit is a projection.** ``business_os_attr_credits`` holds the fractional
  per-touchpoint credit computed by the engine under a named model. It is always
  rebuildable by replaying the two logs, so it is never the authority.
* **Value is integer cents.** A conversion's ``value_cents`` is a non-negative
  integer; credit is a remainder-safe integer split that sums back to it exactly.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on each log makes
  a replayed event a no-op (NULL ``external_ref`` — manual entries — is exempt); a
  UNIQUE ``(conversion_id, model, touchpoint_id)`` makes credit exactly-once so a
  recompute after a crash is deterministic and safe.

Text UUID primary keys everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_ATTRIBUTION"


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
    """Create the attribution tables if absent. Idempotent; safe at startup and in
    tests. Owns its connection unless one is passed in (so callers can compose it into
    a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Append-only touchpoint log (an exposure/click; part of a path) -----
        # One row per interaction between a user and a channel/campaign. occurred_at
        # is the authoritative ordering + lookback key. This table is NEVER updated
        # in place — corrections are new rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_attr_touchpoints (
                touchpoint_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                touch_type TEXT NOT NULL CHECK (touch_type IN
                    ('impression','click','engagement','visit')),
                campaign_ref TEXT,
                occurred_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attr_touch_user_time "
            "ON business_os_attr_touchpoints (user_id, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attr_touch_campaign "
            "ON business_os_attr_touchpoints (campaign_ref)"
        )
        # Idempotent ingest: a feed replaying the same event must not create a
        # duplicate touchpoint. NULL external_ref (manual entries) is exempt.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attr_touch_source_ref "
            "ON business_os_attr_touchpoints (source, external_ref)"
        )

        # --- Append-only conversion log (the thing being attributed) -----------
        # value_cents is the revenue value to distribute across the path; lookback
        # bounds which prior touchpoints are eligible.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_attr_conversions (
                conversion_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                conversion_type TEXT NOT NULL,
                value_cents INTEGER NOT NULL CHECK (value_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                occurred_at TEXT NOT NULL,
                lookback_days INTEGER NOT NULL DEFAULT 30 CHECK (lookback_days > 0),
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                related_object TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attr_conv_user_time "
            "ON business_os_attr_conversions (user_id, occurred_at)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attr_conv_source_ref "
            "ON business_os_attr_conversions (source, external_ref)"
        )

        # --- Computed credit projection (rebuildable; never the authority) ------
        # One row per (conversion, model, touchpoint). credit_cents is a remainder-
        # safe integer split of the conversion value; per (conversion, model) the
        # rows sum to value_cents exactly. credit_fraction is the transparent decimal
        # weight the model assigned (for reporting).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_attr_credits (
                credit_id TEXT PRIMARY KEY,
                conversion_id TEXT NOT NULL,
                touchpoint_id TEXT NOT NULL,
                model TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel TEXT,
                campaign_ref TEXT,
                credit_cents INTEGER NOT NULL CHECK (credit_cents >= 0),
                credit_fraction TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attr_credit_conv_model_touch "
            "ON business_os_attr_credits (conversion_id, model, touchpoint_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attr_credit_conv_model "
            "ON business_os_attr_credits (conversion_id, model)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attr_credit_campaign_model "
            "ON business_os_attr_credits (campaign_ref, model)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attr_credit_channel_model "
            "ON business_os_attr_credits (channel, model)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_attr_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_attr_audit_subject "
            "ON business_os_attr_audit (subject_type, subject_ref)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
