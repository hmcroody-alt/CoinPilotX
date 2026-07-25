-- Symmetric rollback for 0010_advertising_billing.sql. Drops EXACTLY the three
-- objects that migration created and nothing else. Never references the legacy
-- pulse_ads_service / pulse_ad_* tables, the canonical ledger, or any slice 1-7
-- table.
DROP INDEX IF EXISTS idx_ad_pricing_lookup;
DROP INDEX IF EXISTS idx_ad_billing_created;
DROP INDEX IF EXISTS idx_ad_billing_status;
DROP INDEX IF EXISTS idx_ad_billing_source;
DROP INDEX IF EXISTS idx_ad_billing_advertiser;
DROP INDEX IF EXISTS idx_ad_billing_campaign;
DROP TABLE IF EXISTS business_os_ad_spend_accumulator;
DROP TABLE IF EXISTS business_os_ad_pricing_policy;
DROP TABLE IF EXISTS business_os_ad_billing_events;
