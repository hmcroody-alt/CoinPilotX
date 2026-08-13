"""Ads-intelligence schema — additive ``ads_intel_*`` tables.

Follows the advertising / attribution / performance ``ensure_schema`` convention
exactly: idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev /
PostgreSQL prod), no ``bot.py`` import, and it NEVER mutates a legacy table. The
legacy ``pulse_ad_*`` tables and the ``business_os_ad_*`` tables are left completely
untouched; this subsystem is a measurement and decision layer that sits *beside*
both of them.

What this subsystem owns
------------------------
Events, delivery decisions, interest, performance rollups, pacing, frequency,
and diagnostics. That is the whole list.

What it deliberately does NOT own
---------------------------------
Advertisers, campaigns, creatives, audiences, wallets, ledgers, pricing, billing,
review queues, and admin approval — those already have exactly one home each and
keep it. **Attribution is also not owned here**: ``services/business_os/attribution``
already implements touchpoints → conversions → credits with lookback windows,
remainder-safe cent splitting, and a ``campaign_report``. Ad clicks and conversions
are forwarded into that engine (``source='ads_intel'``, ``external_ref`` = the
event's dedup key, which makes the forward idempotent for free). Building a second
attribution store here would have been the exact mistake the architecture rule
forbids.

Design invariants
-----------------
* **The event log is the truth; everything else is a projection.** ``ads_intel_events``
  and ``ads_intel_delivery_decisions`` are append-only. The daily rollups, affinity
  scores, and diagnostics are all rebuildable by replaying them, so none of them is
  ever the authority for anything.
* **Idempotent by construction.** ``ads_intel_events.dedup_key`` is UNIQUE, so a
  client retry, a duplicated batch, or a replayed worker pass collides at the
  database rather than inflating a metric. Batch ingest is separately idempotent on
  ``ads_intel_ingest_batches.batch_key``.
* **No money authority.** Nothing here debits, credits, prices, or bills. Billing
  stays in the advertising slice's accumulator and the ledger. This layer can mark
  an event ``invalid`` so it is *excluded* from billing, which is a veto, never a
  charge.
* **Pseudonymous subjects.** The viewer is stored as ``subject_ref`` — a
  deterministic keyed digest of the user/session id, computed in ``privacy.py`` —
  not as a raw user id. Deterministic so deletion and per-user export remain a
  single exact lookup; keyed so the analytics surface is not a second copy of the
  user table.
* **Versioned decisions.** Every projection records the code version that produced
  it (ranking / feature / processing). A row whose version no longer matches is
  recomputable rather than silently mixed into a metric.

Text UUID primary keys everywhere, to avoid engine-specific ``lastrowid`` semantics
and to stay out of ``bot.AUTO_PK_TABLES`` entirely.

Creating these tables is inert: empty tables change zero runtime behaviour. Every
read and write is gated behind ``BUSINESS_OS_ADS_INTELLIGENCE`` in ``api.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


def new_id() -> str:
    """Opaque text primary key."""
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _existing_columns(conn, table: str) -> set:
    """Column names present on ``table`` (cross-engine). Empty set on any error."""
    try:
        if db.ENGINE_NAME == "sqlite":
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {r[1] for r in rows}  # PRAGMA: (cid, name, type, ...)
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _ensure_columns(conn, table: str, columns: dict) -> None:
    """Additively add any missing ``{name: sql_type}`` columns to ``table``.

    Idempotent: introspects first and only issues ``ALTER TABLE ADD COLUMN`` for
    absent ones. Names/types come from fixed literal mappings below, never from
    caller input, so the f-string DDL carries no injection surface.
    """
    present = _existing_columns(conn, table)
    for name, sql_type in columns.items():
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def ensure_schema(conn=None) -> None:
    """Create the ads-intelligence tables if absent. Idempotent.

    Safe to call at startup and from tests. Owns its connection unless one is
    passed in, so callers can compose it into a larger transaction.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        _ensure_events(conn)
        _ensure_decisions(conn)
        _ensure_interest(conn)
        _ensure_rollups(conn)
        _ensure_pacing_and_frequency(conn)
        _ensure_diagnostics(conn)
        _ensure_policy(conn)
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# --------------------------------------------------------------------------- #
# Event fabric
# --------------------------------------------------------------------------- #

def _ensure_events(conn) -> None:
    # The canonical append-only event log. One row per accepted ad event.
    #
    # `dedup_key` is UNIQUE and is the entire idempotency story: the client (or
    # worker) computes it from stable facts about the event, so a retry after a
    # dropped response, a duplicated batch, or a replayed backfill collides here
    # instead of double-counting an impression or a click.
    #
    # `validity` starts 'valid' and is the ONLY column on this table that is ever
    # updated in place — invalid-traffic rules downgrade it to 'suspect'/'invalid'
    # after the fact. The event itself is never edited or deleted, so a fraud
    # ruling is always reversible and auditable.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_events (
            event_id TEXT PRIMARY KEY,
            dedup_key TEXT NOT NULL UNIQUE,
            event_name TEXT NOT NULL,
            event_family TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            subject_ref TEXT,
            session_ref TEXT,
            decision_id TEXT,
            campaign_id TEXT,
            creative_id TEXT,
            placement_key TEXT,
            platform TEXT,
            app_version TEXT,
            surface TEXT,
            percent_visible INTEGER,
            duration_ms INTEGER,
            value_cents INTEGER,
            currency TEXT,
            validity TEXT NOT NULL DEFAULT 'valid',
            invalid_reason TEXT,
            billable INTEGER NOT NULL DEFAULT 0,
            quality_status TEXT NOT NULL DEFAULT 'ok',
            quality_notes TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            processing_version INTEGER NOT NULL DEFAULT 1,
            ingest_source TEXT NOT NULL DEFAULT 'client',
            batch_key TEXT,
            meta_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # The privacy class the row was written under, stored rather than derived.
    #
    # The class is a pure function of the event name, so a reader could compute
    # it. It is stored anyway, because deriving it at read time means every
    # future change to the classification silently rewrites the past — and the
    # dangerous direction is the permissive one. If an event is reclassified
    # from measurement-only to product-signal, read-time derivation
    # retroactively grants permission to shape delivery for signals that were
    # collected under the narrower promise.
    #
    # Storing it makes the two directions behave differently, which is what we
    # want: a restriction can be applied to history by a deliberate, visible
    # backfill, and an expansion cannot happen by accident at all.
    _ensure_columns(conn, "ads_intel_events", {"privacy_class": "TEXT"})

    for name, cols in (
        ("idx_ads_intel_events_campaign_day", "(campaign_id, occurred_at)"),
        ("idx_ads_intel_events_creative_day", "(creative_id, occurred_at)"),
        ("idx_ads_intel_events_decision", "(decision_id)"),
        ("idx_ads_intel_events_subject", "(subject_ref, occurred_at)"),
        ("idx_ads_intel_events_name_day", "(event_name, occurred_at)"),
        ("idx_ads_intel_events_validity", "(validity)"),
    ):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON ads_intel_events {cols}"
        )

    # Batch-level idempotency for the ingest endpoint. A client that retries a
    # whole batch (because it never saw the 200) is answered from here rather
    # than being re-parsed event by event.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_ingest_batches (
            batch_id TEXT PRIMARY KEY,
            batch_key TEXT NOT NULL UNIQUE,
            subject_ref TEXT,
            received_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            reject_reasons_json TEXT,
            ingest_source TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_batches_created "
        "ON ads_intel_ingest_batches (created_at)"
    )


# --------------------------------------------------------------------------- #
# Delivery decisions
# --------------------------------------------------------------------------- #

def _ensure_decisions(conn) -> None:
    # One row per ad OPPORTUNITY — every time a surface asked for an ad, whether
    # or not it got one. This is the single biggest gap in the legacy path:
    # `select_ads` currently returns an empty list with no record of why, so a
    # campaign that never delivers is indistinguishable from one nobody asked
    # for.
    #
    # Opportunity and decision are one row on purpose. They are recorded at the
    # same instant by the same caller and are 1:1; splitting them would double
    # the write cost of every feed render to buy a join and no new fact.
    #
    # The funnel counters (`eligible_count` etc.) are what turn "no ad shown"
    # into an actionable answer: 0 candidates means a targeting problem, while
    # candidates>0 with 0 eligible names the exact filter that removed them.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_delivery_decisions (
            decision_id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            subject_ref TEXT,
            session_ref TEXT,
            placement_key TEXT,
            surface TEXT,
            platform TEXT,
            filled INTEGER NOT NULL DEFAULT 0,
            no_fill_reason TEXT,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            ranked_count INTEGER NOT NULL DEFAULT 0,
            campaign_id TEXT,
            creative_id TEXT,
            score REAL,
            score_breakdown_json TEXT,
            exclusion_counts_json TEXT,
            ranking_version INTEGER NOT NULL DEFAULT 1,
            ranking_mode TEXT NOT NULL DEFAULT 'legacy',
            experiment_key TEXT,
            experiment_variant TEXT,
            exploration INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    for name, cols in (
        ("idx_ads_intel_decisions_opportunity", "(opportunity_id)"),
        ("idx_ads_intel_decisions_campaign", "(campaign_id, occurred_at)"),
        ("idx_ads_intel_decisions_nofill", "(no_fill_reason, occurred_at)"),
        ("idx_ads_intel_decisions_day", "(occurred_at)"),
        ("idx_ads_intel_decisions_placement", "(placement_key, occurred_at)"),
    ):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {name} "
            f"ON ads_intel_delivery_decisions {cols}"
        )


# --------------------------------------------------------------------------- #
# Interest graph
# --------------------------------------------------------------------------- #

def _ensure_interest(conn) -> None:
    # Decayed first-party affinity, one row per (subject, category, window).
    #
    # `score` is a projection: it is recomputed from the event log under the
    # weights in `ads_intel_signal_policy`, so changing a weight changes the
    # graph by replay rather than by hand-editing scores. `last_signal_at` and
    # `signal_count` are kept so a stale or thin affinity can be told apart from
    # a genuinely low one — a distinction targeting has to make before it uses
    # the score at all.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_interest_affinity (
            affinity_id TEXT PRIMARY KEY,
            subject_ref TEXT NOT NULL,
            category TEXT NOT NULL,
            window_days INTEGER NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            positive_signals INTEGER NOT NULL DEFAULT 0,
            negative_signals INTEGER NOT NULL DEFAULT 0,
            signal_count INTEGER NOT NULL DEFAULT 0,
            last_signal_at TEXT,
            computed_at TEXT NOT NULL,
            policy_version INTEGER NOT NULL DEFAULT 1,
            feature_version INTEGER NOT NULL DEFAULT 1,
            UNIQUE (subject_ref, category, window_days)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_affinity_subject "
        "ON ads_intel_interest_affinity (subject_ref, window_days)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_affinity_category "
        "ON ads_intel_interest_affinity (category, window_days)"
    )


# --------------------------------------------------------------------------- #
# Performance rollups
# --------------------------------------------------------------------------- #

def _ensure_rollups(conn) -> None:
    # Creative × day funnel. Rebuildable from `ads_intel_events` at any time.
    #
    # Counts are stored separately at every funnel stage (served → rendered →
    # viewable → click) instead of being collapsed into a single "impressions"
    # number, because the legacy tables' inability to tell those apart is what
    # makes the current CTR untrustworthy: a CTR over "served" and a CTR over
    # "viewable" are different metrics and the old schema could not say which
    # one it was reporting.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_creative_daily (
            rollup_id TEXT PRIMARY KEY,
            creative_id TEXT NOT NULL,
            campaign_id TEXT,
            day TEXT NOT NULL,
            served_count INTEGER NOT NULL DEFAULT 0,
            rendered_count INTEGER NOT NULL DEFAULT 0,
            viewable_count INTEGER NOT NULL DEFAULT 0,
            click_count INTEGER NOT NULL DEFAULT 0,
            engagement_count INTEGER NOT NULL DEFAULT 0,
            negative_count INTEGER NOT NULL DEFAULT 0,
            conversion_count INTEGER NOT NULL DEFAULT 0,
            invalid_count INTEGER NOT NULL DEFAULT 0,
            unique_subjects INTEGER NOT NULL DEFAULT 0,
            total_dwell_ms INTEGER NOT NULL DEFAULT 0,
            fatigue_state TEXT,
            computed_at TEXT NOT NULL,
            processing_version INTEGER NOT NULL DEFAULT 1,
            UNIQUE (creative_id, day)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_creative_daily_campaign "
        "ON ads_intel_creative_daily (campaign_id, day)"
    )

    # Campaign × day funnel, plus the opportunity-side counters that only the
    # decision log can supply (how often this campaign was even considered, and
    # how often it lost). Together these answer "why is my campaign not
    # spending" without anyone reading a log file.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_campaign_daily (
            rollup_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            day TEXT NOT NULL,
            opportunity_count INTEGER NOT NULL DEFAULT 0,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            won_count INTEGER NOT NULL DEFAULT 0,
            served_count INTEGER NOT NULL DEFAULT 0,
            viewable_count INTEGER NOT NULL DEFAULT 0,
            click_count INTEGER NOT NULL DEFAULT 0,
            negative_count INTEGER NOT NULL DEFAULT 0,
            conversion_count INTEGER NOT NULL DEFAULT 0,
            invalid_count INTEGER NOT NULL DEFAULT 0,
            unique_subjects INTEGER NOT NULL DEFAULT 0,
            top_exclusion_reason TEXT,
            computed_at TEXT NOT NULL,
            processing_version INTEGER NOT NULL DEFAULT 1,
            UNIQUE (campaign_id, day)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_campaign_daily_day "
        "ON ads_intel_campaign_daily (day)"
    )


# --------------------------------------------------------------------------- #
# Pacing and frequency
# --------------------------------------------------------------------------- #

def _ensure_pacing_and_frequency(conn) -> None:
    # Pacing STATE only — never a balance. The authoritative spend lives in the
    # advertising slice's accumulator and the ledger; this row holds the derived
    # judgement ("UNDERPACING") and the observed-vs-target numbers that produced
    # it, so pacing can be explained and replayed without ever being able to
    # move or invent money.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_campaign_pacing (
            pacing_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            day TEXT NOT NULL,
            pacing_state TEXT NOT NULL DEFAULT 'ON_TARGET',
            daily_budget_cents INTEGER,
            observed_spend_cents INTEGER NOT NULL DEFAULT 0,
            target_spend_cents INTEGER NOT NULL DEFAULT 0,
            delivery_ratio REAL,
            throttle_factor REAL NOT NULL DEFAULT 1.0,
            computed_at TEXT NOT NULL,
            processing_version INTEGER NOT NULL DEFAULT 1,
            UNIQUE (campaign_id, day)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_pacing_state "
        "ON ads_intel_campaign_pacing (pacing_state, day)"
    )

    # Windowed frequency counters. The legacy `pulse_ad_frequency_caps` counts
    # LIFETIME impressions with no time window, which means a cap can only ever
    # tighten and a long-lived user eventually becomes ineligible for every
    # campaign forever. This table is keyed by an explicit window bucket so a
    # cap is "n per day" rather than "n ever", and old buckets simply age out.
    #
    # The hot path reads the shared cache; this table is the durable backstop so
    # a cache flush cannot silently reset everyone's caps to zero.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_frequency_windows (
            window_id TEXT PRIMARY KEY,
            subject_ref TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_ref TEXT NOT NULL,
            window_key TEXT NOT NULL,
            window_started_at TEXT NOT NULL,
            exposure_count INTEGER NOT NULL DEFAULT 0,
            last_exposure_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (subject_ref, scope, scope_ref, window_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_freq_window "
        "ON ads_intel_frequency_windows (window_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_freq_subject "
        "ON ads_intel_frequency_windows (subject_ref, window_key)"
    )


# --------------------------------------------------------------------------- #
# Diagnostics and recommendations
# --------------------------------------------------------------------------- #

def _ensure_diagnostics(conn) -> None:
    # Rule-based, explainable campaign diagnostics and the advice derived from
    # them. Deliberately NOT stored in `business_os_rec_*`: that subsystem is a
    # user→item recommender fed by implicit feedback, whereas these are
    # advertiser-facing findings about a campaign with a required human-readable
    # reason. Same word, different shape.
    #
    # `evidence_json` is mandatory in practice — a recommendation the system
    # cannot justify with numbers it already holds is exactly the "fake AI" the
    # mission forbids. `applied`/`feedback` close the loop so advice that nobody
    # takes, or that makes things worse, is measurable rather than permanent.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_diagnostics (
            diagnostic_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            advertiser_user_id TEXT,
            code TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            title TEXT NOT NULL,
            detail TEXT,
            recommendation TEXT,
            evidence_json TEXT,
            observed_from TEXT,
            observed_to TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            applied INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT,
            feedback TEXT,
            feedback_at TEXT,
            rule_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (campaign_id, code, observed_to)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_diag_campaign "
        "ON ads_intel_diagnostics (campaign_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_diag_code "
        "ON ads_intel_diagnostics (code, severity)"
    )


# --------------------------------------------------------------------------- #
# Versioned policy
# --------------------------------------------------------------------------- #

def _ensure_policy(conn) -> None:
    # Versioned, append-only configuration: signal weights, decay half-lives,
    # frequency caps, ad-load limits, ranking coefficients.
    #
    # Append-only and versioned so that any stored projection can name the exact
    # policy that produced it. Without this, changing a weight silently rewrites
    # the meaning of every historical score and no past number can be defended.
    # Defaults live in `taxonomy.py`; a row here overrides one, so an empty
    # table is a valid, fully-functioning state.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_intel_signal_policy (
            policy_id TEXT PRIMARY KEY,
            policy_kind TEXT NOT NULL,
            policy_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (policy_kind, policy_version)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_intel_policy_active "
        "ON ads_intel_signal_policy (policy_kind, active)"
    )
