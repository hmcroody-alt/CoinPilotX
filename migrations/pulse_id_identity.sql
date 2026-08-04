-- PulseSoc permanent account identity.
-- Runtime migration is performed by services/pulse_id_service.py so the same
-- backfill is safe on SQLite and PostgreSQL. This file documents the durable
-- production schema contract.
ALTER TABLE users ADD COLUMN IF NOT EXISTS pulse_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_pulse_id ON users(pulse_id) WHERE pulse_id IS NOT NULL;
