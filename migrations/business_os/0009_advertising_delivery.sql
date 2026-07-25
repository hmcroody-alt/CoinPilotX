-- Business OS — Advertising slice 7: Basic Feed/Reels delivery MVP.
-- Additive-only. Creates the canonical delivery-instance, impression-event, and
-- click-event tables plus their indexes. NEVER touches the legacy
-- pulse_ads_service / pulse_ad_* tables, the canonical ledger, or any slice 1-6
-- table. A delivery instance is a server-authorized opportunity to display ONE
-- approved creative version; the event tables are immutable append-only logs. No
-- money is stored or moved here (billing_eligible/billing_processed are booleans
-- for the NEXT slice to consume; escrow lives in the ledger).

-- One row per server-authorized delivery opportunity. Binds the EXACT hierarchy
-- (campaign/ad set/creative + creative_version) that was eligible at decision
-- time. eligibility_snapshot_json is the structured snapshot of why it was
-- eligible. subject_ref is a privacy-safe (salted-hash) viewer reference — never
-- a raw user id. impression_token is a server secret proving the client received
-- THIS delivery; the creative can never be substituted because the authoritative
-- creative/version is read back from this row, never from client input.
CREATE TABLE IF NOT EXISTS business_os_ad_delivery_instances (
    delivery_id TEXT PRIMARY KEY,
    advertiser_user_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    ad_set_id TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    creative_version INTEGER NOT NULL,
    placement TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    destination_type TEXT,
    destination_ref TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    decision_at TEXT NOT NULL,
    expires_at TEXT,
    request_ref TEXT,
    impression_token TEXT NOT NULL,
    eligibility_snapshot_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_delivery_campaign
    ON business_os_ad_delivery_instances (campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_delivery_creative
    ON business_os_ad_delivery_instances (creative_id);
CREATE INDEX IF NOT EXISTS idx_ad_delivery_advertiser
    ON business_os_ad_delivery_instances (advertiser_user_id);
CREATE INDEX IF NOT EXISTS idx_ad_delivery_subject
    ON business_os_ad_delivery_instances (subject_ref, created_at);
CREATE INDEX IF NOT EXISTS idx_ad_delivery_placement
    ON business_os_ad_delivery_instances (placement);

-- Immutable impression events. dedup_key is UNIQUE -> a retried/duplicate
-- impression collides and is served idempotently, never creating a second row.
-- The (campaign_id, subject_ref, event_at) index backs the server-authoritative
-- frequency cap. billing_eligible/billing_processed carry enough canonical
-- reference for the next billing slice WITHOUT deducting money here.
CREATE TABLE IF NOT EXISTS business_os_ad_impression_events (
    event_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    ad_set_id TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    creative_version INTEGER NOT NULL,
    placement TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    advertiser_user_id TEXT NOT NULL,
    event_at TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    request_meta_json TEXT,
    fraud_status TEXT NOT NULL DEFAULT 'clean',
    billing_eligible INTEGER NOT NULL DEFAULT 0,
    billing_processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_impr_delivery
    ON business_os_ad_impression_events (delivery_id);
CREATE INDEX IF NOT EXISTS idx_ad_impr_campaign
    ON business_os_ad_impression_events (campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_impr_freq
    ON business_os_ad_impression_events (campaign_id, subject_ref, event_at);

-- Immutable click events. Requires (by policy) an accepted impression on the
-- same delivery. The destination is SERVER-RESOLVED from the delivery's bound
-- creative version — never trusted from the client. dedup_key UNIQUE makes
-- duplicate clicks idempotent.
CREATE TABLE IF NOT EXISTS business_os_ad_click_events (
    event_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    impression_event_id TEXT,
    campaign_id TEXT NOT NULL,
    ad_set_id TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    creative_version INTEGER NOT NULL,
    placement TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    advertiser_user_id TEXT NOT NULL,
    destination_type TEXT,
    destination_ref TEXT,
    event_at TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    request_meta_json TEXT,
    fraud_status TEXT NOT NULL DEFAULT 'clean',
    billing_eligible INTEGER NOT NULL DEFAULT 0,
    billing_processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ad_click_delivery
    ON business_os_ad_click_events (delivery_id);
CREATE INDEX IF NOT EXISTS idx_ad_click_campaign
    ON business_os_ad_click_events (campaign_id);
