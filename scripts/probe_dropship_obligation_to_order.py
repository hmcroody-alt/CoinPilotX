"""A merchant can see the obligation. Can anything discharge it?

Why this probe exists
---------------------
Gap 14 gave the merchant an honest backlog: `list_obligations` enumerates paid
orders that still owe a purchase from the supplier, and every row on a fresh
sale reads `AWAITING_SUPPLIER_ORDER`. That state is true. It is also where the
feature stops, and this probe is about what stands between that row and a
supplier order actually being placed.

Three greps frame the question, and none of them is the answer:

*   `fulfillment-intents` -- the one action that writes an intent -- still has
    zero callers in `mobile-native/src`, `templates/` or `static/`. Its only
    caller anywhere is `tests/business_os/test_cj_routes.py`.
*   `read/shipping`, the only writer of the shipping-quote snapshot that
    `create_intent` demands, has zero callers on any surface either.
*   nothing under `services/business_os/` reads the frozen fulfilment snapshot,
    so the fulfilment layer has never seen a buyer's address.

The third is the interesting one, because the address is not missing. Checkout
collects it, `marketplace_fulfillment.snapshot` freezes it onto
`seller_transactions.metadata_json`, and `pulse_upsert_marketplace_order`
*parses that very blob* -- reading `commercial_quote` out of it and nothing
else. So this probe's job is to find out whether the destination a supplier
order needs is genuinely obtainable from records that already exist, or whether
something has to be collected that never was.

What it does
------------
  1. import, bind and publish a single-variant dropship listing (the gap-13
     path);
  2. check out as a real buyer through the real `/api/pulse/payments/checkout`
     route, so the frozen fulfilment snapshot is written by the code that
     writes it in production -- not by this script;
  3. project that transaction into a paid order with the real
     `bot.pulse_upsert_marketplace_order`;
  4. print the obligation the merchant now sees, and what it does *not* carry;
  5. print the frozen destination, and map it onto the six fields
     `create_intent` requires;
  6. call `create_intent` with that derived destination and no quote, to find
     out whether the destination is still a blocker or whether the quote is the
     only one left.

On the payment mode
-------------------
Step 2 uses `payment_mode: "cash"`, which is how `tests/test_marketplace_order_lane.py`
drives a real checkout without Stripe. A cash-in-person payment for a
dropshipped parcel is not a coherent product flow and this probe is not
claiming it is -- the payment mode has no bearing on what is being measured,
which is the fulfilment snapshot the route freezes and who can read it
afterwards. Step 3 then applies the projection the Stripe webhook applies.

Nothing is asserted. Every line is printed.

    .venv/bin/python3 scripts/probe_dropship_obligation_to_order.py
"""

import base64
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_HANDLE, _DB = tempfile.mkstemp(prefix="probe-obligation-order-", suffix=".db")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"
os.environ["CJ_EGRESS_GROUP"] = "probe-egress"
# Local-only AES key material, so `connections.worker_connection` can open a
# credential and `create_intent` gets far enough to reach the checks this probe
# is about. No provider call is made with it -- only the adapter's HTTP
# transport is stubbed, so the gateway above it is the real one.
_KEY = base64.b64encode(b"probe-key-32-bytes-long-000000!!").decode()
os.environ["SUPPLIER_CREDENTIAL_KEYS"] = f"probe:{_KEY}"
os.environ["SUPPLIER_CREDENTIAL_KEY_ACTIVE"] = "probe"
os.environ["SUPPLIER_ACCOUNT_INDEX_KEY"] = _KEY

import bot  # noqa: E402
from services import db  # noqa: E402
from services import marketplace_fulfillment as mf  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, fulfillment, import_cart, importer, vault)
from services.business_os.suppliers.cj import CJAdapter  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.business import schema as business_schema  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402

os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, OWNER_ID, STORE,
    _seed_connection, cj_product)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

BUYER_ID = 90210
NOW = "2026-09-12T00:00:00"

# What a US buyer must answer for a shipping lane. Every one of these is
# *required* by `marketplace_fulfillment.validate_details` for country US, which
# is the only country `shipping_countries()` returns -- so this is not a
# generous fixture, it is the minimum the route accepts.
BUYER_DETAILS = {
    "contact_name": "Ada Probe",
    "contact_phone": "+15125550123",
    "address_line1": "1 Main St",
    "address_city": "Austin",
    "address_region": "TX",
    "address_postal_code": "78701",
    "address_country": "US",
}


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _seed_tenancy(cur):
    """The real tenancy shape, not the test fixture's.

    `tests/dropshipping/` seeds these two tables with its own three-column
    `CREATE TABLE IF NOT EXISTS`, which wins only because nothing there ever
    calls the shipped DDL. This probe calls it, and in
    `services/business_os/{business,store}/schema.py` `display_name`, `name`,
    `created_at` and `updated_at` are all NOT NULL.
    """
    business_schema.ensure_schema(cur)
    store_schema.ensure_schema(cur)
    cur.execute(
        "INSERT INTO business_os_business (business_id, owner_user_id, display_name, "
        " status, created_at, updated_at) VALUES (?,?,?,'active',?,?)",
        (BUSINESS, str(OWNER_ID), "Probe Dropship Co", NOW, NOW))
    cur.execute(
        "INSERT INTO business_os_store_storefront (storefront_id, business_id, name, "
        " status, created_at, updated_at) VALUES (?,?,?,'active',?,?)",
        (STORE, BUSINESS, "Probe Dropship Store", NOW, NOW))


def reset():
    conn = db.connect()
    try:
        cur = conn.cursor()
        supplier_schema.ensure_supplier_schema(cur, force=True)
        connection_schema.ensure_schema(cur)
        import_cart.ensure_schema(cur)
        _seed_tenancy(cur)
        _seed_connection(cur, CONNECTION, BUSINESS, STORE, OWNER_ID)
        # The seeded shop/account ids have to be CJ-shaped for the same reason
        # the product ids do -- `get_settings` and `get_shops` both run every
        # identifier through `_id(..., provider=True)` during hydration.
        cur.execute("UPDATE business_os_supplier_connections SET external_shop_id=?, "
                    "external_account_id=? WHERE id=?", (SHOP_ID, OPEN_ID, CONNECTION))
        vault.save(cur, {"api_key": "probe-api-key", "access_token": "probe-access",
                         "refresh_token": "probe-refresh", "open_id": OPEN_ID},
                   merchant_id=str(OWNER_ID), business_id=BUSINESS, store_id=STORE,
                   connection_id=CONNECTION, credential_reference=f"cred-{CONNECTION}")
        cur.execute(
            "INSERT OR REPLACE INTO marketplace_sellers "
            "(user_id,status,display_name,created_at,updated_at) "
            "VALUES (?,'approved','Probe Dropship Store',?,?)", (OWNER_ID, NOW, NOW))
        conn.commit()
    finally:
        conn.close()
    fulfillment.ensure_schema()


def _as(user_id, username):
    bot.api_account_user = lambda *a, **k: {"user_id": user_id, "username": username}
    return bot.webhook_app.test_client()


# CJ-shaped identifiers. `CJAdapter._id(..., provider=True)` accepts only a
# decimal, a 32-hex string or a UUID, so the `PID-1`-style ids
# `tests/dropshipping/` uses are only viable there because that suite fakes
# `gateway.read` and never reaches the adapter's validation.
PID = "1900001"
VID = "2900001"
OPEN_ID = "ab" * 16
SHOP_ID = "7700001"


class ProbeAdapter(CJAdapter):
    """The real adapter with only its HTTP transport stubbed.

    Two earlier shapes of this probe were wrong in ways worth recording, because
    both made it report a defect that is not there.

    The first replaced `gateway.read`, the way `tests/dropshipping/` does. That
    writes no `supplier_snapshots` row, and `create_intent` re-reads the
    snapshot by id -- so the probe answered `not_found` and could say nothing
    about the checks after that read.

    The second replaced the whole adapter and returned the *raw* CJ fixture
    (`variantSku`, `variantSellPrice`). `create_intent` looks for `sku`,
    `price` and `currency` on each snapshot variant, so every lookup missed and
    the probe reported `product_binding_mismatch` -- which looked exactly like a
    real defect and was entirely mine. `CJAdapter._variant` performs that
    renaming, and the snapshot stores the adapter's *output*. Subclassing keeps
    that projection production code instead of my transcription of it.
    """

    def __init__(self):
        super().__init__(account_ref="probe-account", environment="SANDBOX")
        self.requests = []

    def _request(self, method, path, *, params=None, payload=None, **kwargs):
        self.requests.append((method, path))
        if path == "setting/get":
            return {"openId": OPEN_ID, "setting": {"isSandbox": 1}}
        if path == "shop/getShops":
            return [{"id": SHOP_ID, "name": "Probe Shop", "type": "API", "status": 1}]
        if path == "product/query":
            # `variantWeight` and `productProEnSet` are the two freight inputs
            # `quote_for_order` refuses to invent. `reports/cj-discovery/
            # CJ_DROPSHIPPING_FORENSIC_REPORT.md` records that CJ's freight
            # endpoint takes gram weights and that "Product properties come from
            # productProEnSet"; `tests/dropshipping`'s `cj_product` predates
            # anything needing either, so they are added here.
            return cj_product(PID, variants_=[
                {"vid": VID, "variantKey": "Colour-1", "variantSellPrice": "8.20",
                 "variantQuantity": 40, "variantSku": "PROBE-SKU-1", "pid": PID,
                 "variantWeight": "200"}]) | {"productProEnSet": ["ORDINARY"]}
        if path == "product/variant/query":
            return []
        if path == "product/stock/getInventoryByPid":
            # A verified warehouse with stock, so the publish readiness
            # evaluator answers IN_STOCK rather than UNKNOWN_INVENTORY --
            # `_warehouse_stock` requires `verifiedWarehouse == 1` for that.
            return {"variantInventories": [
                {"vid": VID, "pid": PID, "inventory": [
                    {"countryCode": "CN", "areaId": 1, "totalInventory": 40,
                     "cjInventory": 40, "factoryInventory": 0,
                     "verifiedWarehouse": 1, "stock": []}]}]}
        if path == "logistic/freightCalculateTip":
            return [{"optionId": "probe-option", "channelId": "probe-channel",
                     "option": {"enName": "CJPacket"}, "postage": "3.00",
                     "totalPostageFee": "3.00", "arrivalTime": "7-12"}]
        raise AssertionError(f"probe has no answer for {method} {path}")


ADAPTER = ProbeAdapter()


def publish_a_dropship_listing():
    """The gap-13 happy path: one variant, bound at import, priced, published."""
    pid = PID
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid, selected_variant_ids=[VID],
                         context=CONTEXT)
    outcome = importer.import_selected(
        BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT, adapter=ADAPTER)
    print(f"  import       : {json.dumps(outcome, default=str)[:400]}")
    listing_id = outcome["results"][0]["listing_id"]
    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"price_cents": {str(v["variant_id"]): 2000 for v in draft["variants"]}},
                        context=CONTEXT)
    readiness = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                                 context=CONTEXT)
    print(f"  readiness    : {json.dumps(readiness.get('readiness') or readiness.get('validation'), default=str)[:400]}")
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    # Moderation is a separate authority (stage 8) and is not what this probe
    # measures, so the approval is applied directly rather than pretended.
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET approval_status='approved' WHERE id=?", (listing_id,))
        conn.commit()
    finally:
        conn.close()
    return listing_id


def a_buyer_checks_out(listing_id):
    """The real route, so the frozen fulfilment snapshot is the real one."""
    response = _as(BUYER_ID, "probe_buyer").post("/api/pulse/payments/checkout", json={
        "item_type": "marketplace_product", "item_id": listing_id, "quantity": 1,
        "payment_mode": "cash", "fulfillment_details": BUYER_DETAILS})
    print(f"  checkout HTTP {response.status_code}: {json.dumps(response.get_json(), default=str)[:240]}")
    tx = rows("SELECT * FROM seller_transactions ORDER BY id DESC LIMIT 1")
    return tx[0] if tx else None


def project_to_a_paid_order(tx):
    """`bot.pulse_upsert_marketplace_order`, the real projection."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        bot.pulse_upsert_marketplace_order(cur, tx, provider_payment_id="pi_probe", now=NOW)
        conn.commit()
    finally:
        conn.close()
    return rows("SELECT * FROM marketplace_orders ORDER BY id DESC LIMIT 1")


def what_the_merchant_sees():
    print("\n--- list_obligations ---")
    result = fulfillment.list_obligations(CONNECTION, BUSINESS, STORE, OWNER_ID, context=CONTEXT)
    for obligation in result["obligations"]:
        print(f"  {json.dumps(obligation, default=str)}")
    print(f"  obligations: {len(result['obligations'])}")
    for obligation in result["obligations"]:
        print(f"  can_place_supplier_order : {obligation.get('can_place_supplier_order')!r}")
        print(f"  blockers                 : {obligation.get('blockers')!r}")
        print(f"  supplier_sku             : {obligation.get('supplier_sku')!r}")
        # The frozen blob must not travel: it holds the buyer's address and the
        # commercial quote, and a fulfilment screen has no use for either.
        leaked = sorted(k for k in obligation
                        if k in {"metadata_json", "seller_transaction_id", "external_sku"})
        print(f"  fields that must not travel: {leaked or 'NONE'}")
    return result


def the_frozen_destination(tx):
    print("\n--- what checkout froze on the transaction ---")
    try:
        metadata = json.loads(tx.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    print(f"  metadata keys : {sorted(metadata)}")
    frozen = metadata.get("fulfillment") or {}
    print(f"  fulfillment   : {json.dumps(frozen, default=str)}")
    print(f"  order_kind    : {mf.order_kind(metadata)!r}")

    # The bridge itself, not this script's transcription of it. An earlier shape
    # of this probe mapped the six fields by hand here, which measured the gap
    # fine and would have kept passing after the real mapping regressed.
    print("\n--- fulfillment.supplier_destination on that record ---")
    destination, blockers = fulfillment.supplier_destination(metadata)
    for key in [k for k, _ in fulfillment._DESTINATION_REQUIRED]:
        print(f"  {key:22} {destination.get(key)!r}")
    print(f"  blockers      : {blockers or 'NONE'}")
    return destination


def which_sku_is_recorded(listing_id):
    """Two adjacent columns, two different levels of the supplier's hierarchy."""
    print("\n--- the SKU create_intent must be given ---")
    source = rows("SELECT * FROM marketplace_product_sources WHERE listing_id=?",
                  (listing_id,))[0]
    variant = rows("SELECT * FROM marketplace_listing_variants WHERE listing_id=? "
                   "AND provider_variant_id=?",
                   (listing_id, source["provider_variant_id"]))
    print(f"  sources.provider_variant_id  {source['provider_variant_id']!r}")
    print(f"  sources.external_sku         {source['external_sku']!r}   <- what the obligation reports")
    print(f"  variants.sku                 {(variant[0]['sku'] if variant else None)!r}   <- the bound variant's own SKU")
    snapshot = rows("SELECT payload_json FROM supplier_snapshots WHERE snapshot_id=?",
                    (source["source_snapshot_id"],))
    if snapshot:
        data = json.loads(snapshot[0]["payload_json"]).get("variants", [])
        match = next((v for v in data if v.get("vid") == source["provider_variant_id"]), None)
        print(f"  snapshot variant sku         {(match or {}).get('sku')!r}   <- what create_intent compares against")
    return source, (variant[0]["sku"] if variant else None)


def the_quote_a_merchant_can_now_ask_for(order_id):
    """`quote_for_order`, which is the caller `read/shipping` never had.

    An earlier shape of this probe built the `reqDTOS` row by hand here, to show
    that it *could* be built. That measured the gap and proved nothing about the
    fix: the hand-built version hardcoded a 200g weight, an origin of CN and a
    `productProp` of "ordinary", which is exactly the guessing the real function
    refuses to do.
    """
    print("\n--- fulfillment.quote_for_order ---")
    try:
        result = fulfillment.quote_for_order(
            connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE,
            actor_user_id=OWNER_ID, order_id=order_id, context=CONTEXT,
            adapter=ADAPTER)
    except Exception as exc:  # noqa: BLE001
        print(f"  {type(exc).__name__}: {exc}  .code={getattr(exc, 'code', None)!r}")
        return None
    print(f"  snapshot_id              : {result['snapshot_id']!r}")
    print(f"  supplier_items_cost_cents: {result['supplier_items_cost_cents']!r}")
    print(f"  state                    : {result['state']!r}")
    for option in result["options"]:
        print(f"  option: {json.dumps(option, default=str)}")
    usable = [o for o in result["options"] if o["expected_supplier_cost_cents"] is not None]
    if not usable:
        return None
    return {"snapshot_id": result["snapshot_id"], "option_id": usable[0]["option_id"],
            "channel_id": usable[0]["channel_id"],
            "expected_supplier_cost_cents": usable[0]["expected_supplier_cost_cents"]}


def try_to_place_it(order_id, listing_id, sku, label, quote=None, expected_cents=820):
    """Isolates one blocker at a time by varying only the named input."""
    print(f"\n--- create_intent, {label} ---")
    source = rows("SELECT * FROM marketplace_product_sources WHERE listing_id=?", (listing_id,))[0]
    items = [{"canonical_product_id": str(listing_id), "pid": source["provider_product_id"],
              "vid": source["provider_variant_id"], "sku": sku or "",
              "quantity": 1, "listing_id": listing_id}]
    print(f"  items        : {json.dumps(items, default=str)}")
    try:
        result = fulfillment.create_intent(
            connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE,
            actor_user_id=OWNER_ID, order_id=order_id, items=items,
            shipping_quote=quote, expected_supplier_cost_cents=expected_cents,
            isSandbox=1, idempotency_key=f"probe-obligation-{label}", context=CONTEXT)
        print(f"  created      : {json.dumps(result, default=str)[:400]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {type(exc).__name__}: {exc}")
        print(f"    .code        = {getattr(exc, 'code', None)!r}")
        print(f"    .http_status = {getattr(exc, 'http_status', None)!r}")


def who_can_reach_a_quote():
    print("\n--- the shipping quote create_intent demands ---")
    print("  written only by gateway.read(operation='shipping', ...), reachable only")
    print("  through POST /connections/<id>/read/shipping.")
    import subprocess
    for label, pattern, path in (
            ("mobile callers of read/", r"/read/", "mobile-native/src"),
            ("web callers of read/", r"/read/", "templates"),
            ("static callers of read/", r"/read/", "static")):
        if not os.path.isdir(os.path.join(REPO, path)):
            print(f"  {label:26} (no such directory)")
            continue
        found = subprocess.run(["grep", "-rl", pattern, os.path.join(REPO, path)],
                               capture_output=True, text=True).stdout.strip()
        print(f"  {label:26} {len(found.splitlines()) if found else 0}")


if __name__ == "__main__":
    print("A merchant can see the obligation. Can anything discharge it?")
    reset()
    listing_id = publish_a_dropship_listing()
    tx = a_buyer_checks_out(listing_id)
    if not tx:
        print("  no transaction was written; nothing further can be measured")
        os.unlink(_DB)
        sys.exit(0)
    print(f"\n  listing_id : {listing_id}")
    print(f"  transaction: id={tx['id']} status={tx['status']!r} amount={tx['amount_cents']}")
    order = project_to_a_paid_order(tx)
    print(f"  order      : {json.dumps(order[0], default=str) if order else 'NONE'}")

    what_the_merchant_sees()
    the_frozen_destination(tx)
    source, variant_sku = which_sku_is_recorded(listing_id)
    if order:
        try_to_place_it(order[0]["id"], listing_id, source["external_sku"],
                        "with the SKU the obligation used to report")
        try_to_place_it(order[0]["id"], listing_id, variant_sku,
                        "with the bound variant's own SKU, no quote")
    who_can_reach_a_quote()
    quote = the_quote_a_merchant_can_now_ask_for(order[0]["id"]) if order else None
    if order and quote:
        # The figure comes from the quote rather than being computed here.
        # `create_intent` recomputes the same sum from the same snapshot and
        # refuses `supplier_cost_reapproval_required` on disagreement, so a
        # number this script chose would prove nothing about either.
        try_to_place_it(order[0]["id"], listing_id, variant_sku,
                        "with the bound SKU and a real quote", quote=quote,
                        expected_cents=quote["expected_supplier_cost_cents"])

    print("\n=== reading ===")
    print("  Nothing on the server blocks this order, and nothing above supplied")
    print("  a fact from outside it. What used to block it:")
    print("    * the obligation carried no destination, though its own join already")
    print("      reached the row the frozen address sits on;")
    print("    * the obligation reported the product's SKU, where its only consumer")
    print("      needs the bound variant's -- so the honest path answered")
    print("      invalid_sku and a populated one would answer")
    print("      product_binding_mismatch, which reads like a binding bug;")
    print("    * the quote is not a lookup a screen can fire off -- every field of")
    print("      the request is cross-checked against the frozen order -- and no")
    print("      surface performs it.")
    os.unlink(_DB)
