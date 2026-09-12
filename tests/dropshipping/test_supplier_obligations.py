"""A paid dropship sale owes a purchase from the supplier, and says so.

What this file is defending
---------------------------
Gap 14: a buyer could pay for a published dropship listing and *nothing* on the
supplier side recorded that the merchant now owed a purchase. Not a row, not a
queue entry, not a screen. `create_intent` is the only writer of
`business_os_supplier_intents`, its one production call site had zero callers in
`mobile-native/src`, `templates/` or `static/`, and nothing in `bot.py` so much
as mentions `marketplace_product_sources`. A probe confirmed it end-to-end:
sell a bound dropship listing for real money, and both supplier tables stay at
zero rows.

The fix is not to call `create_intent` at checkout. Measured from its own
bytecode, that function demands a shipping quote less than 300 seconds old and
an `expected_supplier_cost_cents` that must equal the quote exactly -- it is a
merchant *approval* action, and a payment webhook holds neither of those
things. So the obligation is derived on read instead: `list_obligations` joins
paid orders to their supplier source and to any intent that already exists.
No new column, no new state, and no contact with the supplier.

What each test here is for
--------------------------
The first test is the one that would have caught the gap: a paid dropship sale
must appear. The rest defend the edges that make it *correct* rather than merely
non-empty -- because a query that returns everything is as wrong as one that
returns nothing, and only a filter test can tell them apart.

Two tests do not exercise behaviour at all, deliberately:

* `test_the_order_id_join_casts_...` reads the SQL literal. `marketplace_orders.id`
  is INTEGER and `business_os_supplier_intents.order_id` is TEXT, because
  `create_intent` writes `str(order_id)`. On PostgreSQL `i.order_id = o.id` is a
  type error; on SQLite it silently matches nothing. So the SQLite failure mode
  is a list where every row looks unplaced -- no exception, no empty result, no
  failing test. Behaviour cannot see this. The literal can.
* `test_marketplace_orders_has_exactly_one_writer` reads `bot.py`. The filter
  `LOWER(o.status)='paid'` is only right while one writer hardcodes 'paid'.

Both are reading *data* -- a SQL string, a count of INSERT sites -- not asking a
function to describe itself.

Runs alone. `tests/dropshipping/` files each bind their own `DATABASE_URL` at
import, so the suite is run one file per process.
"""

import json
import os
import sys
import tempfile
import time
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="supplier-obligations-", suffix=".db")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, fulfillment, gateway, import_cart, importer)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.connections import SupplierConnectionError  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

# Importing the pipeline suite rebinds DATABASE_URL to *its* temp file at
# module scope. `services.db.connect()` reads the variable per call, so
# re-asserting ours afterwards is enough -- but it has to happen after, not
# before, and it is why this is not a plain top-of-file import block.
from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OTHER_BUSINESS, OTHER_CONNECTION,
    OTHER_OWNER_ID, OTHER_STORE, OWNER_ID, STORE, _seed_connection, _seed_tenancy,
    cj_product)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

BUYER_ID = 90210
OUTSIDER_ID = 777001

# Verbatim from bot.init_db(). bot.py cannot be imported here -- 111k lines and
# a Flask app at module scope -- so the columns are copied. A drift makes the
# INSERTs below fail loudly rather than measuring a table of local invention.
MARKETPLACE_ORDERS_DDL = """
CREATE TABLE IF NOT EXISTS marketplace_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_transaction_id INTEGER UNIQUE,
    buyer_user_id INTEGER NOT NULL,
    seller_user_id INTEGER NOT NULL,
    listing_id INTEGER NOT NULL,
    quantity INTEGER DEFAULT 1,
    unit_price_cents INTEGER DEFAULT 0,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    status TEXT DEFAULT 'pending_payment',
    payment_provider TEXT DEFAULT 'stripe',
    provider_payment_id TEXT,
    created_at TEXT,
    paid_at TEXT,
    updated_at TEXT
)
"""

# Also verbatim from bot.init_db(), for the same reason. This is the record
# checkout freezes the buyer's delivery address onto, and gap 15 made it the
# only source of a supplier destination -- so a suite that omits it is not
# measuring a cheaper version of the obligation list, it is measuring a
# different query from the one that runs in production.
SELLER_TRANSACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS seller_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_user_id INTEGER,
    seller_user_id INTEGER,
    seller_type TEXT,
    item_type TEXT,
    item_id INTEGER,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    platform_fee_cents INTEGER DEFAULT 0,
    seller_net_cents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'created',
    stripe_checkout_session_id TEXT,
    stripe_payment_intent_id TEXT,
    metadata_json TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

#: A complete US delivery address, in the shape `marketplace_fulfillment.snapshot`
#: writes and with keys spelled the way `validate_details` spells them. Complete
#: on purpose: the default paid sale in this file is one a supplier could ship,
#: so a blocker appearing on it is a finding rather than the fixture's fault.
FROZEN_DETAILS = {
    "contact_name": "Fixture Buyer",
    "contact_phone": "+15550100",
    "address_line1": "1 Fixture Way",
    "address_city": "Fixture City",
    "address_region": "CA",
    "address_postal_code": "94000",
    "address_country": "US",
}


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()
    if hasattr(gateway, "reset_schema_cache"):
        gateway.reset_schema_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        seed_production_listings(cur)
        cur.execute("DELETE FROM marketplace_listings")
        supplier_schema.ensure_supplier_schema(cur, force=True)
        _seed_tenancy(conn)
        connection_schema.ensure_schema(conn)
        _seed_connection(conn, CONNECTION, BUSINESS, STORE, OWNER_ID)
        _seed_connection(conn, OTHER_CONNECTION, OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID)
        cur.execute(MARKETPLACE_ORDERS_DDL)
        cur.execute(SELLER_TRANSACTIONS_DDL)
        conn.commit()
    finally:
        conn.close()
    import_cart.ensure_schema()
    gateway.ensure_schema()
    fulfillment.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(importer.gateway, "read", fake)
    monkeypatch.setattr(gateway, "read", fake)
    return fake


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def execute(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def publish_dropship_listing(provider, pid="SALE-1", *, business=BUSINESS, store=STORE,
                             connection=CONNECTION, actor=OWNER_ID, price_cents=2000):
    """The gap-13 happy path: one variant, so import binds the source row."""
    provider.add(cj_product(pid, variants_=[
        {"vid": f"{pid}-V1", "variantKey": "Colour-1", "variantSellPrice": "8.20",
         "variantQuantity": 40, "variantSku": f"{pid}-SKU-1", "pid": pid}]))
    import_cart.add_item(business, store, actor, connection,
                         external_product_id=pid, selected_variant_ids=[f"{pid}-V1"],
                         context=CONTEXT)
    result = importer.import_selected(business, store, actor, connection, context=CONTEXT)
    listing_id = result["results"][0]["listing_id"]
    draft = drafts.get_draft(business, store, actor, connection, listing_id, context=CONTEXT)
    drafts.update_draft(
        business, store, actor, connection, listing_id,
        fields={"price_cents": {str(v["variant_id"]): price_cents for v in draft["variants"]}},
        context=CONTEXT)
    drafts.publish(business, store, actor, connection, listing_id, context=CONTEXT)
    return listing_id


def a_buyer_pays(listing_id, *, quantity=1, unit_price_cents=2000, status="paid",
                 seller_transaction_id=None, details=FROZEN_DETAILS, kind="shipping",
                 transaction=True):
    """Project a paid transaction into an order, as `pulse_upsert_marketplace_order` does.

    Writes both halves, because production has both: the transaction Stripe's
    webhook settles and the canonical order projected off it. Gap 15 made the
    second half read the first -- the delivery address lives only on the
    transaction -- so a helper that wrote the order alone would manufacture a
    `DESTINATION_MISSING` on every sale in the file.

    `details=None` freezes a lane with no address; `transaction=False` writes an
    order whose `seller_transaction_id` points at nothing. Both are states a
    real database reaches, and both are the subject of their own test below.
    """
    stamp = "2026-09-12T00:00:00"
    transaction_id = (seller_transaction_id if seller_transaction_id is not None
                      else int(uuid.uuid4().int % 10**8))
    if transaction:
        frozen = {"kind": kind}
        if details is not None:
            frozen["details"] = dict(details)
        execute(
            "INSERT INTO seller_transactions (id,buyer_user_id,seller_user_id,seller_type,"
            "item_type,item_id,amount_cents,currency,status,stripe_payment_intent_id,"
            "metadata_json,created_at,updated_at) VALUES (?,?,?,'user',?,?,?,?,?,?,?,?,?)",
            (transaction_id, BUYER_ID, OWNER_ID, "marketplace_listing", listing_id,
             unit_price_cents * quantity, "USD", status, "pi_test",
             json.dumps({"fulfillment": frozen}), stamp, stamp))
    return execute(
        "INSERT INTO marketplace_orders (seller_transaction_id,buyer_user_id,seller_user_id,"
        "listing_id,quantity,unit_price_cents,amount_cents,currency,status,payment_provider,"
        "provider_payment_id,created_at,paid_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'stripe',?,?,?,?)",
        (transaction_id, BUYER_ID, OWNER_ID, listing_id, quantity, unit_price_cents,
         unit_price_cents * quantity, "USD", status, "pi_test", stamp, stamp, stamp))


def obligations(**kwargs):
    payload = fulfillment.list_obligations(
        kwargs.pop("connection_id", CONNECTION), kwargs.pop("business_id", BUSINESS),
        kwargs.pop("store_id", STORE), kwargs.pop("actor_user_id", OWNER_ID),
        context=CONTEXT, **kwargs)
    return payload["obligations"]


def an_intent_exists_for(order_id, *, state=None, provider_order_id=None,
                         last_error=None, connection=CONNECTION):
    """Write the intent/outbox pair `create_intent` writes, without its approval gate.

    `create_intent` demands a fresh shipping quote and an approved spend, which
    is the whole reason it cannot run at checkout. These tests are about what
    `list_obligations` reports once an intent exists, so the pair is written
    directly -- with `str(order_id)`, which is the cast the join has to survive.
    """
    identity = "cjf_" + uuid.uuid4().hex
    now = time.time()
    execute(
        "INSERT INTO business_os_supplier_intents (id,connection_id,business_id,store_id,"
        "merchant_id,order_id,external_account_id,external_shop_id,idempotency_key,"
        "external_order_ref,snapshot_json,snapshot_hash,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (identity, connection, BUSINESS, STORE, str(OWNER_ID), str(order_id),
         "cj-account-1", "cj-shop-1", "idem-" + identity, "pss_" + identity,
         "{}", "hash", now))
    if state is not None:
        execute(
            "INSERT INTO business_os_supplier_outbox (intent_id,state,provider_order_id,"
            "last_error,available_at,updated_at) VALUES (?,?,?,?,?,?)",
            (identity, state, provider_order_id, last_error, now, now))
    return identity


# --------------------------------------------------------------------------
# The gap itself
# --------------------------------------------------------------------------

def test_a_paid_dropship_sale_becomes_a_visible_obligation(provider):
    # The test that would have caught gap 14. Before `list_obligations` existed,
    # nothing in the codebase could answer this question at all.
    listing_id = publish_dropship_listing(provider)
    order_id = a_buyer_pays(listing_id)

    found = obligations()

    assert len(found) == 1, (
        "A buyer paid for a dropship listing and the merchant is owed a supplier "
        f"purchase, but nothing reports it. Orders: {rows('SELECT id,listing_id,status FROM marketplace_orders')}")
    assert found[0]["order_id"] == order_id
    assert found[0]["listing_id"] == listing_id


def test_an_unplaced_obligation_says_no_supplier_order_exists(provider):
    order_id = a_buyer_pays(publish_dropship_listing(provider))
    only = obligations()[0]
    assert only["state"] == fulfillment.AWAITING_SUPPLIER_ORDER
    assert only["supplier_order_placed"] is False
    assert only["intent_id"] is None
    assert only["order_id"] == order_id


def test_the_obligation_carries_what_is_needed_to_place_the_order(provider):
    # An obligation nobody can act on is a notification, not a fix. The supplier
    # product and variant ids are what a merchant needs to buy the right thing.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-ACT"))
    only = obligations()[0]
    assert only["provider_product_id"] == "SALE-ACT"
    assert only["provider_variant_id"] == "SALE-ACT-V1"
    assert only["quantity"] == 1
    assert only["title"]


def test_the_supplier_cost_reaches_the_merchant(provider):
    # Section 27 forbids a *buyer* from seeing supplier cost. The merchant is
    # the one who has to pay it, so on this merchant-scoped read it must travel
    # -- and it must not be silently zero, which is section 8's UNKNOWN != $0.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-COST"))
    only = obligations()[0]
    assert "supplier_cost_cents" in only
    assert only["supplier_cost_cents"] == 820, (
        "The supplier cost on the obligation is not the cost the source row "
        f"holds: {rows('SELECT supplier_cost_cents FROM marketplace_product_sources')}")


# --------------------------------------------------------------------------
# Filters: a query that returns everything is as wrong as one returning nothing
# --------------------------------------------------------------------------

def test_an_unpaid_order_is_not_an_obligation(provider):
    listing_id = publish_dropship_listing(provider)
    a_buyer_pays(listing_id, status="pending_payment")
    assert obligations() == [], (
        "An order that was never paid owes the supplier nothing, and buying "
        "goods for it spends real money on a sale that may never complete")


def test_a_hand_fulfilled_listing_is_not_an_obligation(provider):
    # The merchant ships this one themselves. Its source row exists, so only the
    # fulfillment_mode filter keeps it out.
    listing_id = publish_dropship_listing(provider, "SALE-HAND")
    execute("UPDATE marketplace_product_sources SET fulfillment_mode='HAND' WHERE listing_id=?",
            (listing_id,))
    a_buyer_pays(listing_id)
    assert obligations() == []


def test_a_listing_with_no_supplier_source_is_not_an_obligation(provider):
    listing_id = publish_dropship_listing(provider, "SALE-ORPHAN")
    execute("DELETE FROM marketplace_product_sources WHERE listing_id=?", (listing_id,))
    a_buyer_pays(listing_id)
    assert obligations() == [], "A listing with no supplier cannot owe one a purchase"


def test_another_tenants_paid_sale_is_not_in_my_obligations(provider):
    listing_id = publish_dropship_listing(provider, "SALE-MINE")
    a_buyer_pays(listing_id)
    execute("UPDATE marketplace_product_sources SET supplier_connection_id=?, business_id=?, "
            "store_id=? WHERE listing_id=?", (OTHER_CONNECTION, OTHER_BUSINESS, OTHER_STORE,
                                              listing_id))
    assert obligations() == [], (
        "A paid sale sourced from another tenant's supplier connection appeared "
        "in this merchant's obligations")


def test_a_non_physical_listing_is_not_an_obligation(provider):
    # Nothing ships, so nothing is owed. `effective_listing_type` is the shared
    # derivation rather than a local reading of two columns.
    listing_id = publish_dropship_listing(provider, "SALE-DIGITAL")
    execute("UPDATE marketplace_listings SET listing_type='digital', product_type='digital' "
            "WHERE id=?", (listing_id,))
    a_buyer_pays(listing_id)
    assert obligations() == []


# --------------------------------------------------------------------------
# What happens once an intent exists
# --------------------------------------------------------------------------

def test_an_existing_intent_marks_the_obligation_placed(provider):
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-PLACED"))
    identity = an_intent_exists_for(order_id, state="LINKED", provider_order_id="CJ-7788")

    only = obligations()[0]
    assert only["supplier_order_placed"] is True
    assert only["intent_id"] == identity
    assert only["state"] == "LINKED"
    assert only["provider_order_id"] == "CJ-7788"


@pytest.mark.parametrize("state", ["READY", "SENDING", "UNKNOWN", "RECONCILE", "BLOCKED"])
def test_the_outbox_state_is_passed_through_verbatim(provider, state):
    # Not re-spelled. `dispatch` and `settle` are the only code that decides
    # these names; a second vocabulary here would be one more thing to keep in
    # step with them, and mobile's copy table is keyed on these exact strings.
    order_id = a_buyer_pays(publish_dropship_listing(provider, f"SALE-{state}"))
    an_intent_exists_for(order_id, state=state)
    assert obligations()[0]["state"] == state


def test_an_intent_without_an_outbox_row_is_unknown_not_awaiting(provider):
    # `create_intent` writes both rows in one transaction, so this should be
    # unreachable -- but the LEFT JOIN can express it, so it needs an answer,
    # and the answer must not be "no supplier order yet" beside placed=True.
    # That pairing is a contradiction, and it is the one that costs money:
    # it invites the merchant to buy the same goods a second time.
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-NOBOX"))
    an_intent_exists_for(order_id, state=None)

    only = obligations()[0]
    assert only["supplier_order_placed"] is True
    assert only["state"] == "UNKNOWN"
    assert only["state"] != fulfillment.AWAITING_SUPPLIER_ORDER


def test_a_blocked_supplier_order_carries_its_error(provider):
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-BLOCKED"))
    an_intent_exists_for(order_id, state="BLOCKED", last_error="preflight_blocked")
    only = obligations()[0]
    assert only["state"] == "BLOCKED"
    assert only["last_error"] == "preflight_blocked"


def test_the_raw_outbox_column_never_travels_beside_the_derived_state(provider):
    # `outbox_state` is popped unconditionally. The first draft of this function
    # popped it only when an intent existed, so on the unplaced path a null
    # `outbox_state` rode along beside `state` -- two spellings of one fact,
    # with a caller free to read the null one.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-RAW"))
    only = obligations()[0]
    assert "outbox_state" not in only
    assert "listing_type" in only and "product_type" not in only


def test_one_order_yields_one_obligation_not_a_fan_out(provider):
    # Both LEFT JOINs are 1:1 by unique index -- `uq_supplier_canonical_order` on
    # intents.order_id and `idx_mkt_source_listing` on sources.listing_id. This
    # is that claim measured, because a fan-out would double-count money owed.
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-ONE"))
    an_intent_exists_for(order_id, state="LINKED")
    assert len(obligations()) == 1


# --------------------------------------------------------------------------
# Things behaviour cannot see
# --------------------------------------------------------------------------

def _obligation_sql():
    """The SELECT literal `list_obligations` runs, read off its bytecode."""
    sql = [const for const in fulfillment.list_obligations.__code__.co_consts
           if isinstance(const, str) and "FROM marketplace_orders" in const]
    assert len(sql) == 1, "list_obligations no longer holds exactly one SELECT literal"
    return sql[0]


def test_the_order_id_join_casts_rather_than_comparing_integer_to_text():
    # `marketplace_orders.id` is INTEGER; `business_os_supplier_intents.order_id`
    # is TEXT because `create_intent` writes `str(order_id)`. On PostgreSQL a
    # bare `i.order_id = o.id` raises; on SQLite it matches nothing and every
    # obligation reports itself unplaced. The SQLite failure mode is silent, so
    # no behavioural test in this file can catch it -- only the literal can.
    sql = _obligation_sql()
    assert "i.order_id = CAST(o.id AS TEXT)" in sql, (
        "The intent join must cast the integer order id to text. Comparing them "
        "directly raises on PostgreSQL and silently matches nothing on SQLite, "
        "which shows every placed supplier order as unplaced.")


def test_the_query_is_scoped_by_all_three_tenancy_columns():
    # A missing predicate here leaks another merchant's sales *and* their
    # supplier costs. The cross-tenant test above covers the connection id; this
    # covers all three at once, since a single fixture cannot vary each alone.
    sql = _obligation_sql()
    for predicate in ("s.supplier_connection_id = ?", "s.business_id = ?", "s.store_id = ?"):
        assert predicate in sql, f"obligation query is not scoped by {predicate}"


def test_marketplace_orders_has_exactly_one_status_writer():
    # `LOWER(o.status)='paid'` is correct only while one writer hardcodes 'paid'.
    # A second writer spelling it 'checkout_completed' or 'succeeded' (the
    # vocabulary of seller_transactions, a different table) would make those
    # obligations invisible -- gap 14 again, narrower and harder to notice.
    with open(os.path.join(REPO, "bot.py"), encoding="utf-8") as handle:
        source = handle.read()
    writers = source.count("INSERT INTO marketplace_orders")
    assert writers == 1, (
        f"bot.py now has {writers} writers of marketplace_orders. Check each one "
        "spells paid as 'paid', or list_obligations goes blind to the others.")
    assert "VALUES (?,?,?,?,?,?,?,?, 'paid',?,?,?,?,?)" in source


# --------------------------------------------------------------------------
# Authorization, limits, and the promise not to touch the supplier
# --------------------------------------------------------------------------

# The refusal `connections.get_connection` raises. Named explicitly rather than
# widened to `ValueError`: all three of these classes derive from it, so
# `pytest.raises(ValueError)` would also pass on an ordinary bug in the query.
DENIED = (SupplierConnectionError, SupplierError, fulfillment.FulfillmentError)


def test_an_outsider_cannot_list_obligations(provider):
    a_buyer_pays(publish_dropship_listing(provider, "SALE-PRIVATE"))
    with pytest.raises(DENIED):
        obligations(actor_user_id=OUTSIDER_ID)


def test_a_guessed_connection_id_does_not_reach_another_tenant(provider):
    a_buyer_pays(publish_dropship_listing(provider, "SALE-GUESS"))
    with pytest.raises(DENIED):
        obligations(connection_id=OTHER_CONNECTION)


def test_a_nonsense_limit_is_refused_rather_than_defaulted(provider):
    with pytest.raises(fulfillment.FulfillmentError) as raised:
        obligations(limit="all of them")
    assert raised.value.code == "invalid_limit"


def test_the_limit_is_clamped_not_trusted(provider):
    # The route passes a query-string value straight through.
    for listing in range(3):
        a_buyer_pays(publish_dropship_listing(provider, f"SALE-LIM-{listing}"))
    assert len(obligations(limit=1)) == 1
    assert len(obligations(limit=100000)) == 3
    assert len(obligations(limit="2")) == 2


def test_listing_obligations_never_contacts_the_supplier(monkeypatch, provider):
    # Section 37: real supplier spend stays at $0. This read is derived from
    # rows the system already has, so any call out to CJ here would be a bug --
    # and the funding-gated `dispatch` remains the only thing that may do that.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-QUIET"))

    def explode(*args, **kwargs):
        raise AssertionError("list_obligations reached out to the supplier")

    monkeypatch.setattr(gateway, "read", explode)
    assert len(obligations()) == 1


def test_the_payload_reports_sandbox_and_that_production_is_off(provider):
    a_buyer_pays(publish_dropship_listing(provider, "SALE-SANDBOX"))
    payload = fulfillment.list_obligations(CONNECTION, BUSINESS, STORE, OWNER_ID,
                                           context=CONTEXT)
    # 1, not True: `assert_sandbox` refuses bools elsewhere in this module, so
    # the flag is the same literal shape everywhere it appears.
    assert payload["isSandbox"] == 1
    assert payload["production_fulfillment_enabled"] is False


def test_no_supplier_account_identifier_reaches_the_caller(provider):
    # Sections 27 and 95: no token, no openId, no supplier shop or account id.
    # The intent row holds `external_account_id` and `external_shop_id`; neither
    # is selected, and this is what keeps that true as columns get added.
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-SECRET"))
    an_intent_exists_for(order_id, state="LINKED")
    only = obligations()[0]
    for forbidden in ("external_account_id", "external_shop_id", "access_token",
                      "refresh_token", "openId", "open_id", "snapshot_json",
                      "snapshot_hash", "idempotency_key", "external_order_ref"):
        assert forbidden not in only, f"{forbidden} travels to the caller"


def test_the_suppliers_status_does_not_travel_under_the_payment_providers_name(provider):
    # The outbox column is `provider_status`; the wire field deliberately is
    # not. On this platform `provider_status` is Stripe's subscription status,
    # and `mobile-native/src/entitlements/__tests__/noClientTierInference.test.ts`
    # lists it among the raw membership fields no unlisted file may hold -- so
    # shipping a supplier order status under that name forces a choice between
    # exempting `api/dropshipping.ts` from the entitlement boundary and
    # weakening the guard. Neither is an acceptable price for a name collision,
    # so the SELECT aliases it. This is what stops the alias being tidied away.
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-ALIAS"))
    an_intent_exists_for(order_id, state="LINKED", provider_order_id="CJ-ORDER-1")
    only = obligations()[0]
    assert "provider_status" not in only, (
        "the obligation carries `provider_status`, which names the payment "
        "provider's subscription status everywhere else in this platform")
    assert "supplier_order_status" in only


# --------------------------------------------------------------------------
# Gap 15: an obligation a merchant can see is not yet one they can discharge
#
# Gap 14 made the obligation visible. Measuring what could then be done with
# it found four blockers, and every test below is one of them written down.
# The theme is the same in all four: a fact needed by the next step already
# existed somewhere the code had reached, and nothing carried it across.
# --------------------------------------------------------------------------

def a_blocker_on(order_id=None, **kwargs):
    """The blockers of the single obligation, or of the one for `order_id`."""
    found = obligations(**kwargs)
    if order_id is not None:
        found = [row for row in found if row["order_id"] == order_id]
    assert len(found) == 1, f"expected exactly one obligation, got {len(found)}"
    return found[0]


def test_a_shippable_paid_sale_names_no_blocker_at_all(provider):
    # The baseline the rest of this block is measured against. If a complete
    # US address on a bound shop with a known cost and a bound variant still
    # reports a blocker, every test below is measuring the fixture.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-READY"))
    only = a_blocker_on()
    assert only["blockers"] == []
    assert only["can_place_supplier_order"] is True


def test_the_obligation_reports_the_bound_variants_sku_not_the_products(provider):
    # The measured root cause of gap 15's second blocker. `importer._write_variants`
    # writes each variant's `external_sku` onto the variant row;
    # `importer.link_source` writes the *product's* onto the source row. Two
    # adjacent columns, two levels of one hierarchy, and `create_intent` matches
    # only the variant's -- so reporting the source column answered `invalid_sku`
    # when it was NULL and `product_binding_mismatch` when it was not.
    listing_id = publish_dropship_listing(provider, "SALE-SKU")
    execute("UPDATE marketplace_product_sources SET external_sku=? WHERE listing_id=?",
            ("PRODUCT-LEVEL-SKU", listing_id))
    a_buyer_pays(listing_id)
    only = a_blocker_on()
    assert only["supplier_sku"] == "SALE-SKU-SKU-1", (
        "the obligation is reporting a SKU from the wrong level of the "
        f"supplier's hierarchy: {rows('SELECT external_sku FROM marketplace_product_sources')}")
    assert "PRODUCT-LEVEL-SKU" not in json.dumps(only), (
        "the product-level SKU travels too. One obligation carrying two "
        "spellings of `the SKU` is an invitation to read the one that cannot work")


def test_a_variant_with_no_supplier_code_is_blocked_rather_than_silently_unorderable(provider):
    # What a multi-variant import reaches: `link_source` records no
    # `provider_variant_id`, `NULL = NULL` joins nothing, so the SKU is absent.
    # That is the truth about the order, and it has to be said out loud.
    listing_id = publish_dropship_listing(provider, "SALE-NOSKU")
    execute("UPDATE marketplace_listing_variants SET sku=NULL WHERE listing_id=?", (listing_id,))
    a_buyer_pays(listing_id)
    only = a_blocker_on()
    assert only["supplier_sku"] is None
    assert fulfillment.SUPPLIER_SKU_MISSING in only["blockers"]
    assert only["can_place_supplier_order"] is False


def test_an_unknown_supplier_cost_blocks_the_order_instead_of_pricing_it_at_zero(provider):
    # Section 8. A NULL cost means the import never established what this
    # variant costs; ordering at a price we cannot state is how a merchant
    # finds out the margin afterwards.
    listing_id = publish_dropship_listing(provider, "SALE-NOCOST")
    execute("UPDATE marketplace_product_sources SET supplier_cost_cents=NULL WHERE listing_id=?",
            (listing_id,))
    a_buyer_pays(listing_id)
    only = a_blocker_on()
    assert only["supplier_cost_cents"] is None
    assert fulfillment.SUPPLIER_COST_UNKNOWN in only["blockers"]


def test_a_sale_with_no_frozen_address_says_so_rather_than_waiting_forever(provider):
    # Before gap 15 this row read `awaiting` indefinitely: the obligation
    # carried no destination, so nothing could tell a merchant why their
    # supplier order was never going to be placeable.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-NOADDR"), details=None)
    only = a_blocker_on()
    assert fulfillment.DESTINATION_MISSING in only["blockers"]
    assert only["can_place_supplier_order"] is False


def test_an_order_whose_payment_record_is_gone_is_blocked_not_ready(provider):
    # The LEFT JOIN can express it, so it needs an answer, and the answer is
    # not "no blockers". An order pointing at a transaction that is not there
    # has no address, and has it for a reason worth investigating.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-ORPHAN"), transaction=False)
    only = a_blocker_on()
    assert fulfillment.DESTINATION_MISSING in only["blockers"]
    assert only["can_place_supplier_order"] is False


def test_a_sale_that_is_not_being_shipped_is_not_a_parcel_to_order(provider):
    # A physical listing offering collection is still a physical listing, and
    # still has a supplier source. What it does not have is a destination, and
    # a supplier order for it would ship goods to nobody.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-PICKUP"), kind="pickup")
    only = a_blocker_on()
    assert fulfillment.NOT_SHIPPING_LANE in only["blockers"]
    assert fulfillment.DESTINATION_MISSING not in only["blockers"], (
        "a collection order has no missing address -- it has no address to miss")


def test_an_undecided_lane_is_not_treated_as_shipping(provider):
    # `shipping_or_pickup` is what a listing offering both freezes when nothing
    # narrowed it. Reading it as shipping would order goods against a lane the
    # buyer never chose.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-BOTH"), kind="shipping_or_pickup")
    assert fulfillment.NOT_SHIPPING_LANE in a_blocker_on()["blockers"]


def test_a_buyer_outside_the_ten_region_countries_is_incomplete_not_ready(provider):
    # `marketplace_fulfillment._REGION_REQUIRED` holds ten countries, so a
    # buyer in the United Kingdom completes a valid checkout with no
    # `address_region` -- while CJ requires `shippingProvince` unconditionally.
    # This is a real paid order that genuinely cannot be placed, which is why
    # DESTINATION_INCOMPLETE is not defensive code.
    details = dict(FROZEN_DETAILS, address_country="GB", address_region="")
    a_buyer_pays(publish_dropship_listing(provider, "SALE-UK"), details=details)
    only = a_blocker_on()
    assert fulfillment.DESTINATION_INCOMPLETE in only["blockers"]
    assert fulfillment.DESTINATION_MISSING not in only["blockers"], (
        "the address is present and one field short of usable; calling it "
        "missing tells the merchant to look for something that is there")


def test_a_country_this_platform_can_spell_but_not_name_is_incomplete(provider):
    # `country_name` answers "" for a code its table does not hold, and the
    # supplier needs the name, not the code. Sending the code as the name would
    # be this repo's recurring defect in one line.
    details = dict(FROZEN_DETAILS, address_country="ZZ")
    a_buyer_pays(publish_dropship_listing(provider, "SALE-ZZ"), details=details)
    assert fulfillment.DESTINATION_INCOMPLETE in a_blocker_on()["blockers"]


def test_an_unbound_supplier_shop_blocks_every_obligation(provider):
    # A property of the connection, not of any order, and it blocks all of
    # them: `create_intent` refuses `shop_binding_required` because
    # `_validate_observed` proves a placed order came back on the shop we
    # bound, and with nothing bound there is no such proof to make.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-UNBOUND-1"))
    a_buyer_pays(publish_dropship_listing(provider, "SALE-UNBOUND-2"))
    execute("UPDATE business_os_supplier_connections SET external_shop_id='' WHERE id=?",
            (CONNECTION,))
    found = obligations()
    assert len(found) == 2
    for row in found:
        assert fulfillment.SHOP_BINDING_REQUIRED in row["blockers"], (
            "the shop binding blocks every order, so it has to be reported on "
            "every row -- a merchant reads rows, not connections")
        assert row["can_place_supplier_order"] is False


def test_an_already_placed_order_is_blocked_from_being_placed_again(provider):
    # The one blocker that is about money already spent. Ordering the same
    # goods twice is the error this list exists to prevent.
    order_id = a_buyer_pays(publish_dropship_listing(provider, "SALE-DOUBLE"))
    an_intent_exists_for(order_id, state="LINKED")
    only = a_blocker_on()
    assert fulfillment.SUPPLIER_ORDER_ALREADY_PLACED in only["blockers"]
    assert only["can_place_supplier_order"] is False
    assert only["supplier_order_placed"] is True


def test_the_blockers_are_reported_in_the_vocabularys_own_order(provider):
    # So the list reads the same way every time and the merchant is told the
    # thing to act on first, rather than whichever check happened to run first.
    listing_id = publish_dropship_listing(provider, "SALE-MANY")
    a_buyer_pays(listing_id, details=None)
    execute("UPDATE marketplace_listing_variants SET sku=NULL WHERE listing_id=?", (listing_id,))
    execute("UPDATE marketplace_product_sources SET supplier_cost_cents=NULL WHERE listing_id=?",
            (listing_id,))
    execute("UPDATE business_os_supplier_connections SET external_shop_id='' WHERE id=?",
            (CONNECTION,))
    found = a_blocker_on()["blockers"]
    assert len(found) > 1, "this test is only meaningful with several blockers"
    order = [fulfillment.BLOCKERS.index(name) for name in found]
    assert order == sorted(order), (
        f"blockers arrived out of vocabulary order: {found}")


def test_every_blocker_reported_is_one_the_vocabulary_names(provider):
    # A blocker the mobile copy map has never heard of renders as a fallback
    # string. That is the right answer to a newer server and the wrong answer
    # to a typo, so the backend may only emit names it declares.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-VOCAB"), details=None)
    for name in a_blocker_on()["blockers"]:
        assert name in fulfillment.BLOCKERS, f"{name} is not a declared blocker"


def test_readiness_is_the_absence_of_blockers_and_never_disagrees_with_them(provider):
    # One evaluator, section 61. The screen counts unorderable sales off
    # `can_place_supplier_order` rather than off `blockers.length`, so the two
    # disagreeing would put a number above a list that contradicts it.
    ready = publish_dropship_listing(provider, "SALE-INV-OK")
    blocked = publish_dropship_listing(provider, "SALE-INV-NO")
    ready_order = a_buyer_pays(ready)
    blocked_order = a_buyer_pays(blocked, details=None)
    by_order = {row["order_id"]: row for row in obligations()}
    for row in by_order.values():
        assert row["can_place_supplier_order"] == (not row["blockers"])
    assert by_order[ready_order]["can_place_supplier_order"] is True
    assert by_order[blocked_order]["can_place_supplier_order"] is False


def test_the_buyers_address_never_travels_on_an_obligation(provider):
    # The obligation is read to answer "can this be ordered?", which needs the
    # frozen record -- and the answer must travel without the record. Sections
    # 27 and 95 are about what a *buyer* sees, but a merchant fulfilment screen
    # has no use for the payments-ledger key either, and a field nothing reads
    # is a field that leaks the first time a payload is forwarded.
    a_buyer_pays(publish_dropship_listing(provider, "SALE-PRIVACY"),
                 details=dict(FROZEN_DETAILS, address_line1="1 Leak Street",
                              address_city="Leakville", contact_phone="+15550199"))
    only = a_blocker_on()
    for forbidden in ("metadata_json", "seller_transaction_id", "shipping_destination",
                      "address_line1", "contact_name", "contact_phone"):
        assert forbidden not in only, f"{forbidden} travels to the caller"
    serialized = json.dumps(only)
    for value in ("1 Leak Street", "Leakville", "+15550199", "Fixture Buyer"):
        assert value not in serialized, f"{value!r} reached the wire"


# --------------------------------------------------------------------------
# `order_destination`: the one server-side place an order becomes an address
# --------------------------------------------------------------------------

def test_the_destination_is_read_off_the_order_the_buyer_paid(provider):
    order_id = a_buyer_pays(publish_dropship_listing(provider, "DEST-OK"))
    assert fulfillment.order_destination(order_id) == {
        "shippingCountryCode": "US",
        "shippingCountry": "United States",
        "shippingProvince": "CA",
        "shippingCity": "Fixture City",
        "shippingCustomerName": "Fixture Buyer",
        "shippingAddress": "1 Fixture Way",
        "shippingZip": "94000",
        "shippingPhone": "+15550100",
    }


def test_an_absent_optional_field_is_omitted_rather_than_sent_empty(provider):
    # `_text` in `create_intent` rejects an empty string, and a blank phone
    # number is not a phone number.
    details = {key: value for key, value in FROZEN_DETAILS.items()
               if key not in ("contact_phone", "address_postal_code")}
    order_id = a_buyer_pays(publish_dropship_listing(provider, "DEST-THIN"), details=details)
    destination = fulfillment.order_destination(order_id)
    assert "shippingPhone" not in destination
    assert "shippingZip" not in destination
    assert destination["shippingAddress"] == "1 Fixture Way"


@pytest.mark.parametrize("kwargs,refusal", [
    ({"details": None}, "order_destination_missing"),
    ({"transaction": False}, "order_destination_missing"),
    ({"kind": "pickup"}, "order_not_shipping_lane"),
    ({"details": dict(FROZEN_DETAILS, address_country="GB", address_region="")},
     "order_destination_incomplete"),
])
def test_a_destination_that_cannot_be_stated_is_refused_by_name(provider, kwargs, refusal):
    # Each is a statement about the order rather than about the request, which
    # is why none of them is a 4xx the caller can fix by retrying differently.
    order_id = a_buyer_pays(publish_dropship_listing(provider, f"DEST-{refusal[-6:]}"), **kwargs)
    with pytest.raises(fulfillment.FulfillmentError) as raised:
        fulfillment.order_destination(order_id)
    assert raised.value.code == refusal


def test_an_order_that_does_not_exist_is_not_an_empty_address(provider):
    with pytest.raises(fulfillment.FulfillmentError) as raised:
        fulfillment.order_destination(999999)
    assert raised.value.code == "order_destination_missing"
    with pytest.raises(fulfillment.FulfillmentError) as raised:
        fulfillment.order_destination("not-a-number")
    assert raised.value.code == "order_not_found"


def test_creating_an_intent_takes_no_destination_from_its_caller():
    # The larger half of gap 15's fix. While `shipping_destination` was an
    # argument, a merchant-authenticated request could name any address at all
    # -- the address the buyer paid to ship to was frozen on the transaction
    # and nothing compared the two. Removing the parameter is the fix; this is
    # what stops it being added back for the convenience of a caller.
    import inspect
    parameters = inspect.signature(fulfillment.create_intent).parameters
    for name in ("shipping_destination", "destination", "address", "shipping_address"):
        assert name not in parameters, (
            f"`create_intent` accepts `{name}` again, which lets the caller "
            "redirect a parcel the buyer paid to have sent somewhere else")
