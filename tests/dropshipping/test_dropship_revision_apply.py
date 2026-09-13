"""A supplier read reaching a live listing, against a real database. §23/§24.

What this file is defending
---------------------------
``test_dropship_revisions.py`` tests the planners, which are pure and therefore
cheap to test exhaustively. It cannot catch any of the things that actually go
wrong once a decision becomes an UPDATE, and those are the expensive failures:

* **An outage must not delist the catalogue.** A provider that times out teaches
  us nothing. ``lifecycle.inventory_available`` returns False for a NULL
  quantity, so a read that blanked ``stock_quantity`` and a read that wrote 0
  both take the product off sale — and only one of them is honest. This is the
  defect the whole planner/applier split exists to make readable, so it is
  asserted against the database rather than against a returned dict.
* **``marketplace_listings.quantity`` is a ledger, not a mirror.** The cart
  decrements it per unit reserved and credits it back on release. Assigning the
  supplier's count over it silently releases every outstanding reservation, which
  is indistinguishable from a restock right up until two buyers are sold the last
  one unit. The test for this holds a reservation across a sync.
* **Repricing a variant without moving ``price_label`` charges the old price.**
  The merchant's records and the buyer's card come from different columns, and
  nothing but this pairing joins them.
* **The production payload shape is not the shape the other suites use.**
  Everything else here fakes ``gateway.read`` and feeds raw CJ JSON. The worker
  feeds ``cj.CJAdapter._product``'s *projection*, whose keys are different
  (``price``/``vid``, not ``variantSellPrice``/``vid`` with CJ's envelope). A
  normalizer that quietly stopped reading one of those shapes would break §23 in
  production while every existing test stayed green, so both shapes are applied
  here and asserted to produce the same cents.

Why this file runs alone
------------------------
Same reason as its neighbours: it binds ``DATABASE_URL`` to its own temp file at
import, before ``services.db`` computes ``IS_POSTGRES``. One file, one process.

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_revision_apply.py

On identifiers
--------------
The provider ids here are decimal strings, not the ``PID-1`` the sibling suites
use. That is not cosmetic: ``cj.ID_PATTERN`` accepts only decimal digits, a
32-char hex string or a UUID, so ``PID-1`` cannot survive the real adapter. The
sibling suites get away with it because they replace ``gateway.read`` and the
adapter never runs. The moment a test wants the true production projection it has
to use ids CJ could actually have issued.
"""

import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-revapply-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    cj, drafts, gateway, import_cart, importer, normalize, revisions, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from services.marketplace_supplier_schema import (  # noqa: E402
    MODE_DROPSHIP, MODE_STOCKED, STOCK_IN_STOCK, STOCK_OUT_OF_STOCK, STOCK_UNKNOWN,
    SYNC_STALE, SYNC_SYNCED)
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

OWNER_ID = "4001"
BUSINESS, STORE, CONNECTION = "biz-a", "store-a", "conn-a"
CONTEXT = {"account_status": "active", "access_enabled": True}

PID = "2000000001"
VID = "3000000001"
VID_2 = "3000000002"


# ---------------------------------------------------------------------------
# Provider payloads
# ---------------------------------------------------------------------------

def cj_product(pid=PID, *, cost="8.20", second_cost="8.60", variants_=None):
    """Raw CJ product JSON, the shape ``gateway.read`` hands back."""
    payload = {
        "pid": pid,
        "productNameEn": "Cotton Tee",
        "categoryName": "Apparel",
        "description": "Soft combed cotton.",
        "sellPrice": cost,
        "productImage": f"https://cdn.example.com/{pid}.jpg",
        "productImageSet": [f"https://cdn.example.com/{pid}-2.jpg"],
        "variants": variants_ if variants_ is not None else [
            {"vid": VID, "variantKey": "Black-S", "variantSellPrice": cost,
             "variantQuantity": 40, "variantSku": "SKU-1"},
            {"vid": VID_2, "variantKey": "Black-M", "variantSellPrice": second_cost,
             "variantQuantity": 12, "variantSku": "SKU-2"},
        ],
    }
    for variant in payload["variants"]:
        variant.setdefault("pid", pid)
    return payload


def cj_inventory(pid=PID, *, rows=None):
    """Raw CJ inventory rows, as a bare list.

    A list rather than ``{"variantInventories": [...]}`` because that is what
    reaches ``normalize``: ``gateway.read("inventory")`` hands back
    ``{"data": [...]}`` and the caller passes the ``data``. ``_cj_inventory``
    unwraps ``data``/``result``/``content`` but not ``variantInventories``, so a
    payload wrapped in CJ's own key normalizes to ``{}`` — silently, since an
    empty reading is indistinguishable from a provider with nothing to say.
    """
    return rows if rows is not None else [
        {"vid": VID, "pid": pid, "totalInventoryNum": 40, "countryCode": "CN"},
        {"vid": VID_2, "pid": pid, "totalInventoryNum": 12, "countryCode": "CN"},
    ]


def unreadable_inventory(vid=VID, pid=PID):
    """A provider that answered, but without a usable count."""
    return [{"vid": vid, "pid": pid, "totalInventoryNum": None, "stockStatus": "unknown"}]


def counted_inventory(quantity, vid=VID, pid=PID):
    return [{"vid": vid, "pid": pid, "totalInventoryNum": quantity}]


def adapter_projection(payload):
    """What ``worker._read_job`` actually passes to the applier in production.

    ``cj.CJAdapter._product`` is reached without ``__init__`` on purpose. The
    constructor wants a credential vault and a quota ledger; the projection wants
    neither, and building the real thing here would make an assertion about
    *normalization* depend on a credential fixture. The method under test is a
    pure function of its argument.
    """
    class _Bare(cj.CJAdapter):
        def __init__(self):  # noqa: D107 - deliberately no credential setup
            pass

    return _Bare()._product(payload, detail=True)


class FakeProvider:
    """Stands in for ``gateway.read`` during the import that sets the stage."""

    def __init__(self):
        self.products = {}
        self.inventory = {}

    def add(self, payload, inventory=None):
        self.products[payload["pid"]] = payload
        self.inventory[payload["pid"]] = inventory if inventory is not None else cj_inventory(payload["pid"])
        return payload["pid"]

    def __call__(self, operation, *, business_id, store_id, actor_user_id,
                 connection_id, params=None, context=None, adapter=None):
        params = params or {}
        pid = params.get("pid")
        if operation == "product":
            if pid not in self.products:
                raise SupplierError("product_unavailable", http_status=404)
            return {"data": self.products[pid], "cached": False, "snapshot_id": f"snap-{pid}"}
        if operation == "variants":
            return {"data": self.products.get(pid, {}).get("variants", []), "cached": False}
        if operation == "inventory":
            return {"data": self.inventory.get(pid, {}), "cached": False}
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
    conn.execute("INSERT INTO business_os_business (business_id, owner_user_id, status) "
                 "VALUES (?,?,'active')", (BUSINESS, OWNER_ID))
    conn.execute("INSERT INTO business_os_store_storefront (storefront_id, business_id, status) "
                 "VALUES (?,?,'active')", (STORE, BUSINESS))


def _seed_connection(conn):
    now, later = "2026-09-07T00:00:00Z", "2099-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO business_os_supplier_connections "
        "(id, merchant_id, business_id, store_id, provider, connection_type, "
        " external_account_id, external_shop_id, status, credential_reference, "
        " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
        "VALUES (?,?,?,?,'CJ','API_KEY',?,?,'CONNECTED',?,?,?,'UNKNOWN',?,?)",
        (CONNECTION, OWNER_ID, BUSINESS, STORE, f"acct-{CONNECTION}",
         f"shop-{CONNECTION}", f"cred-{CONNECTION}", later, later, now, now))


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
        _seed_connection(conn)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def listing_row(listing_id):
    return rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]


def variant_rows(listing_id):
    return rows(f"SELECT * FROM {variants.VARIANT_TABLE} WHERE listing_id=? ORDER BY id",
                (listing_id,))


def source_row(listing_id):
    return rows(f"SELECT * FROM {variants.SOURCE_TABLE} WHERE listing_id=?", (listing_id,))[0]


def bound_variant(listing_id):
    source = source_row(listing_id)
    for row in variant_rows(listing_id):
        if str(row.get("provider_variant_id")) == str(source.get("provider_variant_id")):
            return row
    raise AssertionError("listing has no bound variant")


def set_store_policy(**fields):
    conn = db.connect()
    try:
        result = store_policy.set_policy(conn, BUSINESS, STORE, **fields)
        conn.commit()
        return result
    finally:
        conn.close()


def import_one(provider, *, selected=(VID,), pid=PID, payload=None, rule=None):
    """Import one product and return its listing id.

    A single selected variant, because a bound listing is the only kind §23/§24
    can move: ``drafts._sold_variant`` resolves the ledger through
    ``provider_variant_id``, and an unbound listing has no unit count to revise.
    """
    provider.add(payload if payload is not None else cj_product(pid))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid, selected_variant_ids=list(selected),
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      pricing_rule=rule, context=CONTEXT)
    created = [r for r in result.get("results", []) if r.get("listing_id")]
    assert created, f"import produced no listing: {result}"
    return int(created[0]["listing_id"])


def publish(listing_id):
    return drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)


def apply_read(kind, payload, *, resource_id=PID):
    return revisions.apply_supplier_read(
        connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE,
        kind=kind, resource_id=resource_id, payload=payload)


# ---------------------------------------------------------------------------
# The stage is real
# ---------------------------------------------------------------------------

def test_the_fixture_really_produces_a_bound_priced_listing(provider):
    """Guards every other test in this file.

    If the import stopped binding a variant or stopped pricing one, the stock and
    cost tests below would still pass — against a listing where there was nothing
    to revise. A revision suite whose subject is inert reports success for a
    pipeline that does nothing, so the subject is asserted before it is used.
    """
    listing_id = import_one(provider)
    bound = bound_variant(listing_id)

    assert bound["cost_cents"] == 820
    assert bound["price_cents"] and bound["price_cents"] > bound["cost_cents"]
    assert source_row(listing_id)["fulfillment_mode"] == MODE_DROPSHIP


# ---------------------------------------------------------------------------
# §24 stock
# ---------------------------------------------------------------------------

def test_an_unreadable_inventory_read_keeps_the_count_and_the_listing_on_sale(provider):
    """The defect this module exists to prevent, asserted against the database.

    ``lifecycle.inventory_available`` is False for a NULL quantity, so blanking
    ``stock_quantity`` and writing 0 have the same visible effect: the product
    comes off sale. One of those is a supplier saying "sold out" and the other is
    a provider timing out, and a merchant whose catalogue empties during an
    outage cannot tell which happened.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    before_units = listing_row(listing_id)["quantity"]

    out = apply_read("inventory", unreadable_inventory())

    bound = bound_variant(listing_id)
    assert bound["stock_state"] == STOCK_UNKNOWN
    assert bound["stock_quantity"] == 40, "an unreadable read must not erase a real count"
    assert listing_row(listing_id)["quantity"] == before_units, \
        "an outage must not take the listing off sale"
    assert source_row(listing_id)["sync_state"] == SYNC_STALE
    assert "STOCK_UNREADABLE" in "".join(out["attention"])


def test_an_unreadable_read_does_not_advance_the_synced_stamp(provider):
    """``stock_synced_at`` answers "when did we last learn this variant's stock".

    A read that taught us nothing must leave it alone, or a stale-stock sweep
    reads a fresh timestamp over an unknown count and concludes the row is
    current.
    """
    listing_id = import_one(provider)
    apply_read("inventory", cj_inventory())
    stamped = bound_variant(listing_id)["stock_synced_at"]
    assert stamped, "a successful read must stamp the row"

    apply_read("inventory", unreadable_inventory())

    assert bound_variant(listing_id)["stock_synced_at"] == stamped


def test_a_supplier_sell_out_zeroes_the_listing(provider):
    listing_id = import_one(provider)
    publish(listing_id)

    out = apply_read("inventory", counted_inventory(0))

    bound = bound_variant(listing_id)
    assert bound["stock_state"] == STOCK_OUT_OF_STOCK
    assert bound["stock_quantity"] == 0
    assert listing_row(listing_id)["quantity"] == 0
    assert "SUPPLIER_OUT_OF_STOCK" in "".join(out["attention"])


def test_a_restock_credits_the_difference_and_spares_a_held_reservation(provider):
    """``marketplace_listings.quantity`` is moved by a delta, never assigned.

    The cart decrements this column per unit reserved
    (``marketplace_cart_routes``: ``quantity=quantity-?``) and credits it back on
    release, so it is a ledger of what is still *available*, not a mirror of what
    the supplier holds. Assigning the supplier's count over it releases every
    outstanding reservation at once — invisibly, because the number goes *up*,
    which looks exactly like the restock that is also happening.

    Here the supplier goes 40 -> 50 while a buyer holds 5. Available must end at
    45: the ten new units arrive, the five held ones stay held. An assignment
    would write 50 and sell those five twice.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    assert listing_row(listing_id)["quantity"] == 40

    # A buyer reserves 5, exactly as the cart does it.
    conn = db.connect()
    try:
        conn.cursor().execute(
            "UPDATE marketplace_listings SET quantity=quantity-? WHERE id=? AND quantity>=?",
            (5, listing_id, 5))
        conn.commit()
    finally:
        conn.close()
    assert listing_row(listing_id)["quantity"] == 35

    apply_read("inventory", counted_inventory(50))

    assert bound_variant(listing_id)["stock_quantity"] == 50
    assert listing_row(listing_id)["quantity"] == 45, \
        "the held 5 must survive the sync; 50 here means reservations were released"


def test_the_last_few_units_are_synced_like_any_other_count(provider):
    """A real CJ read of 3 units normalizes to LOW_STOCK, not IN_STOCK.

    ``normalize.stock_state`` returns ``LOW_STOCK`` below five, so this is the
    path every nearly-sold-out product takes — and it was landing in the applier's
    unreadable branch, which meant the ledger stayed at 40 while the supplier had
    3. Asserted end to end rather than only on the planner, because the value that
    matters is the one a buyer's checkout is compared against.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    assert listing_row(listing_id)["quantity"] == 40

    out = apply_read("inventory", counted_inventory(3))

    bound = bound_variant(listing_id)
    assert bound["stock_state"] == STOCK_IN_STOCK
    assert bound["stock_quantity"] == 3
    assert listing_row(listing_id)["quantity"] == 3, \
        "the ledger must come down to the real count before the last units oversell"
    assert source_row(listing_id)["sync_state"] == SYNC_SYNCED
    assert out["attention"] == []


def test_a_variant_the_read_did_not_mention_is_left_alone(provider):
    """Omission is a short answer, not a sell-out.

    Providers routinely return only the warehouses that have rows, which is the
    rule ``normalize.apply_inventory`` already states. Treating an absent variant
    as zero would delist half a catalogue on a partial response.
    """
    listing_id = import_one(provider, selected=(VID, VID_2))
    before = {r["provider_variant_id"]: dict(r) for r in variant_rows(listing_id)}

    apply_read("inventory", counted_inventory(7))

    after = {r["provider_variant_id"]: dict(r) for r in variant_rows(listing_id)}
    assert after[VID]["stock_quantity"] == 7
    assert after[VID_2]["stock_quantity"] == before[VID_2]["stock_quantity"]
    assert after[VID_2]["updated_at"] == before[VID_2]["updated_at"], \
        "an unmentioned variant must not even be touched"


def test_the_ledger_never_goes_negative(provider):
    """A delta plus an oversold ledger can compute below zero.

    Reservations can legitimately exceed the supplier's new count — the buyer
    holds units the supplier no longer has — and a negative ``quantity`` is not a
    number any buyer surface is prepared for. The floor is applied as its own
    statement because ``MAX``/``GREATEST`` are spelled differently on SQLite and
    PostgreSQL.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    conn = db.connect()
    try:
        conn.cursor().execute("UPDATE marketplace_listings SET quantity=1 WHERE id=?", (listing_id,))
        conn.commit()
    finally:
        conn.close()

    # Supplier drops 40 -> 2, a delta of -38 against a ledger holding 1.
    apply_read("inventory", counted_inventory(2))

    assert listing_row(listing_id)["quantity"] == 0


# ---------------------------------------------------------------------------
# §23 cost
# ---------------------------------------------------------------------------

def test_a_cost_rise_reprices_the_variant_and_the_buyers_price_label(provider):
    """The two halves keep price in different columns and both must move.

    ``marketplace_listings.price_label`` is the number a stranger's card is
    charged; every buyer surface parses it and none of them read
    ``marketplace_listing_variants``. Repricing the variant alone leaves the
    merchant's records saying one number while checkout charges another — worse
    than not repricing, because it is silent on both sides.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    before = dict(bound_variant(listing_id))
    before_label = listing_row(listing_id)["price_label"]

    apply_read("product", cj_product(cost="16.40", second_cost="16.80"))

    bound = bound_variant(listing_id)
    assert bound["cost_cents"] == 1640, "supplier cost must be recorded"
    assert bound["price_cents"] > before["price_cents"], "the rule must reprice upward"

    label = listing_row(listing_id)["price_label"]
    assert label != before_label
    assert str(bound["price_cents"] // 100) in label, \
        f"price_label {label!r} does not reflect repriced {bound['price_cents']}"
    assert source_row(listing_id)["supplier_cost_cents"] == 1640


def test_a_cost_rise_on_a_draft_leaves_price_label_alone(provider):
    """``price_label`` belongs to the buyer's world, which a draft has not entered.

    ``drafts.publish`` is where a product crosses that line and writes the label
    after ``_validate`` has passed. Writing one from a sync would hand an
    unvalidated listing a checkout price.

    Auto-publish is turned off explicitly, because it is on by default — §18, and
    the reason the other tests here get a live listing straight out of the import.
    The draft state is then asserted rather than assumed: if the policy write ever
    stopped taking effect, this test would still pass while comparing a published
    listing's label against itself.
    """
    set_store_policy(auto_publish=False)
    listing_id = import_one(provider)
    assert listing_row(listing_id)["status"] not in ("published", "live", "active"), \
        "this test is only meaningful against a listing that never went live"
    before_label = listing_row(listing_id)["price_label"]

    apply_read("product", cj_product(cost="16.40", second_cost="16.80"))

    assert bound_variant(listing_id)["cost_cents"] == 1640, "the cost still syncs"
    assert listing_row(listing_id)["price_label"] == before_label


def test_a_merchant_priced_variant_is_not_repriced_by_a_supplier_read(provider):
    """§44. A merchant who set their own price owns it.

    Field-level ownership is what makes re-sync safe at all: without it the only
    choices are never refreshing cost or destroying the merchant's margin on
    every tick. The supplier's *cost* still lands — that is a fact, and hiding it
    would leave the margin warning unable to fire.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    bound = bound_variant(listing_id)

    conn = db.connect()
    try:
        cur = conn.cursor()
        variants.mark_overridden(cur, listing_id=listing_id,
                                 seller_user_id=int(bound["seller_user_id"]),
                                 # "price_label", because that is the spelling
                                 # every real writer records: `drafts.edit_draft`
                                 # appends it when given `price_cents`, and the
                                 # seller reprice route passes it literally.
                                 # `sync_updates_allowed` is an exact string
                                 # match, so a test that invented "price" here
                                 # would assert against an ownership record no
                                 # production path ever writes.
                                 fields=["price_label"])
        conn.commit()
    finally:
        conn.close()
    held_price = bound_variant(listing_id)["price_cents"]
    held_label = listing_row(listing_id)["price_label"]

    apply_read("product", cj_product(cost="16.40", second_cost="16.80"))

    after = bound_variant(listing_id)
    assert after["cost_cents"] == 1640, "cost is a fact and still syncs"
    assert after["price_cents"] == held_price, "the merchant's price must stand"
    assert listing_row(listing_id)["price_label"] == held_label


def test_a_cost_rise_that_swallows_the_margin_is_reported(provider):
    """§23. The merchant is told, and the number is not quietly left wrong."""
    listing_id = import_one(provider)
    publish(listing_id)

    out = apply_read("product", cj_product(cost="400.00", second_cost="400.00"))

    assert out["variants"] >= 1
    assert bound_variant(listing_id)["cost_cents"] == 40000


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_a_stocked_listing_is_not_touched_by_a_supplier_read(provider):
    """``STOCKED`` inventory is the merchant's own, in their own warehouse.

    The supplier's count describes stock the merchant is not selling from, so
    applying it would overwrite a real shelf count with an irrelevant one.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    conn = db.connect()
    try:
        conn.cursor().execute(
            f"UPDATE {variants.SOURCE_TABLE} SET fulfillment_mode=? WHERE listing_id=?",
            (MODE_STOCKED, listing_id))
        conn.commit()
    finally:
        conn.close()
    before = dict(bound_variant(listing_id))

    out = apply_read("inventory", counted_inventory(0))

    assert out["listings"] == 0 and out["skipped"] >= 1
    after = bound_variant(listing_id)
    assert after["stock_quantity"] == before["stock_quantity"]
    assert after["stock_state"] == before["stock_state"]
    assert listing_row(listing_id)["quantity"] != 0


def test_another_tenants_read_cannot_move_this_listing(provider):
    """The scope tuple bounds the answer, not the payload's claims.

    A provider can name any product id it likes in a webhook body, and a revision
    that trusted the payload would let one merchant's supplier event rewrite
    another's prices.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    before = dict(bound_variant(listing_id))

    out = revisions.apply_supplier_read(
        connection_id="conn-somebody-else", business_id=BUSINESS, store_id=STORE,
        kind="inventory", resource_id=PID,
        payload=counted_inventory(0))

    assert out["listings"] == 0
    assert bound_variant(listing_id)["stock_quantity"] == before["stock_quantity"]


def test_an_unusable_payload_is_a_no_op_not_a_crash(provider):
    """The worker's retry budget is for read failures, not for junk bodies.

    Raising here would push a non-event into the worker's error path, burn the
    job's retries re-reading a supplier that answered fine, and eventually tell
    the merchant their connection is broken.
    """
    listing_id = import_one(provider)
    before = dict(bound_variant(listing_id))

    for payload in (None, {}, {"variantInventories": "nonsense"}, [], "nonsense"):
        out = apply_read("inventory", payload)
        assert out["variants"] == 0, payload

    assert bound_variant(listing_id)["stock_quantity"] == before["stock_quantity"]


def test_an_unknown_kind_is_ignored(provider):
    import_one(provider)
    assert apply_read("order", cj_inventory())["listings"] == 0


# ---------------------------------------------------------------------------
# The production payload shape
# ---------------------------------------------------------------------------

def test_the_adapter_projection_and_the_raw_payload_apply_identically(provider):
    """The worker feeds a projection; every other suite feeds raw CJ JSON.

    ``worker._read_job`` returns ``adapter.get_product(pid)``, which is
    ``cj.CJAdapter._product``'s projection: ``price`` and ``vid``, not
    ``variantSellPrice`` inside CJ's envelope. ``normalize._cj_variant`` reads
    both spellings today, and nothing but this test would notice if it stopped —
    §23 would simply never fire in production while the suite stayed green.
    """
    raw = cj_product(cost="16.40", second_cost="16.80")
    projected = adapter_projection(raw)
    assert projected["variants"][0]["price"] == "16.40", "sanity: projection carries cost"

    from_raw = normalize.product("cj", raw)
    from_projection = normalize.product("cj", projected)
    costs = [{v["external_variant_id"]: v["cost_cents"] for v in n["variants"]}
             for n in (from_raw, from_projection)]
    assert costs[0] == costs[1] == {VID: 1640, VID_2: 1680}

    listing_id = import_one(provider)
    publish(listing_id)
    apply_read("product", projected)

    assert bound_variant(listing_id)["cost_cents"] == 1640, \
        "the production payload shape must reach the listing"


def test_the_adapter_projection_of_an_inventory_read_applies(provider):
    """``get_inventory`` projects to ``variants[].warehouses[]``, not CJ's rows.

    ``normalize._cj_inventory`` branches on that key, and the branch is only
    exercised in production — the fakes all return CJ's own spelling.
    """
    listing_id = import_one(provider)
    publish(listing_id)

    apply_read("inventory", {"pid": PID, "variants": [
        {"vid": VID, "pid": PID, "warehouses": [
            {"country": "CN", "total": 3, "verified": 1, "state": STOCK_IN_STOCK}]}]})

    assert bound_variant(listing_id)["stock_quantity"] == 3
    assert listing_row(listing_id)["quantity"] == 3
