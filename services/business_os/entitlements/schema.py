"""Canonical entitlement schema — additive `business_os_ent_*` tables.

Mirrors the ledger's in-code ``ensure_schema`` convention (services/business_os/
ledger/ledger.py) so the two Business OS slices behave identically at startup:
idempotent ``CREATE TABLE IF NOT EXISTS`` statements, engine-portable via
``services.db`` (SQLite dev / PostgreSQL prod), no ``bot.py`` import.

This module is *only* structural. It never mutates the legacy entitlement tables
(``user_entitlements``, ``premium_entitlements``, ``pulse_premium_entitlements``,
``subscriptions``). Creating these empty tables changes zero runtime behaviour;
all reads/writes are gated in the service + facade behind the
``BUSINESS_OS_ENTITLEMENTS`` flag.

The DDL here is kept byte-for-byte consistent with
``migrations/business_os/0003_entitlements.sql`` so that whichever path a given
environment uses (migration runner in prod, ``ensure_schema()`` in dev/tests) the
resulting tables are the same.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from services import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- Seed catalog -----------------------------------------------------------
# Products map to the five business systems plus crypto. Kept small and explicit;
# adding a product/plan/entitlement is a data change here, not a code change
# elsewhere.
_SEED_PRODUCTS = [
    # (product_key, name, category)
    ("pulsesoc_premium", "PulseSoc Premium", "premium"),
    ("pulsesoc_premium_business", "PulseSoc Business", "business"),
    ("merchant_marketplace", "Marketplace Seller", "marketplace"),
    ("advertiser_portal", "Advertiser Portal", "advertising"),
    ("creator_pro", "Creator Pro", "creator"),
    ("crypto_intelligence_pro", "Crypto Intelligence Pro", "crypto"),
]

_SEED_PLANS = [
    # (plan_key, product_key, plan_type, price_cents, billing_interval)
    ("pulse_premium_monthly", "pulsesoc_premium", "monthly", 999, "month"),
    ("pulse_premium_annual", "pulsesoc_premium", "annual", 9999, "year"),
    ("pulse_premium_trial", "pulsesoc_premium", "trial", 0, None),
    ("pulse_premium_grandfathered", "pulsesoc_premium", "grandfathered", 0, None),
    ("pulse_business_monthly", "pulsesoc_premium_business", "monthly", 4999, "month"),
    ("merchant_standard", "merchant_marketplace", "monthly", 0, "month"),
    ("advertiser_standard", "advertiser_portal", "monthly", 0, "month"),
    ("creator_pro_monthly", "creator_pro", "monthly", 1999, "month"),
    ("crypto_pro_monthly", "crypto_intelligence_pro", "monthly", 1499, "month"),
]

# (plan_key, entitlement_key, limit_value, limit_period)
# limit_value NULL => boolean capability; non-NULL => metered quota.
_SEED_CATALOG = [
    # Premium — the first vertical slice lives here.
    # ``premium.access`` is the umbrella MEMBERSHIP key: it answers "is this
    # subject a paying Premium member?" and is what the legacy
    # ``is_premium_user`` shim resolves through. Every plan that confers Premium
    # membership must grant it, so membership is one canonical fact rather than
    # something inferred by OR-ing capability keys together.
    ("pulse_premium_monthly", "premium.access", None, None),
    ("pulse_premium_monthly", "premium.profile.customization", None, None),
    ("pulse_premium_monthly", "premium.media.higher_quality", None, None),
    ("pulse_premium_monthly", "premium.undx.advanced", None, None),
    ("pulse_premium_monthly", "premium.crypto.advanced_alerts", None, None),
    ("pulse_premium_monthly", "premium.crypto.portfolio", None, None),
    ("pulse_premium_monthly", "premium.crypto.intelligence", None, None),
    ("pulse_premium_annual", "premium.access", None, None),
    ("pulse_premium_annual", "premium.profile.customization", None, None),
    ("pulse_premium_annual", "premium.media.higher_quality", None, None),
    ("pulse_premium_annual", "premium.undx.advanced", None, None),
    ("pulse_premium_annual", "premium.crypto.advanced_alerts", None, None),
    ("pulse_premium_annual", "premium.crypto.portfolio", None, None),
    ("pulse_premium_annual", "premium.crypto.intelligence", None, None),
    ("pulse_premium_trial", "premium.access", None, None),
    ("pulse_premium_trial", "premium.profile.customization", None, None),
    # Crypto intelligence follows MEMBERSHIP, so it is seeded on every plan that
    # grants ``premium.access`` — including trial and business. The facade's
    # legacy reader for these keys is premium truthiness, which is true for a
    # trial member; omitting trial here would mean cutting over to canonical
    # mode silently revoked a capability the member already had.
    ("pulse_premium_trial", "premium.crypto.advanced_alerts", None, None),
    ("pulse_premium_trial", "premium.crypto.portfolio", None, None),
    ("pulse_premium_trial", "premium.crypto.intelligence", None, None),
    ("pulse_premium_grandfathered", "premium.access", None, None),
    ("pulse_premium_grandfathered", "premium.profile.customization", None, None),
    ("pulse_premium_grandfathered", "premium.media.higher_quality", None, None),
    ("pulse_premium_grandfathered", "premium.undx.advanced", None, None),
    ("pulse_premium_grandfathered", "premium.crypto.advanced_alerts", None, None),
    ("pulse_premium_grandfathered", "premium.crypto.portfolio", None, None),
    ("pulse_premium_grandfathered", "premium.crypto.intelligence", None, None),
    # Business — a Business subscription confers Premium membership too.
    ("pulse_business_monthly", "premium.access", None, None),
    ("pulse_business_monthly", "premium.profile.customization", None, None),
    ("pulse_business_monthly", "premium.crypto.advanced_alerts", None, None),
    ("pulse_business_monthly", "premium.crypto.portfolio", None, None),
    ("pulse_business_monthly", "premium.crypto.intelligence", None, None),
    ("pulse_business_monthly", "business.team_members", 10, None),
    ("pulse_business_monthly", "business.analytics.advanced", None, None),
    # Marketplace.
    ("merchant_standard", "marketplace.sell.physical", None, None),
    ("merchant_standard", "marketplace.sell.digital", None, None),
    # Advertising.
    ("advertiser_standard", "advertising.campaign.create", 25, "month"),
    ("advertiser_standard", "advertising.analytics.advanced", None, None),
    # Creator.
    ("creator_pro_monthly", "premium.media.higher_quality", None, None),
    # Crypto. NOTE: ``crypto.alerts.advanced`` is read by no gate. The key the
    # alert engine actually enforces is ``premium.crypto.advanced_alerts``
    # above; do not wire a new gate to this lookalike.
    ("crypto_pro_monthly", "crypto.alerts.advanced", 50, "day"),
]


def ensure_schema(conn=None) -> None:
    """Create the canonical entitlement tables if absent. Idempotent.

    Safe to call at startup and from tests. Owns its connection unless one is
    passed in (so callers can compose it into a larger transaction).
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_products (
                product_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_plans (
                plan_key TEXT PRIMARY KEY,
                product_key TEXT NOT NULL,
                plan_type TEXT NOT NULL,
                price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                billing_interval TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_catalog (
                plan_key TEXT NOT NULL,
                entitlement_key TEXT NOT NULL,
                limit_value INTEGER,
                limit_period TEXT,
                metadata_json TEXT,
                PRIMARY KEY (plan_key, entitlement_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type TEXT NOT NULL DEFAULT 'user',
                subject_id TEXT NOT NULL,
                entitlement_key TEXT NOT NULL,
                source TEXT NOT NULL,
                source_reference TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                starts_at TEXT,
                expires_at TEXT,
                grace_until TEXT,
                limit_value INTEGER,
                limit_period TEXT,
                region TEXT,
                platform TEXT,
                revocation_reason TEXT,
                created_by TEXT,
                audit_reference TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (subject_type, subject_id, entitlement_key, source, source_reference)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_grants_subject "
            "ON business_os_ent_grants (subject_type, subject_id, entitlement_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_grants_status "
            "ON business_os_ent_grants (status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_grants_expiry "
            "ON business_os_ent_grants (expires_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_usage (
                subject_type TEXT NOT NULL DEFAULT 'user',
                subject_id TEXT NOT NULL,
                entitlement_key TEXT NOT NULL,
                period_key TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (subject_type, subject_id, entitlement_key, period_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type TEXT NOT NULL DEFAULT 'user',
                subject_id TEXT NOT NULL,
                entitlement_key TEXT,
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
            "CREATE INDEX IF NOT EXISTS idx_ent_audit_subject "
            "ON business_os_ent_audit (subject_type, subject_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ent_provider_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_subscription_id TEXT NOT NULL UNIQUE,
                subject_type TEXT NOT NULL DEFAULT 'user',
                subject_id TEXT NOT NULL,
                plan_key TEXT,
                status TEXT NOT NULL DEFAULT 'unknown',
                current_period_end TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_provider_subs_subject "
            "ON business_os_ent_provider_subs (subject_type, subject_id)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def seed_catalog(conn=None) -> dict:
    """Insert the default products/plans/catalog rows if missing. Idempotent.

    Uses portable "UPDATE else INSERT" upserts (no engine-specific ON CONFLICT)
    so a repeated call never duplicates or errors. Returns counts of rows
    inserted per table.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    inserted = {"products": 0, "plans": 0, "catalog": 0}
    now = _utc_now_iso()
    try:
        for product_key, name, category in _SEED_PRODUCTS:
            if not _exists(conn, "business_os_ent_products", "product_key", product_key):
                conn.execute(
                    "INSERT INTO business_os_ent_products "
                    "(product_key, name, category, status, metadata_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'active', NULL, ?, ?)",
                    (product_key, name, category, now, now),
                )
                inserted["products"] += 1
        for plan_key, product_key, plan_type, price_cents, interval in _SEED_PLANS:
            if not _exists(conn, "business_os_ent_plans", "plan_key", plan_key):
                conn.execute(
                    "INSERT INTO business_os_ent_plans "
                    "(plan_key, product_key, plan_type, price_cents, currency, "
                    "billing_interval, status, metadata_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'usd', ?, 'active', NULL, ?, ?)",
                    (plan_key, product_key, plan_type, price_cents, interval, now, now),
                )
                inserted["plans"] += 1
        for plan_key, ent_key, limit_value, limit_period in _SEED_CATALOG:
            cur = conn.execute(
                "SELECT 1 FROM business_os_ent_catalog "
                "WHERE plan_key = ? AND entitlement_key = ?",
                (plan_key, ent_key),
            )
            if cur.fetchone() is None:
                conn.execute(
                    "INSERT INTO business_os_ent_catalog "
                    "(plan_key, entitlement_key, limit_value, limit_period, metadata_json) "
                    "VALUES (?, ?, ?, ?, NULL)",
                    (plan_key, ent_key, limit_value, limit_period),
                )
                inserted["catalog"] += 1
        if owned:
            conn.commit()
        return inserted
    finally:
        if owned:
            conn.close()


def _exists(conn, table: str, col: str, value) -> bool:
    cur = conn.execute(f"SELECT 1 FROM {table} WHERE {col} = ?", (value,))
    return cur.fetchone() is not None


def ensure_ready(conn=None) -> None:
    """Convenience: create tables then seed the default catalog. Idempotent."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        seed_catalog(conn)
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
