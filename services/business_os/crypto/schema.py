"""Crypto-intelligence vertical schema — additive ``business_os_crypto_*`` tables (Stage 5).

Follows the marketplace / advertising ``ensure_schema`` convention exactly:
idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev /
PostgreSQL prod), no ``bot.py`` import, and it NEVER mutates any legacy table. In
particular the legacy ``portfolio_items``, ``manual_portfolio``, ``user_alerts``,
``watchlist_items`` and ``watchlists`` tables are left completely untouched; this
builds a new canonical surface beside them.

Design invariants (informational-only accounting — NO custody, NO trading):

* **Money is integer cents.** Every USD amount (``unit_price_cents``, ``fee_cents``,
  realized proceeds/cost) is a signed/unsigned integer number of cents. No floats
  for money, ever.
* **Quantity is a decimal string.** Crypto amounts need far more than cents'
  precision (satoshis, gwei), so ``quantity`` is stored as TEXT holding a canonical
  decimal string and parsed with ``decimal.Decimal`` in the engine. Storing it as a
  REAL would silently lose precision on 8+ decimal places.
* **Transactions are append-only.** ``business_os_crypto_transactions`` is the
  source of truth: an immutable lot log (buys create lots, sells consume them). The
  holdings table is a *projection* that can always be rebuilt by replaying it, so it
  is never the authority.
* **Alerts are durable and dedupe per crossing.** ``business_os_crypto_alerts`` holds
  the standing definition plus a small amount of edge-detect state
  (``last_state`` / ``last_fired_at``); ``business_os_crypto_alert_events`` is the
  append-only fired-log that makes evaluation idempotent and restart-safe — one row
  per crossing, so a process restart mid-sweep never double-notifies.

Text UUID primary keys are used everywhere to avoid depending on engine-specific
``lastrowid`` semantics across SQLite/PostgreSQL.

Everything here is structural and inert: creating empty tables changes zero runtime
behaviour. All reads/writes are gated in the service layer behind the
``BUSINESS_OS_CRYPTO`` flag.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


FLAG_ENV = "BUSINESS_OS_CRYPTO"


def new_id() -> str:
    """Opaque text UUID primary key (engine-agnostic)."""
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _existing_columns(conn, table: str) -> set:
    """Column names present on ``table`` (cross-engine). Empty set on any error."""
    try:
        if db.ENGINE_NAME == "sqlite":
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {r[1] for r in rows}
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _ensure_columns(conn, table: str, columns: dict) -> None:
    """Additively add any missing ``{name: sql_type}`` columns. Names/types come only
    from the fixed literal mapping below (never caller input), so the f-string DDL
    carries no injection surface."""
    present = _existing_columns(conn, table)
    for name, sql_type in columns.items():
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def ensure_schema(conn=None) -> None:
    """Create the crypto-intelligence tables if absent. Idempotent; safe at startup
    and in tests. Owns its connection unless one is passed in (so callers can compose
    it into a larger transaction)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # --- Append-only transaction / lot log (the source of truth) -----------
        # A buy adds a lot; a sell consumes lots (FIFO or average, decided by the
        # engine). This table is NEVER updated in place — corrections are new rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_crypto_transactions (
                txn_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('buy','sell')),
                quantity TEXT NOT NULL,
                unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                fee_cents INTEGER NOT NULL DEFAULT 0 CHECK (fee_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                executed_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_txn_user_symbol "
            "ON business_os_crypto_transactions (user_id, symbol, executed_at)"
        )
        # Idempotent ingest: an external feed replaying the same event must not
        # create a duplicate lot. NULL external_ref (manual entries) is exempt.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_crypto_txn_source_ref "
            "ON business_os_crypto_transactions (source, external_ref)"
        )

        # --- Holdings projection (rebuildable from the log; never the authority) -
        # One row per (user, symbol) summarising open position: net quantity and the
        # remaining cost basis in integer cents, plus realized P&L accumulated from
        # closed lots. Recomputed by the engine after each new transaction.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_crypto_holdings (
                holding_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '0',
                cost_basis_cents INTEGER NOT NULL DEFAULT 0,
                realized_pnl_cents INTEGER NOT NULL DEFAULT 0,
                method TEXT NOT NULL DEFAULT 'fifo',
                last_txn_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_crypto_holdings_user_symbol "
            "ON business_os_crypto_holdings (user_id, symbol)"
        )

        # --- Open-lot ledger (FIFO consumption bookkeeping) --------------------
        # Each buy opens a lot with a remaining quantity; sells decrement remaining
        # across lots oldest-first. Lets the engine compute realized cost precisely
        # without re-scanning the whole transaction history each time.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_crypto_lots (
                lot_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                txn_id TEXT NOT NULL,
                original_quantity TEXT NOT NULL,
                remaining_quantity TEXT NOT NULL,
                unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
                acquired_at TEXT NOT NULL,
                closed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_lots_open "
            "ON business_os_crypto_lots (user_id, symbol, closed, acquired_at)"
        )

        # --- Durable price alerts (standing definitions + edge-detect state) ---
        # metric: what to watch (e.g. 'price_usd', 'pct_change_24h'); comparator:
        # 'above'/'below'/'crosses_above'/'crosses_below'; threshold as a decimal
        # string. last_state records the most recent evaluated side so the sweeper
        # only fires on an actual crossing, not on every tick above threshold.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_crypto_alerts (
                alert_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                metric TEXT NOT NULL DEFAULT 'price_usd',
                comparator TEXT NOT NULL,
                threshold TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                repeat_mode TEXT NOT NULL DEFAULT 'once',
                last_state TEXT,
                last_value TEXT,
                last_fired_at TEXT,
                cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_alerts_active "
            "ON business_os_crypto_alerts (active, symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_alerts_user "
            "ON business_os_crypto_alerts (user_id)"
        )

        # --- Append-only fired-log (idempotent, restart-safe dedupe) -----------
        # One row per crossing. A unique key on (alert_id, crossing_key) makes a
        # replay after a mid-sweep restart a no-op, so a user is never double-paged
        # for the same crossing.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_crypto_alert_events (
                event_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                crossing_key TEXT NOT NULL,
                observed_value TEXT NOT NULL,
                threshold TEXT NOT NULL,
                comparator TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_crypto_alert_events_crossing "
            "ON business_os_crypto_alert_events (alert_id, crossing_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_alert_events_alert "
            "ON business_os_crypto_alert_events (alert_id, created_at)"
        )

        # --- Append-only audit -------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_crypto_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type TEXT NOT NULL,
                subject_ref TEXT,
                action TEXT NOT NULL,
                actor TEXT,
                reason TEXT,
                before_json TEXT,
                after_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_audit_subject "
            "ON business_os_crypto_audit (subject_type, subject_ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crypto_audit_action "
            "ON business_os_crypto_audit (action)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
