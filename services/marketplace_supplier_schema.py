"""The canonical owner of the variant + supplier-source schema.

Why this module exists
----------------------
``COMMERCE_DROPSHIPPING_FOUNDATION_MAP.md`` established that ``marketplace_listings``
is the only populated product ledger in production, and that a supplier-sourced
product has nowhere to live on it. A product row is flat: the nearest thing to a
variant is a twelve-entry JSON list inside ``listing_metadata_json`` whose
validator reduces every entry to exactly ``{"name", "value"}``
(``marketplace_listing_types.py:194-197``), so it cannot carry an id even if a
caller sent one. Nothing addressable, therefore nothing for a SKU, a supplier
cost, a per-variant stock level, or a provider's own identifier to attach to.

This module adds that missing unit *beside* the listing rather than inside it.
The listing stays the product; a variant is a purchasable configuration of it.

What this module deliberately does not do
-----------------------------------------
It does not make variants a money authority. ``price_cents`` is nullable and NULL
means "this variant has no price of its own; the listing's ``price_label``
governs", which is exactly today's behaviour. Retail money on the live path is
still parsed from prose by ``bot.parse_price_label_to_cents`` and this module does
not change that, because ``marketplace_listings`` has live orders against it and
swapping its money representation is not a schema change — it is a migration.
Until that happens, a variant carries the *supplier* side of the money (integer
``cost_cents``) and leaves the buyer-facing side alone. Two money representations
that both claim authority is the failure this ordering avoids.

Nor does it publish anything. Publication in this codebase is not something a
writer does — ``marketplace_listing_lifecycle.is_public`` tests it at read time —
so a variant cannot make a listing visible and no new gate is needed here.

The rule this module enforces
-----------------------------
There is exactly one copy of this DDL and everything that needs the schema calls
the same function. This is the lesson ``marketplace_reservation_schema`` was
written to record: reservation lifecycle columns lived in a cart route handler,
so the expiry sweeper — a different process that never serves HTTP — died on
``UndefinedColumn`` until a buyer happened to open a cart. A supplier import will
run in a worker for exactly the same reason a sweep does, so its schema must not
be contingent on web traffic.

``ensure_supplier_schema`` is idempotent, non-destructive, safe to call
concurrently from the web process and a worker at the same instant, and safe on
both engines (SQLite in tests, PostgreSQL in production). It only ever creates: a
table if absent, nullable columns if absent, an index if absent. It never drops,
never rewrites and never backfills. It never raises — the failure is returned as
data so a worker loop that calls it can degrade and retry rather than die.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

VARIANT_TABLE = "marketplace_listing_variants"
SOURCE_TABLE = "marketplace_product_sources"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Stock is three-valued and the third value is the point of it. A supplier's
#: stock is *routinely* unknown — the last sync failed, the provider is rate
#: limiting, the item is new — and the live column cannot say so:
#: ``marketplace_listing_lifecycle.inventory_available`` returns ``False`` for a
#: NULL quantity, so a sync outage is indistinguishable from a sell-out. Storing
#: the distinction is the only way a caller can choose different behaviour for
#: "we know there are none" and "we do not know", which are different facts with
#: different correct responses.
STOCK_UNKNOWN = "UNKNOWN"
STOCK_IN_STOCK = "IN_STOCK"
STOCK_OUT_OF_STOCK = "OUT_OF_STOCK"
STOCK_STATES = (STOCK_UNKNOWN, STOCK_IN_STOCK, STOCK_OUT_OF_STOCK)

#: Where a variant came from. ``manual`` is a first-class provider, not a null
#: case: a merchant-authored variant and a CJ-authored one differ in who owns the
#: fields, and modelling "no provider" as an absent row would make that question
#: unanswerable for the merchant's own products.
PROVIDER_MANUAL = "manual"
PROVIDERS = (PROVIDER_MANUAL, "cj", "printful", "printify")

#: Provider identity is separate from how the thing is fulfilled. A product can
#: be imported from CJ and stocked in the seller's own garage, or authored by
#: hand and drop-shipped. Collapsing these two into one column is the modelling
#: error that makes "which supplier do I call for this order" unanswerable later.
MODE_STOCKED = "STOCKED"
MODE_DROPSHIP = "DROPSHIP"
FULFILLMENT_MODES = (MODE_STOCKED, MODE_DROPSHIP)


VARIANT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {VARIANT_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    seller_user_id INTEGER NOT NULL,
    variant_key TEXT NOT NULL,
    options_json TEXT DEFAULT '[]',
    sku TEXT,
    provider_variant_id TEXT,
    price_cents INTEGER,
    cost_cents INTEGER,
    currency TEXT,
    stock_quantity INTEGER,
    stock_state TEXT DEFAULT '{STOCK_UNKNOWN}',
    stock_synced_at TEXT,
    position INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
)
"""

SOURCE_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {SOURCE_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    seller_user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    provider_product_id TEXT NOT NULL,
    fulfillment_mode TEXT DEFAULT '{MODE_DROPSHIP}',
    overridden_fields_json TEXT DEFAULT '[]',
    last_synced_at TEXT,
    last_sync_error TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

# Added defensively rather than by editing the DDL above. There is no migration
# framework here — schema is created imperatively and must be idempotent — so an
# edit to the CREATE would only reach fresh databases and would silently skip
# production, which already has the table. This list is where later columns go.
VARIANT_COLUMNS = (
    ("sku", "TEXT"),
    ("provider_variant_id", "TEXT"),
    ("price_cents", "INTEGER"),
    ("cost_cents", "INTEGER"),
    ("currency", "TEXT"),
    ("stock_quantity", "INTEGER"),
    ("stock_state", "TEXT"),
    ("stock_synced_at", "TEXT"),
    ("position", "INTEGER"),
    ("status", "TEXT"),
)

SOURCE_COLUMNS = (
    ("fulfillment_mode", "TEXT"),
    ("overridden_fields_json", "TEXT"),
    ("last_synced_at", "TEXT"),
    ("last_sync_error", "TEXT"),
)

# Uniqueness is expressed as an index rather than a table constraint because the
# table may already exist on a database that predates this module, and there is
# no ALTER path to add a UNIQUE constraint idempotently across both engines.
#
# The variant index is what makes re-import idempotent: `variant_key` is derived
# from the option set (order-independent — see marketplace_variants.variant_key),
# so importing the same supplier product twice updates rather than duplicates.
#
# The source index is per (provider, provider_product_id, seller). Not per
# provider product alone: two different sellers importing the same CJ product is
# normal and must not collide. Not per listing alone either, though a second
# index enforces one source per listing — a listing that claimed two suppliers
# would have no answer to "who fulfils this".
VARIANT_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_mkt_variant_listing_key "
    f"ON {VARIANT_TABLE} (listing_id, variant_key)"
)
VARIANT_LOOKUP_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS idx_mkt_variant_seller "
    f"ON {VARIANT_TABLE} (seller_user_id, listing_id)"
)
SOURCE_PROVIDER_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_mkt_source_provider_ref "
    f"ON {SOURCE_TABLE} (provider, provider_product_id, seller_user_id)"
)
SOURCE_LISTING_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_mkt_source_listing "
    f"ON {SOURCE_TABLE} (listing_id)"
)

#: Without these there is no honest answer to "what are this listing's variants",
#: so a caller must report that it could not look rather than that it looked and
#: found none. Those two outcomes are identical in every counter and opposite in
#: meaning — the same reasoning as REQUIRED_SWEEP_COLUMNS in the reservation
#: schema, and the same reason this is a separate list from the DDL.
REQUIRED_VARIANT_COLUMNS = ("listing_id", "seller_user_id", "variant_key",
                            "stock_state", "cost_cents")
REQUIRED_SOURCE_COLUMNS = ("listing_id", "seller_user_id", "provider",
                           "provider_product_id", "fulfillment_mode")

STATUS_READY = "ready"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"

_SCHEMA_READY = False
_COLUMN_CACHE: dict[str, set[str]] | None = None


def reset_schema_cache() -> None:
    """Forget the caches. For tests, and for any caller that changed the tables."""
    global _SCHEMA_READY, _COLUMN_CACHE
    _SCHEMA_READY = False
    _COLUMN_CACHE = None


def _columns(cur, table: str) -> set[str]:
    from services import db as db_module

    return db_module.get_table_columns(cur, table)


def _result(status: str, *, added=(), missing=(), error: str | None = None) -> dict:
    return {
        "status": status,
        "added": list(added),
        "missing": list(missing),
        "error": error,
    }


def _ensure_table(cur, table: str, ddl: str, columns, required) -> tuple[list, list]:
    """Create one table and its columns. Returns (added, still_missing)."""
    try:
        cur.execute(ddl)
    except Exception as exc:
        # Not fatal on its own. The overwhelmingly common case is that the table
        # already exists and this is a no-op, and a CREATE ... IF NOT EXISTS that
        # raises anyway (a concurrent creator, a role with ALTER but not CREATE)
        # should still let the column check below decide whether the schema is
        # usable, rather than aborting on a statement whose whole job is to be
        # skippable.
        LOGGER.warning("SUPPLIER_SCHEMA_TABLE_DDL_FAILED table=%s error=%s", table, exc)

    existing = _columns(cur, table)
    added = []
    for column, definition in columns:
        if column in existing:
            continue
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            added.append(column)
        except Exception:
            # A concurrent process may have added it between the introspection
            # and here. Losing that race is the correct outcome, not an error.
            # Anything else (no ALTER grant, a lock) shows up below as a column
            # that is still missing.
            LOGGER.exception("SUPPLIER_COLUMN_ADD_FAILED table=%s column=%s", table, column)

    if added:
        existing = _columns(cur, table)
    return added, [name for name in required if name not in existing]


def ensure_supplier_schema(cur, *, force: bool = False) -> dict:
    """Create the variant and source tables, their columns and their indexes.

    Returns a structured result; never raises. ``status`` is one of:

    ``ready``    every required column is present — callers may proceed.
    ``missing``  the ensure completed but required columns are still absent,
                 e.g. the role cannot ``ALTER``. Callers must not proceed.
    ``error``    the ensure itself failed — locked, unreachable, permissions.
                 Callers must not proceed and the next interval retries.

    ``force`` bypasses the process cache, for tests and for any caller with
    reason to believe the tables changed underneath it.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return _result(STATUS_READY)

    try:
        variant_added, variant_missing = _ensure_table(
            cur, VARIANT_TABLE, VARIANT_TABLE_DDL, VARIANT_COLUMNS,
            REQUIRED_VARIANT_COLUMNS)
        source_added, source_missing = _ensure_table(
            cur, SOURCE_TABLE, SOURCE_TABLE_DDL, SOURCE_COLUMNS,
            REQUIRED_SOURCE_COLUMNS)
    except Exception as exc:
        LOGGER.exception("SUPPLIER_SCHEMA_ENSURE_FAILED stage=tables")
        return _result(STATUS_ERROR, error=str(exc)[:500])

    for ddl in (VARIANT_INDEX_DDL, VARIANT_LOOKUP_INDEX_DDL,
                SOURCE_PROVIDER_INDEX_DDL, SOURCE_LISTING_INDEX_DDL):
        try:
            cur.execute(ddl)
        except Exception as exc:
            # Deliberately non-fatal, but note this is a weaker guarantee than
            # the reservation schema's index: two of these are UNIQUE and carry
            # the idempotency of re-import. Without them a repeated import
            # duplicates rather than updates. The writer therefore does not rely
            # on them — marketplace_variants reads before it writes — so a
            # database that refuses the index degrades to a race window rather
            # than to silent duplication.
            LOGGER.warning("SUPPLIER_INDEX_CREATE_FAILED error=%s", exc)

    added = variant_added + source_added
    missing = variant_missing + source_missing
    if missing:
        LOGGER.error("SUPPLIER_SCHEMA_MISSING missing=%s added=%s",
                     ",".join(missing), ",".join(added) or "-")
        return _result(STATUS_MISSING, added=added, missing=missing)

    _SCHEMA_READY = True
    LOGGER.info("SUPPLIER_SCHEMA_READY added=%s", ",".join(added) or "-")
    return _result(STATUS_READY, added=added)
