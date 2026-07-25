-- Rollback for 0005_advertising_review.sql. Drops only the additive
-- review_reason column added by slice 3; no other table or column is touched.
-- (PostgreSQL supports DROP COLUMN; on SQLite dev the column is managed by
-- ensure_schema and a table rebuild would be required for a hard drop.)
ALTER TABLE business_os_ad_campaigns DROP COLUMN review_reason;
