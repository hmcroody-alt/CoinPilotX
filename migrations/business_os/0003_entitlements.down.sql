-- Rollback for 0003_entitlements.sql
-- Drops the canonical entitlement tables. Destructive: only run when
-- intentionally retiring the Business OS entitlement slice. Data in these tables
-- is NOT recoverable. Legacy entitlement tables are untouched by this migration
-- and remain the system of record after rollback.

DROP INDEX IF EXISTS idx_ent_provider_subs_subject;
DROP TABLE IF EXISTS business_os_ent_provider_subs;

DROP INDEX IF EXISTS idx_ent_audit_subject;
DROP TABLE IF EXISTS business_os_ent_audit;

DROP TABLE IF EXISTS business_os_ent_usage;

DROP INDEX IF EXISTS idx_ent_grants_expiry;
DROP INDEX IF EXISTS idx_ent_grants_status;
DROP INDEX IF EXISTS idx_ent_grants_subject;
DROP TABLE IF EXISTS business_os_ent_grants;

DROP TABLE IF EXISTS business_os_ent_catalog;
DROP TABLE IF EXISTS business_os_ent_plans;
DROP TABLE IF EXISTS business_os_ent_products;
