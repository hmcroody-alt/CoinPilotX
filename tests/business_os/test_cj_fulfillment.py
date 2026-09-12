"""SQLite supplier outbox integration with a synthetic, no-network CJ boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import copy
import json
import time

import pytest

from services import db
from services.business_os.marketplace import schema as market_schema
from services.business_os.suppliers import connections, fulfillment as f, gateway, worker
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_connections import database, connect, FakeAdapter
from tests.marketplace_production_listings import (
    PHYSICAL_PUBLISHED_ID, seed_orders_table, seed_production_listings)

PID, VID = "10001", "20001"

#: The merchant behind ``biz-a``/``store-a`` — this is
#: ``business_os_business.owner_user_id``, and it is the same integer as
#: ``marketplace_listings.seller_user_id``. That identity, not a translation
#: table, is what lets the supplier gateway reach the canonical ledger.
MERCHANT = 100
FOREIGN_MERCHANT = 200

#: A physical, published listing owned by ``MERCHANT``. Passed to the gateway as
#: a string because it arrives over HTTP as one; the gateway coerces.
OWNED_LISTING = str(PHYSICAL_PUBLISHED_ID)

#: What checkout froze on the buyer's transaction. ``create_intent`` takes no
#: destination argument — it reads this record — so a suite that wants to vary
#: the address varies this, which is the whole point of the change: the parcel
#: goes where the buyer paid for it to go and a request body cannot say otherwise.
#: The values line up with the ``reqDTOS`` row the fixture quotes with, because a
#: quote for one address cannot authorise an order to another.
FROZEN_DETAILS = {
    "contact_name": "Sandbox Fixture",
    "address_line1": "Synthetic fixture address",
    "address_city": "Test City",
    "address_region": "CA",
    "address_country": "US",
}

_TRANSACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS seller_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_user_id INTEGER,
    seller_user_id INTEGER,
    item_type TEXT,
    item_id INTEGER,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    status TEXT DEFAULT 'created',
    metadata_json TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

#: Owned by ``FOREIGN_MERCHANT``. Binding or fulfilling against it must refuse.
FOREIGN_LISTING = "99"

#: The customer order. Integer-shaped because ``marketplace_orders.id`` is an
#: INTEGER PRIMARY KEY, unlike the opaque ``'order-a'`` this suite used when the
#: canonical order lived in ``business_os_mkt_orders``.
ORDER_ID = "4001"


class CommerceAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.created = []
        self.observed = None
        self.create_error = None
        self.stock = 8
        self.verified = 1
        self.read_error = None
        # Two facts `quote_for_order` needs and `create_intent` does not: what
        # the parcel weighs, and how CJ routes it. Attributes rather than
        # literals so the tests for the two "we do not know" refusals can take
        # them away, which is the only honest way to reach those branches.
        self.weight_grams = "100"
        self.logistics_properties = ["COMMON"]

    def get_product(self, pid):
        return {"pid": pid, "variants": self.get_variants(pid), "supplier_price": "2.00", "currency": "USD",
                "logistics_properties": list(self.logistics_properties)}

    def get_variants(self, pid):
        return [{"pid": pid, "vid": VID, "sku": "FIXTURE-SKU", "price": "2.00", "currency": "USD",
                 "weight_grams": self.weight_grams}]

    def get_inventory(self, pid, vid=None):
        if self.read_error:
            raise self.read_error
        state = "UNKNOWN" if self.stock is None or self.verified != 1 else "IN_STOCK" if self.stock > 0 else "OUT_OF_STOCK"
        return {"pid": pid, "variants": [{"vid": VID, "pid": pid, "warehouses": [
            {"country": "CN", "state": state, "verified": self.verified, "total": self.stock}]}], "state": "UNKNOWN"}

    def estimate_shipping(self, payload):
        return {"quotes": [{"service": "Fixture Channel", "channel_id": "channel-1", "option_id": "option-1",
            "origin": "CN", "destination": "US", "provider_total": "3.00", "currency": "USD", "available": True,
            "quoted_at": datetime.now(timezone.utc).isoformat(), "guaranteed": False}]}

    def create_sandbox_fulfillment(self, payload):
        self.created.append(copy.deepcopy(payload))
        self.observed = {"order_id": "90001", "external_order_ref": payload["orderNumber"],
            "shop_id": "cj-shop-a", "is_sandbox": 1, "provider_status": "CREATED",
            "products": [{"vid": VID, "quantity": 1}]}
        if self.create_error:
            raise self.create_error
        return self.observed

    def get_fulfillment(self, order_id=None, external_order_ref=None):
        return self.observed

    def connection_health(self, **kwargs):
        return {"status": "CONNECTED"}

    def get_subscriptions(self, *args, **kwargs):
        return {"products": [], "page": 1, "size": 20}


@pytest.fixture
def ready(database):
    market_schema.ensure_schema()
    gateway.ensure_schema()
    f.ensure_schema()
    worker.ensure_schema()
    adapter = CommerceAdapter()
    connection = connect(adapter)
    conn = db.connect()
    cur = conn.cursor()
    # The real ledger, in its real shape: ids 8..13, one owner, price as prose.
    # The foreign row exists so the cross-merchant refusals below have something
    # real to refuse rather than only an id that was never issued.
    seed_production_listings(cur, owner=MERCHANT, extra_owner=FOREIGN_MERCHANT)
    seed_orders_table(cur)
    cur.execute(_TRANSACTIONS_DDL)
    cur.execute("INSERT INTO seller_transactions (id,buyer_user_id,seller_user_id,item_type,"
                "item_id,amount_cents,status,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
                (7001, 999, MERCHANT, "marketplace_listing", int(OWNED_LISTING), 900, "paid",
                 json.dumps({"fulfillment": {"kind": "shipping", "details": dict(FROZEN_DETAILS)}})))
    cur.execute("INSERT INTO marketplace_orders (id,seller_transaction_id,buyer_user_id,seller_user_id,listing_id,"
                "quantity,unit_price_cents,amount_cents,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(ORDER_ID), 7001, 999, MERCHANT, int(OWNED_LISTING), 1, 900, 900, "paid", "now"))
    conn.commit()
    conn.close()
    gateway.bind_product(connection_id=connection["id"], business_id="biz-a", store_id="store-a", actor_user_id="100",
                         canonical_product_id=OWNED_LISTING, pid=PID, vid=VID, adapter=adapter)
    quote = gateway.read("shipping", connection_id=connection["id"], business_id="biz-a", store_id="store-a",
                         actor_user_id="100", params={"reqDTOS": [{"srcAreaCode": "CN", "destAreaCode": "US", "weight": 100,
                           "productProp": ["COMMON"], "skuList": ["FIXTURE-SKU"], "province": "CA", "city": "Test City",
                           "recipientAddress": "Synthetic fixture address",
                           "freightTrialSkuList": [{"vid": VID, "sku": "FIXTURE-SKU", "skuQuantity": 1}]}]}, adapter=adapter)
    request = dict(connection_id=connection["id"], business_id="biz-a", store_id="store-a", actor_user_id="100", order_id=ORDER_ID,
                   items=[{"canonical_product_id": OWNED_LISTING, "pid": PID, "vid": VID, "sku": "FIXTURE-SKU", "quantity": 1}],
                   shipping_quote={"snapshot_id": quote["snapshot_id"], "option_id": "option-1", "channel_id": "channel-1"},
                   expected_supplier_cost_cents=500, isSandbox=1, idempotency_key="fixture-intent-1")
    return adapter, connection, request


def outbox(intent_id):
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM business_os_supplier_outbox WHERE intent_id=?", (intent_id,)).fetchone())
    finally:
        conn.close()


def refreeze(**changes):
    """Rewrite the buyer's frozen shipping details.

    The only way left to move the destination of an intent, which is the point:
    no argument reaches it, so a test that wants a different address has to move
    the record the buyer paid against.
    """
    conn = db.connect()
    try:
        conn.execute("UPDATE seller_transactions SET metadata_json=? WHERE id=?",
                     (json.dumps({"fulfillment": {"kind": "shipping",
                                                  "details": dict(FROZEN_DETAILS) | changes}}), 7001))
        conn.commit()
    finally:
        conn.close()


def canonical_order():
    """The customer order as the marketplace stores it.

    Read as a whole row rather than a status column, so a supplier-side write
    that touched quantity, amount or listing_id would be caught too. The customer
    order and the CJ supplier order are separate rows on purpose; this is how the
    tests below prove nothing leaks from the second into the first.
    """
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM marketplace_orders WHERE id=?",
                                 (int(ORDER_ID),)).fetchone())
    finally:
        conn.close()


def attempt(ready):
    adapter, connection, request = ready
    intent = f.create_intent(**request)
    claimed = f.claim(now=time.time() + .1)
    state = f.dispatch(claimed, adapter, connections.worker_connection(connection["id"], "biz-a", "store-a")["connection"])
    return intent, claimed, state


@pytest.mark.parametrize("flag", [None, 0, False, True, "1", 2])
def test_caller_rejects_missing_or_noninteger_sandbox(ready, flag):
    request = ready[2] | {"isSandbox": flag}
    with pytest.raises(f.FulfillmentError):
        f.create_intent(**request)
    assert ready[0].created == []


def test_intent_persists_before_provider_and_is_immutable(ready):
    adapter, connection, request = ready
    intent = f.create_intent(**request)
    assert adapter.created == [] and outbox(intent["intent_id"])["state"] == "READY"
    assert f.create_intent(**request) == {"intent_id": intent["intent_id"], "duplicate": True}
    # The recipient's name, deliberately: it is part of the snapshot the intent
    # is hashed over but not part of what the freight quote was priced on, so
    # this reaches the replay check instead of stopping at the quote cross-check.
    refreeze(contact_name="Another fixture recipient")
    with pytest.raises(f.FulfillmentError, match="immutable"):
        f.create_intent(**request)


def test_product_binding_cannot_be_forged_or_cross_merchant(ready):
    adapter, connection, request = ready
    with pytest.raises(SupplierError):
        gateway.bind_product(connection_id=connection["id"], business_id="biz-a", store_id="store-a", actor_user_id="100",
                             canonical_product_id=FOREIGN_LISTING, pid=PID, vid=VID, adapter=adapter)
    changed = copy.deepcopy(request)
    changed["items"][0]["vid"] = "forged-vid"
    with pytest.raises(f.FulfillmentError, match="binding"):
        f.create_intent(**changed)
    with pytest.raises(connections.SupplierConnectionError):
        f.create_intent(**(request | {"actor_user_id": "200"}))


@pytest.mark.parametrize("override", [{"expected_supplier_cost_cents": 1}, {"shipping_quote": {"snapshot_id": "fake"}},
                                     {"items": [{"canonical_product_id": FOREIGN_LISTING, "pid": PID, "vid": VID, "sku": "FIXTURE-SKU", "quantity": 1}]}])
def test_unverified_cost_quote_and_binding_denied(ready, override):
    with pytest.raises((f.FulfillmentError, SupplierError)):
        f.create_intent(**(ready[2] | override))


def test_exactly_one_concurrent_claim(ready):
    intent = f.create_intent(**ready[2])
    now = time.time() + .1
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: f.claim(now=now), range(2)))
    assert len([row for row in claims if row]) == 1
    assert outbox(intent["intent_id"])["attempts"] == 1


def test_shipping_quote_binds_exact_items_and_destination(ready):
    adapter, connection, request = ready
    # Moving the buyer's street after the quote was taken must invalidate the
    # quote, not silently ship to the new address on the old freight price.
    refreeze(address_line1="Different fixture street")
    with pytest.raises(f.FulfillmentError, match="destination_mismatch"):
        f.create_intent(**request)
    refreeze()
    conn = db.connect()
    row = conn.execute("SELECT payload_json FROM supplier_snapshots WHERE snapshot_id=?", (request["shipping_quote"]["snapshot_id"],)).fetchone()
    payload = json.loads(row[0])
    payload["quote_request"]["reqDTOS"][0]["freightTrialSkuList"][0]["vid"] = "20002"
    conn.close()
    mismatched = gateway._snapshot(connection["id"], "biz-a", "store-a", "shipping", "quote", payload)
    changed = copy.deepcopy(request)
    changed["shipping_quote"]["snapshot_id"] = mismatched
    with pytest.raises(f.FulfillmentError, match="items_mismatch"):
        f.create_intent(**changed)
    assert adapter.created == []


def test_queued_cost_change_blocks_create(ready, monkeypatch):
    result = f.create_intent(**ready[2])
    monkeypatch.setattr(ready[0], "get_variants", lambda pid: [{"pid": pid, "vid": VID, "sku": "FIXTURE-SKU", "currency": "USD", "price": "50.00"}])
    intent = f.claim(now=time.time() + .1)
    state = f.dispatch(intent, ready[0], connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"])
    assert state == "BLOCKED" and outbox(result["intent_id"])["state"] == "BLOCKED"
    assert ready[0].created == []


@pytest.mark.parametrize("override", [{"shop_id": "other-shop"}, {"is_sandbox": 0}, {"is_sandbox": True},
                                     {"external_order_ref": "foreign"}, {"products": [{"vid": VID, "quantity": 2}]}])
def test_readback_mismatch_never_links_or_recreates(ready, override):
    result, _, state = attempt(ready)
    assert state == "UNKNOWN"
    ready[0].observed.update(override)
    claimed = f.claim(now=time.time() + 4)
    assert f.dispatch(claimed, ready[0], connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]) == "UNKNOWN"
    assert len(ready[0].created) == 1 and outbox(result["intent_id"])["provider_order_id"] is None


def test_supplier_status_readback_updates_only_supplier_outbox(ready):
    before = canonical_order()
    result, _, _ = attempt(ready)
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    assert f.dispatch(f.claim(now=time.time() + 4), ready[0], meta) == "LINKED"
    ready[0].observed["provider_status"] = "SHIPPED"
    f.observe_linked(meta, "90001", ready[0].observed)
    assert outbox(result["intent_id"])["provider_status"] == "SHIPPED"
    assert canonical_order() == before


def test_one_canonical_order_cannot_create_via_two_owned_stores(ready):
    first = f.create_intent(**ready[2])
    conn = db.connect()
    conn.execute("UPDATE business_os_business SET owner_user_id='100' WHERE business_id='biz-b'")
    conn.commit()
    conn.close()
    second = connections.connect_cj("biz-b", "store-b", "100", "fixture-api-key-A", "cj-shop-a", adapter=ready[0])
    gateway.bind_product(connection_id=second["id"], business_id="biz-b", store_id="store-b", actor_user_id="100",
                         canonical_product_id=OWNED_LISTING, pid=PID, vid=VID, adapter=ready[0])
    conn = db.connect()
    stored = json.loads(conn.execute("SELECT payload_json FROM supplier_snapshots WHERE snapshot_id=?", (ready[2]["shipping_quote"]["snapshot_id"],)).fetchone()[0])
    conn.close()
    quote = gateway.read("shipping", connection_id=second["id"], business_id="biz-b", store_id="store-b", actor_user_id="100",
                         params=stored["quote_request"], adapter=ready[0])
    second_request = copy.deepcopy(ready[2])
    second_request.update(connection_id=second["id"], business_id="biz-b", store_id="store-b", idempotency_key="second-store-intent")
    second_request["shipping_quote"]["snapshot_id"] = quote["snapshot_id"]
    with pytest.raises(f.FulfillmentError, match="conflict"):
        f.create_intent(**second_request)
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM business_os_supplier_intents WHERE order_id=?", (ORDER_ID,)).fetchone()[0] == 1
    conn.close()
    assert outbox(first["intent_id"])["state"] == "READY" and ready[0].created == []


def test_sandbox_create_requires_independent_readback(ready):
    intent, claimed, state = attempt(ready)
    assert state == "UNKNOWN"
    assert len(ready[0].created) == 1
    payload = ready[0].created[0]
    assert payload["isSandbox"] == 1 and payload["payType"] == 3 and payload["orderFlow"] == 1
    assert payload["storeName"] == "Same name"
    later = f.claim(now=time.time() + 4)
    assert f.dispatch(later, ready[0], connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]) == "LINKED"
    assert outbox(intent["intent_id"])["provider_order_id"] == "90001"
    assert len(ready[0].created) == 1


def test_timeout_after_provider_success_never_retries_post(ready):
    ready[0].create_error = TimeoutError("synthetic timeout after CJ stored order")
    intent, claimed, state = attempt(ready)
    assert state == "UNKNOWN"
    later = f.claim(now=time.time() + 40)
    assert f.dispatch(later, ready[0], connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]) == "LINKED"
    assert len(ready[0].created) == 1


def test_unknown_not_found_is_not_absence_proof(ready):
    ready[0].create_error = TimeoutError()
    intent, claimed, state = attempt(ready)
    ready[0].observed = None
    for index in range(3):
        later = f.claim(now=time.time() + 400 * (index + 1))
        assert f.dispatch(later, ready[0], connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]) == "UNKNOWN"
    assert len(ready[0].created) == 1


def test_expired_sending_lease_never_reclaims_create(ready):
    result = f.create_intent(**ready[2])
    claimed = f.claim(now=time.time())
    f._sending(claimed, time.time())
    recovered = f.claim(now=time.time() + 200)
    assert recovered["state"] == "UNKNOWN"
    assert f.dispatch(recovered, ready[0], connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]) == "UNKNOWN"
    assert ready[0].created == []


@pytest.mark.parametrize("stock,verified", [(None, 1), (0, 1), (10, 2), (10, None)])
def test_unknown_zero_unverified_inventory_never_creates(ready, stock, verified):
    ready[0].stock, ready[0].verified = stock, verified
    assert attempt(ready)[2] == "BLOCKED"
    assert ready[0].created == []


def test_429_honors_long_retry_after_and_does_not_mark_stock_zero(ready):
    ready[0].read_error = SupplierError("RATE_LIMITED", http_status=429, retry_after=7200)
    started = time.time()
    result, _, state = attempt(ready)
    row = outbox(result["intent_id"])
    assert state == "READY" and row["available_at"] >= started + 7200
    assert ready[0].created == [] and ready[0].stock == 8


def test_orders_and_funding_remain_canonical_unchanged(ready):
    before = canonical_order()
    result, _, _ = attempt(ready)
    with pytest.raises(f.FulfillmentError):
        f.fund_fulfillment(result["intent_id"])
    assert outbox(result["intent_id"])["funding_state"] == "FUNDING_NOT_READY"
    # A whole-row comparison, not just status: a supplier dispatch that
    # decremented the customer order's quantity or rewrote its amount would be
    # just as much a breach of "customer order ≠ supplier order" as one that
    # marked it shipped.
    assert canonical_order() == before
    assert before["status"] == "paid"
    with pytest.raises(connections.SupplierConnectionError):
        f.get_intent(result["intent_id"], ready[1]["id"], "biz-a", "store-a", "200")


def test_a_connection_with_no_bound_cj_shop_cannot_place_an_order(ready):
    """Connecting no longer needs a CJ shop. Fulfilling still does.

    `_validate_observed` proves a placed order came back on the shop we bound.
    With nothing bound there is no such proof available, so the honest answer is
    to decline rather than to skip the check -- the skipped-check version looks
    identical in every green test and differs only in what it would accept.
    """
    _, connection, request = ready
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET external_shop_id='' WHERE id=?",
                 (connection["id"],))
    conn.commit()
    conn.close()
    with pytest.raises(f.FulfillmentError, match="shop_binding_required"):
        f.create_intent(**request)


# --------------------------------------------------------------------------
# `quote_for_order`: the server-side assembler the shipping endpoint lacked
#
# `gateway.read("shipping", ...)` had zero callers in `mobile-native/src`,
# `templates/` and `static/` -- measured, not assumed -- while `create_intent`
# refused without one of its snapshots. The reason is in the request: origin
# comes from the inventory, properties from the product, weight and SKU from
# the bound variant, and every address field from a record frozen on the
# transaction that no client has ever seen. No screen could build it, so the
# endpoint was unreachable by construction rather than by oversight.
# --------------------------------------------------------------------------

def quote(ready, **overrides):
    adapter, connection, request = ready
    return f.quote_for_order(connection_id=connection["id"], business_id="biz-a",
                             store_id="store-a", actor_user_id="100",
                             order_id=overrides.pop("order_id", ORDER_ID),
                             adapter=adapter, **overrides)


def restate_product(ready, **changes):
    """Rewrite the product snapshot the binding points at.

    The snapshot is what `quote_for_order` reads, so a test about a product
    fact CJ did not state has to change the stored record rather than the
    adapter -- re-binding reuses the snapshot it already has.
    """
    binding = gateway.get_product_binding(ready[1]["id"], "biz-a", "store-a", OWNED_LISTING)
    conn = db.connect()
    try:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM supplier_snapshots WHERE snapshot_id=?",
            (binding["snapshot_id"],)).fetchone()[0])
        payload.update(changes)
        conn.execute("UPDATE supplier_snapshots SET payload_json=? WHERE snapshot_id=?",
                     (json.dumps(payload), binding["snapshot_id"]))
        conn.commit()
    finally:
        conn.close()


def test_a_paid_order_can_be_quoted_with_no_caller_supplied_request(ready):
    result = quote(ready)
    assert result["snapshot_id"]
    assert result["order_id"] == int(ORDER_ID)
    assert result["quantity"] == 1
    # 2.00 a unit, one unit, in cents. Not the buyer's price.
    assert result["supplier_items_cost_cents"] == 200
    assert len(result["options"]) == 1
    assert result["options"][0]["available"] is True
    assert result["isSandbox"] == 1
    assert result["production_fulfillment_enabled"] is False


def test_the_quote_is_priced_to_the_address_the_buyer_paid_for(ready):
    # The whole point of assembling it server-side. Nobody passed this address
    # in; it came off the frozen transaction, and it is what the freight was
    # priced against -- which is also why `create_intent`'s own cross-checks
    # pass instead of being a hurdle a caller has to guess its way over.
    result = quote(ready)
    conn = db.connect()
    try:
        stored = json.loads(conn.execute(
            "SELECT payload_json FROM supplier_snapshots WHERE snapshot_id=?",
            (result["snapshot_id"],)).fetchone()[0])
    finally:
        conn.close()
    line = stored["quote_request"]["reqDTOS"][0]
    assert line["destAreaCode"] == FROZEN_DETAILS["address_country"]
    assert line["province"] == FROZEN_DETAILS["address_region"]
    assert line["city"] == FROZEN_DETAILS["address_city"]
    assert line["recipientAddress"] == FROZEN_DETAILS["address_line1"]


def test_the_quoted_cost_is_the_one_create_intent_will_accept(ready):
    # The two-call flow must not ask the caller to reproduce this module's
    # arithmetic. `create_intent` recomputes it and refuses on disagreement
    # regardless, so this being wrong is a dead end rather than a wrong charge
    # -- but a dead end no merchant can get out of.
    adapter, connection, request = ready
    result = quote(ready)
    option = result["options"][0]
    assert option["expected_supplier_cost_cents"] == 500
    handed_back = dict(
        request,
        shipping_quote={"snapshot_id": result["snapshot_id"],
                        "option_id": option["option_id"],
                        "channel_id": option["channel_id"]},
        expected_supplier_cost_cents=option["expected_supplier_cost_cents"],
        idempotency_key="quote-then-order-1")
    created = f.create_intent(**handed_back)
    assert created["intent_id"]
    assert created["duplicate"] is False


def test_an_option_the_supplier_marked_unavailable_costs_unknown_not_nothing(ready, monkeypatch):
    # Returned rather than dropped, because a merchant who cannot see why the
    # cheap service is missing assumes the platform lost it. With a null cost,
    # because an option whose landed cost we cannot state is not a free one.
    adapter = ready[0]
    monkeypatch.setattr(adapter, "estimate_shipping", lambda payload: {"quotes": [
        {"service": "Restricted Channel", "channel_id": "channel-9", "option_id": "option-9",
         "origin": "CN", "destination": "US", "provider_total": "3.00", "currency": "USD",
         "available": False, "restrictions": ["No batteries"], "guaranteed": False}]})
    only = quote(ready)["options"][0]
    assert only["available"] is False
    assert only["expected_supplier_cost_cents"] is None
    assert only["restrictions"] == ["No batteries"]


def test_a_parcel_of_unstated_weight_is_not_quoted_at_a_guess(ready):
    # `_money` answers None for a weight CJ did not state. The freight this
    # would quote is for a parcel whose contents we cannot price.
    restate_product(ready, variants=[{"pid": PID, "vid": VID, "sku": "FIXTURE-SKU",
                                      "price": "2.00", "currency": "USD",
                                      "weight_grams": None}])
    with pytest.raises(f.FulfillmentError, match="supplier_shipping_inputs_unknown"):
        quote(ready)


def test_goods_of_unknown_routing_class_are_not_quoted_as_ordinary(ready):
    # CJ routes batteries, liquids and magnets differently, and substituting a
    # plausible "ORDINARY" quotes the wrong service for exactly the goods where
    # it matters most.
    restate_product(ready, logistics_properties=[])
    with pytest.raises(f.FulfillmentError, match="supplier_logistics_properties_unknown"):
        quote(ready)


@pytest.mark.parametrize("stock,verified", [(0, 1), (None, 1), (8, 0)])
def test_freight_is_not_quoted_from_a_warehouse_holding_no_stock(ready, stock, verified):
    # Not a hardcoded "CN". Quoting from a country that holds none of the stock
    # prices a shipment that will not happen, and only a warehouse CJ has
    # verified is eligible reports IN_STOCK at all.
    adapter = ready[0]
    adapter.stock, adapter.verified = stock, verified
    with pytest.raises(f.FulfillmentError, match="supplier_origin_unknown"):
        quote(ready)


def test_an_order_with_no_frozen_address_is_refused_before_the_supplier_is_asked(ready):
    # The refusal has to come from `order_destination`, which runs first --
    # before the connection read, the binding, the snapshot and the two
    # provider calls. An order that cannot be shipped must not cost a request.
    adapter = ready[0]
    conn = db.connect()
    try:
        conn.execute("UPDATE seller_transactions SET metadata_json='{}' WHERE id=?", (7001,))
        conn.commit()
    finally:
        conn.close()
    calls = []
    adapter.get_inventory = lambda *a, **k: calls.append("inventory")
    adapter.estimate_shipping = lambda payload: calls.append("shipping")
    with pytest.raises(f.FulfillmentError, match="order_destination_missing"):
        quote(ready)
    assert calls == [], f"the supplier was contacted for an unshippable order: {calls}"
    refreeze()


def test_a_cancelled_order_is_not_quoted(ready):
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_orders SET status='cancelled' WHERE id=?", (int(ORDER_ID),))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(f.FulfillmentError, match="order_not_eligible"):
        quote(ready)


def test_another_merchants_order_cannot_be_quoted(ready):
    # A real, paid, shippable order with a complete frozen address -- so the
    # destination resolves and the tenancy check is what refuses, rather than
    # the refusal arriving for an unrelated reason and looking like a pass.
    conn = db.connect()
    try:
        conn.execute("INSERT INTO seller_transactions (id,buyer_user_id,seller_user_id,item_type,"
                     "item_id,amount_cents,status,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
                     (7002, 999, FOREIGN_MERCHANT, "marketplace_listing", int(OWNED_LISTING), 900,
                      "paid", json.dumps({"fulfillment": {"kind": "shipping",
                                                          "details": dict(FROZEN_DETAILS)}})))
        conn.execute("INSERT INTO marketplace_orders (id,seller_transaction_id,buyer_user_id,"
                     "seller_user_id,listing_id,quantity,unit_price_cents,amount_cents,status,"
                     "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (4002, 7002, 999, FOREIGN_MERCHANT, int(OWNED_LISTING), 1, 900, 900,
                      "paid", "now"))
        conn.commit()
    finally:
        conn.close()
    assert f.order_destination(4002)["shippingCity"] == FROZEN_DETAILS["address_city"]
    with pytest.raises(f.FulfillmentError, match="order_not_found"):
        quote(ready, order_id="4002")


def test_an_order_id_that_was_never_issued_is_refused(ready):
    # `order_destination` runs before the tenancy check, so this refuses under
    # its name rather than `order_not_found`. Both are 409/404 dead ends that
    # tell the caller nothing about whether the id exists, which is the point.
    with pytest.raises(f.FulfillmentError, match="order_destination_missing"):
        quote(ready, order_id="999999")


def test_a_quote_carries_no_supplier_account_identifier(ready):
    # Sections 27 and 95. The quote is a merchant-scoped read, so the freight
    # price travels; nothing identifying the connection does.
    serialized = json.dumps(quote(ready), default=str)
    for forbidden in ("access_token", "refresh_token", "openId", "open_id",
                      "credential_reference", "external_account_id", "cj-shop-a"):
        assert forbidden not in serialized, f"{forbidden} reached the caller"
