"""A buyer pays for a dropshipped product. Then what?

Why this probe exists
---------------------
Gap 13 ended with a merchant able to bind a variant and publish a dropship
listing. That listing is now for sale. The next question is the one the whole
feature exists to answer: when somebody buys it, does anything at all happen on
the supplier side?

Six greps say no. `fulfillment.create_intent` is the only writer of
`business_os_supplier_intents`; its only production call site is the
`fulfillment-intents` action in `services/business_os_supplier_routes.py`; that
action has zero callers in `mobile-native/src`, `templates/` or `static/`;
`worker.py` only claims intents that already exist; `supplier_worker.py` is not
in the Procfile; and `bot.py` contains no reference to
`marketplace_product_sources`, `supplier_binding`, `get_product_binding` or
`fulfillment_mode`.

A grep proves a caller is absent. It does not prove what the merchant is left
with, and it does not tell me what shape the fix has to be -- so this probe
runs the real thing:

  1. import, bind and publish a single-variant dropship listing (the gap-13
     path, which now works);
  2. write the paid `marketplace_orders` row exactly as
     `bot.pulse_upsert_marketplace_order` projects one from a paid transaction;
  3. print every supplier-side record that exists afterwards;
  4. call `create_intent` with everything a caller could actually hold at
     payment time, and print the refusal verbatim.

Step 4 is the important one. The interesting answer is not "nobody calls it" --
it is *why* nobody could sensibly call it from the payment path, because that
decides whether the fix is "call it at checkout" or something else.

Nothing is asserted. Every line is printed.

    .venv/bin/python3 scripts/probe_dropship_paid_order_fulfillment.py
"""

import json
import os
import sys
import tempfile
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_HANDLE, _DB = tempfile.mkstemp(prefix="probe-paid-order-", suffix=".db")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, fulfillment, gateway, import_cart, importer)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OWNER_ID, STORE,
    _seed_connection, _seed_tenancy, cj_product)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

BUYER_ID = 90210

# Verbatim from bot.init_db(). The probe cannot import bot.py -- 111k lines and
# a Flask app at module scope -- so the columns are copied. If they drift, the
# INSERT below fails loudly rather than measuring a table of its own invention.
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


def reset():
    open(_DB, "w").close()
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
        connection_schema.ensure_schema(cur)
        import_cart.ensure_schema(cur)
        cur.execute(MARKETPLACE_ORDERS_DDL)
        _seed_tenancy(cur)
        _seed_connection(cur, CONNECTION, BUSINESS, STORE, OWNER_ID)
        conn.commit()
    finally:
        conn.close()
    fulfillment.ensure_schema()


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def publish_a_dropship_listing():
    """The gap-13 happy path: one variant, bound, priced, published."""
    provider = FakeProvider()
    gateway.read = provider

    pid = "PROBE-SALE"
    variants_ = [
        {"vid": f"{pid}-V{i}", "variantKey": f"Colour-{i}", "variantSellPrice": "8.20",
         "variantQuantity": 40, "variantSku": f"{pid}-SKU-{i}", "pid": pid}
        for i in range(1, 3)
    ]
    provider.add(cj_product(pid, variants_=variants_))

    # One variant only, so `importer` binds the source row at import and the
    # listing is publishable without the merchant answering anything.
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid, selected_variant_ids=[f"{pid}-V1"],
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    listing_id = result["results"][0]["listing_id"]

    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    drafts.update_draft(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
        fields={"price_cents": {str(v["variant_id"]): 2000 for v in draft["variants"]}},
        context=CONTEXT)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    return listing_id


def a_buyer_pays(listing_id, *, quantity=1, unit_price_cents=2000):
    """Project a paid transaction into an order, as the payment path does.

    Mirrors `bot.pulse_upsert_marketplace_order`: status 'paid', the quantity
    and unit price read off the frozen commercial quote, amount including
    neither tax nor shipping here because the probe is not measuring pricing.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_orders (seller_transaction_id,buyer_user_id,seller_user_id,"
            "listing_id,quantity,unit_price_cents,amount_cents,currency,status,payment_provider,"
            "provider_payment_id,created_at,paid_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'paid','stripe',?,?,?,?)",
            (4242, BUYER_ID, OWNER_ID, listing_id, quantity, unit_price_cents,
             unit_price_cents * quantity, "USD", "pi_probe",
             "2026-09-12T00:00:00", "2026-09-12T00:00:00", "2026-09-12T00:00:00"))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def what_the_supplier_side_holds(order_id):
    print("\n--- supplier-side records after the sale ---")
    for table in ("business_os_supplier_intents", "business_os_supplier_outbox",
                  "business_os_supplier_sync_jobs"):
        try:
            count = rows(f"SELECT count(*) AS n FROM {table}")[0]["n"]
            print(f"  {table:38}: {count} row(s)")
        except Exception as exc:  # noqa: BLE001 - an absent table is a measurement
            print(f"  {table:38}: {type(exc).__name__}: {exc}")
    linked = rows("SELECT * FROM business_os_supplier_intents WHERE order_id=?", (str(order_id),))
    print(f"  intents naming this order              : {len(linked)}")


def try_to_create_the_intent(order_id, listing_id):
    """Call it with what a payment-time caller could actually hold.

    At payment time the system has: the order, its listing, its quantity, the
    binding (`gateway.get_product_binding`), and the buyer's shipping address.
    It does not have a shipping quote -- nobody asked CJ what freight costs to
    this address -- and it does not have a merchant-approved supplier cost.

    So this call is deliberately made with the fields that do exist and `None`
    for the two that cannot, and the refusal is the point.
    """
    binding = None
    try:
        # Positional, and no actor: this is the worker-side lookup, which is
        # exactly why it is the one a payment hook would reach for.
        binding = gateway.get_product_binding(CONNECTION, BUSINESS, STORE, str(listing_id))
    except Exception as exc:  # noqa: BLE001
        print(f"\n  get_product_binding                    : {type(exc).__name__}: {exc}")
    print(f"\n  binding the checkout path could resolve: {json.dumps(binding, default=str)}")

    items = [{"canonical_product_id": str(listing_id),
              "pid": (binding or {}).get("pid") or "PROBE-SALE",
              "vid": (binding or {}).get("vid") or "PROBE-SALE-V1",
              "sku": (binding or {}).get("sku") or "PROBE-SALE-SKU-1",
              "quantity": 1}]
    # No destination is built here any more. When this probe was written a caller
    # supplied one, which is the hole gap 15 closed: `create_intent` now reads the
    # address off the buyer's frozen transaction and takes no such argument.
    print("\n--- create_intent, called the way a payment hook would ---")
    print("  Note: this probe reuses the *import pipeline* suite's connection")
    print("  fixture, which is seeded to import products, not to fulfil them. If")
    print("  the refusal below is `credential_unusable`, that is the fixture's")
    print("  vault blob, not a claim about production. The refusals that matter")
    print("  for the fix are enumerated after this, off the function itself.")
    try:
        result = fulfillment.create_intent(
            connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE,
            actor_user_id=OWNER_ID, order_id=order_id, items=items,
            shipping_quote=None,                    # nobody quoted freight
            expected_supplier_cost_cents=None,      # nobody approved a spend
            isSandbox=1,                            # literal 1; assert_sandbox refuses True
            idempotency_key="probe-" + uuid.uuid4().hex,
            context=CONTEXT)
        print(f"  OK: {json.dumps(result, default=str)}")
    except Exception as exc:  # noqa: BLE001 - the refusal is the measurement
        print(f"  {type(exc).__name__}: {exc}")
        for attribute in ("code", "http_status", "status"):
            if hasattr(exc, attribute):
                print(f"    .{attribute} = {getattr(exc, attribute)!r}")


def what_create_intent_demands():
    """The refusals `create_intent` can emit, read off its own bytecode.

    Same technique as the `SUPPLIER_VARIANT_UNBOUND` copy pin: the docstring
    above a function is a claim, the constants inside it are a measurement. The
    two numbers matter as much as the codes -- a quote freshness window and an
    exact cost equality are what decide whether a payment hook could ever call
    this, and neither is something a payment hook holds.
    """
    consts = fulfillment.create_intent.__code__.co_consts
    codes = sorted({c for c in consts if isinstance(c, str)
                    and c.islower() and "_" in c and " " not in c
                    and not c.startswith(("shipping", "variant", "canonical"))})
    numbers = sorted({c for c in consts if isinstance(c, int) and c not in (0, 1)})
    print("\n--- what create_intent refuses with ---")
    for code in codes:
        print(f"  {code}")
    print(f"  bare integer constants (freshness window, limits): {numbers}")


def can_anything_enumerate_it():
    """Is there a function that answers "which orders owe a supplier purchase"?

    Measured off the module rather than asserted: every public callable in
    `fulfillment`, and whether it takes an `intent_id`. A reader keyed on
    `intent_id` can only answer questions about an intent that already exists,
    which is no help to a merchant who has none.
    """
    import inspect
    print("\n--- what the fulfilment module can be asked ---")
    for name, value in sorted(vars(fulfillment).items()):
        if name.startswith("_") or not callable(value) or not inspect.isfunction(value):
            continue
        if getattr(value, "__module__", None) != fulfillment.__name__:
            continue
        params = list(inspect.signature(value).parameters)
        keyed = "intent_id" in params or "intent" in params
        print(f"  {name:26} ({', '.join(params) or '-'})"
              f"{'   <- keyed on an existing intent' if keyed else ''}")


def what_the_merchant_can_now_see():
    """`list_obligations`, the thing the module could not be asked before."""
    print("\n--- list_obligations ---")
    try:
        result = fulfillment.list_obligations(CONNECTION, BUSINESS, STORE, OWNER_ID,
                                              context=CONTEXT)
    except Exception as exc:  # noqa: BLE001
        print(f"  {type(exc).__name__}: {exc}")
        return
    for obligation in result["obligations"]:
        print(f"  {json.dumps(obligation, default=str)}")
    print(f"  obligations: {len(result['obligations'])}")
    print(f"  isSandbox  : {result['isSandbox']}")


if __name__ == "__main__":
    print("A published, bound dropship listing is bought. What reaches the supplier?")
    reset()
    listing_id = publish_a_dropship_listing()
    order_id = a_buyer_pays(listing_id)

    print(f"\n  listing_id : {listing_id}")
    print(f"  order      : {json.dumps(rows('SELECT * FROM marketplace_orders WHERE id=?', (order_id,))[0], default=str)}")
    print(f"  source row : {json.dumps(rows('SELECT listing_id, provider_product_id, provider_variant_id, fulfillment_mode FROM marketplace_product_sources WHERE listing_id=?', (listing_id,)), default=str)}")

    what_the_supplier_side_holds(order_id)
    try_to_create_the_intent(order_id, listing_id)
    what_the_supplier_side_holds(order_id)
    what_create_intent_demands()
    can_anything_enumerate_it()
    what_the_merchant_can_now_see()

    print("\n=== reading ===")
    print("  The sale is a complete, paid record. The supplier side is empty, and")
    print("  the refusal above says what a payment-time caller is missing.")
    os.unlink(_DB)
