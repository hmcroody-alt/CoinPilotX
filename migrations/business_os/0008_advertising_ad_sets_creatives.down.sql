-- Rollback for 0008_advertising_ad_sets_creatives.sql. Drops only the two
-- additive slice-6 tables and their indexes. It never touches the authoritative
-- media table (pulse_media_assets), the canonical ledger tables, the slice 1-5
-- advertising tables (campaigns/advertisers/funding/operations/audit), or any
-- legacy pulse_ads table.
DROP INDEX IF EXISTS idx_ad_creatives_status;
DROP INDEX IF EXISTS idx_ad_creatives_owner;
DROP INDEX IF EXISTS idx_ad_creatives_campaign;
DROP INDEX IF EXISTS idx_ad_creatives_ad_set;
DROP TABLE IF EXISTS business_os_ad_creatives;
DROP INDEX IF EXISTS idx_ad_sets_status;
DROP INDEX IF EXISTS idx_ad_sets_owner;
DROP INDEX IF EXISTS idx_ad_sets_campaign;
DROP TABLE IF EXISTS business_os_ad_sets;
