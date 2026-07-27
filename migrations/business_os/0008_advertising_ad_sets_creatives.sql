-- Business OS Stage 2 — Advertising vertical, slice 6 (ad sets + creative
-- foundation). ADDITIVE ONLY. Creates the two new `business_os_ad_*` tables that
-- extend the canonical hierarchy `advertiser -> campaign -> ad set -> creative`.
-- It never touches the legacy advertising tables (pulse_ad_campaigns,
-- pulse_ad_creatives, pulse_ad_media_assets, ...), the authoritative media table
-- (pulse_media_assets), the canonical ledger tables, or the slice 1-5 advertising
-- tables. No delivery, impression, auction, pacing, spend, or attribution surface
-- is created here — this slice builds the objects only.
--
-- Separation of concerns is preserved. An ad set carries its OWN lifecycle
-- `status` (draft|submitted|approved|rejected|paused|archived), distinct from the
-- campaign review status, the funding_status, and the operational_status. An ad
-- set is NEVER deliverable merely because its parent campaign is active; delivery
-- readiness is DERIVED live in the application layer from all the separate inputs
-- and is never stored as a single boolean.
--
--   business_os_ad_sets
--     placements_json         : placement allowlist selection (feed/reels) — config
--                               only, no delivery is wired.
--     audience_json           : governed, validated, versioned audience spec.
--     schedule_start_at/end_at: optional schedule override.
--     budget_allocation_json  : optional allocation METADATA only — no money is
--                               stored or moved here.
--     version                 : optimistic-concurrency / revision counter.
--
--   business_os_ad_creatives
--     media_asset_id /         : canonical references into the authoritative
--     thumbnail_asset_id         PulseSoc media ownership system
--                                (pulse_media_assets.id) — never a client path.
--     destination_type/ref     : internal canonical id (existence verified) OR a
--                                normalized external HTTPS URL (stored behind an
--                                explicit later-safety-review boundary).
--     status / review_reason   : review state + admin's structured rejection reason
--                                (visible to the owner).
--     version /                : revision counter + link to the version this row
--     supersedes_creative_id     supersedes when an already-submitted/approved
--                                creative is materially revised (the prior version
--                                and its review history are never silently mutated).
--
-- Every transition is additionally written to the existing append-only
-- business_os_ad_audit trail; no competing audit framework is introduced.
--
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation.
-- Apply with the migration runner, or via
-- services.business_os.advertising.schema.ensure_schema() in dev/tests. Everything
-- is gated in the application layer behind BUSINESS_OS_ADVERTISING; creating these
-- empty tables changes zero behaviour on its own.
-- Rollback: 0008_advertising_ad_sets_creatives.down.sql

-- Ad sets — campaign children -----------------------------------------------
CREATE TABLE IF NOT EXISTS business_os_ad_sets (
    ad_set_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    advertiser_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    placements_json TEXT,
    audience_json TEXT,
    schedule_start_at TEXT,
    schedule_end_at TEXT,
    budget_allocation_json TEXT,
    review_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    archived_at TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_sets_campaign
    ON business_os_ad_sets (campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_sets_owner
    ON business_os_ad_sets (advertiser_user_id);
CREATE INDEX IF NOT EXISTS idx_ad_sets_status
    ON business_os_ad_sets (status);

-- Creatives — ad-set leaves -------------------------------------------------
CREATE TABLE IF NOT EXISTS business_os_ad_creatives (
    creative_id TEXT PRIMARY KEY,
    ad_set_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    advertiser_user_id TEXT NOT NULL,
    creative_type TEXT NOT NULL,
    media_asset_id TEXT,
    thumbnail_asset_id TEXT,
    headline TEXT,
    body TEXT,
    call_to_action TEXT,
    destination_type TEXT,
    destination_ref TEXT,
    accessibility_text TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    review_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_creative_id TEXT,
    archived_at TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_ad_set
    ON business_os_ad_creatives (ad_set_id);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_campaign
    ON business_os_ad_creatives (campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_owner
    ON business_os_ad_creatives (advertiser_user_id);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_status
    ON business_os_ad_creatives (status);
