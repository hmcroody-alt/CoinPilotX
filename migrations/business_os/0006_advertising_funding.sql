-- Business OS Stage 3 — Advertising vertical, slice 4 (campaign funding readiness).
-- ADDITIVE ONLY. Creates two new `business_os_ad_*` tables that hold per-campaign
-- funding STATE and an append-only funding operation log. It never touches the
-- legacy advertising tables (pulse_ad_campaigns, ...) and never touches the
-- canonical ledger tables (ledger_transactions / ledger_entries / ledger_balances).
--
-- The MONEY itself is NOT stored here. All value movement lives in the canonical
-- ledger as immutable, double-entry, idempotent entries. These tables only hold:
--   * the configured budget + funding STATE
--     (unfunded | funding_pending | funded | funding_failed | released),
--     deliberately SEPARATE from the campaign review `status` column; and
--   * references (transaction ids) back to the ledger entries that drove each
--     reserve/release, so the state is reconstructable and auditable.
-- No mutable balance is stored — balances are always derived from the ledger.
--
-- Idempotency is enforced at the DB level: business_os_ad_funding_ops has a
-- UNIQUE idempotency_key. A retried reserve/release collides here and is served
-- as a no-op; the same key reused for a DIFFERENT operation is detected and
-- rejected by the application layer.
--
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation
-- (INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL). Apply with the migration runner,
-- or via services.business_os.advertising.schema.ensure_schema() in dev/tests.
-- Everything is gated in the application layer behind BUSINESS_OS_ADVERTISING;
-- creating these empty tables changes zero behaviour on its own.
-- Rollback: 0006_advertising_funding.down.sql

-- 1. Per-campaign funding record — ONE row per campaign ---------------------
-- funding_status: unfunded | funding_pending | funded | funding_failed | released.
-- reservation_txn_id / release_txn_id reference canonical ledger transactions.
CREATE TABLE IF NOT EXISTS business_os_ad_campaign_funding (
    campaign_id TEXT PRIMARY KEY,
    advertiser_user_id TEXT NOT NULL,
    budget_cents INTEGER,
    currency TEXT,
    funding_status TEXT NOT NULL DEFAULT 'unfunded',
    reserved_amount_cents INTEGER,
    reservation_key TEXT,
    reservation_txn_id TEXT,
    release_txn_id TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_funding_status
    ON business_os_ad_campaign_funding (funding_status);

-- 2. Append-only funding operation log --------------------------------------
-- The UNIQUE idempotency_key is the DB-level idempotency guarantee for
-- reserve/release. ledger_txn_id references the ledger transaction each op drove.
CREATE TABLE IF NOT EXISTS business_os_ad_funding_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    ledger_txn_id TEXT,
    related_txn_id TEXT,
    actor TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_funding_ops_campaign
    ON business_os_ad_funding_ops (campaign_id);
