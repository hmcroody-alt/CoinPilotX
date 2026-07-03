-- PulseSoc Communications Engine Foundation
-- PostgreSQL-compatible schema for audio/video call state, participants,
-- events, quality telemetry, and device sessions.

CREATE TABLE IF NOT EXISTS communication_calls (
    id SERIAL PRIMARY KEY,
    public_id TEXT UNIQUE,
    conversation_id INTEGER,
    room_name TEXT UNIQUE,
    provider TEXT DEFAULT 'livekit',
    call_type TEXT CHECK (call_type IN ('audio','video')),
    call_scope TEXT CHECK (call_scope IN ('direct','group','live','room')),
    status TEXT CHECK (status IN ('created','ringing','accepted','connecting','connected','reconnecting','ended','missed','declined','failed','canceled')),
    created_by_user_id INTEGER,
    started_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER DEFAULT 0,
    end_reason TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_communication_calls_conversation_status
    ON communication_calls(conversation_id, status);
CREATE INDEX IF NOT EXISTS idx_communication_calls_creator_created
    ON communication_calls(created_by_user_id, created_at);

CREATE TABLE IF NOT EXISTS communication_call_participants (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES communication_calls(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    role TEXT CHECK (role IN ('caller','callee','host','guest')),
    status TEXT CHECK (status IN ('invited','ringing','joined','left','declined','missed','failed')),
    muted_audio BOOLEAN DEFAULT FALSE,
    muted_video BOOLEAN DEFAULT FALSE,
    screen_sharing BOOLEAN DEFAULT FALSE,
    joined_at TIMESTAMPTZ,
    left_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    device_info JSONB DEFAULT '{}'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(call_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_communication_participants_user_status
    ON communication_call_participants(user_id, status);

CREATE TABLE IF NOT EXISTS communication_call_events (
    id SERIAL PRIMARY KEY,
    call_id INTEGER REFERENCES communication_calls(id) ON DELETE CASCADE,
    user_id INTEGER,
    event_type TEXT,
    event_payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_communication_events_call_created
    ON communication_call_events(call_id, created_at);

CREATE TABLE IF NOT EXISTS communication_call_quality_reports (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES communication_calls(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    latency_ms INTEGER,
    jitter_ms INTEGER,
    packet_loss NUMERIC,
    bitrate_audio INTEGER,
    bitrate_video INTEGER,
    fps NUMERIC,
    resolution TEXT,
    network_type TEXT,
    device_info JSONB DEFAULT '{}'::jsonb,
    quality_score NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_communication_quality_call_user
    ON communication_call_quality_reports(call_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS communication_call_device_sessions (
    id SERIAL PRIMARY KEY,
    call_id INTEGER REFERENCES communication_calls(id) ON DELETE CASCADE,
    user_id INTEGER,
    device_id TEXT,
    platform TEXT,
    browser TEXT,
    permissions JSONB DEFAULT '{}'::jsonb,
    connection_state TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
