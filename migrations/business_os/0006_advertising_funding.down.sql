-- Rollback for 0006_advertising_funding.sql. Drops only the two additive slice-4
-- funding tables and their indexes. It never touches the canonical ledger tables
-- (ledger_transactions / ledger_entries / ledger_balances) — those hold the money
-- and are owned by the ledger migrations — nor any legacy pulse_ads table.
DROP INDEX IF EXISTS idx_ad_funding_ops_campaign;
DROP TABLE IF EXISTS business_os_ad_funding_ops;

DROP INDEX IF EXISTS idx_ad_funding_status;
DROP TABLE IF EXISTS business_os_ad_campaign_funding;
