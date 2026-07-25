-- Business OS Stage 1 — durable, idempotent provider-webhook inbox
-- Persist-before-process. UNIQUE (provider, provider_event_id) makes replays
-- no-ops. reconcile_pending() replays received/failed/stranded rows.
-- Rollback: 0002_provider_webhook_events.down.sql

CREATE TABLE IF NOT EXISTS provider_webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_type TEXT,
    payload_json TEXT NOT NULL,
    signature_verified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'received',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON provider_webhook_events (status);
