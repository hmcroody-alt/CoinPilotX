"""Localization schema — additive ``business_os_l10n_*`` tables (Stage 6).

Follows the attribution / recommendations / merchant-automation / creator-commerce /
governed-UNDX ``ensure_schema`` convention exactly: idempotent
``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev / PostgreSQL prod), no
``bot.py`` import, and it NEVER mutates any legacy table. It builds a new canonical
localization surface beside whatever exists; nothing legacy is read or written here.

Design invariants (informational-only resolution projection — nothing renders):

* **Two append-only inputs are the truth.** ``business_os_l10n_locales`` is the catalog
  of locales an org declared (a ``locale`` code, whether it is the org default, an
  optional explicit ``fallback_locale``, and an ``active`` toggle).
  ``business_os_l10n_strings`` is the append-only translation fact log (a ``string_key``
  has a ``value`` in some ``locale``). Neither is ever updated in place — corrections are
  new rows, and the newest row for a ``(string_key, locale)`` is the active value.
* **The resolution list is a projection.** ``business_os_l10n_resolutions`` holds the
  per-(org, target-locale, key) resolved value the engine computes: the value, the locale
  it actually came from, the match type, and a deterministic rank. It is always
  rebuildable by replaying the two inputs, so it is never the authority.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on both input logs
  makes a replayed feed event a no-op (NULL ``external_ref`` — manual entries — is
  exempt); UNIQUE ``(org_id, locale, string_key)`` on the projection makes a resolution
  row exactly-once so a recompute after a crash is deterministic and safe.

Text UUID primary keys everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_LOCALIZATION"


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
    """Create the localization tables if absent. Idempotent; safe at startup and in
    tests. Owns its connection unless one is passed in (so callers can compose it into a
    larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Declared locales (append-only truth) ------------------------------
        # One row per declared locale. is_default marks the org fallback of last resort;
        # fallback_locale is an optional explicit intermediate fallback; active toggles a
        # locale out of resolution without deleting it. Corrections are new rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_l10n_locales (
                locale_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                locale TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                fallback_locale TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_l10n_locale_org "
            "ON business_os_l10n_locales (org_id, active)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_l10n_locale_source_ref "
            "ON business_os_l10n_locales (source, external_ref)"
        )

        # --- Append-only translation fact log ----------------------------------
        # One row per (string_key, locale, value) assertion. NEVER updated in place —
        # corrections are new rows and the newest row for a (key, locale) is active.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_l10n_strings (
                string_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                string_key TEXT NOT NULL,
                locale TEXT NOT NULL,
                value TEXT NOT NULL,
                context TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_l10n_string_key "
            "ON business_os_l10n_strings (org_id, string_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_l10n_string_locale "
            "ON business_os_l10n_strings (org_id, locale)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_l10n_string_source_ref "
            "ON business_os_l10n_strings (source, external_ref)"
        )

        # --- Computed resolution projection (rebuildable; never authority) -----
        # One row per (org, target locale, string_key). value is the resolved string
        # (NULL when missing); resolved_from is the locale the value came from; match_type
        # is exact/fallback/base/default/missing; rank is the deterministic 1-based order
        # (missing first, then default, base, fallback, exact; then locale asc, key asc).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_l10n_resolutions (
                row_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                locale TEXT NOT NULL,
                string_key TEXT NOT NULL,
                value TEXT,
                resolved_from TEXT,
                match_type TEXT NOT NULL,
                rank INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_l10n_resolution_key "
            "ON business_os_l10n_resolutions (org_id, locale, string_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_l10n_resolution_rank "
            "ON business_os_l10n_resolutions (org_id, rank)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_l10n_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_l10n_audit_subject "
            "ON business_os_l10n_audit (subject_type, subject_ref)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
