"""Import writes a DRAFT against the canonical ledger, or it writes nothing.

What this file is defending
---------------------------
Everything between "the merchant tapped Import" and "a draft exists":

* **The trust boundary.** The client names cart rows. Every economic fact —
  cost, inventory, title, variant identity — is re-read from the provider. A
  client that sends a cost must not be able to set one, and the test for that is
  the one that survives someone adding a well-meaning convenience parameter.
* **Import never publishes.** ``status`` and ``approval_status`` are SQL
  literals in ``_create_draft_listing`` precisely so that no argument can move
  them. If a draft can reach ``published`` without ``drafts.publish``, the
  merchant's storefront gained a product they never saw.
* **Duplicate prevention.** A double-tapped Import button reopens one draft. Two
  listings for one supplier product means two prices, two edits, and a customer
  buying the stale one.
* **Honest partial success.** Ten items, one provider failure, nine drafts. The
  fresh connection per item is the mechanism; a shared transaction silently
  rolls the nine back and the test that catches it is the one that asserts on
  the *neighbour*, not on the failure.
* **Tenancy.** A valid store plus a guessed connection id must not reach another
  merchant's supplier account.

Why this file runs alone
------------------------
It sets ``DATABASE_URL`` to its own temp file at import time, before
``services.db`` computes ``IS_POSTGRES``. Sharing a pytest process with another
suite that does the same means one of the two loses. Run it as its own process:

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_import_pipeline.py

On the provider seam
--------------------
``gateway.read`` is replaced, not the adapter. The adapter path (credential
vault, refresh lease, quota) has its own tests and dragging it in here would
mean every assertion about *importing* depends on a credential fixture. What is
replaced is exactly one function, and what it returns is raw provider-shaped
JSON — so ``normalize`` still runs for real on realistic input, which is where
the interesting failures live.
"""

import json
import os
import sys
import tempfile
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

# Before services.db is imported anywhere: it reads DATABASE_URL at import to
# decide IS_POSTGRES, and per-call to find the sqlite path.
_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-test-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, gateway, import_cart, importer, normalize, pricing)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

OWNER_ID = "4001"
OTHER_OWNER_ID = "4002"
BUSINESS, STORE, CONNECTION = "biz-a", "store-a", "conn-a"
OTHER_BUSINESS, OTHER_STORE, OTHER_CONNECTION = "biz-b", "store-b", "conn-b"
CONTEXT = {"account_status": "active", "access_enabled": True}


# ---------------------------------------------------------------------------
# Provider payloads
# ---------------------------------------------------------------------------

def cj_product(pid="PID-1", *, title="Cotton Tee", variants_=None, media=True,
               category="Apparel", description="Soft combed cotton."):
    payload = {
        "pid": pid,
        "productNameEn": title,
        "categoryName": category,
        "description": description,
        "sellPrice": "8.20",
        "variants": variants_ if variants_ is not None else [
            {"vid": f"{pid}-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
             "variantQuantity": 40, "variantSku": f"{pid}-SKU-1"},
            {"vid": f"{pid}-V2", "variantKey": "Black-M", "variantSellPrice": "8.60",
             "variantQuantity": 12, "variantSku": f"{pid}-SKU-2"},
        ],
    }
    # CJ stamps the parent pid onto every variant, and `gateway.bind_product`
    # checks it -- `any(v["vid"] == vid and v["pid"] == pid ...)` -- so a fake
    # that omitted it made every binding answer `variant_mismatch`. Filled in
    # rather than required of each caller: a variant without its own pid is not a
    # payload CJ produces, so no test should be able to construct one by accident.
    for variant in payload["variants"]:
        variant.setdefault("pid", pid)
    if media:
        payload["productImage"] = f"https://cdn.example.com/{pid}.jpg"
        payload["productImageSet"] = [f"https://cdn.example.com/{pid}-2.jpg"]
    return payload


class FakeProvider:
    """Stands in for ``gateway.read``. Records calls; can be told to fail."""

    def __init__(self):
        self.products = {}
        self.inventory = {}
        self.fail = {}          # pid -> SupplierError code for the product read
        self.fail_inventory = set()
        self.calls = []

    def add(self, payload, inventory=None):
        self.products[payload["pid"]] = payload
        if inventory is not None:
            self.inventory[payload["pid"]] = inventory
        return payload["pid"]

    def __call__(self, operation, *, business_id, store_id, actor_user_id,
                 connection_id, params=None, context=None, adapter=None):
        params = params or {}
        pid = params.get("pid")
        self.calls.append((operation, pid))
        if operation == "product":
            if pid in self.fail:
                raise SupplierError(self.fail[pid], http_status=503)
            if pid not in self.products:
                raise SupplierError("product_unavailable", http_status=404)
            return {"data": self.products[pid], "cached": False,
                    "snapshot_id": f"snap-{pid}"}
        if operation == "variants":
            return {"data": self.products.get(pid, {}).get("variants", []), "cached": False}
        if operation == "inventory":
            if pid in self.fail_inventory:
                raise SupplierError("provider_unavailable", http_status=503)
            return {"data": self.inventory.get(pid, []), "cached": False}
        if operation == "search":
            return {"data": {"list": list(self.products.values()),
                             "total": len(self.products)}, "cached": False}
        raise SupplierError("unknown_operation", http_status=404)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_tenancy(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business (
        business_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_os_store_storefront (
        storefront_id TEXT PRIMARY KEY, business_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business_members (
        business_id TEXT NOT NULL, user_id TEXT NOT NULL,
        role TEXT NOT NULL, status TEXT NOT NULL)""")
    for business, store, owner in ((BUSINESS, STORE, OWNER_ID),
                                   (OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID)):
        conn.execute("INSERT INTO business_os_business (business_id, owner_user_id, status) "
                     "VALUES (?,?,'active')", (business, owner))
        conn.execute("INSERT INTO business_os_store_storefront (storefront_id, business_id, status) "
                     "VALUES (?,?,'active')", (store, business))


def _seed_connection(conn, connection_id, business, store, owner):
    now = "2026-09-07T00:00:00Z"
    later = "2099-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO business_os_supplier_connections "
        "(id, merchant_id, business_id, store_id, provider, connection_type, "
        " external_account_id, external_shop_id, status, credential_reference, "
        " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
        "VALUES (?,?,?,?,'CJ','API_KEY',?,?,'CONNECTED',?,?,?,'UNKNOWN',?,?)",
        (connection_id, owner, business, store, f"acct-{connection_id}",
         f"shop-{connection_id}", f"cred-{connection_id}", later, later, now, now))


@pytest.fixture(autouse=True)
def database(monkeypatch):
    # A file, not :memory:. The importer opens a fresh connection per item on
    # purpose, and every :memory: connection would be a different database.
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
        conn.commit()
    finally:
        conn.close()
    import_cart.ensure_schema()
    gateway.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(importer.gateway, "read", fake)
    return fake


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def add_to_cart(pid, *, selected=None, business=BUSINESS, store=STORE,
                connection=CONNECTION, actor=OWNER_ID):
    return import_cart.add_item(business, store, actor, connection,
                                external_product_id=pid, selected_variant_ids=selected,
                                context=CONTEXT)


def run_import(*, item_ids=None, rule=None, business=BUSINESS, store=STORE,
               connection=CONNECTION, actor=OWNER_ID):
    return importer.import_selected(business, store, actor, connection,
                                    item_ids=item_ids, pricing_rule=rule, context=CONTEXT)


# ---------------------------------------------------------------------------
# Import Cart
# ---------------------------------------------------------------------------

def test_adding_the_same_product_twice_updates_one_row(provider):
    add_to_cart("PID-1", selected=["PID-1-V1"])
    add_to_cart("PID-1", selected=["PID-1-V1", "PID-1-V2"])
    cart = import_cart.get_cart(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    assert len(cart["items"]) == 1
    assert cart["items"][0]["selected_variant_ids"] == ["PID-1-V1", "PID-1-V2"]


def test_cart_is_scoped_to_one_tenant(provider):
    add_to_cart("PID-1")
    add_to_cart("PID-9", business=OTHER_BUSINESS, store=OTHER_STORE,
                connection=OTHER_CONNECTION, actor=OTHER_OWNER_ID)
    mine = import_cart.get_cart(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    assert [i["external_product_id"] for i in mine["items"]] == ["PID-1"]


def test_a_guessed_connection_id_does_not_reach_another_tenant(provider):
    # A legitimate store plus someone else's connection id. Authorization on the
    # store alone would let this through.
    with pytest.raises(Exception) as exc:
        add_to_cart("PID-1", connection=OTHER_CONNECTION)
    assert getattr(exc.value, "http_status", None) in (403, 404)


def test_an_outsider_cannot_read_the_cart(provider):
    add_to_cart("PID-1")
    with pytest.raises(Exception) as exc:
        import_cart.get_cart(BUSINESS, STORE, "9999", CONNECTION, context=CONTEXT)
    assert getattr(exc.value, "http_status", None) in (401, 403, 404)


def test_an_unusable_variant_id_is_refused_not_silently_dropped(provider):
    # Dropping one id from a selection of five imports four variants and reports
    # success; the merchant finds out when a customer cannot buy a size.
    with pytest.raises(SupplierError):
        add_to_cart("PID-1", selected=["V1", "has space"])


def test_cart_cache_is_display_only(provider):
    # The cached product influences what the cart shows. It must not influence
    # what the import writes: the import re-reads the provider.
    provider.add(cj_product("PID-1", title="Real Title From Provider"))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id="PID-1",
                         product={"title": "Client Supplied Lie",
                                  "external_product_id": "PID-1",
                                  "variants": [{"external_variant_id": "V1",
                                                "cost_cents": 1}]},
                         context=CONTEXT)
    run_import()
    listing = rows("SELECT title FROM marketplace_listings")[0]
    assert listing["title"] == "Real Title From Provider"


# ---------------------------------------------------------------------------
# Import creates a draft, never a published listing
# ---------------------------------------------------------------------------

def test_import_creates_a_draft_listing(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    result = run_import()

    assert result["imported"] == 1
    assert result["published"] is False
    assert result["results"][0]["outcome"] == importer.IMPORTED

    listing = rows("SELECT * FROM marketplace_listings")[0]
    assert listing["status"] == "draft"
    assert listing["approval_status"] == "pending_review"
    assert listing["title"] == "Cotton Tee"
    assert listing["seller_user_id"] in (int(OWNER_ID), OWNER_ID)


def test_import_never_produces_a_public_listing(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    statuses = {r["status"] for r in rows("SELECT status FROM marketplace_listings")}
    assert statuses == {"draft"}
    assert not (statuses & {"published", "live", "active"})


def test_import_does_not_seed_the_public_price_label_with_supplier_cost(provider):
    # price_label is storefront prose. Seeding it from cost prints the
    # merchant's own buying price on their shop.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    label = rows("SELECT price_label FROM marketplace_listings")[0]["price_label"]
    assert not label
    assert "8.20" not in str(label)


def test_import_does_not_seed_a_stock_count_either(provider):
    # The same rule as the price above, for the field that did not follow it.
    # `quantity` was a literal 0 in the insert, and 0 is not "unknown" -- it is
    # the merchant's own count, asserting an empty shelf. An import has counted
    # nothing, and on a dropship listing the merchant never counts anything:
    # the units are in the supplier's warehouse and arrive at publish time via
    # `_sellable_units`.
    #
    # Measured in production before this was fixed: all seven physical drafts
    # carried quantity=0, so every one of them told its seller "Out of stock --
    # hidden / Restock". Restock what? Nobody had counted them.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    listing = rows("SELECT quantity FROM marketplace_listings")[0]
    assert listing["quantity"] is None, "an uncounted import claimed a count of zero"


def test_an_uncounted_import_reads_as_uncounted_not_sold_out(provider):
    # The column is the mechanism; this is the sentence the seller is shown.
    # Both verdicts stop checkout -- that part was never in question. The
    # difference is whether the seller is asked for a number or sent to a
    # supplier.
    from services.business_os.marketplace import listing_readiness

    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    verdict = listing_readiness.evaluate(rows("SELECT * FROM marketplace_listings")[0])

    assert "UNKNOWN_INVENTORY" in verdict["warnings"]
    assert "OUT_OF_STOCK" not in verdict["warnings"]
    assert verdict["checkout_ready"] is False


def test_import_persists_the_cover_image_on_the_listing_row(provider):
    # `_validate` refuses a product with no media because a listing with no
    # image is a black card in the grid. That guard was defeated by the insert
    # it protects: media went only into listing_metadata_json, so every reader
    # of the cover_image_url *column* -- the dropshipping products list, the
    # merchant's store list, the buyer grid -- got NULL and drew the black card
    # anyway. Only readers that derive the cover from the metadata, like
    # get_draft, ever saw an image.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()

    listing = rows("SELECT cover_image_url, listing_metadata_json "
                   "FROM marketplace_listings")[0]
    assert listing["cover_image_url"], "imported listing has no cover image"
    # The column and the metadata are two copies of one fact; a reader must not
    # be able to pick the one that disagrees.
    media = json.loads(listing["listing_metadata_json"])["media"]
    assert listing["cover_image_url"] == media[0]


def test_the_imported_products_list_shows_a_cover_image(provider):
    # The assertion that actually matches what the merchant sees. `list_drafts`
    # now falls back to the metadata media when the column is empty, so this no
    # longer doubles as the column's guard -- the test above is the one that
    # fails if `_insert_listing` stops writing `cover_image_url`. What this one
    # still defends is the surface: whatever the import wrote, the products list
    # must be able to find it.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()

    listed = drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                context=CONTEXT)["items"][0]
    assert listed["cover_image_url"], "products list would render a blank tile"


def test_import_writes_one_variant_row_per_provider_variant(provider):
    provider.add(cj_product("PID-1", variants_=[
        {"vid": f"PID-1-V{i}", "variantKey": f"Black-{size}", "variantSellPrice": "8.20",
         "variantQuantity": 10}
        for i, size in enumerate(["S", "M", "L", "XL", "XXL", "3XL"])]))
    add_to_cart("PID-1")
    run_import()
    listing_id = rows("SELECT id FROM marketplace_listings")[0]["id"]
    written = rows("SELECT * FROM marketplace_listing_variants WHERE listing_id=?", (listing_id,))
    # Six provider variants, six rows. One row means variant_key collapsed them.
    assert len(written) == 6
    assert len({v["provider_variant_id"] for v in written}) == 6


def test_import_maps_the_listing_to_its_supplier_product(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    source = rows("SELECT * FROM marketplace_product_sources")
    assert len(source) == 1
    assert source[0]["provider_product_id"] == "PID-1"
    assert source[0]["supplier_connection_id"] == CONNECTION
    listing_id = rows("SELECT id FROM marketplace_listings")[0]["id"]
    assert source[0]["listing_id"] == listing_id


def test_one_selected_variant_is_recorded_as_the_variant_orders_are_placed_for(provider):
    # `fulfillment.create_intent` resolves every line through
    # `gateway.get_product_binding`, which raises `product_binding_required` when
    # `provider_variant_id` is NULL. Until this was written the importer never set
    # it, so every imported listing was one nothing could ship -- measured on
    # production listing 14, live and moderator-approved with the column NULL.
    #
    # One chosen variant is not a choice made on the merchant's behalf. It is the
    # only thing this listing can be.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1", selected=["PID-1-V2"])
    run_import()
    assert rows("SELECT provider_variant_id FROM marketplace_product_sources")[0] \
        == {"provider_variant_id": "PID-1-V2"}


def test_several_selected_variants_leave_the_binding_unmade(provider):
    # With two chosen there genuinely is a question about which variant a buyer
    # receives, this import has no answer to it, and inventing one would ship a
    # stranger whichever variant we guessed. Left NULL, and publication refuses
    # the listing by name (`SUPPLIER_VARIANT_UNBOUND`) rather than putting an
    # unshippable product on sale.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    assert rows("SELECT provider_variant_id FROM marketplace_product_sources")[0] \
        == {"provider_variant_id": None}


def test_a_successful_import_clears_its_cart_row(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    cart = import_cart.get_cart(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    assert cart["items"] == []


def test_only_the_selected_variants_are_imported(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1", selected=["PID-1-V2"])
    run_import()
    listing_id = rows("SELECT id FROM marketplace_listings")[0]["id"]
    written = rows("SELECT provider_variant_id FROM marketplace_listing_variants "
                   "WHERE listing_id=?", (listing_id,))
    assert [v["provider_variant_id"] for v in written] == ["PID-1-V2"]


def test_selecting_variants_the_provider_no_longer_lists_fails_the_item(provider):
    # Importing all of them instead would substitute a different product
    # configuration for the one the merchant chose.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1", selected=["PID-1-V-GONE"])
    result = run_import()
    assert result["results"][0]["outcome"] == importer.NO_VARIANTS
    assert rows("SELECT id FROM marketplace_listings") == []


# ---------------------------------------------------------------------------
# The trust boundary
# ---------------------------------------------------------------------------

def test_import_re_reads_the_provider_rather_than_trusting_the_cart(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    provider.calls.clear()
    run_import()
    assert ("product", "PID-1") in provider.calls, \
        "import must re-fetch; a cached cart row is not an authority"


def test_import_selected_accepts_no_economic_input_from_the_caller():
    # The structural half of the trust boundary: there is no parameter through
    # which a client could assert a cost, a stock level or a title. A convenience
    # parameter added later fails here before it can reach a price.
    import inspect
    accepted = set(inspect.signature(importer.import_selected).parameters)
    forbidden = {"cost_cents", "cost", "price_cents", "price", "retail_cents",
                 "title", "description", "stock", "stock_quantity", "inventory",
                 "variants", "product", "products", "media", "supplier_cost_cents"}
    assert not (accepted & forbidden), sorted(accepted & forbidden)
    assert accepted == {"business_id", "store_id", "actor_user_id", "connection_id",
                        "item_ids", "pricing_rule", "context", "adapter"}


# ---------------------------------------------------------------------------
# Unknown cost is not zero, unknown stock is not out of stock
# ---------------------------------------------------------------------------

def test_unknown_supplier_cost_is_stored_as_null_not_zero(provider):
    provider.add(cj_product("PID-1", variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantQuantity": 10}]))
    add_to_cart("PID-1")
    run_import()
    variant = rows("SELECT cost_cents FROM marketplace_listing_variants")[0]
    assert variant["cost_cents"] is None, "unknown cost became a number"
    source = rows("SELECT supplier_cost_cents FROM marketplace_product_sources")[0]
    assert source["supplier_cost_cents"] is None


def test_a_product_with_unknown_cost_reports_unknown_margin(provider):
    provider.add(cj_product("PID-1", variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantQuantity": 10}]))
    add_to_cart("PID-1")
    run_import()
    listing_id = rows("SELECT id FROM marketplace_listings")[0]["id"]
    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    states = {v["margin_state"] for v in draft["variants"]}
    assert states == {pricing.UNKNOWN}
    assert pricing.HEALTHY not in states


def test_an_inventory_outage_does_not_mark_variants_out_of_stock(provider):
    # An empty shelf looks like a normal bad day; nobody pages anyone about it.
    provider.add(cj_product("PID-1", variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20"}]))
    provider.fail_inventory.add("PID-1")
    add_to_cart("PID-1")
    run_import()
    variant = rows("SELECT stock_state FROM marketplace_listing_variants")[0]
    assert variant["stock_state"] != "OUT_OF_STOCK"
    assert variant["stock_state"] == "UNKNOWN"


def test_a_confirmed_zero_is_out_of_stock(provider):
    provider.add(cj_product("PID-1", variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
         "variantQuantity": 0}]))
    add_to_cart("PID-1")
    run_import()
    assert rows("SELECT stock_state FROM marketplace_listing_variants")[0]["stock_state"] == "OUT_OF_STOCK"


def test_low_stock_is_stored_as_confirmed_in_stock(provider):
    # LOW_STOCK is a display refinement of IN_STOCK and must collapse *upward*.
    # The storage layer only knows three states, and its defensive coercion sends
    # any word it does not recognise to UNKNOWN — so an importer that wrote the
    # provider's own vocabulary straight through would turn every nearly-sold-out
    # variant into "we do not know", which is not sellable and blocks publication.
    provider.add(cj_product("PID-1", variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
         "variantQuantity": 3}]))
    add_to_cart("PID-1")
    run_import()
    variant = rows("SELECT stock_state, stock_quantity FROM marketplace_listing_variants")[0]
    assert variant["stock_state"] == "IN_STOCK"
    assert variant["stock_quantity"] == 3


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

def test_importing_the_same_supplier_product_twice_reopens_one_listing(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    first = run_import()
    add_to_cart("PID-1")
    second = run_import()

    assert first["results"][0]["outcome"] == importer.IMPORTED
    assert second["results"][0]["outcome"] == importer.ALREADY_EXISTS
    assert second["results"][0]["listing_id"] == first["results"][0]["listing_id"]
    assert len(rows("SELECT id FROM marketplace_listings")) == 1
    assert len(rows("SELECT listing_id FROM marketplace_product_sources")) == 1


def test_a_duplicate_import_does_not_revert_merchant_edits(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    listing_id = run_import()["results"][0]["listing_id"]
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"title": "My Own Title"}, context=CONTEXT)
    add_to_cart("PID-1")
    run_import()
    assert rows("SELECT title FROM marketplace_listings")[0]["title"] == "My Own Title"


def test_two_tenants_may_import_the_same_supplier_product(provider):
    # The identity is per merchant. A shared catalogue product is not a global
    # singleton and one merchant importing it must not block another.
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    add_to_cart("PID-1", business=OTHER_BUSINESS, store=OTHER_STORE,
                connection=OTHER_CONNECTION, actor=OTHER_OWNER_ID)
    result = run_import(business=OTHER_BUSINESS, store=OTHER_STORE,
                        connection=OTHER_CONNECTION, actor=OTHER_OWNER_ID)
    assert result["results"][0]["outcome"] == importer.IMPORTED
    assert len(rows("SELECT id FROM marketplace_listings")) == 2


# ---------------------------------------------------------------------------
# Honest partial success
# ---------------------------------------------------------------------------

def test_one_provider_failure_does_not_discard_its_neighbours(provider):
    provider.add(cj_product("PID-1"))
    provider.add(cj_product("PID-3"))
    provider.fail["PID-2"] = "provider_unavailable"
    for pid in ("PID-1", "PID-2", "PID-3"):
        add_to_cart(pid)
    result = run_import()

    outcomes = {r["external_product_id"]: r["outcome"] for r in result["results"]}
    assert outcomes["PID-1"] == importer.IMPORTED
    assert outcomes["PID-2"] == importer.PROVIDER_UNAVAILABLE
    assert outcomes["PID-3"] == importer.IMPORTED
    assert result["imported"] == 2
    # The neighbours survived the failure's rollback. A shared transaction here
    # would leave zero listings and still report two imports.
    titles = {r["title"] for r in rows("SELECT title FROM marketplace_listings")}
    assert len(titles) == 1 and len(rows("SELECT id FROM marketplace_listings")) == 2


def test_a_failed_item_stays_in_the_cart(provider):
    provider.add(cj_product("PID-1"))
    provider.fail["PID-2"] = "provider_unavailable"
    add_to_cart("PID-1")
    add_to_cart("PID-2")
    run_import()
    remaining = import_cart.get_cart(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    assert [i["external_product_id"] for i in remaining["items"]] == ["PID-2"]


def test_a_failed_item_writes_no_partial_listing(provider):
    provider.fail["PID-2"] = "provider_unavailable"
    add_to_cart("PID-2")
    run_import()
    assert rows("SELECT id FROM marketplace_listings") == []
    assert rows("SELECT listing_id FROM marketplace_product_sources") == []


def test_per_item_outcomes_are_named_not_a_single_boolean(provider):
    provider.add(cj_product("PID-1"))
    provider.fail["PID-2"] = "provider_unavailable"
    add_to_cart("PID-1")
    add_to_cart("PID-2")
    result = run_import()
    assert set(result["counts"]) <= set(importer.OUTCOMES)
    for entry in result["results"]:
        assert entry["outcome"] in importer.OUTCOMES


def test_a_product_with_no_usable_media_is_refused(provider):
    # A listing with no image is a black card in the marketplace grid.
    payload = cj_product("PID-1", media=False)
    payload["productImage"] = "http://cdn.example.com/insecure.jpg"
    provider.add(payload)
    add_to_cart("PID-1")
    result = run_import()
    assert result["results"][0]["outcome"] == importer.NO_MEDIA
    assert rows("SELECT id FROM marketplace_listings") == []


def test_a_product_with_no_variants_is_refused(provider):
    provider.add(cj_product("PID-1", variants_=[]))
    add_to_cart("PID-1")
    assert run_import()["results"][0]["outcome"] == importer.NO_VARIANTS


def test_an_empty_cart_is_a_refusal_not_a_silent_success(provider):
    with pytest.raises(SupplierError) as exc:
        run_import()
    assert exc.value.code == "import_cart_empty"


# ---------------------------------------------------------------------------
# Pricing at import
# ---------------------------------------------------------------------------

def test_a_pricing_rule_proposes_retail_but_cost_is_still_recorded(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1", selected=["PID-1-V1"])
    run_import(rule={"type": pricing.MULTIPLIER, "value": 2})
    variant = rows("SELECT price_cents, cost_cents FROM marketplace_listing_variants")[0]
    assert variant["cost_cents"] == 820
    assert variant["price_cents"] == 1640


def test_no_pricing_rule_leaves_variants_unpriced(provider):
    provider.add(cj_product("PID-1"))
    add_to_cart("PID-1")
    run_import()
    assert all(v["price_cents"] is None
               for v in rows("SELECT price_cents FROM marketplace_listing_variants"))


def test_a_rule_cannot_price_a_variant_whose_cost_is_unknown(provider):
    provider.add(cj_product("PID-1", variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantQuantity": 5}]))
    add_to_cart("PID-1")
    run_import(rule={"type": pricing.COST_PLUS_FIXED, "value": 500})
    variant = rows("SELECT price_cents FROM marketplace_listing_variants")[0]
    assert variant["price_cents"] is None, "an unknown cost was priced from zero"
