"""Normalized supplier reads, immutable snapshots, and explicit product bindings.

Every public entry point resolves canonical Business OS/store ownership before
loading a credential or cache. Supplier content remains untrusted data and can
only create an import draft, never publish or change a retail product.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from services import db
from services import marketplace_supplier_schema
from services import marketplace_variants
from services.business_os.suppliers import connections, policy
from services.business_os.suppliers.errors import SupplierError


def ensure_schema(conn=None):
    """Snapshots, read cache and import drafts — but *not* a product mapping.

    This function used to also create ``supplier_product_links``, a mapping from
    a CJ (pid, vid) to a ``business_os_mkt_products`` row. That made it a second
    product-provenance authority competing with ``marketplace_product_sources``,
    and it keyed off a ledger with no rows in production while the live
    marketplace ran on ``marketplace_listings``. The mapping now lives in the
    canonical table and this module ensures that schema instead of its own.

    The remaining three tables are supplier-gateway concerns and stay here:
    immutable provider snapshots, a provider read cache, and merchant import
    drafts. None of them claims to say what a product *is*.
    """
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_snapshots (
            snapshot_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
            business_id TEXT NOT NULL, store_id TEXT NOT NULL,
            kind TEXT NOT NULL, resource_id TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_read_cache (
            cache_key TEXT PRIMARY KEY, payload_json TEXT,
            expires_at DOUBLE PRECISION NOT NULL DEFAULT 0, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
            lease_owner TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_import_drafts (
            draft_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
            business_id TEXT NOT NULL, store_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            merchant_fields_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
            created_at DOUBLE PRECISION NOT NULL)""")
        # The canonical mapping schema, ensured here so that a supplier worker —
        # which never serves an HTTP request and never runs bot.init_db() — can
        # still create it. Returned as data, never raised: a worker loop that
        # cannot get the schema must degrade and retry, not die on import.
        marketplace_supplier_schema.ensure_supplier_schema(conn)
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _owned_listing_id(conn, canonical_product_id, merchant_id):
    """Resolve a merchant-supplied product reference to an owned listing id.

    This is the §3 lookup chain's middle link: the caller has already been
    authenticated and authorised for the store, and ``merchant_id`` came from
    ``connections._canonical_scope`` — it is ``business_os_business.owner_user_id``,
    which is the same identity as ``marketplace_listings.seller_user_id``, stored
    as TEXT on one side and INTEGER on the other. Nothing here infers tenancy
    from a CJ id.

    Every failure — unparseable reference, absent listing, someone else's
    listing, a merchant_id that is not a user id — raises the identical 404. A
    caller cannot use this to discover which listing ids exist.
    """
    try:
        listing_id = marketplace_variants.coerce_listing_id(canonical_product_id)
        owner_user_id = int(str(merchant_id).strip())
    except (marketplace_variants.VariantRejected, TypeError, ValueError):
        raise SupplierError("not_found", http_status=404) from None
    row = conn.execute("SELECT seller_user_id FROM marketplace_listings WHERE id=?",
                       (listing_id,)).fetchone()
    if row is None or row["seller_user_id"] is None or int(row["seller_user_id"]) != owner_user_id:
        raise SupplierError("not_found", http_status=404)
    return listing_id, owner_user_id


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _scope(connection_id, business_id, store_id, actor, context=None, write=False):
    policy.require_enabled()
    return connections.get_connection(connection_id, business_id, store_id, actor,
                                      context=context, write=write)


def _cache_key(connection_id, business_id, store_id, operation, args):
    return hashlib.sha256(_json([connection_id, business_id, store_id, operation, args]).encode()).hexdigest()


def _cached_read(key, ttl, fetch):
    """Cross-process single-flight; a busy caller queues by Retry-After, not sleep.

    A failed provider read never overwrites stock with zero or serves stale data
    as verified. Lease ownership prevents a late result from overwriting a newer
    result after a process stalled. No transaction stays open across HTTP.
    """
    ensure_schema()
    now = time.time()
    owner = uuid.uuid4().hex
    conn = db.connect()
    try:
        conn.execute("INSERT INTO supplier_read_cache (cache_key) VALUES (?) ON CONFLICT(cache_key) DO NOTHING", (key,))
        conn.commit()
        row = conn.execute("SELECT * FROM supplier_read_cache WHERE cache_key=?", (key,)).fetchone()
        if row["payload_json"] and row["expires_at"] > now:
            return json.loads(row["payload_json"]), True
        claim = conn.execute("UPDATE supplier_read_cache SET lease_until=?,lease_owner=? WHERE cache_key=? AND lease_until<=? AND expires_at<=?",
                             (now + 90, owner, key, now, now))
        conn.commit()
        if claim.rowcount != 1:
            raise SupplierError("request_in_progress", http_status=429, retry_after=2)
    finally:
        conn.close()
    try:
        value = fetch()
        encoded = _json(value)
        if len(encoded.encode()) > 1_000_000:
            raise SupplierError("provider_response_too_large", http_status=502)
        conn = db.connect()
        try:
            conn.execute("UPDATE supplier_read_cache SET payload_json=?,expires_at=?,lease_until=0,lease_owner=NULL WHERE cache_key=? AND lease_owner=?",
                         (encoded, time.time() + ttl, key, owner))
            conn.commit()
        finally:
            conn.close()
        return value, False
    finally:
        conn = db.connect()
        try:
            conn.execute("UPDATE supplier_read_cache SET lease_until=0,lease_owner=NULL WHERE cache_key=? AND lease_owner=?", (key, owner))
            conn.commit()
        finally:
            conn.close()


def _snapshot(connection_id, business_id, store_id, kind, resource_id, payload):
    snapshot_id = uuid.uuid4().hex
    conn = db.connect()
    try:
        conn.execute("INSERT INTO supplier_snapshots (snapshot_id,connection_id,business_id,store_id,kind,resource_id,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (snapshot_id, connection_id, business_id, store_id, kind, str(resource_id), _json(payload), time.time()))
        conn.commit()
    finally:
        conn.close()
    return snapshot_id


def read(operation, *, business_id, store_id, actor_user_id, connection_id,
         params=None, context=None, adapter=None):
    params = params or {}
    if not isinstance(params, dict):
        raise SupplierError("invalid_input", http_status=400)
    connection = _scope(connection_id, business_id, store_id, actor_user_id, context)
    allowed = {"categories", "search", "product", "variants", "inventory", "warehouses", "shipping", "subscriptions", "balance"}
    if operation not in allowed:
        raise SupplierError("unknown_operation", http_status=404)
    active_adapter = None
    # One normalized gateway; there is no generic path/HTTP proxy.
    def fetch():
        nonlocal active_adapter
        cj = connections.adapter_for(business_id, store_id, actor_user_id,
                                     connection_id, context=context, adapter=adapter)
        active_adapter = cj
        if operation == "categories":
            return cj.get_categories()
        if operation == "search":
            try:
                page, size = int(params.get("page", 1)), int(params.get("size", 20))
            except (TypeError, ValueError):
                raise SupplierError("invalid_pagination", http_status=400) from None
            return cj.search_products(params.get("filters", {}), page=page, size=size)
        if operation == "product":
            return cj.get_product(params.get("pid"))
        if operation == "variants":
            return cj.get_variants(params.get("pid"))
        if operation == "inventory":
            return cj.get_inventory(params.get("pid"), params.get("vid"))
        if operation == "warehouses":
            return cj.get_warehouses()
        if operation == "shipping":
            return cj.estimate_shipping(params)
        if operation == "subscriptions":
            return cj.get_subscriptions(connection["external_shop_id"], page=1, size=20)
        return cj.get_balance()
    ttl = {"inventory": 10, "shipping": 30, "balance": 5, "subscriptions": 30}.get(operation, 300)
    def observed_fetch():
        try:
            value = fetch()
        except Exception as exc:
            connections.record_activity(connection_id, business_id, store_id, adapter=active_adapter, error=exc)
            raise
        connections.record_activity(connection_id, business_id, store_id, adapter=active_adapter)
        return value
    payload, cached = _cached_read(_cache_key(connection_id, business_id, store_id, operation, params), ttl, observed_fetch)
    _scope(connection_id, business_id, store_id, actor_user_id, context)
    result = {"data": payload, "cached": cached, "untrusted_content": True}
    if operation in {"product", "inventory", "shipping"}:
        stored = payload | {"quote_request": params} if operation == "shipping" else payload
        result["snapshot_id"] = _snapshot(connection_id, business_id, store_id, operation, params.get("pid", "quote"), stored)
    return result


def get_snapshot(snapshot_id, connection_id, business_id, store_id, actor_user_id, *, context=None):
    _scope(connection_id, business_id, store_id, actor_user_id, context)
    ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM supplier_snapshots WHERE snapshot_id=? AND connection_id=? AND business_id=? AND store_id=?",
                           (snapshot_id, connection_id, business_id, store_id)).fetchone()
        if row is None:
            raise SupplierError("not_found", http_status=404)
        return {"snapshot_id": row["snapshot_id"], "kind": row["kind"], "data": json.loads(row["payload_json"]), "created_at": row["created_at"], "untrusted_content": True}
    finally:
        conn.close()


def create_import_draft(snapshot_id, connection_id, business_id, store_id, actor_user_id, merchant_fields, *, context=None):
    _scope(connection_id, business_id, store_id, actor_user_id, context, write=True)
    snapshot = get_snapshot(snapshot_id, connection_id, business_id, store_id, actor_user_id, context=context)
    if snapshot["kind"] != "product":
        raise SupplierError("product_snapshot_required", http_status=400)
    if not isinstance(merchant_fields, dict) or set(merchant_fields) - {"title", "description", "price_cents", "currency"}:
        raise SupplierError("invalid_merchant_fields", http_status=400)
    for field, limit in (("title", 160), ("description", 8000)):
        if field in merchant_fields and (not isinstance(merchant_fields[field], str) or len(merchant_fields[field]) > limit):
            raise SupplierError("invalid_merchant_fields", http_status=400)
    price = merchant_fields.get("price_cents")
    if price is not None and (type(price) is not int or not 0 <= price <= 1_000_000_000):
        raise SupplierError("invalid_price", http_status=400)
    currency = merchant_fields.get("currency")
    if currency is not None and (not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or currency != currency.upper()):
        raise SupplierError("invalid_currency", http_status=400)
    draft_id = uuid.uuid4().hex
    conn = db.connect()
    try:
        merchant = connections._authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
        connections._row(conn, connection_id, business_id, store_id, merchant)
        conn.execute("INSERT INTO supplier_import_drafts (draft_id,connection_id,business_id,store_id,snapshot_id,merchant_fields_json,created_at) VALUES (?,?,?,?,?,?,?)",
                     (draft_id, connection_id, business_id, store_id, snapshot_id, _json(merchant_fields), time.time()))
        conn.commit()
    finally:
        conn.close()
    return {"draft_id": draft_id, "status": "DRAFT", "snapshot_id": snapshot_id,
            "published": False, "requires_merchant_review": True}


def bind_product(*, connection_id, business_id, store_id, actor_user_id,
                 canonical_product_id, pid, vid, context=None, adapter=None):
    """Explicit bridge for an OWNED marketplace listing, never a display-label join.

    ``canonical_product_id`` names a ``marketplace_listings.id``. It used to name
    a ``business_os_mkt_products.product_id``, which is a ledger with zero rows in
    production; binding there meant the supplier gateway could never reach a real
    product. The full chain is now: authenticated actor → authorised store →
    owned ``marketplace_listings`` row → ``marketplace_product_sources`` →
    supplier connection → CJ (pid, vid).

    Ownership is checked twice, before and after the provider read, because the
    read is a network call and a product can be transferred while it is in
    flight. The second check is inside the same transaction as the write.
    """
    connection = _scope(connection_id, business_id, store_id, actor_user_id, context, write=True)
    ensure_schema()
    conn = db.connect()
    try:
        _owned_listing_id(conn, canonical_product_id, connection["merchant_id"])
    finally:
        conn.close()
    detail = read("product", business_id=business_id, store_id=store_id, actor_user_id=actor_user_id,
                  connection_id=connection_id, params={"pid": pid}, context=context, adapter=adapter)
    product = detail["data"]
    if product.get("pid") != pid or not any(v.get("vid") == vid and v.get("pid") == pid for v in product.get("variants", [])):
        raise SupplierError("variant_mismatch", http_status=400)
    conn = db.connect()
    try:
        merchant = connections._authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
        connections._row(conn, connection_id, business_id, store_id, merchant)
        listing_id, owner_user_id = _owned_listing_id(conn, canonical_product_id, merchant)
        # `marketplace_variants` takes a cursor, not a connection. On SQLite the
        # two are nearly interchangeable — `Connection.execute` returns a cursor —
        # but only until something reads a result, because `fetchone` lives on the
        # cursor alone. Handing over a connection therefore type-checks, runs, and
        # dies at the first read. One cursor, reused, so the ownership check and
        # the write it guards are demonstrably on the same transaction.
        cur = conn.cursor()
        try:
            marketplace_variants.link_source(
                cur, listing_id=listing_id, seller_user_id=owner_user_id,
                provider="cj", provider_product_id=pid, provider_variant_id=vid,
                supplier_connection_id=connection_id,
                business_id=business_id, store_id=store_id,
                source_snapshot_id=detail["snapshot_id"],
                sync_state=marketplace_supplier_schema.SYNC_SYNCED)
        except marketplace_variants.VariantRejected as exc:
            # Already bound to a different provider product or variant. Refused
            # rather than overwritten: a listing whose supplier changed silently
            # would keep selling a page describing the old product.
            conn.rollback()
            raise SupplierError("binding_conflict", http_status=409) from exc
        conn.commit()
        row = marketplace_variants.supplier_binding(
            conn.cursor(), listing_id=listing_id, supplier_connection_id=connection_id,
            business_id=business_id, store_id=store_id)
        if row is None or row["provider_product_id"] != pid or row["provider_variant_id"] != vid:
            raise SupplierError("binding_conflict", http_status=409)
    finally:
        conn.close()
    return {"canonical_product_id": canonical_product_id, "pid": pid, "vid": vid, "connection_id": connection_id}


def get_product_binding(connection_id, business_id, store_id, canonical_product_id):
    """Internal worker lookup; HTTP callers must use an authorized gateway method.

    Returns the canonical source row under the gateway's own field names
    (``pid``/``vid``/``snapshot_id``) so that fulfillment keeps speaking the
    provider's vocabulary while the storage is canonical. The listing id travels
    alongside, because an intent that cannot name the canonical listing it is
    fulfilling is the split this reconciliation removed.
    """
    ensure_schema()
    conn = db.connect()
    try:
        row = marketplace_variants.supplier_binding(
            conn.cursor(), listing_id=canonical_product_id, supplier_connection_id=connection_id,
            business_id=business_id, store_id=store_id)
        if row is None or not row.get("provider_variant_id"):
            raise SupplierError("product_binding_required", http_status=409)
        return dict(row) | {
            "canonical_product_id": str(row["listing_id"]),
            "listing_id": row["listing_id"],
            "pid": row["provider_product_id"],
            "vid": row["provider_variant_id"],
            "snapshot_id": row["source_snapshot_id"],
        }
    finally:
        conn.close()
