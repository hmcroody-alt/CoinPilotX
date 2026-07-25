-- Rollback for 0002_provider_webhook_events.sql
-- Drops the webhook inbox. Destructive: unprocessed events are NOT recoverable.

DROP INDEX IF EXISTS idx_webhook_events_status;
DROP TABLE IF EXISTS provider_webhook_events;
