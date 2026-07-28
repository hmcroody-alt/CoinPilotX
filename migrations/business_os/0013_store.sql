-- 0013_store.sql — Business OS Section 2: canonical Store (storefront + catalog).
--
-- Additive, strangler-pattern. Creates the business_os_store_* tables — the
-- business-scoped storefront + product catalog + merchandising collections for a
-- Section-1 Business. Every row is keyed to a business_id; identity and RBAC live in
-- business_os_business_* and are REUSED (no membership rows here). NOTHING legacy and
-- no marketplace table is altered, dropped, or referenced. Mirrors
-- services/business_os/store/schema.py::ensure_schema. Idempotent: IF NOT EXISTS.

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
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_store_storefront_business ON business_os_store_storefront (business_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_store_storefront_slug ON business_os_store_storefront (slug);

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
);
CREATE INDEX IF NOT EXISTS idx_store_products_business ON business_os_store_products (business_id);
CREATE INDEX IF NOT EXISTS idx_store_products_status ON business_os_store_products (business_id, status);

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
);
CREATE INDEX IF NOT EXISTS idx_store_collections_business ON business_os_store_collections (business_id);

CREATE TABLE IF NOT EXISTS business_os_store_collection_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_store_collection_product ON business_os_store_collection_products (collection_id, product_id);
CREATE INDEX IF NOT EXISTS idx_store_collection_products_product ON business_os_store_collection_products (product_id);

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
);
CREATE INDEX IF NOT EXISTS idx_store_audit_business ON business_os_store_audit (business_id);
CREATE INDEX IF NOT EXISTS idx_store_audit_action ON business_os_store_audit (action);
