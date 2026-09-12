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
    # Which refusal it was, not just that there was one. `dispatch` used to store
    # the single word `preflight_blocked` for about a dozen distinct causes, and
    # that word is rendered to the merchant, so half a dozen fixable problems
    # arrived as one unactionable sentence.
    assert outbox(result["intent_id"])["last_error"] == "supplier_cost_changed"


def test_dispatch_measures_quote_age_against_the_clock_it_was_given(ready):
    """The freshness rule, executed for the first time.

    `dispatch` takes a `now`, and the line comparing the frozen `quoted_at`
    against it read `datetime.now(timezone.utc)` instead. That made the
    300-second window unreachable from a test by any means other than waiting
    300 real seconds: the snapshot is pinned by `snapshot_hash`, so `quoted_at`
    cannot be edited, and the only other input is the clock the function was
    ignoring.

    Nothing waited. So neither side of the reapproval rule standing between a
    merchant and a price CJ has since changed had ever been executed -- the
    existing coverage
    (`test_queued_cost_change_blocks_create`) exercises the cost half of what
    used to be one compound condition, and an expired quote and a changed cost
    are different things to tell a merchant.
    """
    result = f.create_intent(**ready[2])
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    stale = time.time() + f.QUOTE_MAX_AGE_SECONDS + 1
    claimed = f.claim(now=stale)

    assert f.dispatch(claimed, ready[0], meta, now=stale) == "BLOCKED"
    row = outbox(result["intent_id"])
    assert row["state"] == "BLOCKED"
    # Named as the expiry it is. A merchant told "your cost changed" goes looking
    # for a price difference that is not there; the action here is to re-quote.
    assert row["last_error"] == "supplier_quote_expired"
    # And nothing was bought. This is the assertion that entitles BLOCKED's copy
    # to say the order was not sent.
    assert ready[0].created == []
    assert row["provider_order_id"] is None


def test_a_quote_inside_the_window_is_not_refused_for_age(ready):
    """The other side of the same condition, which had also never run.

    A guard that cannot be made to fire and a guard that fires always are the
    same class of defect, and one test proves neither. Same injected clock, one
    second inside the window instead of one second outside it.
    """
    result = f.create_intent(**ready[2])
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    fresh = time.time() + f.QUOTE_MAX_AGE_SECONDS - 1
    claimed = f.claim(now=fresh)

    assert f.dispatch(claimed, ready[0], meta, now=fresh) == "UNKNOWN"
    row = outbox(result["intent_id"])
    assert row["last_error"] == "awaiting_create_readback"
    assert len(ready[0].created) == 1


def test_the_freshness_window_is_five_minutes_and_is_the_same_window_twice():
    """The one assertion the tests above structurally cannot make.

    Every other test here is written *relative* to `QUOTE_MAX_AGE_SECONDS` --
    `now + MAX + 1`, `now + MAX - 1` -- which is what makes them tests of the
    mechanism rather than of the number. It also means they move with the
    constant: widening the window to fifty minutes leaves all of them green.
    The mutation battery found that by widening it tenfold and watching nothing
    fail.

    So the size is pinned here, once, with the reason. It is a policy about
    money: how stale a freight price this deployment will spend real funds
    against. And it is pinned as *one* window, because `create_intent` refuses
    to freeze a quote older than this and `dispatch` refuses to spend one --
    the merchant's review time is subtracted from the worker's budget, and two
    separate numbers would mean a quote could be frozen that the worker would
    never accept, or accepted long after the merchant approved it.
    """
    assert f.QUOTE_MAX_AGE_SECONDS == 300, (
        "The freshness window changed. It is how stale a freight price this "
        "deployment will place a real order against, so it is a deliberate "
        "number rather than a tunable -- if it is meant to move, move it here "
        "too and say why.")
    source = open(f.__file__, encoding="utf-8").read()
    for function in ("create_intent", "dispatch"):
        body = source.split(f"def {function}(", 1)[-1].split("\ndef ", 1)[0]
        assert "QUOTE_MAX_AGE_SECONDS" in body, (
            f"{function} no longer measures quote age against the shared "
            "constant, so the two halves of one window can now drift apart")


def test_a_quote_dated_in_the_future_is_refused_too(ready):
    """The lower bound of the window, which the compound condition also hid.

    `0 <= age` is not decoration. `create_intent` refuses a future-dated quote,
    but that check runs on the web process's clock and this one runs on the
    worker's; a worker whose clock is behind sees every quote as being from the
    future. Accepting that would mean spending against a price on the strength
    of a disagreement between two machines.
    """
    result = f.create_intent(**ready[2])
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    # Claimed on a sane clock, dispatched on a skewed one -- the two `now`s are
    # independent arguments, which is the only reason this is reachable.
    claimed = f.claim(now=time.time() + .1)

    assert f.dispatch(claimed, ready[0], meta, now=time.time() - 600) == "BLOCKED"
    assert outbox(result["intent_id"])["last_error"] == "supplier_quote_expired"
    assert ready[0].created == []


def test_a_failure_after_the_write_is_never_reported_as_not_sent(ready, monkeypatch):
    """What entitles BLOCKED's copy to say nothing went out.

    `dispatch`'s handler tests `sent` first, so any exception raised after the
    POST becomes `UNKNOWN` -- "may or may not exist, do not re-order" -- and
    never `BLOCKED`. Without that ordering a network failure on the way back
    from a successful create would tell a merchant the order was not sent, and
    they would place it again.
    """
    result = f.create_intent(**ready[2])
    def explode(payload):
        raise RuntimeError("connection reset after POST")
    monkeypatch.setattr(ready[0], "create_sandbox_fulfillment", explode)
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    claimed = f.claim(now=time.time() + .1)

    assert f.dispatch(claimed, ready[0], meta) == "UNKNOWN"
    row = outbox(result["intent_id"])
    assert row["state"] == "UNKNOWN"
    assert row["last_error"] == "readback_required"
    assert row["provider_order_id"] is None


def test_a_refusal_this_module_did_not_choose_is_never_stored_verbatim(ready, monkeypatch):
    """The flattening existed for a real reason and the fix must not undo it.

    No provider message, code or body may become persisted state. Replacing
    `preflight_blocked` with `exc.code` outright would have done exactly that,
    because a `SupplierError`'s code is provider-shaped. `PREFLIGHT_REASONS` is
    a closed dict keyed by this module's own constants, so a code from anywhere
    else can only miss it.
    """
    result = f.create_intent(**ready[2])
    def refuse(pid):
        raise SupplierError("CJ_SAYS_ACCOUNT_SUSPENDED_FOR_user_at_example_com")
    monkeypatch.setattr(ready[0], "get_variants", refuse)
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    claimed = f.claim(now=time.time() + .1)

    assert f.dispatch(claimed, ready[0], meta) == "BLOCKED"
    stored = outbox(result["intent_id"])["last_error"]
    assert stored == "preflight_blocked"
    assert stored in f.OUTBOX_REASONS
    assert ready[0].created == []


def test_every_stored_refusal_is_one_the_merchant_can_be_shown(ready, monkeypatch):
    """Whatever reaches the column is in the declared vocabulary.

    The column is rendered on the orders screen, so an undeclared value is a
    raw identifier on a merchant's phone. Parametrising the causes would test
    the ones I thought of; this walks the mapping itself, so a cause added to
    `PREFLIGHT_REASONS` without copy is caught by the pairing of this with
    `tests/dropshipping/test_supplier_obligation_copy.py`.
    """
    assert set(f.PREFLIGHT_REASONS.values()) <= set(f.OUTBOX_REASONS)
    result = f.create_intent(**ready[2])
    monkeypatch.setattr(ready[0], "get_inventory",
                        lambda pid, vid=None: {"variants": [{"pid": pid, "vid": vid, "warehouses": [
                            {"country": "CN", "state": "OUT_OF_STOCK", "verified": 1, "total": 0}]}]})
    meta = connections.worker_connection(ready[1]["id"], "biz-a", "store-a")["connection"]
    claimed = f.claim(now=time.time() + .1)

    assert f.dispatch(claimed, ready[0], meta) == "BLOCKED"
    assert outbox(result["intent_id"])["last_error"] == "supplier_stock_unconfirmed"
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


# --------------------------------------------------------------------------
# Recovering an order whose supplier order was never sent
#
# Nothing above this line could have caught gap 18, and the reason is worth
# stating. Every test here that touches a failed send asserts on the *state*
# the row lands in -- `test_dispatch_*` prove BLOCKED is reached and carries
# its reason. None asked what that state left the merchant able to do, and the
# answer was nothing: BLOCKED is terminal (`claim` selects only READY, UNKNOWN
# and RECONCILE), `list_obligations` reported it as
# `SUPPLIER_ORDER_ALREADY_PLACED`, and `create_intent` refused a retry under
# both of its branches -- `immutable_intent_conflict` on a fresh key, and
# `duplicate: True` on the original one, which is a *success* pointing at an
# order that does not exist.
#
# So a buyer had paid, nothing had been ordered, and every surface said it was
# handled. These tests are written from the merchant's position -- "can I still
# get this order placed?" -- because that is the question the row-existence
# checks were silently answering no to.
# --------------------------------------------------------------------------

def a_blocked_never_sent_intent(ready, *, reason="supplier_sku_missing"):
    """Settle a real intent to the state `dispatch` reaches when it refuses to send."""
    first = f.create_intent(**ready[2])
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_outbox SET state='BLOCKED',last_error=? "
                 "WHERE intent_id=?", (reason, first["intent_id"]))
    conn.commit()
    conn.close()
    return first["intent_id"]


def retry(ready, *, key="merchant-retry-1"):
    request = copy.deepcopy(ready[2])
    request["idempotency_key"] = key
    return f.create_intent(**request)


def intent_row(intent_id):
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM business_os_supplier_intents WHERE id=?",
                                 (intent_id,)).fetchone())
    finally:
        conn.close()


def test_an_order_whose_supplier_order_was_never_sent_can_be_ordered_again(ready):
    """The gap, from the merchant's side: a fixable failure stops being permanent."""
    dead = a_blocked_never_sent_intent(ready)
    result = retry(ready)
    assert result["duplicate"] is False
    assert result["intent_id"] != dead, "a retry has to be a new intent, not the dead one"
    assert outbox(result["intent_id"])["state"] == "READY"
    assert ready[0].created == [], "recovery must not contact the supplier"


def test_the_failed_attempt_survives_as_an_audit_row(ready):
    """Retired, not deleted, and not rewritten.

    The alternative design -- reuse the dead intent and overwrite its snapshot --
    needs no schema change and was rejected for what it does to
    `external_order_ref`. That value is what `_validate_observed` uses to prove a
    provider order belongs to this intent, so reusing it across two different
    commercial offers would let a stray order from the first attempt
    authenticate against the second. A fresh intent cannot be confused that way.
    """
    dead = a_blocked_never_sent_intent(ready)
    before = intent_row(dead)
    result = retry(ready)
    after = intent_row(dead)

    assert after["superseded_at"] is not None, "the dead intent has to be marked retired"
    assert after["snapshot_json"] == before["snapshot_json"], "an intent stays immutable"
    assert after["snapshot_hash"] == before["snapshot_hash"]
    assert after["external_order_ref"] == before["external_order_ref"]
    # The one that matters: the replacement must not inherit the identity the
    # supplier could still answer the first attempt on.
    assert intent_row(result["intent_id"])["external_order_ref"] != before["external_order_ref"]
    assert intent_row(result["intent_id"])["superseded_at"] is None


def test_replaying_the_spent_key_is_refused_rather_than_called_a_duplicate(ready):
    """The sharpest half of the defect, and the one a caller could not see.

    Before this, retrying with the *original* idempotency key returned
    `{"duplicate": True}` pointing at the BLOCKED intent. `duplicate: True`
    means "the order your request asked for already exists" -- a caller shows
    "already placed" and stops. For a retired intent nothing exists, so the
    reply was a success that had placed nothing, which is strictly worse than
    the error the fresh-key path gave.
    """
    a_blocked_never_sent_intent(ready)
    retry(ready)
    with pytest.raises(f.FulfillmentError, match="intent_superseded_use_new_key"):
        f.create_intent(**ready[2])


def test_a_spent_key_cannot_mint_a_second_supplier_order(ready):
    """Why the idempotency match still spans retired intents.

    The order match was narrowed to live intents; this one deliberately was not.
    A key is the caller's promise that this is the same request, and a spent key
    minting a second order is the double-purchase the whole outbox exists to
    prevent.
    """
    a_blocked_never_sent_intent(ready)
    retry(ready)
    conn = db.connect()
    live = conn.execute("SELECT count(*) FROM business_os_supplier_intents "
                        "WHERE order_id=? AND superseded_at IS NULL", (ORDER_ID,)).fetchone()[0]
    total = conn.execute("SELECT count(*) FROM business_os_supplier_intents "
                         "WHERE order_id=?", (ORDER_ID,)).fetchone()[0]
    conn.close()
    assert (live, total) == (1, 2), "one live intent, one retired, never two live"


@pytest.mark.parametrize("state", ["SENDING", "UNKNOWN", "RECONCILE", "LINKED"])
def test_an_order_that_may_have_been_sent_is_never_recoverable(ready, state):
    """Fail closed, which is the entire safety argument.

    `SENDING` may be mid-write. `UNKNOWN` is the state whose own reason string
    is `absence_not_proven`. `RECONCILE` is the same family. `LINKED` is a
    confirmed provider order. Recovering any of them buys the same goods twice,
    so only positive proof of a non-send unlocks a retry -- never the absence of
    proof of one.
    """
    first = f.create_intent(**ready[2])
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_outbox SET state=? WHERE intent_id=?",
                 (state, first["intent_id"]))
    conn.commit()
    conn.close()
    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)
    assert intent_row(first["intent_id"])["superseded_at"] is None


def test_a_queued_order_is_not_recoverable_merely_because_nothing_was_sent(ready):
    """Never-sent is necessary and not sufficient, and READY is the proof.

    A READY row has contacted nobody -- it satisfies `NEVER_SENT_STATES` -- but
    it is live: `claim` will pick it up the moment a drain exists. Offering a
    retry here would queue the same purchase twice, so recovery needs the row to
    be past retrying as well as unsent, which is what `RECOVERABLE_STATES` says
    and `READY` is deliberately not in.
    """
    first = f.create_intent(**ready[2])
    assert outbox(first["intent_id"])["state"] == "READY"
    assert "READY" in f.NEVER_SENT_STATES and "READY" not in f.RECOVERABLE_STATES
    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)
    assert intent_row(first["intent_id"])["superseded_at"] is None


def test_an_intent_with_no_outbox_row_cannot_testify_and_so_blocks(ready):
    """`create_intent` writes both rows in one transaction, so this is unreachable.

    The LEFT JOIN can still express it, and a row that cannot say whether it was
    sent must not be treated as saying no.
    """
    first = f.create_intent(**ready[2])
    conn = db.connect()
    conn.execute("DELETE FROM business_os_supplier_outbox WHERE intent_id=?", (first["intent_id"],))
    conn.commit()
    conn.close()
    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)
    assert intent_row(first["intent_id"])["superseded_at"] is None


def test_a_blocked_row_still_holding_a_provider_order_id_is_not_recoverable(ready):
    """Why the predicate reads the id as well as the state.

    `settle` writes `provider_order_id=COALESCE(?,provider_order_id)`, so once
    set it is never cleared. A row that acquired an id and later reads BLOCKED is
    reporting two things, and the id is the older and far more expensive one.
    """
    dead = a_blocked_never_sent_intent(ready)
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_outbox SET provider_order_id='CJ-STRAY' "
                 "WHERE intent_id=?", (dead,))
    conn.commit()
    conn.close()
    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)
    assert intent_row(dead)["superseded_at"] is None


def test_an_order_can_be_recovered_more_than_once(ready):
    """Two failed attempts in a row, because the second is where the filter bites.

    `create_intent` looks up the intent holding this order with
    ``AND i.superseded_at IS NULL``. Dropping that condition still lets the
    *first* recovery through -- there is only one intent and it is live -- so a
    single-recovery test cannot see the difference. On the second recovery the
    lookup starts returning retired rows too, and `fetchone` gets whichever one
    the engine hands back: if it is the dead one, the conditional UPDATE finds
    nothing and a legitimate retry is refused.

    A merchant can plausibly fail twice -- a missing SKU fixed, then a stale
    quote -- so this is not a contrived path, and "recovery works once and then
    stops" is a worse failure than no recovery at all, because it looks fixed.
    """
    first = a_blocked_never_sent_intent(ready)
    second = retry(ready, key="merchant-retry-1")["intent_id"]

    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_outbox SET state='BLOCKED',last_error=? "
                 "WHERE intent_id=?", ("quote_expired", second))
    conn.commit()
    conn.close()

    third = retry(ready, key="merchant-retry-2")["intent_id"]
    assert len({first, second, third}) == 3, "three distinct intents, each its own offer"

    conn = db.connect()
    try:
        live = [row["id"] for row in conn.execute(
            "SELECT id FROM business_os_supplier_intents WHERE order_id=? "
            "AND superseded_at IS NULL", (str(ready[2]["order_id"]),)).fetchall()]
        total = conn.execute("SELECT count(*) FROM business_os_supplier_intents "
                             "WHERE order_id=?", (str(ready[2]["order_id"]),)).fetchone()[0]
    finally:
        conn.close()
    assert live == [third], "exactly one live intent, and it is the newest"
    assert total == 3, "both failed attempts stay on file as audit rows"
    assert ready[0].created == [], "two recoveries, still nothing sent to the supplier"


def test_the_predicate_refuses_a_provider_order_id_on_its_own(ready):
    """The same refusal as the test above, asked of the predicate directly.

    The test above goes through `create_intent`, and `create_intent` checks the
    id twice: once in `_recoverable_intent` and again in the conditional UPDATE's
    `AND o.provider_order_id IS NULL`. That duplication is deliberate -- the
    whole point of re-checking inside the write is that the read may be stale --
    and it means deleting *either* copy leaves the other one refusing and the
    suite green. The mutation battery found exactly that: dropping
    `and not provider_order_id` from the predicate survived.

    So the predicate is asked on its own, with no SQL underneath it to answer for
    it. `_recoverable_intent` also feeds `list_obligations`, which has no second
    guard at all, so a predicate that stops reading the id is not merely
    redundant there -- it is the whole answer.
    """
    assert f._recoverable_intent("BLOCKED", None) is True
    assert f._recoverable_intent("BLOCKED", "CJ-STRAY") is False, (
        "a row holding a provider order id has a real supplier order behind it, "
        "whatever its state now reads")


def test_the_provider_order_id_guard_is_in_the_write_too(ready, monkeypatch):
    """The other half of the duplicated check, isolated the same way.

    Companion to `test_the_predicate_refuses_a_provider_order_id_on_its_own`:
    that one removes the SQL from the picture, this one removes the Python. With
    the predicate forced to say "recoverable" about a row holding a
    `provider_order_id`, the only thing left that can refuse is the UPDATE's own
    `AND o.provider_order_id IS NULL` -- which is the state a lost race actually
    produces, since `settle` can write an id between the read and the write.
    """
    dead = a_blocked_never_sent_intent(ready)
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_outbox SET provider_order_id='CJ-RACED' "
                 "WHERE intent_id=?", (dead,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(f, "_recoverable_intent", lambda state, provider_order_id: True)

    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)
    assert intent_row(dead)["superseded_at"] is None, (
        "an intent whose outbox row names a provider order must not be retired, "
        "no matter what the Python in front of the statement believes")


class _RowsAlreadyRead:
    """The rows a lookup really returned, served back after the proxy moved on."""

    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _RetiresBehindTheRead:
    """A connection that stands in for the recovery which won the race.

    `create_intent` reads the live intent, asks whether it is recoverable, and
    then retires it. The `UPDATE` carries `AND superseded_at IS NULL` for the
    interval between those statements: a second recovery can read the same live
    row and retire it first, and the loser must then find nothing to retire.

    That clause cannot be reached through the front door, because the lookup
    above it filters retired rows out and so never hands the statement one --
    which is exactly why deleting it leaves the suite green. SQLite will not run
    a second writer inside the open transaction either, so the race is
    reproduced from the inside: the proxy watches for the live lookup, lets it
    return precisely what it really returns, and retires that row before the
    caller acts on it. What `create_intent` then holds is what the losing
    recovery holds -- proof about a row another transaction has already spent.
    """

    #: Matched by text because the interception has to happen between two
    #: specific statements. If the query is reformatted this stops matching,
    #: nothing is retired, and the assertions below fail loudly rather than
    #: passing on a race that never happened.
    LOOKUP = "WHERE i.connection_id=? AND i.order_id=? AND i.superseded_at IS NULL"

    def __init__(self, inner):
        self.inner = inner
        self.retired = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def execute(self, sql, params=()):
        cursor = self.inner.execute(sql, params)
        if self.LOOKUP not in sql:
            return cursor
        rows = cursor.fetchall()
        for row in rows:
            self.retired.append(dict(row)["id"])
            self.inner.execute("UPDATE business_os_supplier_intents SET superseded_at=? "
                               "WHERE id=?", (time.time(), dict(row)["id"]))
        return _RowsAlreadyRead(rows)


def test_the_retirement_statement_refuses_an_already_retired_row(ready, monkeypatch):
    """The third duplicated guard, exercised through the code that carries it."""
    dead = a_blocked_never_sent_intent(ready)
    raced = []
    real_connect = db.connect
    monkeypatch.setattr(db, "connect",
                        lambda: raced.append(_RetiresBehindTheRead(real_connect())) or raced[-1])

    # Retiring an intent a second time would give one customer order two live
    # replacements, leaving the canonical-order invariant to the index rather
    # than to the code. The recovery that lost the race has to refuse.
    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)

    assert [proxy.retired for proxy in raced if proxy.retired] == [[dead]], (
        "the race has to have actually happened: the proxy must have seen the "
        "live lookup and retired the row it returned, or this test proves nothing")

    # The simulated winner wrote inside the loser's transaction, so the refusal
    # rolls its retirement back too. That costs nothing here: what matters is
    # what the loser left behind, and the answer has to be nothing at all.
    conn = real_connect()
    try:
        surviving = [dict(row)["id"] for row in conn.execute(
            "SELECT id FROM business_os_supplier_intents WHERE order_id=?",
            (str(ready[2]["order_id"]),)).fetchall()]
    finally:
        conn.close()
    assert surviving == [dead], (
        "and no replacement is written: an intent the loser could not retire "
        "must not acquire a successor anyway")


def test_the_guard_on_retiring_an_intent_is_in_the_write_not_only_the_read(ready, monkeypatch):
    """A stale "recoverable" verdict must not be enough to retire an intent.

    `create_intent` reads the outbox, asks `_recoverable_intent`, and then
    retires the row. Between the read and the write a dispatch can reclaim the
    intent, so the `UPDATE` re-checks the outbox in its own `WHERE` instead of
    trusting the verdict it was given.

    Tested by making the verdict lie rather than by racing two connections --
    SQLite will not let a second connection write inside `create_intent`'s
    transaction, and a test that cannot run is worse than no test. Forcing the
    predicate to return True against a row that is really SENDING reproduces
    exactly the state a lost race leaves behind, and the assertion is that the
    SQL refuses anyway.
    """
    dead = a_blocked_never_sent_intent(ready)
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_outbox SET state='SENDING' WHERE intent_id=?", (dead,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(f, "_recoverable_intent", lambda state, provider_order_id: True)

    with pytest.raises(f.FulfillmentError, match="immutable_intent_conflict"):
        retry(ready)
    assert intent_row(dead)["superseded_at"] is None, (
        "the conditional UPDATE has to refuse a row that moved, not retire it anyway")
    conn = db.connect()
    live = conn.execute("SELECT count(*) FROM business_os_supplier_intents "
                        "WHERE order_id=? AND superseded_at IS NULL", (ORDER_ID,)).fetchone()[0]
    conn.close()
    assert live == 1, "the in-flight intent keeps the order"


def test_recovery_places_no_order_and_spends_nothing(ready):
    """Section 37. Retiring an intent is a local bookkeeping write."""
    a_blocked_never_sent_intent(ready)
    before = canonical_order()
    retry(ready)
    assert ready[0].created == []
    assert canonical_order() == before, "the customer order is untouched by recovery"


# --------------------------------------------------------------------------
# Whether the new shape reaches a database that already exists
#
# `CREATE TABLE IF NOT EXISTS` is a no-op on every database that has ever run
# this code, so a DDL edit alone would only ever apply to a fresh one -- and the
# suite above runs on a fresh SQLite file every time, so it would prove a
# recovery path production does not have. That is this mission's recurring
# defect with a schema in the subject position, so the reshape is measured on a
# deliberately old-shaped table rather than assumed to have applied.
# --------------------------------------------------------------------------

OLD_SHAPE = """CREATE TABLE business_os_supplier_intents (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, business_id TEXT NOT NULL,
    store_id TEXT NOT NULL, merchant_id TEXT NOT NULL, order_id TEXT NOT NULL,
    external_account_id TEXT NOT NULL, external_shop_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, external_order_ref TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL, UNIQUE(connection_id, idempotency_key),
    UNIQUE(connection_id, order_id))"""


def revert_to_the_old_shape():
    """Rebuild the table exactly as it was declared before supersession existed."""
    conn = db.connect()
    conn.execute("DROP TABLE IF EXISTS business_os_supplier_intents")
    conn.execute(OLD_SHAPE)
    conn.execute("CREATE UNIQUE INDEX uq_supplier_canonical_order "
                 "ON business_os_supplier_intents(order_id)")
    conn.execute("DROP INDEX IF EXISTS uq_supplier_live_canonical_order")
    conn.execute(
        "INSERT INTO business_os_supplier_intents (id,connection_id,business_id,store_id,"
        "merchant_id,order_id,external_account_id,external_shop_id,idempotency_key,"
        "external_order_ref,snapshot_json,snapshot_hash,created_at) "
        "VALUES ('cjf_legacy','conn-legacy','biz-a','store-a','100','9999','acct','shop',"
        "'legacy-key','pss_legacy','{\"items\": []}','legacyhash',1.0)")
    conn.commit()
    conn.close()


def table_sql():
    conn = db.connect()
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                           "AND name='business_os_supplier_intents'").fetchone()
        return (row["sql"] or "").replace("\n", " ") if row else ""
    finally:
        conn.close()


def index_names():
    conn = db.connect()
    try:
        return {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='business_os_supplier_intents'").fetchall()}
    finally:
        conn.close()


def test_an_existing_database_is_reshaped_rather_than_left_behind(database):
    """The migration, measured on the shape production actually has."""
    revert_to_the_old_shape()
    assert "connection_id, order_id" in table_sql(), "this test is only meaningful on the old shape"

    f.ensure_schema()

    declared = table_sql()
    assert "superseded_at" in declared, "the recovery column has to reach an existing table"
    assert "connection_id, order_id" not in declared, (
        "the unconditional pair uniqueness is what makes a replacement impossible, "
        "so the reshape has to drop it -- on SQLite that needs a table rebuild")
    # Still enforced, and still the caller's promise: a spent key may never mint
    # a second order, retired intent or not.
    assert "connection_id, idempotency_key" in declared


def test_the_reshape_replaces_the_unconditional_canonical_index_with_a_conditional_one(database):
    revert_to_the_old_shape()
    f.ensure_schema()
    names = index_names()
    assert "uq_supplier_live_canonical_order" in names
    assert "uq_supplier_canonical_order" not in names, (
        "an unconditional unique index on order_id refuses the replacement no matter "
        "what the table constraints say")


def test_the_blocking_index_is_dropped_even_when_the_table_is_not_rebuilt(database, monkeypatch):
    """Isolates the `DROP INDEX`, which the rebuild was silently doing for it.

    `test_the_reshape_replaces_the_unconditional_canonical_index_with_a_conditional_one`
    looks like it covers this and does not. On SQLite the reshape rebuilds the
    table, and rebuilding a table destroys its indexes as a side effect -- so
    that test passes with the explicit `DROP INDEX IF EXISTS` deleted. The
    mutation battery caught it: the drop survived removal.

    The drop is load-bearing on **PostgreSQL**, where the reshape takes the
    `DROP CONSTRAINT` branch and no table is ever rebuilt. So the rebuild is
    stubbed rather than induced by a special table shape. An earlier version of
    this test tried the latter -- start from a table the rebuild would decline --
    and could not then prove the decline: SQLite's `ADD COLUMN` inserts the new
    column *ahead of* the trailing table constraints, so the resulting DDL is
    indistinguishable from a rebuilt one. Forcing the branch is simpler and a
    closer model of the engine this actually guards.
    """
    revert_to_the_old_shape()
    declined = []
    monkeypatch.setattr(f, "_drop_sqlite_order_uniqueness",
                        lambda conn: declined.append(True))

    f.ensure_schema()

    assert declined == [True], (
        "the stub has to be what ran -- if the real rebuild executed, this test "
        "is back to proving what the other one already proves")
    names = index_names()
    assert "uq_supplier_canonical_order" not in names, (
        "the unconditional index has to be dropped by name -- on PostgreSQL "
        "there is no rebuild to remove it, and while it exists no order can "
        "ever be attempted a second time")
    assert "uq_supplier_live_canonical_order" in names


def test_the_reshape_keeps_the_rows_it_found(database):
    """A migration that loses an intent loses the record of a real purchase."""
    revert_to_the_old_shape()
    f.ensure_schema()
    row = intent_row("cjf_legacy")
    assert row["external_order_ref"] == "pss_legacy"
    assert row["snapshot_hash"] == "legacyhash"
    assert row["order_id"] == "9999"
    # Live, not retired. Defaulting existing rows to superseded would silently
    # free every order in the table for a second supplier purchase.
    assert row["superseded_at"] is None


def test_one_live_intent_per_order_survives_the_reshape(database):
    """The invariant the dropped constraint used to carry has to still hold."""
    revert_to_the_old_shape()
    f.ensure_schema()
    conn = db.connect()
    try:
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO business_os_supplier_intents (id,connection_id,business_id,store_id,"
                "merchant_id,order_id,external_account_id,external_shop_id,idempotency_key,"
                "external_order_ref,snapshot_json,snapshot_hash,created_at) "
                "VALUES ('cjf_second','conn-other','biz-a','store-a','100','9999','acct','shop',"
                "'other-key','pss_second','{}','h',2.0)")
            conn.commit()
    finally:
        conn.rollback()
        conn.close()


def test_a_retired_intent_frees_the_order_after_the_reshape(database):
    """End to end on a migrated table: the conditional index is what allows recovery."""
    revert_to_the_old_shape()
    f.ensure_schema()
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_intents SET superseded_at=5.0 WHERE id='cjf_legacy'")
    conn.execute(
        "INSERT INTO business_os_supplier_intents (id,connection_id,business_id,store_id,"
        "merchant_id,order_id,external_account_id,external_shop_id,idempotency_key,"
        "external_order_ref,snapshot_json,snapshot_hash,created_at) "
        "VALUES ('cjf_replacement','conn-legacy','biz-a','store-a','100','9999','acct','shop',"
        "'replacement-key','pss_replacement','{}','h2',6.0)")
    conn.commit()
    live = conn.execute("SELECT count(*) FROM business_os_supplier_intents "
                        "WHERE order_id='9999' AND superseded_at IS NULL").fetchone()[0]
    conn.close()
    assert live == 1, "exactly one live intent, with the retired one still on file beside it"


def test_the_reshape_is_safe_to_run_twice(database):
    """It runs on every boot, so running it again has to be a no-op."""
    revert_to_the_old_shape()
    f.ensure_schema()
    first, first_indexes = table_sql(), index_names()
    f.ensure_schema()
    f.ensure_schema()
    assert table_sql() == first
    assert index_names() == first_indexes
    assert intent_row("cjf_legacy")["external_order_ref"] == "pss_legacy"


# --------------------------------------------------------------------------
# The vocabularies that decide whether money can be spent twice
# --------------------------------------------------------------------------

def test_every_recoverable_state_is_also_a_never_sent_state():
    """The subset relation is the safety argument, so it is asserted not assumed.

    `RECOVERABLE_STATES` unlocks a second supplier purchase for an order. If a
    state ever entered it without also being in `NEVER_SENT_STATES`, this
    deployment would offer a retry on an order it cannot prove was never placed,
    which is the double-purchase the outbox exists to prevent.
    """
    assert set(f.RECOVERABLE_STATES) <= set(f.NEVER_SENT_STATES)
    # There is no backend tuple of every outbox state -- `dispatch` and `settle`
    # are the only code that names them, which is why
    # `tests/dropshipping/test_supplier_obligation_copy.py` finds them by
    # scanning literals. So the containment that can be checked here is against
    # the module's own text: a never-sent state this module never writes would be
    # a name with nothing behind it, and a name with nothing behind it is how a
    # vocabulary forks.
    source = open(f.__file__, encoding="utf-8").read()
    for state in f.NEVER_SENT_STATES:
        assert f"'{state}'" in source or f'"{state}"' in source, (
            f"{state} is in NEVER_SENT_STATES but this module never writes it")


def test_no_state_that_may_have_reached_the_supplier_is_never_sent():
    """Stated as an absolute list rather than relative to the tuple.

    Every test above is written in terms of `NEVER_SENT_STATES`, so widening it
    leaves them all green -- a test written relative to a constant cannot detect
    a change to that constant. These four names are the ones whose meaning is
    "a write may have happened", and none of them may ever appear there.
    """
    source = open(f.__file__, encoding="utf-8").read()
    for state in ("SENDING", "UNKNOWN", "RECONCILE", "LINKED"):
        # Spelled here as a literal and confirmed against the module text, so a
        # rename does not quietly turn this loop into four assertions about
        # states that no longer exist -- which would pass.
        assert f"'{state}'" in source or f'"{state}"' in source, (
            f"{state} is no longer a state this module writes -- re-derive the "
            "may-have-been-sent list before trusting these assertions")
        assert state not in f.NEVER_SENT_STATES, (
            f"{state} can follow a send, so treating it as proof of a non-send "
            "buys the same goods twice")
        assert state not in f.RECOVERABLE_STATES


def test_the_never_sent_claim_is_derived_from_the_guard_that_makes_it_true():
    """`READY` and `BLOCKED` prove a non-send only because of one branch in `dispatch`.

    That branch sends anything with `sent` set, or already unconfirmed, or an
    ambiguous write, to `UNKNOWN` -- so the two remaining states are reachable
    only when no write had happened. Change the guard and the proof evaporates
    silently, with every test above still green, so the guard itself is pinned.
    """
    source = open(f.__file__, encoding="utf-8").read()
    assert 'if sent or intent["state"] in {"UNKNOWN", "RECONCILE"}' in source, (
        "dispatch's ambiguous-write guard changed -- re-derive NEVER_SENT_STATES "
        "from the new one before trusting it")
    assert 'state IN (\'READY\',\'UNKNOWN\',\'RECONCILE\')' in source, (
        "claim's due-state list changed -- BLOCKED may no longer be terminal, "
        "which is half of what makes it recoverable")
