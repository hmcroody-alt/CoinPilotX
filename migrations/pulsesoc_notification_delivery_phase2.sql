-- PulseSoc Notification Delivery Adapters Phase 2
-- PostgreSQL-compatible additive migration for provider attempts, push metadata,
-- sound/vibration payloads, and native/Web Push device readiness.

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS sound_key TEXT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS vibration_json TEXT;

ALTER TABLE notification_delivery_jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;
ALTER TABLE notification_delivery_jobs ADD COLUMN IF NOT EXISTS attempted_at TEXT;
ALTER TABLE notification_delivery_jobs ADD COLUMN IF NOT EXISTS failed_at TEXT;
ALTER TABLE notification_delivery_jobs ADD COLUMN IF NOT EXISTS provider_response_json TEXT;

ALTER TABLE notification_device_tokens ADD COLUMN IF NOT EXISTS push_provider TEXT;
ALTER TABLE notification_device_tokens ADD COLUMN IF NOT EXISTS environment TEXT;

CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_attempts
    ON notification_delivery_jobs(status, retry_count, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_provider
    ON notification_delivery_jobs(provider, status, attempted_at);

CREATE INDEX IF NOT EXISTS idx_notification_device_tokens_provider
    ON notification_device_tokens(user_id, platform, push_provider, enabled);
