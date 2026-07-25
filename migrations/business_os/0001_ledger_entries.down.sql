-- Rollback for 0001_ledger_entries.sql
-- Drops the canonical ledger tables. Destructive: only run when intentionally
-- retiring the Business OS ledger slice. Data in these tables is NOT recoverable.

DROP INDEX IF EXISTS idx_ledger_entries_txn;
DROP INDEX IF EXISTS idx_ledger_entries_account;
DROP TABLE IF EXISTS ledger_balances;
DROP TABLE IF EXISTS ledger_entries;
DROP TABLE IF EXISTS ledger_transactions;
