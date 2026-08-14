"""Additive schema for Progress OS.

Every table here is new and additive. Nothing in this module alters an
existing table's semantics, rewrites a user id, or touches content tables.
The program *reads* the canonical authorities (``users``, ``pulse_posts``,
``referral_conversions``) and writes only its own decision records.

The uniqueness constraints are the security model
-------------------------------------------------
Most of this program's integrity is enforced by the database rather than by
application logic, because application logic runs concurrently and databases
do not have to be told twice:

* ``progress_referral_qualifications`` is UNIQUE on
  ``(campaign_id, referred_user_id)`` — one referred person can be claimed by
  exactly one referrer per campaign. Two referrers racing to claim the same
  signup is resolved by the DB, not by whoever wrote last.
* ``progress_posting_days`` is UNIQUE on ``(campaign_id, user_id, day_key)``
  — the same calendar day can never be recorded twice, which is what makes
  "five posts on Monday" worth exactly one posting day.
* ``progress_milestone_awards`` is UNIQUE on
  ``(campaign_id, user_id, milestone_key)`` — a milestone is awarded once.
* ``progress_reward_cycles`` is UNIQUE on
  ``(campaign_id, user_id, cycle_index)`` — cycle 1 pays once, cycle 2 pays
  once. Combined with the rewards engine's own UNIQUE ``event_key``, a replay
  has to get past two independent locks to pay twice.
"""

from __future__ import annotations

from services import db


def ensure_schema(conn=None) -> None:
    """Create the Progress OS tables if absent. Idempotent; safe at startup."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # One row per referred person per campaign. The referrer is pinned
        # here and the DB refuses a second claim.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_referral_qualifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                campaign_version INTEGER NOT NULL DEFAULT 1,
                referrer_user_id INTEGER NOT NULL,
                referred_user_id INTEGER NOT NULL,
                referral_code TEXT,
                state TEXT NOT NULL DEFAULT 'ATTRIBUTED',
                signed_up INTEGER NOT NULL DEFAULT 0,
                profile_completed INTEGER NOT NULL DEFAULT 0,
                posting_days INTEGER NOT NULL DEFAULT 0,
                good_standing INTEGER NOT NULL DEFAULT 1,
                risk_state TEXT NOT NULL DEFAULT 'clear',
                review_reason TEXT,
                qualified_at TEXT,
                disqualified_reason TEXT,
                attributed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (campaign_id, referred_user_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_progress_qual_referrer "
            "ON progress_referral_qualifications "
            "(campaign_id, referrer_user_id, state)"
        )

        # Append-only evidence that a person posted on a given calendar day.
        # Append-only matters: deleting the post later must not retroactively
        # strip a day that was genuinely earned, and re-posting must not add
        # a day that was already counted.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_posting_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                day_key TEXT NOT NULL,
                first_post_id INTEGER,
                recorded_at TEXT NOT NULL,
                UNIQUE (campaign_id, user_id, day_key)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_milestone_awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                campaign_version INTEGER NOT NULL DEFAULT 1,
                user_id INTEGER NOT NULL,
                milestone_key TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                qualified_count_snapshot INTEGER NOT NULL,
                badge_key TEXT,
                entitlement_key TEXT,
                revoked_at TEXT,
                revoked_reason TEXT,
                earned_at TEXT NOT NULL,
                UNIQUE (campaign_id, user_id, milestone_key)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_reward_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                campaign_version INTEGER NOT NULL DEFAULT 1,
                user_id INTEGER NOT NULL,
                cycle_index INTEGER NOT NULL,
                qualified_count_snapshot INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                reward_event_key TEXT NOT NULL,
                reward_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                evidence_json TEXT,
                earned_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (campaign_id, user_id, cycle_index)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                mission_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'AVAILABLE',
                current_progress INTEGER NOT NULL DEFAULT 0,
                target INTEGER NOT NULL DEFAULT 1,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (campaign_id, user_id, mission_id)
            )
            """
        )

        # Audit + the user-facing activity feed come from the same event log,
        # separated by the ``visibility`` column. One log means a user-visible
        # claim and the audit record can never disagree about what happened.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                subject_user_id INTEGER,
                event_type TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'private',
                detail_json TEXT,
                actor TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_progress_events_user "
            "ON progress_events (campaign_id, user_id, id)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def ensure_ready() -> None:
    ensure_schema()
