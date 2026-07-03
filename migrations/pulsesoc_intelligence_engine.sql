-- PostgreSQL-compatible schema for the PulseSoc Galaxy Intelligence Engine.
-- The runtime service keeps SQLite-compatible creation for local development;
-- this migration is the production source for managed PostgreSQL databases.

CREATE TABLE IF NOT EXISTS intelligence_streams (
    id BIGSERIAL PRIMARY KEY,
    stream_key TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    category TEXT NOT NULL,
    default_priority TEXT DEFAULT 'normal',
    default_frequency TEXT DEFAULT 'digest',
    default_enabled BOOLEAN DEFAULT TRUE,
    default_push BOOLEAN DEFAULT FALSE,
    confidence_threshold INTEGER DEFAULT 70,
    config_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_intelligence_streams (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    stream_key TEXT NOT NULL REFERENCES intelligence_streams(stream_key) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT TRUE,
    frequency TEXT DEFAULT 'digest',
    digest_mode TEXT DEFAULT 'daily',
    push_enabled BOOLEAN DEFAULT FALSE,
    email_enabled BOOLEAN DEFAULT FALSE,
    sms_enabled BOOLEAN DEFAULT FALSE,
    breaking_push_only BOOLEAN DEFAULT TRUE,
    confidence_threshold INTEGER DEFAULT 70,
    priority_filter TEXT DEFAULT 'normal',
    quiet_hours_enabled BOOLEAN DEFAULT FALSE,
    muted_until TIMESTAMPTZ,
    last_opened_at TIMESTAMPTZ,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, stream_key)
);

CREATE TABLE IF NOT EXISTS intelligence_sources (
    id BIGSERIAL PRIMARY KEY,
    source_key TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    stream_key TEXT NOT NULL REFERENCES intelligence_streams(stream_key) ON DELETE CASCADE,
    provider_type TEXT NOT NULL,
    trust_score INTEGER DEFAULT 70,
    status TEXT DEFAULT 'configured',
    cache_seconds INTEGER DEFAULT 300,
    required_env_json JSONB DEFAULT '[]'::jsonb,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    failure_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intelligence_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT UNIQUE NOT NULL,
    stream_key TEXT NOT NULL REFERENCES intelligence_streams(stream_key) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    why_it_matters TEXT,
    expected_impact TEXT,
    confidence_score INTEGER DEFAULT 0,
    confidence_label TEXT,
    importance_score INTEGER DEFAULT 0,
    freshness_score INTEGER DEFAULT 0,
    accuracy_score INTEGER DEFAULT 0,
    global_impact INTEGER DEFAULT 0,
    regional_impact INTEGER DEFAULT 0,
    duplicate_confidence INTEGER DEFAULT 0,
    spam_probability INTEGER DEFAULT 0,
    priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'accepted',
    source_count INTEGER DEFAULT 1,
    sources_json JSONB DEFAULT '[]'::jsonb,
    evidence_json JSONB DEFAULT '[]'::jsonb,
    forecast_json JSONB DEFAULT '{}'::jsonb,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    published_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intelligence_forecasts (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT REFERENCES intelligence_events(id) ON DELETE CASCADE,
    stream_key TEXT NOT NULL REFERENCES intelligence_streams(stream_key) ON DELETE CASCADE,
    title TEXT NOT NULL,
    forecast_body TEXT NOT NULL,
    confidence_score INTEGER DEFAULT 0,
    confidence_label TEXT,
    horizon TEXT,
    methodology TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intelligence_feedback (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    event_id BIGINT REFERENCES intelligence_events(id) ON DELETE SET NULL,
    stream_key TEXT,
    feedback_type TEXT NOT NULL,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intelligence_collector_runs (
    id BIGSERIAL PRIMARY KEY,
    collector_key TEXT NOT NULL,
    stream_key TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER DEFAULT 0,
    events_seen INTEGER DEFAULT 0,
    events_accepted INTEGER DEFAULT 0,
    failure_reason TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS intelligence_digest_jobs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    stream_key TEXT REFERENCES intelligence_streams(stream_key) ON DELETE CASCADE,
    digest_type TEXT DEFAULT 'daily',
    status TEXT DEFAULT 'pending',
    scheduled_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    event_ids_json JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intelligence_delivery_log (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT REFERENCES intelligence_events(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    stream_key TEXT NOT NULL,
    notification_id BIGINT,
    delivery_status TEXT,
    channels_json JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(event_id, user_id)
);

INSERT INTO intelligence_streams
    (stream_key, display_name, purpose, category, default_priority, default_frequency,
     default_enabled, default_push, confidence_threshold, config_json, updated_at)
VALUES
    ('pulsesoc_discoveries', 'PulseSoc Discoveries',
     'Teach users useful PulseSoc features naturally without marketing noise.',
     'platform', 'normal', 'digest', TRUE, FALSE, 62,
     '{"examples":["Pulse AI can help you explore PulseSoc.","Create video stories with music.","Messenger supports HD video Pulses."]}'::jsonb, NOW()),
    ('crypto_pulse', 'Crypto Pulse',
     'Surface high-confidence crypto movement, regulatory, and chain intelligence without investment advice.',
     'crypto', 'high', 'digest', TRUE, TRUE, 74,
     '{"examples":["Bitcoin breaks major resistance.","Ethereum volatility rising.","Large whale movement detected."],"no_investment_advice":true}'::jsonb, NOW()),
    ('market_pulse', 'Market Pulse',
     'Watch major market events and macroeconomic signals.',
     'markets', 'normal', 'digest', TRUE, FALSE, 72,
     '{"examples":["Federal Reserve announcement.","Inflation report released.","NASDAQ opens higher."]}'::jsonb, NOW()),
    ('world_pulse', 'World Pulse',
     'Major global events only: emergencies, space, science, infrastructure, elections, and medical breakthroughs.',
     'world', 'normal', 'digest', TRUE, FALSE, 80,
     '{"examples":["NASA launch.","Major earthquake.","Global cybersecurity incident."]}'::jsonb, NOW()),
    ('security_pulse', 'Security Pulse',
     'Protect users with high-confidence security and vulnerability intelligence.',
     'security', 'high', 'realtime', TRUE, TRUE, 76,
     '{"examples":["Apple emergency update.","Critical Android vulnerability.","Major password leak."]}'::jsonb, NOW()),
    ('technology_pulse', 'Technology Pulse',
     'Major AI, device, software, and scientific breakthroughs.',
     'technology', 'normal', 'digest', TRUE, FALSE, 70,
     '{"examples":["Major AI release.","Apple keynote.","New scientific breakthrough."]}'::jsonb, NOW()),
    ('pulsesoc_pulse', 'PulseSoc Pulse',
     'Platform improvements, maintenance, creator spotlights, and trending communities.',
     'platform', 'normal', 'digest', TRUE, FALSE, 60,
     '{"examples":["New features.","Maintenance.","Creator spotlight."]}'::jsonb, NOW()),
    ('creator_pulse', 'Creator Pulse',
     'Personal creator timing, growth, trends, audience, and content recommendations.',
     'creator', 'normal', 'digest', TRUE, FALSE, 58,
     '{"examples":["Best posting time.","Weekly growth.","Trending topics."]}'::jsonb, NOW()),
    ('music_pulse', 'Music Pulse',
     'Trending songs, emerging artists, PulseSoc Music releases, and popular audio.',
     'music', 'low', 'digest', TRUE, FALSE, 58,
     '{"examples":["Trending songs.","Emerging artists.","Popular audio."]}'::jsonb, NOW()),
    ('system_pulse', 'System Pulse',
     'Maintenance, app version, incident, and rollout intelligence from PulseSoc system events.',
     'system', 'high', 'realtime', TRUE, FALSE, 82,
     '{"examples":["Maintenance notice.","New app version.","Incident resolved."]}'::jsonb, NOW())
ON CONFLICT (stream_key) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    purpose = EXCLUDED.purpose,
    category = EXCLUDED.category,
    default_priority = EXCLUDED.default_priority,
    default_frequency = EXCLUDED.default_frequency,
    default_enabled = EXCLUDED.default_enabled,
    default_push = EXCLUDED.default_push,
    confidence_threshold = EXCLUDED.confidence_threshold,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();

INSERT INTO intelligence_sources
    (source_key, display_name, stream_key, provider_type, trust_score, status, cache_seconds, required_env_json, updated_at)
VALUES
    ('pulsesoc_feature_registry', 'PulseSoc Feature Registry', 'pulsesoc_discoveries', 'internal', 92, 'configured', 60, '[]'::jsonb, NOW()),
    ('pulsesoc_telemetry', 'PulseSoc Telemetry', 'pulsesoc_pulse', 'internal', 88, 'configured', 15, '[]'::jsonb, NOW()),
    ('coingecko', 'CoinGecko', 'crypto_pulse', 'market_api', 78, 'configured', 60, '[]'::jsonb, NOW()),
    ('binance', 'Binance Public Market Data', 'crypto_pulse', 'market_api', 74, 'configured', 30, '[]'::jsonb, NOW()),
    ('kraken', 'Kraken Public Market Data', 'crypto_pulse', 'market_api', 74, 'configured', 30, '[]'::jsonb, NOW()),
    ('coinmarketcap', 'CoinMarketCap', 'crypto_pulse', 'market_api', 78, 'config_missing', 60, '["COINMARKETCAP_API_KEY"]'::jsonb, NOW()),
    ('yahoo_finance', 'Yahoo Finance Public Market Data', 'market_pulse', 'market_api', 70, 'configured', 90, '[]'::jsonb, NOW()),
    ('polygon', 'Polygon', 'market_pulse', 'market_api', 78, 'config_missing', 60, '["POLYGON_API_KEY"]'::jsonb, NOW()),
    ('alpha_vantage', 'Alpha Vantage', 'market_pulse', 'market_api', 74, 'config_missing', 60, '["ALPHA_VANTAGE_API_KEY"]'::jsonb, NOW()),
    ('reuters', 'Reuters', 'world_pulse', 'news', 86, 'config_missing', 300, '["REUTERS_API_KEY"]'::jsonb, NOW()),
    ('ap_news', 'Associated Press', 'world_pulse', 'news', 86, 'config_missing', 300, '["AP_NEWS_API_KEY"]'::jsonb, NOW()),
    ('nasa', 'NASA', 'world_pulse', 'official', 90, 'configured', 300, '[]'::jsonb, NOW()),
    ('noaa', 'NOAA', 'world_pulse', 'official', 90, 'configured', 300, '[]'::jsonb, NOW()),
    ('usgs', 'USGS', 'world_pulse', 'official', 90, 'configured', 300, '[]'::jsonb, NOW()),
    ('cisa', 'CISA', 'security_pulse', 'official', 92, 'configured', 600, '[]'::jsonb, NOW()),
    ('nist', 'NIST', 'security_pulse', 'official', 90, 'configured', 600, '[]'::jsonb, NOW()),
    ('microsoft_security', 'Microsoft Security', 'security_pulse', 'official', 88, 'configured', 600, '[]'::jsonb, NOW()),
    ('apple_security', 'Apple Security', 'security_pulse', 'official', 88, 'configured', 600, '[]'::jsonb, NOW()),
    ('google_security', 'Google Security', 'security_pulse', 'official', 88, 'configured', 600, '[]'::jsonb, NOW()),
    ('openai_updates', 'OpenAI Official Updates', 'technology_pulse', 'official', 86, 'configured', 900, '[]'::jsonb, NOW()),
    ('apple_newsroom', 'Apple Newsroom', 'technology_pulse', 'official', 84, 'configured', 900, '[]'::jsonb, NOW()),
    ('creator_analytics', 'Creator Analytics', 'creator_pulse', 'internal', 84, 'configured', 900, '[]'::jsonb, NOW()),
    ('pulse_music', 'PulseSoc Music', 'music_pulse', 'internal', 82, 'configured', 300, '[]'::jsonb, NOW()),
    ('pulsesoc_system', 'PulseSoc System Events', 'system_pulse', 'internal', 92, 'configured', 60, '[]'::jsonb, NOW())
ON CONFLICT (source_key) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    stream_key = EXCLUDED.stream_key,
    provider_type = EXCLUDED.provider_type,
    trust_score = EXCLUDED.trust_score,
    status = EXCLUDED.status,
    cache_seconds = EXCLUDED.cache_seconds,
    required_env_json = EXCLUDED.required_env_json,
    updated_at = NOW();

CREATE INDEX IF NOT EXISTS idx_user_intel_streams_user
    ON user_intelligence_streams(user_id, enabled, stream_key);
CREATE INDEX IF NOT EXISTS idx_intel_events_stream_created
    ON intelligence_events(stream_key, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_events_priority
    ON intelligence_events(priority, confidence_score, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_sources_stream_status
    ON intelligence_sources(stream_key, status);
CREATE INDEX IF NOT EXISTS idx_intel_forecasts_stream_created
    ON intelligence_forecasts(stream_key, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_delivery_user
    ON intelligence_delivery_log(user_id, stream_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_feedback_user
    ON intelligence_feedback(user_id, stream_key, created_at DESC);
