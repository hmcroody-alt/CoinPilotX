"""Performance schema — additive ``business_os_perf_*`` tables (Stage 6).

Follows the attribution / recommendations / merchant-automation / creator-commerce /
governed-UNDX / localization ``ensure_schema`` convention exactly: idempotent
``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev / PostgreSQL prod), no
``bot.py`` import, and it NEVER mutates any legacy table. It builds a new canonical
performance surface beside whatever exists; nothing legacy is read or written here.

Design invariants (informational-only summary projection — nothing renders/acts):

* **Two append-only inputs are the truth.** ``business_os_perf_samples`` is the append-only
  measurement log (a numeric ``value`` for a ``metric_key`` at ``captured_at``, optionally
  bucketed by a ``window`` label). ``business_os_perf_targets`` is the append-only target
  catalog (a warn / breach threshold for a metric, with a ``direction`` and which summary
  statistic to compare). Neither is updated in place — corrections are new rows, and the
  newest active target for a ``metric_key`` is the governing one.
* **The summary list is a projection.** ``business_os_perf_summaries`` holds the per-(org,
  metric_key, window) rollup the engine computes: count/min/max/mean/p50/p95, the compared
  target statistic, the status label, and a deterministic rank. It is always rebuildable by
  replaying the two inputs, so it is never the authority.
* **Idempotent by construction.** UNIQUE ``(source, external_ref)`` on both input logs makes
  a replayed feed event a no-op (NULL ``external_ref`` — manual entries — is exempt);
  UNIQUE ``(org_id, metric_key, window)`` on the projection makes a summary row exactly-once
  so a recompute after a crash is deterministic and safe. ``window`` defaults to ``''``
  (ungrouped) rather than NULL so the projection key never collides with SQLite's
  distinct-NULL semantics.

Text UUID primary keys everywhere to avoid engine-specific ``lastrowid`` semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_PERFORMANCE"


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
    """Create the performance tables if absent. Idempotent; safe at startup and in tests.
    Owns its connection unless one is passed in (so callers can compose it into a larger
    transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Append-only measurement log (truth) -------------------------------
        # One row per (metric_key, window, value) sample. NEVER updated in place —
        # corrections are new rows. window '' means ungrouped (whole-metric rollup).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_perf_samples (
                sample_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                window TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL,
                unit TEXT,
                captured_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_perf_sample_metric "
            "ON business_os_perf_samples (org_id, metric_key, window)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_perf_sample_source_ref "
            "ON business_os_perf_samples (source, external_ref)"
        )

        # --- Append-only target catalog ----------------------------------------
        # One row per target assertion for a metric. direction says which way is good;
        # compare_stat names the summary field compared to the thresholds; active toggles a
        # target out without deleting it. Newest active row for a metric_key governs.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_perf_targets (
                target_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'lower_is_better'
                    CHECK (direction IN ('lower_is_better', 'higher_is_better')),
                compare_stat TEXT NOT NULL DEFAULT 'mean',
                warn_threshold REAL,
                breach_threshold REAL,
                active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_perf_target_metric "
            "ON business_os_perf_targets (org_id, metric_key, active)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_perf_target_source_ref "
            "ON business_os_perf_targets (source, external_ref)"
        )

        # --- Computed summary projection (rebuildable; never authority) --------
        # One row per (org, metric_key, window). Rollup stats plus the compared target
        # statistic, the status label (breach/warn/ok/none) and a deterministic 1-based rank
        # (breach first, then warn, ok, none; then metric_key asc, window asc).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_perf_summaries (
                row_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                window TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL,
                min_value REAL,
                max_value REAL,
                mean_value REAL,
                p50_value REAL,
                p95_value REAL,
                target_stat REAL,
                status TEXT NOT NULL,
                rank INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_perf_summary_key "
            "ON business_os_perf_summaries (org_id, metric_key, window)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_perf_summary_rank "
            "ON business_os_perf_summaries (org_id, rank)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_perf_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_perf_audit_subject "
            "ON business_os_perf_audit (subject_type, subject_ref)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
