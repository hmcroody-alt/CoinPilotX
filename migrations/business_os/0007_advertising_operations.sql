-- Business OS Stage 3 — Advertising vertical, slice 5 (operational lifecycle).
-- ADDITIVE ONLY. Creates one new `business_os_ad_*` table that holds the
-- per-campaign OPERATIONAL state, deliberately SEPARATE from BOTH the campaign
-- review `status` column and the funding_status. It never touches the legacy
-- advertising tables (pulse_ad_campaigns, ...), the canonical ledger tables
-- (ledger_transactions / ledger_entries / ledger_balances), or the slice-4
-- funding tables.
--
-- Four concepts stay separate: review status, funding status, operational status,
-- and delivery execution. This table owns the third only:
--   operational_status: inactive | scheduled | active | paused | completed | cancelled
-- `active` means operationally AUTHORIZED for a future delivery worker — it is NOT
-- currently delivering. No audience, impression, click, spend, escrow, pacing, or
-- auction data lives here; this slice performs none of those actions.
--
-- Optional UTC start_at/end_at bound the run window when supplied (normalized to
-- UTC and range-validated by the application layer). activated_at / paused_at /
-- completed_at / cancelled_at record lifecycle timestamps; last_reason carries the
-- most recent transition reason where applicable. Every transition is additionally
-- written to the existing append-only business_os_ad_audit trail (no competing
-- audit framework is introduced).
--
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation.
-- Apply with the migration runner, or via
-- services.business_os.advertising.schema.ensure_schema() in dev/tests. Everything
-- is gated in the application layer behind BUSINESS_OS_ADVERTISING; creating this
-- empty table changes zero behaviour on its own.
-- Rollback: 0007_advertising_operations.down.sql

-- Per-campaign operational record — ONE row per campaign -------------------
CREATE TABLE IF NOT EXISTS business_os_ad_campaign_operations (
    campaign_id TEXT PRIMARY KEY,
    advertiser_user_id TEXT NOT NULL,
    operational_status TEXT NOT NULL DEFAULT 'inactive',
    start_at TEXT,
    end_at TEXT,
    activated_at TEXT,
    paused_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    last_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_operations_status
    ON business_os_ad_campaign_operations (operational_status);
