-- Rollback for slice 7 (Basic Feed/Reels delivery MVP). Drops ONLY the three
-- slice-7 tables and their indexes. Symmetric with 0009_advertising_delivery.sql.
-- Touches nothing from slices 1-6, the ledger, or the legacy pulse_ads tables.
DROP INDEX IF EXISTS idx_ad_click_campaign;
DROP INDEX IF EXISTS idx_ad_click_delivery;
DROP TABLE IF EXISTS business_os_ad_click_events;

DROP INDEX IF EXISTS idx_ad_impr_freq;
DROP INDEX IF EXISTS idx_ad_impr_campaign;
DROP INDEX IF EXISTS idx_ad_impr_delivery;
DROP TABLE IF EXISTS business_os_ad_impression_events;

DROP INDEX IF EXISTS idx_ad_delivery_placement;
DROP INDEX IF EXISTS idx_ad_delivery_subject;
DROP INDEX IF EXISTS idx_ad_delivery_advertiser;
DROP INDEX IF EXISTS idx_ad_delivery_creative;
DROP INDEX IF EXISTS idx_ad_delivery_campaign;
DROP TABLE IF EXISTS business_os_ad_delivery_instances;
