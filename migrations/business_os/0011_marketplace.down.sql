-- 0011_marketplace.down.sql — rollback for the Stage 3 Marketplace canonical surface.
--
-- Drops ONLY the additive business_os_mkt_* tables. Touches NO legacy table. Because
-- the whole surface is dark behind BUSINESS_OS_MARKETPLACE and rides the shared ledger
-- (whose tables are owned by migration 0001, not dropped here), rollback is safe: the
-- ledger and every legacy marketplace path keep working.

DROP TABLE IF EXISTS business_os_mkt_reviews;
DROP TABLE IF EXISTS business_os_mkt_disputes;
DROP TABLE IF EXISTS business_os_mkt_refunds;
DROP TABLE IF EXISTS business_os_mkt_order_events;
DROP TABLE IF EXISTS business_os_mkt_order_items;
DROP TABLE IF EXISTS business_os_mkt_orders;
DROP TABLE IF EXISTS business_os_mkt_products;
DROP TABLE IF EXISTS business_os_mkt_sellers;
DROP TABLE IF EXISTS business_os_mkt_audit;
