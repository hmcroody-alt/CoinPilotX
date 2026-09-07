"""A merchant's sourcing workspace. It is not a cart, a listing, or a product.

What this is
------------
"I am considering importing these supplier products." That is the entire
meaning. An Import Cart item is a *bookmark with variant selections* — merchant,
store, connection, provider, external product id, and which of that product's
variants the merchant ticked.

What this is not, and why it matters that the code says so
----------------------------------------------------------
There are four other things in this codebase called some kind of cart or
product, and the failure this module is written against is any of them being
mistaken for this one:

* ``marketplace_cart_*`` — a *buyer's* cart. Adding to that spends money.
* ``marketplace_listings`` — the canonical product. Rows there can be public.
* ``marketplace_product_sources`` — where a listing came from. Rows there imply
  a listing exists.
* ``supplier_import_drafts`` — a post-import review record.

Nothing in this table is any of those. Nothing here is visible to a buyer, ever,
by any route. A row here creates no listing, reserves no inventory, and costs
nobody anything. The invariant that keeps that true is that this module has no
write path to any table but its own — check the imports; there are no marketplace
writers here.

On the cached payload
---------------------
``cached_json`` holds the normalized product as it looked when the merchant
added it, so the cart renders without a provider round-trip per row. It is
explicitly *cache*, and :func:`get_cart` marks rows older than
:data:`STALE_AFTER` as ``cached_stale``.

This cache is never an import input. ``importer.import_selected`` re-fetches
from the provider and normalizes again. If a merchant sees $8.20 in the cart and
the real cost has moved to $9.40, the import proceeds on $9.40 and the review
screen shows the merchant the number that is actually true. Trusting the cache
would mean the client's stale view sets the economics of a product — which is
the same defect as trusting the client outright, just with extra steps.
"""

from __future__ import annotations

import json
import time
import uuid

from services import db
from services.business_os.suppliers import connections, normalize, policy
from services.business_os.suppliers.errors import SupplierError

TABLE = "supplier_import_cart_items"

#: A cached provider read older than this is shown as stale. Fifteen minutes is
#: chosen to be shorter than a merchant's sourcing session, so the staleness
#: badge appears while they are still looking rather than only on return.
STALE_AFTER = 900

#: A sourcing workspace, not a bulk-ingest queue. The ceiling exists because
#: ``import_selected`` performs at least one authoritative provider read per
#: item, and provider quota is a shared resource that fulfillment also needs.
MAX_ITEMS = 100

#: Variants a merchant may tick on one product. Matches the storage ceiling in
#: ``marketplace_variants.MAX_VARIANTS_PER_LISTING`` so that a cart selection
#: can never be accepted here and then rejected at import.
MAX_SELECTED_VARIANTS = 100

_schema_ready = False


def ensure_schema(conn=None):
    """Create the cart table. Idempotent; safe to call on every request.

    Follows the module convention in ``suppliers.gateway``: when no connection
    is supplied this opens and commits its own, because a caller that hands in a
    route's connection would have the DDL join that transaction and roll back
    with it. See ``memory: ensure_schema(conn) can hang a route on Postgres``.
    """
    global _schema_ready
    if _schema_ready and conn is None:
        return
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            item_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            business_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            connection_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_product_id TEXT NOT NULL,
            selected_variant_ids_json TEXT NOT NULL DEFAULT '[]',
            cached_json TEXT,
            cached_at DOUBLE PRECISION,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL)""")
        # One row per (tenant, connection, supplier product). This is what makes
        # "add the same product twice" an update instead of a duplicate row, and
        # it includes merchant_id so the constraint can never merge two tenants'
        # rows even if a connection id were somehow reused.
        conn.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS idx_import_cart_identity
            ON {TABLE} (merchant_id, business_id, store_id, connection_id, external_product_id)""")
        conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_import_cart_scope
            ON {TABLE} (business_id, store_id, connection_id)""")
        if owned:
            conn.commit()
            _schema_ready = True
    finally:
        if owned:
            conn.close()


def reset_schema_cache():
    """Test seam — ``ensure_schema`` caches "done" for the process lifetime."""
    global _schema_ready
    _schema_ready = False


def _scope(conn, business_id, store_id, actor_user_id, connection_id, *, context=None, write=False):
    """Authorize the actor, then prove the connection belongs to that tenant.

    Both halves are required and neither implies the other. ``_authorize``
    establishes that this user may manage this store and yields the merchant
    identity; ``_row`` establishes that *this connection id* belongs to that
    merchant. Skipping the second is how a merchant with a legitimate store and
    a guessed connection id reaches another tenant's supplier account.
    """
    merchant_id = connections._authorize(conn, business_id, store_id, actor_user_id,
                                         context=context, write=write)
    connections._row(conn, connection_id, business_id, store_id, merchant_id)
    return merchant_id


def _selected(value) -> list[str]:
    """Validate a merchant's variant selection into clean provider ids.

    Order is preserved and duplicates are dropped. An entry that is not a usable
    provider id is rejected rather than skipped: silently dropping one id from a
    selection of five imports four variants and reports success, and the
    merchant discovers the missing size when a customer cannot buy it.
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise SupplierError("invalid_variant_selection", http_status=400)
    if len(value) > MAX_SELECTED_VARIANTS:
        raise SupplierError("too_many_variants", http_status=400)
    out, seen = [], set()
    for entry in value:
        vid = normalize.external_id(entry)
        if vid is None:
            raise SupplierError("invalid_variant_selection", http_status=400)
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def _public(row, now=None):
    """Merchant-facing projection. Explicit allowlist, never ``dict(row)``.

    ``merchant_id`` is deliberately absent. It is the store owner's user id, and
    a cart row has no reason to carry an identity back to a client that already
    had to prove it owns the store to get here.
    """
    now = now if now is not None else time.time()
    try:
        cached = json.loads(row["cached_json"]) if row["cached_json"] else None
    except (ValueError, TypeError):
        cached = None
    cached_at = row["cached_at"]
    stale = True
    if isinstance(cached_at, (int, float)) and cached is not None:
        stale = (now - float(cached_at)) > STALE_AFTER
    return {
        "item_id": row["item_id"],
        "connection_id": row["connection_id"],
        "provider": row["provider"],
        "external_product_id": row["external_product_id"],
        "selected_variant_ids": json.loads(row["selected_variant_ids_json"] or "[]"),
        "cached": cached,
        "cached_at": cached_at,
        "cached_stale": stale,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _cacheable(product: dict | None) -> dict | None:
    """The subset of a normalized product worth caching for a cart row.

    Bounded on purpose. The full normalized product carries every variant with
    dimensions and warehouse strings; a hundred of those in one cart response is
    a payload no phone needs to render a list of cards.
    """
    if not isinstance(product, dict):
        return None
    low, high = normalize.cost_range(product.get("variants"))
    return {
        "title": product.get("title"),
        "cover_image_url": product.get("cover_image_url"),
        "category": product.get("category"),
        "origin": product.get("origin"),
        "currency": product.get("currency"),
        "variant_count": len(product.get("variants") or ()),
        "cost_low_cents": low,
        "cost_high_cents": high,
        "availability": _availability(product.get("variants")),
    }


def _availability(variants) -> str:
    """Aggregate stock across a product's variants, preserving UNKNOWN.

    Mirrors ``marketplace_variants.listing_availability``: any confirmed
    in-stock variant makes the product available; otherwise any indeterminate
    variant makes the whole thing unknown. Only when every variant is a
    confirmed negative is the product out of stock. A product with no variants
    is UNKNOWN, because "no variants" says nothing about stock.
    """
    seen_unknown = False
    seen_any = False
    for variant in variants or ():
        seen_any = True
        state = variant.get("stock_state")
        if state in (normalize.STOCK_IN_STOCK, normalize.STOCK_LOW_STOCK):
            return normalize.STOCK_IN_STOCK
        if state != normalize.STOCK_OUT_OF_STOCK:
            seen_unknown = True
    if not seen_any:
        return normalize.STOCK_UNKNOWN
    return normalize.STOCK_UNKNOWN if seen_unknown else normalize.STOCK_OUT_OF_STOCK


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cart(business_id, store_id, actor_user_id, connection_id, *, context=None):
    policy.require_enabled()
    ensure_schema()
    conn = db.connect()
    try:
        _scope(conn, business_id, store_id, actor_user_id, connection_id, context=context)
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE business_id=? AND store_id=? AND connection_id=? "
            f"ORDER BY created_at ASC", (business_id, store_id, connection_id)).fetchall()
        now = time.time()
        items = [_public(row, now) for row in rows]
        return {
            "items": items,
            "count": len(items),
            "stale_count": sum(1 for item in items if item["cached_stale"]),
            "max_items": MAX_ITEMS,
        }
    finally:
        conn.close()


def add_item(business_id, store_id, actor_user_id, connection_id, *,
             external_product_id, selected_variant_ids=None, product=None,
             provider="cj", context=None):
    """Add or update one supplier product in the cart.

    ``product`` is a normalized product used only to populate the display cache.
    Passing it is optional and passing a wrong one is harmless: it affects what
    the cart *shows*, never what the import *does*.

    Adding a product already in the cart updates its selection rather than
    creating a second row — the merchant tapped "Add" again, which reads as
    "make sure this is in my cart", not "put it in twice".
    """
    policy.require_enabled()
    ensure_schema()
    product_id = normalize.external_id(external_product_id)
    if product_id is None:
        raise SupplierError("invalid_product_reference", http_status=400)
    provider_key = str(provider or "cj").strip().lower()
    if not normalize.supported(provider_key):
        raise SupplierError("unsupported_provider", http_status=400)
    selection = _selected(selected_variant_ids)
    now = time.time()
    conn = db.connect()
    try:
        merchant_id = _scope(conn, business_id, store_id, actor_user_id, connection_id,
                             context=context, write=True)
        existing = conn.execute(
            f"SELECT item_id FROM {TABLE} WHERE merchant_id=? AND business_id=? AND store_id=? "
            f"AND connection_id=? AND external_product_id=?",
            (merchant_id, business_id, store_id, connection_id, product_id)).fetchone()
        cached = _cacheable(product)
        cached_json = json.dumps(cached, separators=(",", ":")) if cached else None
        if existing is not None:
            conn.execute(
                f"UPDATE {TABLE} SET selected_variant_ids_json=?, cached_json=COALESCE(?, cached_json), "
                f"cached_at=CASE WHEN ? IS NULL THEN cached_at ELSE ? END, updated_at=? "
                f"WHERE item_id=? AND merchant_id=?",
                (json.dumps(selection), cached_json, cached_json, now, now,
                 existing["item_id"], merchant_id))
            conn.commit()
            item_id = existing["item_id"]
        else:
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM {TABLE} WHERE business_id=? AND store_id=? AND connection_id=?",
                (business_id, store_id, connection_id)).fetchone()
            if int(count["n"]) >= MAX_ITEMS:
                raise SupplierError("import_cart_full", http_status=409)
            item_id = "ic_" + uuid.uuid4().hex
            conn.execute(
                f"INSERT INTO {TABLE} (item_id, merchant_id, business_id, store_id, connection_id, "
                f"provider, external_product_id, selected_variant_ids_json, cached_json, cached_at, "
                f"created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, merchant_id, business_id, store_id, connection_id, provider_key,
                 product_id, json.dumps(selection), cached_json, now if cached_json else None,
                 now, now))
            conn.commit()
        row = conn.execute(f"SELECT * FROM {TABLE} WHERE item_id=? AND merchant_id=?",
                           (item_id, merchant_id)).fetchone()
        if row is None:
            raise SupplierError("import_cart_write_failed", http_status=500)
        return _public(row, now)
    finally:
        conn.close()


def update_item(business_id, store_id, actor_user_id, connection_id, item_id, *,
                selected_variant_ids, context=None):
    """Change which variants are ticked on one cart item."""
    policy.require_enabled()
    ensure_schema()
    selection = _selected(selected_variant_ids)
    now = time.time()
    conn = db.connect()
    try:
        merchant_id = _scope(conn, business_id, store_id, actor_user_id, connection_id,
                             context=context, write=True)
        # merchant_id AND the connection scope, not item_id alone. An item id is
        # a random hex string, but "unguessable" is not an authorization model.
        updated = conn.execute(
            f"UPDATE {TABLE} SET selected_variant_ids_json=?, updated_at=? "
            f"WHERE item_id=? AND merchant_id=? AND business_id=? AND store_id=? AND connection_id=?",
            (json.dumps(selection), now, item_id, merchant_id, business_id, store_id, connection_id))
        conn.commit()
        if updated.rowcount != 1:
            raise SupplierError("not_found", http_status=404)
        row = conn.execute(f"SELECT * FROM {TABLE} WHERE item_id=? AND merchant_id=?",
                           (item_id, merchant_id)).fetchone()
        return _public(row, now)
    finally:
        conn.close()


def remove_item(business_id, store_id, actor_user_id, connection_id, item_id, *, context=None):
    policy.require_enabled()
    ensure_schema()
    conn = db.connect()
    try:
        merchant_id = _scope(conn, business_id, store_id, actor_user_id, connection_id,
                             context=context, write=True)
        deleted = conn.execute(
            f"DELETE FROM {TABLE} WHERE item_id=? AND merchant_id=? AND business_id=? "
            f"AND store_id=? AND connection_id=?",
            (item_id, merchant_id, business_id, store_id, connection_id))
        conn.commit()
        if deleted.rowcount != 1:
            raise SupplierError("not_found", http_status=404)
        return {"removed": item_id}
    finally:
        conn.close()


def items_for_import(conn, merchant_id, business_id, store_id, connection_id, item_ids):
    """INTERNAL: rows the importer is about to act on, in the caller's scope.

    Takes the importer's connection so the read joins its transaction. Every
    identifier is re-checked against the tenant here even though the importer
    already authorized: this function is what turns a list of client-supplied
    item ids into rows, and an item id that does not belong to this tenant must
    return nothing rather than a row the importer would then trust.
    """
    if not item_ids:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE merchant_id=? AND business_id=? AND store_id=? "
            f"AND connection_id=? ORDER BY created_at ASC",
            (merchant_id, business_id, store_id, connection_id)).fetchall()
        return [dict(row) for row in rows]
    out = []
    for item_id in item_ids[:MAX_ITEMS]:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE item_id=? AND merchant_id=? AND business_id=? "
            f"AND store_id=? AND connection_id=?",
            (item_id, merchant_id, business_id, store_id, connection_id)).fetchone()
        if row is not None:
            out.append(dict(row))
    return out


def drop_items(conn, merchant_id, item_ids):
    """INTERNAL: remove successfully imported rows, scoped to the owner."""
    for item_id in item_ids or ():
        conn.execute(f"DELETE FROM {TABLE} WHERE item_id=? AND merchant_id=?", (item_id, merchant_id))
