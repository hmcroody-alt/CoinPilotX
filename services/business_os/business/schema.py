"""Canonical Business domain schema — additive ``business_os_business_*`` tables.

Follows the marketplace/advertising ``ensure_schema`` convention exactly: idempotent
``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev / PostgreSQL prod), no
``bot.py`` import, and it NEVER mutates any legacy table. This builds the single
source-of-truth surface for business identity beside whatever ad-hoc business fields
already live inline elsewhere; nothing legacy is altered or dropped.

Tables:

* ``business_os_business`` — the canonical business entity. ONE row per business,
  owned by a user. Carries identity (legal/display name), brand (tagline,
  description, logo/media ref, primary color), category/industry, contact
  (email, phone, website), and an explicit lifecycle ``status``.
* ``business_os_business_locations`` — physical/virtual locations attached to a
  business (address, city, region, country, kind, lifecycle ``status``).
* ``business_os_business_members`` — team + RBAC. ONE row per (business, user),
  with a ``role`` from the fixed role vocabulary and a membership ``status``.
* ``business_os_business_policies`` — versioned policy documents (returns, privacy,
  terms, shipping, …). Append-only per (business, policy_type); the highest
  ``version`` is the live one.
* ``business_os_business_audit`` — append-only business timeline / audit of every
  mutation (also the substrate the timeline read surfaces).

Text UUID primary keys are used for business/location/policy rows to avoid depending
on engine-specific ``lastrowid`` semantics across SQLite/PostgreSQL.

Everything here is structural and inert: creating empty tables changes zero runtime
behaviour. All reads/writes are gated in ``service`` behind the ``BUSINESS_OS_BUSINESS``
flag.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ensure_schema(conn=None) -> None:
    """Create the canonical business tables if absent. Idempotent; safe at startup +
    tests. Owns its connection unless one is passed in (so callers can compose it into
    a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # The canonical business entity — the source of truth every other module
        # references. Owner identity is the account that created it; team access is
        # expressed in business_os_business_members, never by duplicating the row.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_business (
                business_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                legal_name TEXT,
                display_name TEXT NOT NULL,
                tagline TEXT,
                description TEXT,
                category TEXT,
                logo_media_ref TEXT,
                primary_color TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                website_url TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_owner "
            "ON business_os_business (owner_user_id)"
        )

        # Physical / virtual locations attached to a business.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_business_locations (
                location_id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL,
                label TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'physical',
                address_line1 TEXT,
                address_line2 TEXT,
                city TEXT,
                region TEXT,
                postal_code TEXT,
                country TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_locations_business "
            "ON business_os_business_locations (business_id)"
        )

        # Team + RBAC. One row per (business, user). Role drives every permission
        # decision in service._require_permission.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_business_members (
                member_id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                status TEXT NOT NULL DEFAULT 'active',
                invited_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_members_business "
            "ON business_os_business_members (business_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_members_user "
            "ON business_os_business_members (user_id)"
        )
        # One membership row per (business, user) — no duplicate/conflicting roles.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_members_business_user "
            "ON business_os_business_members (business_id, user_id)"
        )

        # Versioned policy documents. Append-only per (business, policy_type); the
        # max(version) row is live. History is never rewritten.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_business_policies (
                policy_id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL,
                policy_type TEXT NOT NULL,
                version INTEGER NOT NULL,
                body TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_policies_business_type "
            "ON business_os_business_policies (business_id, policy_type)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_policies_version "
            "ON business_os_business_policies (business_id, policy_type, version)"
        )

        # Append-only business timeline / audit of every mutation.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_business_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id TEXT,
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
            "CREATE INDEX IF NOT EXISTS idx_business_audit_business "
            "ON business_os_business_audit (business_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_audit_action "
            "ON business_os_business_audit (action)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
