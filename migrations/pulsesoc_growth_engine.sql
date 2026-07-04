-- PulseSoc Growth Engine foundation.
-- PostgreSQL-compatible production migration. Runtime service creates SQLite
-- equivalents for local development and audit runs.

CREATE TABLE IF NOT EXISTS pulse_growth_accounts (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    user_id BIGINT NOT NULL UNIQUE,
    default_ad_account_id BIGINT,
    status TEXT DEFAULT 'ready',
    lifecycle_stage TEXT DEFAULT 'provisioned',
    growth_score INTEGER DEFAULT 0,
    trust_score INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'normal',
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_accounts_user ON pulse_growth_accounts(user_id);

CREATE TABLE IF NOT EXISTS pulse_growth_workspaces (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    growth_account_id BIGINT NOT NULL REFERENCES pulse_growth_accounts(id) ON DELETE CASCADE,
    workspace_name TEXT NOT NULL,
    modules_json JSONB DEFAULT '[]'::jsonb,
    unlocked_modules_json JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_wallets (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    growth_account_id BIGINT NOT NULL REFERENCES pulse_growth_accounts(id) ON DELETE CASCADE,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'inactive',
    credits_cents BIGINT DEFAULT 0,
    coupons_cents BIGINT DEFAULT 0,
    referral_bonus_cents BIGINT DEFAULT 0,
    lifetime_spend_cents BIGINT DEFAULT 0,
    lifetime_refunds_cents BIGINT DEFAULT 0,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_ledger (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    growth_account_id BIGINT NOT NULL REFERENCES pulse_growth_accounts(id) ON DELETE CASCADE,
    entry_type TEXT NOT NULL,
    amount_cents BIGINT DEFAULT 0,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'posted',
    idempotency_key TEXT UNIQUE,
    description TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_ledger_user_created ON pulse_growth_ledger(user_id, created_at);

CREATE TABLE IF NOT EXISTS pulse_growth_audience_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    categories_json JSONB DEFAULT '[]'::jsonb,
    profile_json JSONB DEFAULT '{}'::jsonb,
    privacy_mode TEXT DEFAULT 'aggregate_only',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_audience_models (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    model_version TEXT DEFAULT 'v1',
    learning_state TEXT DEFAULT 'cold_start',
    signals_json JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_creator_growth_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    creator_type TEXT DEFAULT 'creator',
    growth_stage TEXT DEFAULT 'ready',
    recommendations_json JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_promotion_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    growth_account_id BIGINT NOT NULL REFERENCES pulse_growth_accounts(id) ON DELETE CASCADE,
    source_type TEXT,
    source_id TEXT,
    action TEXT,
    status TEXT DEFAULT 'recorded',
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_history_user_created ON pulse_growth_promotion_history(user_id, created_at);

CREATE TABLE IF NOT EXISTS pulse_growth_billing_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    growth_account_id BIGINT NOT NULL REFERENCES pulse_growth_accounts(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'inactive',
    provider TEXT DEFAULT '',
    provider_customer_hash TEXT DEFAULT '',
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_preferences (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    preferences_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_ai_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    session_public_id TEXT UNIQUE,
    status TEXT DEFAULT 'ready',
    last_prompt_at TIMESTAMPTZ NULL,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_analytics_containers (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    container_public_id TEXT UNIQUE,
    conversion_tracking_id TEXT UNIQUE,
    status TEXT DEFAULT 'ready',
    metrics_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_api_keys (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    key_scope TEXT DEFAULT 'promotion_internal',
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    rotated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, key_scope)
);

CREATE TABLE IF NOT EXISTS pulse_growth_scores (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    growth_score INTEGER DEFAULT 0,
    score_factors_json JSONB DEFAULT '{}'::jsonb,
    calculated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_trust_links (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    trust_source TEXT DEFAULT 'user_trust_engine',
    trust_score INTEGER DEFAULT 0,
    trust_level TEXT DEFAULT 'baseline',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_risk_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    risk_level TEXT DEFAULT 'normal',
    risk_score INTEGER DEFAULT 0,
    fraud_flags_json JSONB DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_growth_provisioning_log (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    status TEXT DEFAULT 'ok',
    details_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_log_user_created ON pulse_growth_provisioning_log(user_id, created_at);
