-- PulseSoc Notification System Foundation
-- PostgreSQL-compatible schema for Phase 1 notification event intake, records,
-- delivery jobs, user rules, and device tokens.

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    notification_type TEXT,
    title TEXT,
    message TEXT,
    status TEXT DEFAULT 'unread',
    metadata TEXT,
    created_at TEXT,
    read_at TEXT
);

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS recipient_user_id INTEGER;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS actor_user_id INTEGER;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS type TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'normal';
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS urgency TEXT DEFAULT 'standard';
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS body TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS preview TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deep_link TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS source_type TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS source_id TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS icon_url TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS avatar_url TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS metadata_json TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS seen_at TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivered_at TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS opened_at TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS failed_at TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS failure_reason TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS updated_at TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deleted_at TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS event_id INTEGER;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_status TEXT DEFAULT 'created';

CREATE TABLE IF NOT EXISTS notification_events (
    id SERIAL PRIMARY KEY,
    event_key TEXT UNIQUE,
    event_type TEXT,
    recipient_user_id INTEGER,
    actor_user_id INTEGER,
    source_type TEXT,
    source_id TEXT,
    payload_json TEXT,
    status TEXT DEFAULT 'accepted',
    suppression_reason TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS notification_delivery_jobs (
    id SERIAL PRIMARY KEY,
    notification_id INTEGER,
    user_id INTEGER,
    recipient_user_id INTEGER,
    channel TEXT,
    provider TEXT,
    status TEXT DEFAULT 'queued',
    dedupe_key TEXT UNIQUE,
    retry_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    scheduled_at TEXT,
    next_retry_at TEXT,
    failed_reason TEXT,
    provider_message_id TEXT,
    payload_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS notification_device_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    device_id TEXT,
    platform TEXT,
    push_token TEXT,
    endpoint TEXT,
    p256dh TEXT,
    auth TEXT,
    user_agent TEXT,
    app_version TEXT,
    enabled INTEGER DEFAULT 1,
    token_hash TEXT,
    last_seen_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    deleted_at TEXT,
    UNIQUE(user_id, device_id, platform)
);

CREATE TABLE IF NOT EXISTS notification_preferences (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    category TEXT,
    in_app INTEGER DEFAULT 1,
    push INTEGER DEFAULT 0,
    email INTEGER DEFAULT 0,
    telegram INTEGER DEFAULT 0,
    sms INTEGER DEFAULT 0,
    sound INTEGER DEFAULT 1,
    vibration INTEGER DEFAULT 1,
    lock_screen_preview INTEGER DEFAULT 1,
    quiet_hours_enabled INTEGER DEFAULT 0,
    quiet_hours_start TEXT DEFAULT '22:00',
    quiet_hours_end TEXT DEFAULT '07:00',
    muted_users_json TEXT,
    muted_conversations_json TEXT,
    blocked_users_json TEXT,
    category_rules_json TEXT,
    enable_push_notifications INTEGER DEFAULT 0,
    enable_notification_sound INTEGER DEFAULT 1,
    enable_notification_vibration INTEGER DEFAULT 1,
    notification_sound_type TEXT DEFAULT 'soft',
    updated_at TEXT,
    UNIQUE(user_id, category)
);

ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS sms INTEGER DEFAULT 0;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS sound INTEGER DEFAULT 1;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS vibration INTEGER DEFAULT 1;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS lock_screen_preview INTEGER DEFAULT 1;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS quiet_hours_enabled INTEGER DEFAULT 0;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS quiet_hours_start TEXT DEFAULT '22:00';
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS quiet_hours_end TEXT DEFAULT '07:00';
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS muted_users_json TEXT;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS muted_conversations_json TEXT;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS blocked_users_json TEXT;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS category_rules_json TEXT;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS enable_push_notifications INTEGER DEFAULT 0;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS enable_notification_sound INTEGER DEFAULT 1;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS enable_notification_vibration INTEGER DEFAULT 1;
ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS notification_sound_type TEXT DEFAULT 'soft';

CREATE INDEX IF NOT EXISTS idx_notifications_recipient_read_created ON notifications(recipient_user_id, read_at, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user_status_created ON notifications(user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_category_priority ON notifications(category, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_notification_events_recipient_type ON notification_events(recipient_user_id, event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_status ON notification_delivery_jobs(status, scheduled_at, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_notification ON notification_delivery_jobs(notification_id, channel);
CREATE INDEX IF NOT EXISTS idx_notification_device_tokens_user_enabled ON notification_device_tokens(user_id, enabled, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_notification_device_tokens_hash ON notification_device_tokens(token_hash);
