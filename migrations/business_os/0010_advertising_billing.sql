-- Business OS — Advertising: Canonical billing events + escrow consumption.
-- Additive-only. Creates the immutable billing-event log, the versioned
-- server-side pricing policy, and a per-campaign sub-cent spend accumulator.
-- NEVER touches the legacy pulse_ads_service / pulse_ad_* tables, the canonical
-- ledger (services.business_os.ledger), or any slice 1-7 table. No money is held
-- here: escrow lives in the ledger. A billing event is the immutable record of a
-- server decision to charge (or not charge) for ONE accepted impression/click;
-- the actual double-entry (debit campaign escrow, credit platform advertising
-- revenue) is posted to the canonical ledger and referenced by
-- ledger_txn_reference.

-- One immutable row per billing decision on a source impression/click event.
-- idempotency_key is UNIQUE -> a retried/concurrent bill of the same source event
-- collides and is a no-op (never double-charges). Clients can supply NONE of
-- these fields: advertiser/campaign/hierarchy, price, quantity, currency,
-- eligibility, and ledger reference are ALL server-derived from the authoritative
-- delivery + event rows and the versioned pricing policy.
CREATE TABLE IF NOT EXISTS business_os_ad_billing_events (
    billing_event_id TEXT PRIMARY KEY,
    advertiser_user_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    ad_set_id TEXT,
    creative_id TEXT,
    creative_version INTEGER,
    delivery_instance_id TEXT,
    -- 'impression' (CPM) or 'click' (CPC)
    source_event_type TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    -- 'cpm' or 'cpc'
    billing_model TEXT NOT NULL,
    -- CPM: 1 impression accrued; CPC: 1 click. Quantity of billable units.
    billable_quantity INTEGER NOT NULL DEFAULT 1,
    -- The versioned unit price applied (CPM: cents per 1000 impressions;
    -- CPC: cents per click). Copied from the pricing policy at decision time.
    unit_price_cents INTEGER NOT NULL,
    pricing_policy_version INTEGER,
    -- Whole cents actually posted to the ledger for THIS event. May be 0 for a
    -- sub-cent CPM impression whose fractional cost is carried in the accumulator
    -- until it crosses the 1-cent boundary. CPC always charges the whole rate.
    total_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK (total_amount_cents >= 0),
    -- Fractional CPM cost attributed to this event, in milli-cents (1 cent = 1000
    -- milli-cents). Purely for audit/reconciliation of the accumulator.
    accrued_millicents INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL,
    -- pending | processed | ineligible | failed | reversed
    billing_status TEXT NOT NULL DEFAULT 'pending',
    -- The canonical ledger transaction_id, when a debit/credit was posted.
    ledger_txn_reference TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    -- Structured note on why the event was billed / not billed (e.g. 'ok',
    -- 'not_billing_eligible', 'budget_exhausted', 'duplicate_source').
    eligibility_decision TEXT,
    failure_reason TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ad_billing_campaign
    ON business_os_ad_billing_events (campaign_id);
CREATE INDEX IF NOT EXISTS idx_ad_billing_advertiser
    ON business_os_ad_billing_events (advertiser_user_id);
CREATE INDEX IF NOT EXISTS idx_ad_billing_source
    ON business_os_ad_billing_events (source_event_type, source_event_id);
CREATE INDEX IF NOT EXISTS idx_ad_billing_status
    ON business_os_ad_billing_events (billing_status);
CREATE INDEX IF NOT EXISTS idx_ad_billing_created
    ON business_os_ad_billing_events (campaign_id, created_at);

-- Versioned, server-authoritative pricing policy. There are NO hardcoded
-- production prices in code; the active price for a (billing_model, currency) is
-- the row with the highest effective_version where active=1. Historical versions
-- are retained (immutable-by-convention) so a past billing event's price stays
-- reproducible. min/max bounds guard against a mis-entered price.
CREATE TABLE IF NOT EXISTS business_os_ad_pricing_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    billing_model TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    effective_version INTEGER NOT NULL,
    min_price_cents INTEGER,
    max_price_cents INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (billing_model, currency, effective_version)
);
CREATE INDEX IF NOT EXISTS idx_ad_pricing_lookup
    ON business_os_ad_pricing_policy (billing_model, currency, active, effective_version);

-- Per-campaign sub-cent CPM accumulator + budget-exhaustion latch. CPM bills a
-- fraction of a cent per impression; accrued_millicents carries the remainder
-- across impressions and only whole cents are ever posted to the ledger, so
-- rounding is deterministic and never loses or invents value. budget_exhausted is
-- a DERIVED latch set when an escrow debit is refused by the ledger's overdraft
-- guard — it never itself moves money.
CREATE TABLE IF NOT EXISTS business_os_ad_spend_accumulator (
    campaign_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    accrued_millicents INTEGER NOT NULL DEFAULT 0 CHECK (accrued_millicents >= 0),
    billed_cents INTEGER NOT NULL DEFAULT 0 CHECK (billed_cents >= 0),
    impressions_billed INTEGER NOT NULL DEFAULT 0,
    clicks_billed INTEGER NOT NULL DEFAULT 0,
    budget_exhausted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, currency)
);
