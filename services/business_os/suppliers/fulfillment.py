"""Sandbox supplier intents/outbox. Canonical orders and money remain untouched.

An intent is append-only. Its outbox is the mutable delivery cursor. Persisting
SENDING before the POST means a crash/expired lease can only enter UNKNOWN and
read back; it can never dispatch a second create. No absence proof is invented.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from services import db
from services.business_os.payments import webhook_inbox


class FulfillmentError(ValueError):
    def __init__(self, code, http_status=409):
        self.code, self.http_status = code, http_status
        super().__init__(code)


FUNDING_STATES = frozenset({"FUNDING_NOT_READY", "FUNDING_APPROVAL_REQUIRED",
                          "FUNDING_REAPPROVAL_REQUIRED", "FUNDED", "FUNDING_FAILED"})


def assert_sandbox(value):
    if (os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").upper() != "SANDBOX"
            or os.getenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "false").lower()
            not in {"false", "0", "off", ""}):
        raise FulfillmentError("production_fulfillment_locked")
    if type(value) is not int or value != 1:
        raise FulfillmentError("explicit_sandbox_required", 400)


def ensure_schema(conn=None):
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_intents (
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, business_id TEXT NOT NULL,
            store_id TEXT NOT NULL, merchant_id TEXT NOT NULL, order_id TEXT NOT NULL,
            external_account_id TEXT NOT NULL, external_shop_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, external_order_ref TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL, UNIQUE(connection_id, idempotency_key),
            UNIQUE(connection_id, order_id))""")
        # No split-allocation/replacement contract exists yet. The canonical
        # customer order can have one supplier intent across ALL connections.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_canonical_order ON business_os_supplier_intents(order_id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_outbox (
            intent_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'READY',
            provider_order_id TEXT, provider_status TEXT,
            funding_state TEXT NOT NULL DEFAULT 'FUNDING_NOT_READY',
            lease_token TEXT, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
            available_at DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, updated_at DOUBLE PRECISION NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_outbox_due "
                     "ON business_os_supplier_outbox(state, available_at, lease_until)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value, name, limit=200):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise FulfillmentError("invalid_" + name, 400)
    return value


def _canonical_order(conn, order_id):
    """One customer order from the canonical marketplace ledger, or None.

    Joins to ``marketplace_listings`` for ``listing_type`` because
    ``marketplace_orders`` does not carry one and the physical/digital
    distinction is a property of the product, not of the sale. Resolved through
    the same rule the rest of the app uses (``effective_listing_type``) rather
    than by reading the raw column, so a listing that predates the column and
    only has ``product_type`` set is classified the same way here as it is on the
    listing page.

    Returns None for anything unparseable, so an unusable order id refuses
    identically to an absent one.
    """
    from services.marketplace_listing_types import effective_listing_type

    try:
        resolved = int(str(order_id).strip())
    except (TypeError, ValueError):
        return None
    row = conn.execute(
        "SELECT o.id, o.seller_user_id, o.listing_id, o.quantity, o.status, "
        "l.listing_type, l.product_type FROM marketplace_orders o "
        "JOIN marketplace_listings l ON l.id = o.listing_id WHERE o.id = ?",
        (resolved,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["status"] = str(item.get("status") or "").strip().lower()
    item["listing_type"] = effective_listing_type(item.get("listing_type"),
                                                  item.get("product_type"))
    item["quantity"] = int(item.get("quantity") or 0)
    return item


def create_intent(*, connection_id, business_id, store_id, actor_user_id, order_id,
                  items, shipping_destination, shipping_quote,
                  expected_supplier_cost_cents, isSandbox=None, idempotency_key,
                  context=None):
    """Authorize a merchant, reference canonical order lines, atomically enqueue.

    One sandbox intent per canonical order/connection. Splits/replacements need a
    later explicit revision contract; changing a replay's content is rejected.
    Snapshots are backend-only, never use this return value as a public DTO.
    """
    from . import connections, gateway
    assert_sandbox(isSandbox)
    metadata = connections.get_connection(connection_id, business_id, store_id,
                                          actor_user_id, context=context, write=True)
    bundle = connections.worker_connection(connection_id, business_id, store_id)
    meta = bundle["connection"]
    if metadata.get("id") != meta.get("id") or metadata.get("status") != "CONNECTED":
        raise FulfillmentError("connection_not_ready")
    # Connecting no longer requires choosing a CJ shop, because importing
    # products does not need one. Fulfilment does: `_validate_observed` proves a
    # placed order came back on the shop we bound, and with nothing bound there
    # is no such proof to make. Refusing here is not a policy preference -- it
    # is declining to place an order whose provenance we could not check.
    if not meta.get("external_shop_id"):
        raise FulfillmentError("shop_binding_required", 409)
    _text(idempotency_key, "idempotency_key", 128)
    if type(expected_supplier_cost_cents) is not int or expected_supplier_cost_cents < 0:
        raise FulfillmentError("invalid_expected_supplier_cost", 400)
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise FulfillmentError("invalid_items", 400)
    clean_items = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("quantity")) is not int or not 1 <= item["quantity"] <= 10000:
            raise FulfillmentError("invalid_quantity", 400)
        clean_items.append({key: _text(item.get(key), key) for key in
                            ("canonical_product_id", "pid", "vid", "sku")} |
                           {"quantity": item["quantity"]})
    clean_items.sort(key=lambda x: (x["canonical_product_id"], x["vid"]))
    if len({item["canonical_product_id"] for item in clean_items}) != len(clean_items):
        raise FulfillmentError("duplicate_order_line", 400)
    supplier_items_cost = Decimal(0)
    for item in clean_items:
        binding = gateway.get_product_binding(connection_id, business_id, store_id, item["canonical_product_id"])
        if binding["pid"] != item["pid"] or binding["vid"] != item["vid"]:
            raise FulfillmentError("product_binding_mismatch", 400)
        detail = gateway.get_snapshot(binding["snapshot_id"], connection_id, business_id, store_id, actor_user_id, context=context)
        variants = detail["data"].get("variants", [])
        variant = next((v for v in variants if v.get("pid") == item["pid"] and v.get("vid") == item["vid"] and v.get("sku") == item["sku"]), None)
        if not variant or variant.get("currency") != "USD":
            raise FulfillmentError("product_binding_mismatch", 400)
        try:
            price = Decimal(variant.get("price"))
            if not price.is_finite() or price < 0:
                raise InvalidOperation
            supplier_items_cost += price * item["quantity"]
        except (InvalidOperation, TypeError, ValueError):
            raise FulfillmentError("supplier_cost_unverified") from None
    required = ("shippingCountryCode", "shippingCountry", "shippingProvince",
                "shippingCity", "shippingCustomerName", "shippingAddress")
    if not isinstance(shipping_destination, dict):
        raise FulfillmentError("invalid_destination", 400)
    address = {k: _text(shipping_destination.get(k), k, 500) for k in required}
    for key in ("shippingZip", "shippingPhone", "shippingAddress2", "shippingCounty"):
        if shipping_destination.get(key):
            address[key] = _text(shipping_destination[key], key, 500)
    if len(address["shippingCountryCode"]) != 2:
        raise FulfillmentError("invalid_country", 400)
    if not isinstance(shipping_quote, dict):
        raise FulfillmentError("invalid_quote", 400)
    quote_snapshot = gateway.get_snapshot(_text(shipping_quote.get("snapshot_id"), "quote_snapshot_id"), connection_id,
                                          business_id, store_id, actor_user_id, context=context)
    if quote_snapshot["kind"] != "shipping":
        raise FulfillmentError("shipping_snapshot_required", 400)
    quoted_rows = quote_snapshot["data"].get("quote_request", {}).get("reqDTOS", [])
    if len(quoted_rows) != 1 or not isinstance(quoted_rows[0], dict):
        raise FulfillmentError("shipping_quote_items_unverified", 400)
    quoted_input = quoted_rows[0]
    quoted_items = quoted_input.get("freightTrialSkuList", [])
    if (not isinstance(quoted_items, list) or any(not isinstance(i, dict) for i in quoted_items)
            or sorted((i.get("vid", ""), i.get("sku", ""), i.get("skuQuantity", 0)) for i in quoted_items)
            != sorted((i["vid"], i["sku"], i["quantity"]) for i in clean_items)):
        raise FulfillmentError("shipping_quote_items_mismatch", 400)
    for source, target in (("destAreaCode", "shippingCountryCode"), ("province", "shippingProvince"),
                           ("city", "shippingCity"), ("recipientAddress", "shippingAddress"), ("zip", "shippingZip")):
        if quoted_input.get(source) != address.get(target):
            raise FulfillmentError("shipping_quote_destination_mismatch", 400)
    options = [q for q in quote_snapshot["data"].get("quotes", [])
               if q.get("option_id") == shipping_quote.get("option_id") and q.get("channel_id") == shipping_quote.get("channel_id")]
    if len(options) != 1 or not options[0].get("available"):
        raise FulfillmentError("shipping_quote_not_verified", 400)
    option = options[0]
    if option.get("destination") != address["shippingCountryCode"] or option.get("currency") != "USD":
        raise FulfillmentError("shipping_quote_route_mismatch", 400)
    try:
        quoted = datetime.fromisoformat(option["quoted_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - quoted).total_seconds()
        freight = Decimal(option["provider_total"])
        total = (supplier_items_cost + freight) * 100
        if age < 0 or age > 300 or not freight.is_finite() or freight < 0 or total != total.to_integral_value():
            raise ValueError
        if int(total) != expected_supplier_cost_cents:
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise FulfillmentError("supplier_cost_reapproval_required") from None
    quote = {"logisticName": _text(option.get("service"), "logistic_name", 50),
             "fromCountryCode": _text(option.get("origin"), "origin", 2),
             "currency": "USD", "quoted_at": option["quoted_at"], "provider_total": str(freight),
             "snapshot_id": quote_snapshot["snapshot_id"], "option_id": option.get("option_id"),
             "channel_id": option.get("channel_id")}
    snapshot = {"items": clean_items, "shipping_destination": address,
                "shipping_quote": quote, "expected_supplier_cost_cents": expected_supplier_cost_cents,
                "isSandbox": isSandbox, "payType": 3, "orderFlow": 1}
    encoded = _json(snapshot)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    ensure_schema()
    conn = db.connect()
    try:
        webhook_inbox._begin(conn)
        merchant = connections._authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
        current_connection = connections._row(conn, connection_id, business_id, store_id, merchant)
        _validate_binding({**meta, "connection_id": connection_id}, current_connection)
        # The customer order is a marketplace_orders row, keyed by listing_id —
        # the same identity space canonical_product_id now lives in. It used to be
        # read from business_os_mkt_orders, whose product ids could never match a
        # listing id, so this check could not have passed once the binding moved.
        #
        # This stays firmly the *customer* order. The CJ supplier order is the
        # intent written below; the two are deliberately separate rows with
        # separate lifecycles, and nothing here copies a status between them.
        canonical = _canonical_order(conn, order_id)
        if canonical is None or str(canonical["seller_user_id"]) != str(meta["merchant_id"]):
            raise FulfillmentError("order_not_found", 404)
        if canonical["status"] in {"cancelled", "refunded", "disputed"} or canonical["listing_type"] != "physical":
            raise FulfillmentError("order_not_eligible")
        canonical_items = {str(canonical["listing_id"]): int(canonical["quantity"])}
        if set(canonical_items) != {item["canonical_product_id"] for item in clean_items} or any(canonical_items.get(item["canonical_product_id"]) != item["quantity"] for item in clean_items):
            raise FulfillmentError("order_line_mismatch", 400)
        prior = conn.execute("SELECT * FROM business_os_supplier_intents WHERE connection_id=? "
                             "AND (idempotency_key=? OR order_id=?)",
                             (connection_id, idempotency_key, str(order_id))).fetchone()
        if prior:
            prior = dict(prior)
            if (prior["snapshot_hash"] != digest or prior["order_id"] != str(order_id)
                    or prior["idempotency_key"] != idempotency_key):
                raise FulfillmentError("immutable_intent_conflict")
            webhook_inbox._commit(conn)
            return {"intent_id": prior["id"], "duplicate": True}
        identity = "cjf_" + uuid.uuid4().hex
        external_ref = "pss_" + uuid.uuid4().hex
        now = time.time()
        # ON CONFLICT avoids poisoning PostgreSQL transactions during races.
        cursor = conn.execute("INSERT INTO business_os_supplier_intents "
            "(id,connection_id,business_id,store_id,merchant_id,order_id,external_account_id,"
            "external_shop_id,idempotency_key,external_order_ref,snapshot_json,snapshot_hash,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (identity, connection_id, business_id, store_id, meta["merchant_id"], str(order_id),
             meta["external_account_id"], meta["external_shop_id"], idempotency_key,
             external_ref, encoded, digest, now))
        if cursor.rowcount != 1:
            raise FulfillmentError("concurrent_intent_conflict")
        conn.execute("INSERT INTO business_os_supplier_outbox(intent_id,available_at,updated_at) VALUES(?,?,?)",
                     (identity, now, now))
        webhook_inbox._commit(conn)
        return {"intent_id": identity, "duplicate": False}
    except Exception:
        webhook_inbox._rollback(conn)
        raise
    finally:
        conn.close()


def claim(*, now=None, lease_seconds=120):
    """Claim one due write/read-back using a DB conditional update + fencing token."""
    now = time.time() if now is None else now
    token = uuid.uuid4().hex
    conn = db.connect()
    try:
        webhook_inbox._begin(conn)
        conn.execute("UPDATE business_os_supplier_outbox SET state='UNKNOWN', last_error='dispatch_lease_expired' "
                     "WHERE state='SENDING' AND lease_until<=?", (now,))
        row = conn.execute("SELECT intent_id FROM business_os_supplier_outbox "
             "WHERE state IN ('READY','UNKNOWN','RECONCILE') AND available_at<=? AND lease_until<=? "
             "ORDER BY available_at,intent_id LIMIT 1", (now, now)).fetchone()
        if not row:
            webhook_inbox._commit(conn)
            return None
        cursor = conn.execute("UPDATE business_os_supplier_outbox SET lease_token=?, lease_until=?, "
             "attempts=attempts+1,updated_at=? WHERE intent_id=? AND lease_until<=? "
             "AND state IN ('READY','UNKNOWN','RECONCILE')", (token, now + lease_seconds, now, row[0], now))
        if cursor.rowcount != 1:
            webhook_inbox._commit(conn)
            return None
        result = dict(conn.execute("SELECT i.*,o.state,o.lease_token,o.attempts,o.provider_order_id "
             "FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON i.id=o.intent_id "
             "WHERE i.id=?", (row[0],)).fetchone())
        webhook_inbox._commit(conn)
        return result
    except Exception:
        webhook_inbox._rollback(conn)
        raise
    finally:
        conn.close()


def settle(intent, state, *, now=None, delay=0, error=None, provider_order_id=None, provider_status=None):
    now = time.time() if now is None else now
    conn = db.connect()
    try:
        cursor = conn.execute("UPDATE business_os_supplier_outbox SET state=?,lease_token=NULL,lease_until=0,"
            "available_at=?,last_error=?,provider_order_id=COALESCE(?,provider_order_id),"
            "provider_status=COALESCE(?,provider_status),updated_at=? WHERE intent_id=? AND lease_token=?",
            (state, now + delay, error, provider_order_id, provider_status, now, intent["id"], intent["lease_token"]))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def _sending(intent, now):
    conn = db.connect()
    try:
        cursor = conn.execute("UPDATE business_os_supplier_outbox SET state='SENDING',updated_at=?,lease_until=? "
            "WHERE intent_id=? AND lease_token=? AND lease_until>? AND state='READY'",
            (now, now + 90, intent["id"], intent["lease_token"], now))
        conn.commit()
        if cursor.rowcount != 1:
            raise FulfillmentError("dispatch_claim_lost")
    finally:
        conn.close()


def _validate_binding(intent, meta):
    if meta.get("id") != intent["connection_id"]:
        raise FulfillmentError("connection_binding_changed")
    for field in ("merchant_id", "business_id", "store_id", "external_account_id", "external_shop_id"):
        if str(meta.get(field)) != str(intent[field]):
            raise FulfillmentError("connection_binding_changed")
    if meta.get("status") != "CONNECTED":
        raise FulfillmentError("connection_not_ready")


def _validate_observed(intent, snapshot, observed):
    if not observed or not observed.get("order_id"):
        raise FulfillmentError("absence_not_proven")
    if observed.get("external_order_ref") != intent["external_order_ref"]:
        raise FulfillmentError("provider_order_binding_mismatch")
    if str(observed.get("shop_id")) != str(intent["external_shop_id"]):
        raise FulfillmentError("provider_shop_mismatch")
    if type(observed.get("is_sandbox")) is not int or observed["is_sandbox"] != 1:
        raise FulfillmentError("provider_sandbox_unverified")
    expected_lines = sorted((i["vid"], i["quantity"]) for i in snapshot["items"])
    actual_lines = sorted((p.get("vid"), p.get("quantity")) for p in observed.get("products", []))
    if actual_lines != expected_lines:
        raise FulfillmentError("provider_order_lines_mismatch")


def observe_linked(meta, provider_order_id, observed):
    """Read-only supplier reconciliation; never changes canonical payment/order state."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT i.* FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON o.intent_id=i.id WHERE i.connection_id=? AND i.business_id=? AND i.store_id=? AND o.provider_order_id=? AND o.state='LINKED'",
                           (meta["id"], meta["business_id"], meta["store_id"], provider_order_id)).fetchone()
        if row is None:
            raise FulfillmentError("supplier_order_not_bound")
        intent = dict(row)
        _validate_binding(intent, meta)
        _validate_observed(intent, json.loads(intent["snapshot_json"]), observed)
        if observed["order_id"] != provider_order_id:
            raise FulfillmentError("provider_order_binding_mismatch")
        conn.execute("UPDATE business_os_supplier_outbox SET provider_status=?,updated_at=? WHERE intent_id=? AND state='LINKED' AND provider_order_id=?",
                     (observed.get("provider_status") or "UNKNOWN", time.time(), intent["id"], provider_order_id))
        conn.commit()
    finally:
        conn.close()


def dispatch(intent, adapter, meta, *, now=None):
    """One worker attempt; no exception text/provider body is persisted or logged."""
    now = time.time() if now is None else now
    sent = False
    provider_failure = None
    try:
        _validate_binding(intent, meta)
        snapshot = json.loads(intent["snapshot_json"])
        if hashlib.sha256(_json(snapshot).encode()).hexdigest() != intent["snapshot_hash"]:
            raise FulfillmentError("intent_integrity_failed")
        assert_sandbox(snapshot.get("isSandbox"))
        if intent["state"] in {"UNKNOWN", "RECONCILE"}:
            observed = adapter.get_fulfillment(external_order_ref=intent["external_order_ref"])
            if not observed or not observed.get("order_id"):
                # A single not-found/read timeout is never evidence permitting POST.
                settle(intent, "UNKNOWN", now=now, delay=300, error="absence_not_proven")
                return "UNKNOWN"
            _validate_observed(intent, snapshot, observed)
            settle(intent, "LINKED", now=now, provider_order_id=str(observed["order_id"]),
                   provider_status=str(observed.get("provider_status") or "UNKNOWN")[:100])
            return "LINKED"
        # Check canonical cancellation/ownership again after queueing.
        from . import gateway
        conn = db.connect()
        try:
            current_order = _canonical_order(conn, intent["order_id"])
        finally:
            conn.close()
        if not current_order or str(current_order["seller_user_id"]) != str(intent["merchant_id"]) or current_order["status"] in {"cancelled", "refunded", "disputed"}:
            raise FulfillmentError("order_not_eligible")
        shops = adapter.get_shops()
        selected = [s for s in shops if s.get("shop_id") == intent["external_shop_id"] and s.get("status") == 1]
        if len(selected) != 1 or not selected[0].get("name") or str(selected[0].get("platform")).lower() != "api":
            raise FulfillmentError("api_shop_binding_required")
        if len([s for s in shops if s.get("name") == selected[0]["name"]]) != 1:
            raise FulfillmentError("ambiguous_shop_name")
        # Backend provider validation, never labels/SKUs inferred from display text.
        item_total = Decimal(0)
        for item in snapshot["items"]:
            binding = gateway.get_product_binding(intent["connection_id"], intent["business_id"], intent["store_id"], item["canonical_product_id"])
            if binding["pid"] != item["pid"] or binding["vid"] != item["vid"]:
                raise FulfillmentError("product_binding_mismatch")
            product = adapter.get_product(item["pid"])
            variants = adapter.get_variants(item["pid"])
            variants = variants.get("variants", []) if isinstance(variants, dict) else variants
            if product.get("pid") != item["pid"] or not any(
                v.get("pid") == item["pid"] and v.get("vid") == item["vid"] and v.get("sku") == item["sku"]
                for v in variants):
                raise FulfillmentError("provider_variant_mismatch")
            variant = next(v for v in variants if v.get("vid") == item["vid"])
            price = Decimal(variant.get("price"))
            if variant.get("currency") != "USD" or not price.is_finite() or price < 0:
                raise FulfillmentError("supplier_cost_unverified")
            item_total += price * item["quantity"]
            inventory = adapter.get_inventory(item["pid"], vid=item["vid"])
            warehouses = [w for v in inventory.get("variants", []) if v.get("vid") == item["vid"] and v.get("pid") == item["pid"]
                          for w in v.get("warehouses", []) if w.get("country") == snapshot["shipping_quote"]["fromCountryCode"]]
            # Never sum unrelated warehouses or infer in-stock from unknown.
            if not any(w.get("state") == "IN_STOCK" and w.get("verified") == 1 and type(w.get("total")) is int and w["total"] >= item["quantity"] for w in warehouses):
                raise FulfillmentError("inventory_quantity_not_verified")
        quoted_at = datetime.fromisoformat(snapshot["shipping_quote"]["quoted_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - quoted_at).total_seconds()
        if not 0 <= age <= 300 or (item_total + Decimal(snapshot["shipping_quote"]["provider_total"])) * 100 != snapshot["expected_supplier_cost_cents"]:
            raise FulfillmentError("supplier_cost_reapproval_required")
        payload = dict(snapshot["shipping_destination"])
        payload.update({"orderNumber": intent["external_order_ref"], "isSandbox": snapshot["isSandbox"],
                        "payType": snapshot["payType"], "orderFlow": snapshot["orderFlow"],
                        "logisticName": snapshot["shipping_quote"]["logisticName"],
                        "fromCountryCode": snapshot["shipping_quote"]["fromCountryCode"],
                        "storeName": selected[0]["name"],
                        "products": [{"vid": item["vid"], "quantity": item["quantity"]}
                                     for item in snapshot["items"]]})
        assert_sandbox(payload.get("isSandbox"))
        _sending(intent, time.time())
        sent = True
        adapter.create_sandbox_fulfillment(payload)
        # Always prove identity/sandbox through independent read-back, not POST payload echo.
        settle(intent, "UNKNOWN", now=now, delay=2, error="awaiting_create_readback")
        return "UNKNOWN"
    except Exception as exc:
        from .errors import SupplierError
        if isinstance(exc, SupplierError):
            provider_failure = exc
        retry_after = getattr(exc, "retry_after", None)
        delay = max(30, float(retry_after)) if retry_after else max(30, min(3600, 2 ** min(intent["attempts"], 10)))
        if sent or intent["state"] in {"UNKNOWN", "RECONCILE"} or getattr(exc, "ambiguous_write", False):
            state = "UNKNOWN"
        elif getattr(exc, "http_status", None) == 429 or str(getattr(exc, "code", "")).lower() in {
                "rate_limited", "quota_exhausted", "provider_unavailable", "provider_timeout"}:
            state = "READY"
        else:
            state = "BLOCKED"
        # Codes are chosen locally; no arbitrary provider/transport exceptions copied.
        safe_error = "readback_required" if state == "UNKNOWN" else "preflight_deferred" if state == "READY" else "preflight_blocked"
        settle(intent, state, now=now, delay=delay, error=safe_error)
        return state
    finally:
        from . import connections
        try:
            connections.record_activity(intent["connection_id"], intent["business_id"], intent["store_id"],
                                        adapter=adapter, error=provider_failure)
        except Exception:
            pass  # Telemetry cannot change write disposition or reveal secrets.


def fund_fulfillment(*args, **kwargs):
    raise FulfillmentError("supplier_funding_locked")


def get_intent(intent_id, connection_id, business_id, store_id, actor_user_id, *, context=None):
    from . import connections
    connections.get_connection(connection_id, business_id, store_id, actor_user_id, context=context)
    conn = db.connect()
    try:
        row = conn.execute("SELECT i.id,i.order_id,i.created_at,o.state,o.provider_order_id,o.provider_status,o.funding_state,o.updated_at "
            "FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON o.intent_id=i.id "
            "WHERE i.id=? AND i.connection_id=? AND i.business_id=? AND i.store_id=?",
            (intent_id, connection_id, business_id, store_id)).fetchone()
        if row is None:
            raise FulfillmentError("not_found", 404)
        return dict(row) | {"isSandbox": 1, "production_fulfillment_enabled": False}
    finally:
        conn.close()
