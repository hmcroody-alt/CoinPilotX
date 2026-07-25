-- Rollback for 0007_advertising_operations.sql. Drops only the single additive
-- slice-5 operational table and its index. It never touches the canonical ledger
-- tables, the slice-4 funding tables, the campaign/advertiser/audit tables, or any
-- legacy pulse_ads table.
DROP INDEX IF EXISTS idx_ad_operations_status;
DROP TABLE IF EXISTS business_os_ad_campaign_operations;
