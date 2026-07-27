-- Business OS Stage 1 — canonical financial ledger
-- Immutable, integer-cents, double-entry, idempotent, atomic.
-- Portable across SQLite (dev) and PostgreSQL (prod) via services.db translation
-- (INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL). Apply with the migration runner.
-- Rollback: 0001_ledger_entries.down.sql

CREATE TABLE IF NOT EXISTS ledger_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    transaction_id TEXT NOT NULL UNIQUE,
    actor TEXT,
    entry_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    currency TEXT NOT NULL,
    source_account TEXT NOT NULL,
    destination_account TEXT NOT NULL,
    reason TEXT,
    related_object TEXT,
    provider_reference TEXT,
    status TEXT NOT NULL DEFAULT 'posted',
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    account TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('debit','credit')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    signed_amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    entry_type TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_balances (
    account TEXT NOT NULL,
    currency TEXT NOT NULL,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account, currency)
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_account ON ledger_entries (account, currency);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_txn ON ledger_entries (transaction_id);
