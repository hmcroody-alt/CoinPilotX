-- Pulse AI Messenger + privacy-safe learning foundation
-- PostgreSQL-compatible migration. Runtime SQLite fallback creates equivalent
-- local tables in services/pulse_ai_service.py for development.

CREATE TABLE IF NOT EXISTS pulse_ai_conversations (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    user_id BIGINT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'active',
    pinned_at TIMESTAMPTZ NULL,
    last_message_id BIGINT DEFAULT 0,
    last_message_at TIMESTAMPTZ NULL,
    reset_at TIMESTAMPTZ NULL,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ai_conversations_user
    ON pulse_ai_conversations(user_id);

CREATE TABLE IF NOT EXISTS pulse_ai_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES pulse_ai_conversations(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    body TEXT NOT NULL,
    provider TEXT,
    provider_model TEXT,
    latency_ms INTEGER DEFAULT 0,
    error_code TEXT,
    correlation_id TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pulse_ai_messages_conversation
    ON pulse_ai_messages(conversation_id, id);

CREATE INDEX IF NOT EXISTS idx_pulse_ai_messages_user_created
    ON pulse_ai_messages(user_id, created_at);

CREATE TABLE IF NOT EXISTS pulse_ai_knowledge_items (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    title TEXT NOT NULL,
    category TEXT,
    body TEXT NOT NULL,
    source TEXT DEFAULT 'admin_seed',
    status TEXT DEFAULT 'approved',
    approved_by_user_id BIGINT DEFAULT 0,
    approved_at TIMESTAMPTZ NULL,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_ai_user_memory (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    source TEXT DEFAULT 'user_opt_in',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_pulse_ai_user_memory_user
    ON pulse_ai_user_memory(user_id, status);

CREATE TABLE IF NOT EXISTS pulse_ai_feedback (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    user_id BIGINT NOT NULL,
    message_id BIGINT,
    rating TEXT NOT NULL CHECK (rating IN ('helpful', 'not_helpful', 'wrong', 'unsafe', 'outdated')),
    comment TEXT,
    status TEXT DEFAULT 'queued_review',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_ai_learning_events (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    user_id BIGINT,
    event_type TEXT NOT NULL,
    source TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_ai_safety_reviews (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    feedback_id BIGINT REFERENCES pulse_ai_feedback(id) ON DELETE SET NULL,
    knowledge_item_id BIGINT REFERENCES pulse_ai_knowledge_items(id) ON DELETE SET NULL,
    review_status TEXT DEFAULT 'queued',
    reviewer_user_id BIGINT DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_ai_feature_registry (
    id BIGSERIAL PRIMARY KEY,
    feature_key TEXT UNIQUE,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_ai_conversation_context_permissions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT UNIQUE NOT NULL,
    remember_preferences BOOLEAN DEFAULT FALSE,
    use_pulse_ai_chat_history BOOLEAN DEFAULT TRUE,
    assist_with_messages_when_asked BOOLEAN DEFAULT TRUE,
    improve_from_feedback BOOLEAN DEFAULT TRUE,
    private_context_opt_in BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pulse_ai_web_search_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    query_hash TEXT NOT NULL,
    provider TEXT,
    status TEXT NOT NULL,
    result_count INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    reason TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pulse_ai_web_search_logs_user
    ON pulse_ai_web_search_logs(user_id, created_at);

CREATE TABLE IF NOT EXISTS pulse_ai_provider_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    provider TEXT,
    model TEXT,
    task TEXT,
    status TEXT NOT NULL,
    latency_ms INTEGER DEFAULT 0,
    error_reason TEXT,
    correlation_id TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pulse_ai_provider_events_user
    ON pulse_ai_provider_events(user_id, created_at);

CREATE TABLE IF NOT EXISTS pulse_ai_safety_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    event_type TEXT NOT NULL,
    category TEXT,
    mode TEXT,
    action TEXT NOT NULL,
    reasons_json JSONB DEFAULT '[]'::jsonb,
    correlation_id TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pulse_ai_safety_events_user
    ON pulse_ai_safety_events(user_id, created_at);
