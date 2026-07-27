-- Business OS Stage 3 — Advertising vertical, slice 1 (draft campaigns).
-- ADDITIVE ONLY. Creates a new `business_os_ad_*` namespace and never touches the
-- legacy advertising tables owned by services/pulse_ads_service.py
-- (pulse_ad_campaigns, ...). Those remain the only delivery/auction path.
--
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation
-- (INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL). Apply with the migration runner,
-- or via services.business_os.advertising.schema.ensure_schema() in dev/tests.
-- Everything is gated in the application layer behind BUSINESS_OS_ADVERTISING;
-- creating these empty tables changes zero behaviour on its own.
-- Rollback: 0004_advertising.down.sql

-- 1. Advertiser approval state ----------------------------------------------
-- ONE row per user. This is the merchant/advertiser-approval input, kept
-- SEPARATE from commercial entitlement and from account hold/suspension.
-- status: pending | approved | rejected | suspended.
CREATE TABLE IF NOT EXISTS business_os_ad_advertisers (
    user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    display_name TEXT,
    notes TEXT,
    approved_by TEXT,
    approved_at TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_advertisers_status
    ON business_os_ad_advertisers (status);

-- 2. Campaign drafts — ownership + lifecycle state --------------------------
-- Text UUID primary key avoids engine-specific lastrowid semantics.
-- status (slice 1): draft | archived.
CREATE TABLE IF NOT EXISTS business_os_ad_campaigns (
    campaign_id TEXT PRIMARY KEY,
    advertiser_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    destination_url TEXT,
    created_by TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_campaigns_owner
    ON business_os_ad_campaigns (advertiser_user_id);
CREATE INDEX IF NOT EXISTS idx_ad_campaigns_status
    ON business_os_ad_campaigns (status);

-- 3. Append-only audit ------------------------------------------------------
CREATE TABLE IF NOT EXISTS business_os_ad_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT,
    advertiser_user_id TEXT,
    action TEXT NOT NULL,
    actor TEXT,
    reason TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_audit_campaign
    ON business_os_ad_audit (campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_audit_advertiser
    ON business_os_ad_audit (advertiser_user_id);
