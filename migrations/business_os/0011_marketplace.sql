-- 0011_marketplace.sql — Business OS Stage 3 Marketplace MVP canonical surface.
--
-- Additive, strangler-pattern. Creates the business_os_mkt_* tables alongside the
-- untouched legacy inline-bot.py marketplace (marketplace_listings / marketplace_sellers
-- / seller_transactions / *_placeholder). NOTHING legacy is altered or dropped.
-- Mirrors services/business_os/marketplace/schema.py::ensure_schema (the runtime
-- source of truth). Idempotent: every statement is IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS business_os_mkt_sellers (
    seller_user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    display_name TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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
);
CREATE INDEX IF NOT EXISTS idx_mkt_products_seller ON business_os_mkt_products (seller_user_id);

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
);
CREATE INDEX IF NOT EXISTS idx_mkt_orders_buyer ON business_os_mkt_orders (buyer_user_id);
CREATE INDEX IF NOT EXISTS idx_mkt_orders_seller ON business_os_mkt_orders (seller_user_id);

CREATE TABLE IF NOT EXISTS business_os_mkt_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    title TEXT,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    line_total_cents INTEGER NOT NULL CHECK (line_total_cents >= 0),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mkt_order_items_order ON business_os_mkt_order_items (order_id);

CREATE TABLE IF NOT EXISTS business_os_mkt_order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT,
    reason TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mkt_order_events_order ON business_os_mkt_order_events (order_id);

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
);
CREATE INDEX IF NOT EXISTS idx_mkt_refunds_order ON business_os_mkt_refunds (order_id);

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
);
CREATE INDEX IF NOT EXISTS idx_mkt_disputes_order ON business_os_mkt_disputes (order_id);

CREATE TABLE IF NOT EXISTS business_os_mkt_reviews (
    review_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    buyer_user_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    body TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mkt_reviews_product ON business_os_mkt_reviews (product_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mkt_reviews_buyer_order_product
    ON business_os_mkt_reviews (buyer_user_id, order_id, product_id);

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
);
CREATE INDEX IF NOT EXISTS idx_mkt_audit_subject ON business_os_mkt_audit (subject_type, subject_ref);
CREATE INDEX IF NOT EXISTS idx_mkt_audit_action ON business_os_mkt_audit (action);
