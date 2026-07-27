-- Business OS Stage 2 — canonical, server-authoritative entitlement model
-- ADDITIVE ONLY. This migration creates a new `business_os_ent_*` namespace and
-- never touches the legacy entitlement tables (user_entitlements,
-- premium_entitlements, pulse_premium_entitlements, subscriptions). Those remain
-- the fallback for the compatibility facade until canonical mode is proven.
--
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation
-- (INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL). Apply with the migration runner.
-- Everything is gated in the application layer behind BUSINESS_OS_ENTITLEMENTS;
-- creating these empty tables changes zero behaviour on its own.
-- Rollback: 0003_entitlements.down.sql

-- 1. Product catalog ---------------------------------------------------------
-- A "product" is a sellable/grantable capability family. Categories map to the
-- five business systems plus crypto: premium|business|marketplace|advertising|
-- creator|crypto.
CREATE TABLE IF NOT EXISTS business_os_ent_products (
    product_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 2. Plans -------------------------------------------------------------------
-- A "plan" is a specific offer within a product (monthly, annual, trial,
-- promotional, lifetime, staff comp, or a grandfathered legacy tier).
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
);

-- 3. Catalog -----------------------------------------------------------------
-- Which entitlement keys (and their limits) a plan confers. This is the source
-- of truth for "buying plan X grants entitlements A, B, C". A NULL limit_value
-- means the entitlement is a boolean capability (present = allowed). A non-NULL
-- limit_value + limit_period expresses a metered quota (e.g. 5 per day).
CREATE TABLE IF NOT EXISTS business_os_ent_catalog (
    plan_key TEXT NOT NULL,
    entitlement_key TEXT NOT NULL,
    limit_value INTEGER,
    limit_period TEXT,
    metadata_json TEXT,
    PRIMARY KEY (plan_key, entitlement_key)
);

-- 4. Grants (the load-bearing table) ----------------------------------------
-- Every reason a subject (user or business) holds an entitlement is one row
-- here. Multiple grants for the same entitlement can coexist (e.g. a paid
-- Stripe grant AND a promotional trial); the service resolves precedence.
-- Idempotency: UNIQUE(subject_type, subject_id, entitlement_key, source,
-- source_reference) makes a replayed provider event or repeated admin action a
-- no-op upsert instead of a duplicate.
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
);

CREATE INDEX IF NOT EXISTS idx_ent_grants_subject
    ON business_os_ent_grants (subject_type, subject_id, entitlement_key);
CREATE INDEX IF NOT EXISTS idx_ent_grants_status
    ON business_os_ent_grants (status);
CREATE INDEX IF NOT EXISTS idx_ent_grants_expiry
    ON business_os_ent_grants (expires_at);

-- 5. Usage (quota counters) --------------------------------------------------
-- Per-subject, per-entitlement, per-period consumption. period_key is a caller-
-- chosen bucket string (e.g. '2026-07' for a calendar month, or 'cycle:<ref>'
-- for a billing-cycle window). Atomic increment is guarded by the DB via the
-- composite PK + a conditional UPDATE in the service.
CREATE TABLE IF NOT EXISTS business_os_ent_usage (
    subject_type TEXT NOT NULL DEFAULT 'user',
    subject_id TEXT NOT NULL,
    entitlement_key TEXT NOT NULL,
    period_key TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_type, subject_id, entitlement_key, period_key)
);

-- 6. Audit (append-only) -----------------------------------------------------
-- Every state-changing entitlement action, with before/after snapshots. Never
-- updated or deleted in normal operation. Powers admin "who changed what & why".
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
);

CREATE INDEX IF NOT EXISTS idx_ent_audit_subject
    ON business_os_ent_audit (subject_type, subject_id);

-- 7. Provider subscriptions (adapter landing zone) --------------------------
-- Normalized provider subscription state, deduped by provider_subscription_id.
-- Stripe/Apple/Google adapters write here; reconcile_entitlements() projects
-- these rows into grants. raw_json preserves the untranslated provider payload.
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
);

CREATE INDEX IF NOT EXISTS idx_ent_provider_subs_subject
    ON business_os_ent_provider_subs (subject_type, subject_id);
