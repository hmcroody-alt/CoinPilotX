-- Rollback for 0004_advertising.sql. Drops only the additive `business_os_ad_*`
-- namespace; legacy pulse_ads_service tables are never referenced here.
DROP INDEX IF EXISTS idx_ad_audit_advertiser;
DROP INDEX IF EXISTS idx_ad_audit_campaign;
DROP TABLE IF EXISTS business_os_ad_audit;

DROP INDEX IF EXISTS idx_ad_campaigns_status;
DROP INDEX IF EXISTS idx_ad_campaigns_owner;
DROP TABLE IF EXISTS business_os_ad_campaigns;

DROP INDEX IF EXISTS idx_ad_advertisers_status;
DROP TABLE IF EXISTS business_os_ad_advertisers;
