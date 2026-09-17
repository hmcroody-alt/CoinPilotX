"""A supplier cost that the catalogue showed must survive being added to the cart.

The defect this file is written against
---------------------------------------
A CJ catalogue card read ``$13.27 cost``. The merchant tapped Add to cart. The
import cart row read ``— cost unknown``, the pricing rule could not propose a
sale price, and the product imported as a price-required draft.

Nothing lost the price. It was never read. ``import_cart._cacheable`` takes a
parameter named ``product`` and asked it for ``variants``, which is correct for
a :func:`normalize.product`. But the catalogue screen sends a
:func:`discovery._card` — a projection that has already collapsed the variants
into ``cost_low_cents``/``cost_high_cents`` and dropped the rows. And a CJ
*search* result has no variant rows to collapse at all: its price exists only as
the product-level ``sellPrice``.

The two shapes agree on title, image, category, origin and currency. So the row
rendered correctly in every respect except the fields derived from the one key
the card does not have. That is why it survived review: the row looked right.

What is asserted here
---------------------
* The shape seam itself, against ``_cacheable`` directly. This is the precise
  guard; a test that only drove ``add_item`` would pass the moment the provider
  read below starts working and would stop guarding the seam.
* That already-minor-unit fields are not run through ``normalize.cents`` a
  second time, which would read $13.27 as $1,327.00.
* That a cost of zero stays zero. "Free" and "unknown" must not collapse.
* That the server's own provider read outranks the client's preview, and that a
  failed read degrades to the preview instead of refusing the add.
* That provenance is recorded, so nothing downstream has to guess whether a
  number was verified or echoed.

Why this file runs alone
------------------------
It sets ``DATABASE_URL`` to its own temp file at import time, before
``services.db`` computes ``IS_POSTGRES``. Sharing a pytest process with another
suite in this directory means one of the two loses — see
``memory: tests/dropshipping/ files can't share a pytest process``.

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_cart_cost.py
"""

import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-cart-cost-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    discovery, gateway, import_cart, normalize)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

OWNER_ID = "5101"
BUSINESS, STORE, CONNECTION = "biz-cost", "store-cost", "conn-cost"
CONTEXT = {"account_status": "active", "access_enabled": True}

#: The circled product from the bug report, in CJ's *search* shape. `listV2`
#: answers with `id`/`nameEn`/`sellPrice` and carries no variant rows, which is
#: the whole reason the card's price has nowhere else to come from.
CJ_SEARCH_HIT = {
    "id": "PID-LINEN",
    "nameEn": "Casual Linen Men's Simple Round Neck 34 Sleeves Linen Shirt",
    "sellPrice": "13.27",
    "categoryName": "Apparel",
    "bigImage": "https://cdn.example.com/linen.jpg",
}


def cj_detail(pid="PID-LINEN", *, variant_prices=("13.27",)):
    """CJ's *detail* shape, which does carry variant rows."""
    return {
        "pid": pid,
        "productNameEn": "Casual Linen Men's Simple Round Neck 34 Sleeves Linen Shirt",
        "categoryName": "Apparel",
        "sellPrice": "13.27",
        "productImage": f"https://cdn.example.com/{pid}.jpg",
        "variants": [
            {"pid": pid, "vid": f"{pid}-V{i}", "variantKey": f"Opt-{i}",
             "variantSellPrice": price, "variantQuantity": 25,
             "variantSku": f"{pid}-SKU-{i}"}
            for i, price in enumerate(variant_prices, start=1)
        ],
    }


class FakeProvider:
    """Stands in for ``gateway.read``. Records calls; can be told to fail."""

    def __init__(self):
        self.products = {}
        self.fail = {}
        self.calls = []

    def add(self, payload):
        self.products[payload["pid"]] = payload
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
            return {"data": self.products[pid], "cached": False, "snapshot_id": f"snap-{pid}"}
        if operation == "variants":
            return {"data": self.products.get(pid, {}).get("variants", []), "cached": False}
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
        now, later = "2026-09-17T00:00:00Z", "2099-01-01T00:00:00Z"
        conn.execute(
            "INSERT INTO business_os_supplier_connections "
            "(id, merchant_id, business_id, store_id, provider, connection_type, "
            " external_account_id, external_shop_id, status, credential_reference, "
            " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
            "VALUES (?,?,?,?,'CJ','API_KEY',?,?,'CONNECTED',?,?,?,'UNKNOWN',?,?)",
            (CONNECTION, OWNER_ID, BUSINESS, STORE, "acct-x", "shop-x", "cred-x",
             later, later, now, now))
        conn.commit()
    finally:
        conn.close()
    import_cart.ensure_schema()
    gateway.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()


@pytest.fixture()
def offline(monkeypatch):
    """No provider at all, so the cache can only come from the client preview."""
    def unavailable(*args, **kwargs):
        raise SupplierError("provider_unavailable", http_status=503)
    monkeypatch.setattr(gateway, "read", unavailable)


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(gateway, "read", fake)
    return fake


def add(pid, *, preview=None, selected=None):
    return import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                external_product_id=pid, selected_variant_ids=selected,
                                product=preview, provider="cj", context=CONTEXT)


# ---------------------------------------------------------------------------
# The shape seam. These drive `_cacheable` directly and are the precise guard.
# ---------------------------------------------------------------------------

def test_catalogue_card_keeps_its_cost():
    """The exact payload the catalogue screen posts, through the exact function.

    ``discovery._card`` is called for real rather than hand-written, so this
    breaks if the card's field names drift away from what the cache reads —
    which is the failure being guarded, one release later.
    """
    card = discovery._card("cj", CJ_SEARCH_HIT)
    assert card["cost_low_cents"] == 1327, "fixture no longer reproduces the bug"

    cached = import_cart._cacheable(card)

    assert cached["cost_low_cents"] == 1327
    assert cached["cost_high_cents"] == 1327
    assert cached["currency"] == "USD"


def test_minor_units_are_not_converted_twice():
    """$13.27 is 1327 cents, not 132700.

    ``normalize.cents`` multiplies by 100 because it reads a provider's
    major-unit field. The summary fields have already been through it.
    """
    cached = import_cart._cacheable({"cost_low_cents": 1327, "cost_high_cents": 1499})
    assert (cached["cost_low_cents"], cached["cost_high_cents"]) == (1327, 1499)


def test_search_shape_falls_back_to_the_product_level_price():
    """A normalized search result has ``from_cost_cents`` and no variants."""
    product = normalize.product("cj", CJ_SEARCH_HIT)
    assert product["variants"] == [], "fixture no longer reproduces the bug"

    cached = import_cart._cacheable(product)
    assert cached["cost_low_cents"] == 1327


def test_variants_outrank_the_summary_fields():
    """When real variant rows are present they decide the range, not the summary.

    The summary here is deliberately wrong. A cache that preferred it would
    quote a price no variant actually has.
    """
    product = normalize.product("cj", cj_detail(variant_prices=("13.27", "15.40")))
    product["cost_low_cents"] = 999
    product["cost_high_cents"] = 999

    cached = import_cart._cacheable(product)
    assert (cached["cost_low_cents"], cached["cost_high_cents"]) == (1327, 1540)
    assert cached["variant_count"] == 2


def test_zero_cost_is_a_cost():
    """Free is not unknown. A truthiness test here makes them the same row."""
    cached = import_cart._cacheable({"cost_low_cents": 0, "cost_high_cents": 0})
    assert cached["cost_low_cents"] == 0
    assert cached["cost_source"] is not None


@pytest.mark.parametrize("value", [None, "13.27", -1, True, float("nan"), 12.5])
def test_unusable_summary_costs_stay_unknown(value):
    """Not a fallback to zero, and not a string accepted as a number.

    ``True`` is in here because ``isinstance(True, int)`` is True in Python and
    a bool reaching a money field means something upstream is already wrong.
    """
    cached = import_cart._cacheable({"cost_low_cents": value})
    assert cached["cost_low_cents"] is None
    assert cached["cost_source"] is None, "unknown cost must not claim a source"


def test_unknown_cost_does_not_become_a_number():
    """A payload with no cost anywhere still reports unknown, not zero."""
    cached = import_cart._cacheable({"title": "No price here"})
    assert cached["cost_low_cents"] is None
    assert cached["cost_high_cents"] is None


# ---------------------------------------------------------------------------
# End to end through `add_item`
# ---------------------------------------------------------------------------

def test_cart_row_shows_the_cost_the_catalogue_showed(offline):
    """The user-visible bug, end to end, with the provider unreachable.

    ``offline`` is the point: this passes on the preview path alone, so it keeps
    guarding the seam even where the server cannot read CJ.
    """
    card = discovery._card("cj", CJ_SEARCH_HIT)
    item = add(card["external_product_id"], preview=card)

    assert item["cached"]["cost_low_cents"] == 1327
    assert item["cached"]["cost_source"] == import_cart.COST_SOURCE_PREVIEW
    assert item["cached"]["title"] == card["title"]


def test_the_server_outranks_the_client_preview(provider):
    """A preview that disagrees with CJ loses. The server read is the fact."""
    provider.add(cj_detail(variant_prices=("13.27", "15.40")))
    lying_preview = dict(discovery._card("cj", CJ_SEARCH_HIT), cost_low_cents=1,
                         cost_high_cents=1)

    item = add("PID-LINEN", preview=lying_preview)

    assert (item["cached"]["cost_low_cents"], item["cached"]["cost_high_cents"]) == (1327, 1540)
    assert item["cached"]["cost_source"] == import_cart.COST_SOURCE_PROVIDER
    assert item["cached"]["cost_checked_at"] is not None
    assert ("product", "PID-LINEN") in provider.calls


def test_a_failed_provider_read_does_not_refuse_the_add(provider):
    """Bookmarking a product must not depend on the supplier being up."""
    provider.add(cj_detail())
    provider.fail["PID-LINEN"] = "provider_unavailable"

    item = add("PID-LINEN", preview=discovery._card("cj", CJ_SEARCH_HIT))

    assert item["cached"]["cost_low_cents"] == 1327
    assert item["cached"]["cost_source"] == import_cart.COST_SOURCE_PREVIEW


def test_a_priceless_provider_read_does_not_claim_provenance(provider):
    """"We checked" and "we know" are different sentences.

    CJ answering with no price must not overwrite the merchant's own view of the
    catalogue with a blank stamped ``provider``.
    """
    payload = cj_detail()
    payload["sellPrice"] = None
    for variant in payload["variants"]:
        variant["variantSellPrice"] = None
    provider.add(payload)

    item = add("PID-LINEN", preview=discovery._card("cj", CJ_SEARCH_HIT))

    assert item["cached"]["cost_low_cents"] == 1327
    assert item["cached"]["cost_source"] == import_cart.COST_SOURCE_PREVIEW


def test_adding_to_the_cart_writes_no_listing_and_no_supplier_order(provider):
    """Order safety. A bookmark costs nobody anything.

    Named here rather than only in the fulfillment suite because this is the
    screen a merchant taps while browsing, and the live-mode work is what makes
    the question expensive to get wrong.
    """
    provider.add(cj_detail())
    add("PID-LINEN", preview=discovery._card("cj", CJ_SEARCH_HIT))

    conn = db.connect()
    try:
        listings = conn.execute("SELECT COUNT(*) AS n FROM marketplace_listings").fetchone()
        assert int(listings["n"]) == 0
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_supplier_%'").fetchall()]
    finally:
        conn.close()
    for table in ("business_os_supplier_intents", "business_os_supplier_outbox"):
        if table in tables:
            assert rows_in(table) == 0, f"add-to-cart created a row in {table}"
    assert all(op in ("product", "variants") for op, _ in provider.calls), provider.calls


def rows_in(table):
    conn = db.connect()
    try:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
    finally:
        conn.close()
