"""Marketplace vertical schema — additive ``business_os_mkt_*`` tables (Stage 3).

Follows the advertising slice's ``ensure_schema`` convention exactly: idempotent
``CREATE TABLE IF NOT EXISTS`` via ``services.db`` (SQLite dev / PostgreSQL prod),
no ``bot.py`` import, and it NEVER mutates any legacy table. In particular the
legacy inline-``bot.py`` marketplace tables (``marketplace_listings``,
``marketplace_sellers``, ``seller_transactions``, the ``*_placeholder`` tables …)
are left completely untouched; this builds a new canonical surface beside them.

Tables:

* ``business_os_mkt_sellers`` — seller approval state, ONE row per user (the
  merchant-approval input, separate from account hold and commercial entitlement).
* ``business_os_mkt_products`` — product/listing catalog with ownership, lifecycle
  state, price (integer cents), physical/digital fulfillment type, and inventory.
* ``business_os_mkt_orders`` — the canonical order with an explicit state-machine
  ``status`` column, integer-cents money fields, and a server-authoritative
  platform-fee snapshot. This is the real table the legacy 0-row
  ``marketplace_orders_placeholder`` was always meant to become.
* ``business_os_mkt_order_items`` — immutable per-order line items (price + qty
  snapshotted at purchase time so later product edits never rewrite history).
* ``business_os_mkt_order_events`` — append-only order state-machine transition log.
* ``business_os_mkt_refunds`` — refund/return records (integer cents) tied to a
  ledger reversal reference.
* ``business_os_mkt_disputes`` — buyer-opened disputes and their governed resolution.
* ``business_os_mkt_reviews`` — verified-purchase product reviews (rating 1..5).
* ``business_os_mkt_audit`` — append-only audit of seller/order/refund/dispute
  changes (also the substrate for admin restrictions + appeals, keyed by ``action``).

Text UUID primary keys are used for products/orders/refunds/disputes to avoid
depending on engine-specific ``lastrowid`` semantics across SQLite/PostgreSQL.

Everything here is structural and inert: creating empty tables changes zero
runtime behaviour. All reads/writes are gated in ``service``/``orders`` behind the
``BUSINESS_OS_MARKETPLACE`` flag.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services import db


def _utc_now_iso() -> str:
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
    """Create the marketplace tables if absent. Idempotent; safe at startup + tests.

    Owns its connection unless one is passed in (so callers can compose it into a
    larger transaction).
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # Seller approval — one row per user. Authority is separate from commercial
        # entitlement and from account hold/suspension.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_sellers (
                seller_user_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                display_name TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # Product / listing catalog.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_products (
                product_id TEXT PRIMARY KEY,
                seller_user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                fulfillment_type TEXT NOT NULL DEFAULT 'physical',
                inventory_qty INTEGER,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_products_seller "
            "ON business_os_mkt_products (seller_user_id)"
        )

        # Canonical order with an explicit state-machine status.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_orders (
                order_id TEXT PRIMARY KEY,
                buyer_user_id TEXT NOT NULL,
                seller_user_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                currency TEXT NOT NULL DEFAULT 'usd',
                subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
                total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
                platform_fee_bps INTEGER NOT NULL DEFAULT 0,
                platform_fee_cents INTEGER NOT NULL DEFAULT 0,
                seller_net_cents INTEGER NOT NULL DEFAULT 0,
                refunded_cents INTEGER NOT NULL DEFAULT 0,
                fulfillment_type TEXT NOT NULL DEFAULT 'physical',
                tracking_ref TEXT,
                capture_txn_ref TEXT,
                settle_txn_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_orders_buyer "
            "ON business_os_mkt_orders (buyer_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_orders_seller "
            "ON business_os_mkt_orders (seller_user_id)"
        )

        # Immutable per-order line items (price/qty snapshot at purchase time).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                title TEXT,
                unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                line_total_cents INTEGER NOT NULL CHECK (line_total_cents >= 0),
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_order_items_order "
            "ON business_os_mkt_order_items (order_id)"
        )

        # Append-only order state-machine transition log.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                actor TEXT,
                reason TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_order_events_order "
            "ON business_os_mkt_order_events (order_id)"
        )

        # Refund / return records tied to a ledger reversal reference.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_refunds (
                refund_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                reason TEXT,
                kind TEXT NOT NULL DEFAULT 'refund',
                actor TEXT,
                ledger_txn_ref TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_refunds_order "
            "ON business_os_mkt_refunds (order_id)"
        )

        # Buyer-opened disputes + governed resolution.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_disputes (
                dispute_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                buyer_user_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                reason TEXT,
                resolution TEXT,
                resolver TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_disputes_order "
            "ON business_os_mkt_disputes (order_id)"
        )

        # Verified-purchase product reviews.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_reviews (
                review_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                buyer_user_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                body TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_reviews_product "
            "ON business_os_mkt_reviews (product_id)"
        )
        # One review per (buyer, order, product) — verified-purchase, no spam.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_mkt_reviews_buyer_order_product "
            "ON business_os_mkt_reviews (buyer_user_id, order_id, product_id)"
        )

        # Append-only audit (also the substrate for admin restrictions + appeals).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_audit (
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
            "CREATE INDEX IF NOT EXISTS idx_mkt_audit_subject "
            "ON business_os_mkt_audit (subject_type, subject_ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_audit_action "
            "ON business_os_mkt_audit (action)"
        )

        # Governed-assistant confirmation grants. One row per approval minted by
        # assistant.plan(); consumed exactly once by assistant.execute(). The raw
        # token is NEVER stored — only its sha256 — and the row binds the grant to
        # (user, tool, canonical params) so an approval can never be redeemed for a
        # different actor, tool or payload. ``status`` + ``expires_at`` make the
        # grant single-use and time-limited.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_mkt_assistant_confirmations (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tool TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                params_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                consumed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_asst_confirm_user "
            "ON business_os_mkt_assistant_confirmations (user_id, tool, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mkt_asst_confirm_expiry "
            "ON business_os_mkt_assistant_confirmations (expires_at)"
        )

        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
