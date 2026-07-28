-- 0012_business.sql — Business OS Section 1: canonical Business HQ surface.
--
-- Additive, strangler-pattern. Creates the business_os_business_* tables — the
-- single source of truth for business identity that every other Business OS
-- module (Store, Marketplace, Advertising, Orders, Messages, Insights, Payments,
-- Events, Verification) references. NOTHING legacy is altered or dropped.
-- Mirrors services/business_os/business/schema.py::ensure_schema (the runtime
-- source of truth). Idempotent: every statement is IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS business_os_business (
    business_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    legal_name TEXT,
    display_name TEXT NOT NULL,
    tagline TEXT,
    description TEXT,
    category TEXT,
    logo_media_ref TEXT,
    primary_color TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    website_url TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_owner ON business_os_business (owner_user_id);

CREATE TABLE IF NOT EXISTS business_os_business_locations (
    location_id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'physical',
    address_line1 TEXT,
    address_line2 TEXT,
    city TEXT,
    region TEXT,
    postal_code TEXT,
    country TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_locations_business ON business_os_business_locations (business_id);

CREATE TABLE IF NOT EXISTS business_os_business_members (
    member_id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    status TEXT NOT NULL DEFAULT 'active',
    invited_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_members_business ON business_os_business_members (business_id);
CREATE INDEX IF NOT EXISTS idx_business_members_user ON business_os_business_members (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_business_members_business_user
    ON business_os_business_members (business_id, user_id);

CREATE TABLE IF NOT EXISTS business_os_business_policies (
    policy_id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    policy_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    body TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_policies_business_type
    ON business_os_business_policies (business_id, policy_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_business_policies_version
    ON business_os_business_policies (business_id, policy_type, version);

CREATE TABLE IF NOT EXISTS business_os_business_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id TEXT,
    subject_type TEXT NOT NULL,
    subject_ref TEXT,
    action TEXT NOT NULL,
    actor TEXT,
    reason TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_audit_business ON business_os_business_audit (business_id);
CREATE INDEX IF NOT EXISTS idx_business_audit_action ON business_os_business_audit (action);
