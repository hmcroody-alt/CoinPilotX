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


def dispatch_shop(shops, shop_id):
    """The one CJ shop a binding may place an order through, or a refusal.

    Three conditions, and until now they lived only inside :func:`dispatch`. A
    CJ "shop" can be a Shopify or Woo storefront the merchant authorized; only
    the one CJ's own *API* app creates can receive an order placed over the API.
    And CJ addresses an order by shop name, so two shops sharing a name make the
    destination ambiguous no matter which id was bound.

    Measuring them only at dispatch meant a merchant could choose a shop, see
    the choice accepted, and learn at the first real order that it was never a
    shop an order could go to. :func:`connections.bind_shop` calls this function
    at bind time for exactly that reason -- same conditions, now also measured
    where the choice is made and not only where it is spent.

    Returns the selected shop so the caller can name it; raises otherwise.
    """
    selected = [s for s in shops if s.get("shop_id") == shop_id and s.get("status") == 1]
    if len(selected) != 1 or not selected[0].get("name") or str(selected[0].get("platform")).lower() != "api":
        raise FulfillmentError("api_shop_binding_required")
    if len([s for s in shops if s.get("name") == selected[0]["name"]]) != 1:
        raise FulfillmentError("ambiguous_shop_name")
    return selected[0]


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
        selected_shop = dispatch_shop(adapter.get_shops(), intent["external_shop_id"])
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
                        "storeName": selected_shop["name"],
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


def real_order_activity(conn, connection_id):
    """Count orders that reach CJ as *real*, and when the last one did.

    CJ's inactivity rule counts real orders only, and this deployment sends
    none: `assert_sandbox` rejects any payload without an integral
    ``isSandbox=1``, on every path, so the count returned here is structurally
    zero rather than incidentally zero. That is the correct answer and the
    whole reason the caller needs it -- an integration that never places a real
    order is exactly the one CJ eventually disables.

    Counted from the snapshot rather than from a column, because the snapshot
    is what was actually sent. A column would be a second place for the sandbox
    flag to live, and the two could disagree; the payload cannot disagree with
    itself. ``LINKED`` is the only state where CJ acknowledged an order, so
    intents that never left the outbox are correctly not counted.
    """
    rows = conn.execute(
        "SELECT i.snapshot_json, i.created_at FROM business_os_supplier_intents i "
        "JOIN business_os_supplier_outbox o ON o.intent_id=i.id "
        "WHERE i.connection_id=? AND o.state='LINKED' ORDER BY i.created_at DESC",
        (connection_id,)).fetchall()
    count, last_at = 0, None
    for row in rows:
        try:
            sandbox = json.loads(row["snapshot_json"]).get("isSandbox")
        except (ValueError, TypeError):
            # Unreadable snapshot: we cannot show it was a sandbox order, and
            # guessing "real" would silence the warning this function exists to
            # raise. Not counted, and it does not move the clock.
            continue
        if type(sandbox) is int and sandbox == 1:
            continue
        count += 1
        if last_at is None:
            last_at = row["created_at"]
    return count, last_at


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


#: The state of one obligation when no intent exists for it yet. Deliberately
#: not a member of the outbox's vocabulary: the outbox describes the delivery of
#: an intent, and there is no intent here. Every other value this field can take
#: is an outbox state passed through verbatim rather than re-spelled, because a
#: second copy of that vocabulary would be one more thing to keep in step with
#: `dispatch`, which is the only code that decides those names.
AWAITING_SUPPLIER_ORDER = "AWAITING_SUPPLIER_ORDER"


def list_obligations(connection_id, business_id, store_id, actor_user_id, *,
                     limit=100, context=None):
    """Which paid sales still owe a purchase from the supplier.

    Why this exists
    ---------------
    Before this function, the module could create one intent, read one intent
    by id, and claim one for the worker. Nothing could answer the merchant's
    actual question -- "which of my orders needs a supplier order placed?" --
    so a buyer could pay for a published, bound dropship listing and the only
    record of the obligation was a `marketplace_orders` row indistinguishable
    from a hand-stocked sale. The supplier-orders screen rendered the absence
    of a list it had no endpoint for, and its machine-readable gap note named a
    table (`business_os_supplier_fulfillment_intents`) that does not exist, so
    anyone implementing it grepped for a name nothing in the repo has.

    Derived, not stored
    -------------------
    An obligation is not a row. It is the join of a paid order to the supplier
    source of the listing it was placed on, and it is computed on read. A
    `needs_supplier_order` column on `marketplace_orders` would be a second copy
    of a fact the source row already states, and the two would disagree the
    first time a merchant switched a listing from DROPSHIP to STOCKED. Nothing
    here writes anything.

    The order id join is a cast, on purpose
    ---------------------------------------
    `marketplace_orders.id` is INTEGER; `business_os_supplier_intents.order_id`
    is TEXT, because `create_intent` writes `str(order_id)`. On PostgreSQL
    `i.order_id = o.id` is a type error; on SQLite it is worse -- it silently
    matches nothing, so the list would come back with every obligation looking
    unplaced and no test would fail. `CAST(o.id AS TEXT)` is correct on both.

    Merchant-only payload
    ---------------------
    Every row carries `supplier_cost_cents`, which is the merchant's buying
    price and the number a buyer must never see. That is safe here and only
    here: the caller is authorized through `connections.get_connection`, which
    is a merchant-scope read. Nothing on a buyer-facing route may call this, and
    no field of it may be forwarded into a buyer payload.

    What is deliberately not enumerated
    -----------------------------------
    An order that was refunded or cancelled *after* a supplier order was placed
    is a real situation with real money in it, and it is not in this list: the
    merchant needs to cancel with the supplier, which is a different action from
    placing one. Answering it needs a cancellation path that does not exist yet,
    so it stays a declared gap rather than a row here that implies it is handled.
    """
    from services.marketplace_listing_types import effective_listing_type

    from . import connections

    # Same authorization as `get_intent`: the connection read is what proves the
    # actor may see this scope at all. Nothing below re-derives ownership.
    connections.get_connection(connection_id, business_id, store_id, actor_user_id,
                               context=context)
    try:
        capped = min(max(int(limit), 1), 200)
    except (TypeError, ValueError):
        raise FulfillmentError("invalid_limit", 400) from None

    ensure_schema()
    conn = db.connect()
    try:
        # `LOWER(o.status) = 'paid'` is the whole paid vocabulary of *this*
        # table, measured rather than assumed: `pulse_upsert_marketplace_order`
        # (bot.py) is the only writer of `marketplace_orders`, and it hardcodes
        # 'paid' in both the INSERT and its ON CONFLICT update. The column's
        # DDL default 'pending_payment' is the only other value it can hold.
        #
        # Do not widen this to `marketplace_listing_types.PAID_ORDER_STATUSES`
        # ({paid, checkout_completed, succeeded}). That set is the vocabulary of
        # `seller_transactions` / `creator_transactions`, which is upstream of
        # this table, not in it -- see `buyer_paid_marketplace_listing_ids`.
        # Widening would add two statuses this column never holds and imply the
        # two vocabularies are one.
        #
        # What *would* break this: a second writer of `marketplace_orders` that
        # spells paid differently. Then an obligation goes invisible, which is
        # exactly the defect this function exists to fix. `test_supplier_obligations.py`
        # pins the writer count for that reason.
        rows = conn.execute(
            "SELECT o.id AS order_id, o.listing_id, o.quantity, o.status AS order_status, "
            "o.amount_cents, o.currency, o.paid_at, o.created_at AS ordered_at, "
            "l.title, l.listing_type, l.product_type, "
            "s.provider AS provider, s.provider_product_id, s.provider_variant_id, "
            "s.external_sku, s.supplier_cost_cents, s.supplier_cost_currency, "
            "i.id AS intent_id, i.created_at AS intent_created_at, "
            # `supplier_order_status`, not `provider_status`, on the way out.
            # The column keeps its name; the wire field does not, because
            # "provider status" is already Stripe's subscription status on this
            # platform, and the mobile entitlement drift guard
            # (`noClientTierInference.test.ts`) lists `provider_status` among the
            # raw membership fields no unlisted file may hold. Two unrelated
            # meanings under one name is how a guard ends up either exempting a
            # file it should not or being weakened to let one through.
            "b.state AS outbox_state, b.provider_order_id, "
            "b.provider_status AS supplier_order_status, "
            "b.funding_state, b.last_error, b.updated_at AS intent_updated_at "
            "FROM marketplace_orders o "
            "JOIN marketplace_listings l ON l.id = o.listing_id "
            "JOIN marketplace_product_sources s ON s.listing_id = o.listing_id "
            "LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) "
            "LEFT JOIN business_os_supplier_outbox b ON b.intent_id = i.id "
            "WHERE s.supplier_connection_id = ? AND s.business_id = ? AND s.store_id = ? "
            "AND s.fulfillment_mode = ? AND LOWER(o.status) = 'paid' "
            "ORDER BY o.paid_at DESC, o.id DESC LIMIT ?",
            (connection_id, business_id, store_id, "DROPSHIP", capped)).fetchall()
    finally:
        conn.close()

    obligations = []
    for row in rows:
        item = dict(row)
        # Resolved through the app's own rule rather than by reading the raw
        # column, exactly as `_canonical_order` does. A listing that predates
        # `listing_type` and only has `product_type` set must classify the same
        # way here as it does on the listing page, and as it does in the guard
        # inside `create_intent` that will refuse a non-physical order.
        listing_type = effective_listing_type(item.pop("listing_type"),
                                              item.pop("product_type"))
        if listing_type != "physical":
            continue
        # Popped unconditionally, so the raw column cannot also travel in the
        # payload beside the field derived from it -- two spellings of one fact,
        # and a caller free to read the one that is None.
        outbox_state = item.pop("outbox_state")
        intent_id = item.get("intent_id")
        obligations.append({
            **item,
            "listing_type": listing_type,
            # One field, derived once, in one place. A `placed` boolean *beside*
            # a state would be the same fact twice; `supplier_order_placed`
            # below is instead a restatement of `intent_id is not None`, which
            # `state` cannot express without the caller knowing which outbox
            # names mean "already sent".
            #
            # An intent with no outbox row should be impossible -- `create_intent`
            # writes both in one transaction, and rolls both back together --
            # but the LEFT JOIN can express it, so it needs an answer.
            #
            # That answer is `UNKNOWN`, not `AWAITING_SUPPLIER_ORDER`. An intent
            # exists, so a purchase may already have been made; saying "no
            # supplier order yet" beside `supplier_order_placed: true` is both a
            # contradiction and the one error that costs money, because it
            # invites a merchant to order the same goods twice. `UNKNOWN` is
            # exactly the state whose copy tells them not to.
            "state": outbox_state or ("UNKNOWN" if intent_id else AWAITING_SUPPLIER_ORDER),
            "supplier_order_placed": intent_id is not None,
        })
    return {"obligations": obligations, "isSandbox": 1,
            "production_fulfillment_enabled": False}
