-- Business OS Stage 3 — Advertising vertical, slice 3 (campaign review lifecycle).
-- ADDITIVE ONLY. Adds a single nullable column to the existing
-- business_os_ad_campaigns table so an admin's rejection reason can be surfaced
-- to the campaign owner. The review STATE itself lives in the existing `status`
-- column (draft | submitted | approved | rejected | archived); the reviewing
-- admin is captured in the append-only audit trail, not here.
--
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation
-- (on PostgreSQL, ADD COLUMN is rewritten to ADD COLUMN IF NOT EXISTS, making
-- this idempotent). In dev/tests the same column is added idempotently by
-- services.business_os.advertising.schema.ensure_schema().
--
-- Everything remains gated in the application layer behind BUSINESS_OS_ADVERTISING;
-- adding this nullable column changes zero behaviour on its own.
-- Rollback: 0005_advertising_review.down.sql

ALTER TABLE business_os_ad_campaigns ADD COLUMN review_reason TEXT;
