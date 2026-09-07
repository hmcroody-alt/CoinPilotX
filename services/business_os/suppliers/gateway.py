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
from services.business_os.suppliers import connections, policy
from services.business_os.suppliers.errors import SupplierError


def ensure_schema(conn=None):
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
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_product_links (
            connection_id TEXT NOT NULL, business_id TEXT NOT NULL, store_id TEXT NOT NULL,
            canonical_product_id TEXT NOT NULL, pid TEXT NOT NULL, vid TEXT NOT NULL,
            snapshot_id TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (connection_id, business_id, store_id, canonical_product_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_import_drafts (
            draft_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
            business_id TEXT NOT NULL, store_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            merchant_fields_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
            created_at DOUBLE PRECISION NOT NULL)""")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


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
    """Explicit bridge for an OWNED marketplace product, never a display-label join."""
    connection = _scope(connection_id, business_id, store_id, actor_user_id, context, write=True)
    ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT seller_user_id FROM business_os_mkt_products WHERE product_id=?", (canonical_product_id,)).fetchone()
        if row is None or str(row["seller_user_id"]) != str(connection["merchant_id"]):
            raise SupplierError("not_found", http_status=404)
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
        owned = conn.execute("SELECT seller_user_id FROM business_os_mkt_products WHERE product_id=?", (canonical_product_id,)).fetchone()
        if owned is None or str(owned["seller_user_id"]) != str(merchant):
            raise SupplierError("not_found", http_status=404)
        conn.execute("INSERT INTO supplier_product_links (connection_id,business_id,store_id,canonical_product_id,pid,vid,snapshot_id,created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(connection_id,business_id,store_id,canonical_product_id) DO NOTHING",
                     (connection_id, business_id, store_id, canonical_product_id, pid, vid, detail["snapshot_id"], time.time()))
        conn.commit()
        row = conn.execute("SELECT pid,vid FROM supplier_product_links WHERE connection_id=? AND business_id=? AND store_id=? AND canonical_product_id=?",
                           (connection_id, business_id, store_id, canonical_product_id)).fetchone()
        if row["pid"] != pid or row["vid"] != vid:
            raise SupplierError("binding_conflict", http_status=409)
    finally:
        conn.close()
    return {"canonical_product_id": canonical_product_id, "pid": pid, "vid": vid, "connection_id": connection_id}


def get_product_binding(connection_id, business_id, store_id, canonical_product_id):
    """Internal worker lookup; HTTP callers must use an authorized gateway method."""
    ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM supplier_product_links WHERE connection_id=? AND business_id=? AND store_id=? AND canonical_product_id=?",
                           (connection_id, business_id, store_id, canonical_product_id)).fetchone()
        if row is None:
            raise SupplierError("product_binding_required", http_status=409)
        return dict(row)
    finally:
        conn.close()
