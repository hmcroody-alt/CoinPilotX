"""Canonical Store domain schema — additive ``business_os_store_*`` tables.

Follows the S1 business / marketplace ``ensure_schema`` convention exactly: idempotent
``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev / PostgreSQL prod), no
``bot.py`` import, never mutating a legacy or marketplace table. Every row is keyed to a
Section-1 ``business_id``; identity/RBAC live in ``business_os_business_*`` and are reused
(this module stores zero membership rows of its own).

Tables:

* ``business_os_store_storefront`` — ONE storefront per business: slug, display name,
  headline/about copy, theme JSON, currency, and an explicit lifecycle ``status``.
* ``business_os_store_products`` — the business's product catalog. Price in integer minor
  units, optional SKU/media, ``inventory_qty`` NULL = untracked/unlimited, lifecycle
  ``status`` (draft/active/archived). Never hard-deleted.
* ``business_os_store_collections`` — merchandising groupings (slug, title, position).
* ``business_os_store_collection_products`` — M:N membership of products in collections,
  with an explicit ``position`` for ordering.
* ``business_os_store_audit`` — append-only audit of every store mutation.

Structural and inert: creating empty tables changes zero runtime behaviour. All
reads/writes are gated in ``service`` behind ``BUSINESS_OS_STORE``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ensure_schema(conn=None) -> None:
    """Create the canonical store tables if absent. Idempotent; owns its connection
    unless one is passed in (so callers can compose it into a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # ONE storefront per business (uniqueness enforced below). The storefront is a
        # presentation shell over the catalog; it references the S1 business by id and
        # never copies business identity fields.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_store_storefront (
                storefront_id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL,
                slug TEXT,
                name TEXT NOT NULL,
                headline TEXT,
                about TEXT,
                theme_json TEXT,
                currency TEXT NOT NULL DEFAULT 'USD',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_store_storefront_business "
            "ON business_os_store_storefront (business_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_store_storefront_slug "
            "ON business_os_store_storefront (slug)"
        )

        # The business's product catalog. Price is integer minor units; currency is
        # denormalized from the storefront at write time for stable historical display.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_store_products (
                product_id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT,
                description TEXT,
                price_cents INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                sku TEXT,
                media_ref TEXT,
                inventory_qty INTEGER,
                status TEXT NOT NULL DEFAULT 'draft',
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_store_products_business "
            "ON business_os_store_products (business_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_store_products_status "
            "ON business_os_store_products (business_id, status)"
        )

        # Merchandising collections (groupings) scoped to a business.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_store_collections (
                collection_id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL,
                title TEXT NOT NULL,
                slug TEXT,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_store_collections_business "
            "ON business_os_store_collections (business_id)"
        )

        # M:N membership of products within collections, with explicit ordering.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_store_collection_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_store_collection_product "
            "ON business_os_store_collection_products (collection_id, product_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_store_collection_products_product "
            "ON business_os_store_collection_products (product_id)"
        )

        # Append-only audit of every store mutation.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_store_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_store_audit_business "
            "ON business_os_store_audit (business_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_store_audit_action "
            "ON business_os_store_audit (action)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
