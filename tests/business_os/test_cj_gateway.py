"""Canonical authorization, cache, snapshot and merchant-draft gateway proofs."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from services import db
from services.business_os.suppliers import connections, gateway
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_connections import database, connect, FakeAdapter, auth  # noqa: F401


class CatalogAdapter(FakeAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reads = []
        self.result = {"pid": "1001", "title": "Untrusted supplier content", "supplier_price": "3.00",
                       "variants": [{"pid": "1001", "vid": "2001", "sku": "FIX"}], "untrusted_content": True}
        self.points_info = {"remaining": 5000, "usedToday": 1000, "total": 50000}
        self.read_error = None

    def get_product(self, pid):
        self.reads.append(("product", pid))
        if self.read_error:
            raise self.read_error
        return dict(self.result)

    def get_inventory(self, pid, vid):
        self.reads.append(("inventory", pid, vid))
        if self.read_error:
            raise self.read_error
        return {"pid": pid, "state": "UNKNOWN", "variants": []}

    def get_subscriptions(self, shop_id, **kwargs):
        self.reads.append(("subscriptions", shop_id))
        return {"products": [], "page": 1, "size": 20}


def read(adapter, connection_id, *, actor="100", business="biz-a", store="store-a", operation="product", params=None):
    return gateway.read(operation, business_id=business, store_id=store, actor_user_id=actor,
                        connection_id=connection_id, params=params or {"pid": "1001"}, adapter=adapter)


@pytest.fixture
def connected():
    gateway.ensure_schema()
    return connect()


def test_cache_deduplicates_provider_reads_but_always_authorizes_actor(connected):
    adapter = CatalogAdapter()
    one = read(adapter, connected["id"])
    two = read(adapter, connected["id"], actor="300")  # canonical viewer may read
    assert one["cached"] is False and two["cached"] is True
    assert adapter.reads == [("product", "1001")]
    assert one["snapshot_id"] != two["snapshot_id"]
    assert one["untrusted_content"] and one["data"] == two["data"]
    with pytest.raises(connections.SupplierConnectionError):
        read(adapter, connected["id"], actor="200")
    assert adapter.reads == [("product", "1001")]


def test_cache_does_not_retain_access_after_canonical_membership_revocation(connected):
    adapter = CatalogAdapter()
    read(adapter, connected["id"], actor="300")
    conn = db.connect()
    conn.execute("UPDATE business_os_business_members SET status='inactive' WHERE user_id='300'")
    conn.commit()
    conn.close()
    with pytest.raises(connections.SupplierConnectionError):
        read(adapter, connected["id"], actor="300")


def test_permission_revoked_during_provider_read_blocks_result_and_snapshot(connected):
    class RevokingAdapter(CatalogAdapter):
        def get_product(self, pid):
            conn = db.connect()
            conn.execute("UPDATE business_os_business_members SET status='inactive' WHERE user_id='300'")
            conn.commit()
            conn.close()
            return super().get_product(pid)

    adapter = RevokingAdapter()
    with pytest.raises(connections.SupplierConnectionError):
        read(adapter, connected["id"], actor="300")
    assert len(adapter.reads) == 1
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM supplier_snapshots").fetchone()[0] == 0
    conn.close()


def test_cache_keys_are_connection_business_and_store_scoped(connected):
    first = CatalogAdapter()
    read(first, connected["id"])
    second = CatalogAdapter(bundle=auth(open_id="fixture-open-id-B"),
        shops=[{"shop_id": "cj-shop-b", "name": "B", "platform": "API", "status": 1}])
    other = connections.connect_cj("biz-b", "store-b", "200", "fixture-key-B", "cj-shop-b", adapter=second)
    result = read(second, other["id"], actor="200", business="biz-b", store="store-b")
    assert result["cached"] is False and len(second.reads) == 1
    with pytest.raises(connections.SupplierConnectionError):
        read(second, connected["id"], actor="200", business="biz-b", store="store-b")


def test_snapshots_require_full_owned_tuple_and_never_publish_product(connected):
    result = read(CatalogAdapter(), connected["id"])
    snapshot = gateway.get_snapshot(result["snapshot_id"], connected["id"], "biz-a", "store-a", "100")
    assert snapshot["kind"] == "product" and snapshot["untrusted_content"]
    with pytest.raises(connections.SupplierConnectionError):
        gateway.get_snapshot(result["snapshot_id"], connected["id"], "biz-b", "store-b", "200")
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM business_os_store_products").fetchone()[0] == 0
    conn.close()


def test_import_draft_requires_manager_and_keeps_merchant_retail_fields_separate(connected):
    result = read(CatalogAdapter(), connected["id"])
    fields = {"title": "Merchant title", "description": "Merchant policy", "price_cents": 2999, "currency": "USD"}
    with pytest.raises(connections.SupplierConnectionError):
        gateway.create_import_draft(result["snapshot_id"], connected["id"], "biz-a", "store-a", "300", fields)
    draft = gateway.create_import_draft(result["snapshot_id"], connected["id"], "biz-a", "store-a", "400", fields)
    assert draft["status"] == "DRAFT" and draft["requires_merchant_review"] and not draft["published"]
    conn = db.connect()
    saved = conn.execute("SELECT * FROM supplier_import_drafts WHERE draft_id=?", (draft["draft_id"],)).fetchone()
    assert json.loads(saved["merchant_fields_json"]) == fields
    assert conn.execute("SELECT COUNT(*) FROM business_os_store_products").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("fields", [{"price_cents": True}, {"price_cents": -1}, {"currency": "usd"},
    {"visibility": "published"}, {"title": "x" * 161}, {"access_token": "fixture-forbidden"}])
def test_import_draft_rejects_forged_authority_or_bad_retail_fields(connected, fields):
    result = read(CatalogAdapter(), connected["id"])
    with pytest.raises(SupplierError):
        gateway.create_import_draft(result["snapshot_id"], connected["id"], "biz-a", "store-a", "100", fields)


def test_inventory_snapshot_cannot_become_product_import_draft(connected):
    result = read(CatalogAdapter(), connected["id"], operation="inventory")
    with pytest.raises(SupplierError) as failure:
        gateway.create_import_draft(result["snapshot_id"], connected["id"], "biz-a", "store-a", "100", {})
    assert failure.value.code == "product_snapshot_required"


def test_provider_failure_does_not_cache_zero_inventory_and_updates_health(connected):
    adapter = CatalogAdapter()
    adapter.read_error = SupplierError("RATE_LIMITED", http_status=429, retry_after=90)
    with pytest.raises(SupplierError):
        read(adapter, connected["id"], operation="inventory")
    conn = db.connect()
    rows = conn.execute("SELECT payload_json,lease_owner FROM supplier_read_cache").fetchall()
    assert rows and all(row["payload_json"] is None and row["lease_owner"] is None for row in rows)
    assert conn.execute("SELECT COUNT(*) FROM supplier_snapshots").fetchone()[0] == 0
    conn.close()
    assert connections.get_connection(connected["id"], "biz-a", "store-a", "100")["status"] == "RATE_LIMITED"


def test_subscriptions_ignore_client_shop_claim_and_use_saved_binding(connected):
    adapter = CatalogAdapter()
    read(adapter, connected["id"], operation="subscriptions", params={"shop_id": "cj-shop-b"})
    assert adapter.reads == [("subscriptions", "cj-shop-a")]


def test_no_arbitrary_provider_http_proxy(connected):
    adapter = CatalogAdapter()
    with pytest.raises(SupplierError) as failure:
        read(adapter, connected["id"], operation="shopping/pay/payBalanceV2")
    assert failure.value.code == "unknown_operation" and adapter.reads == []


def test_cache_singleflight_lease_allows_only_one_provider_read(connected):
    started, release = threading.Event(), threading.Event()

    class Blocking(CatalogAdapter):
        def get_product(self, pid):
            started.set()
            assert release.wait(4)
            return super().get_product(pid)

    first, second = Blocking(), CatalogAdapter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(read, first, connected["id"])
        try:
            assert started.wait(3)
            with pytest.raises(SupplierError) as failure:
                read(second, connected["id"])
            assert failure.value.code == "request_in_progress" and second.reads == []
        finally:
            release.set()
        assert not future.result(timeout=5)["cached"]
    assert len(first.reads) == 1


#: The connection fixture's merchant is business_os_business.owner_user_id = '100'
#: for biz-a. That is a *user id*, the same identity marketplace_listings.seller_user_id
#: holds — TEXT on one side, INTEGER on the other. Binding relies on exactly that
#: equivalence, so the fixture states it once rather than restating '100' inline.
OWNED_SELLER = 100
OWNED_LISTING = "8001"
FOREIGN_LISTING = "8002"


def seed_marketplace_products():
    """Seed the canonical ledger, not the legacy business_os one.

    ``marketplace_listings`` is owned by ``bot.init_db`` and cannot be called
    from a unit test, so the columns this suite touches are declared here. Two
    listings: one owned by the connection's merchant and one owned by someone
    else, because "not yours" and "does not exist" have to be shown to refuse
    identically and that needs a real foreign row, not just a missing id.
    """
    conn = db.connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, seller_user_id INTEGER, title TEXT,
        category TEXT, price_label TEXT, status TEXT, approval_status TEXT,
        listing_type TEXT, product_type TEXT, delivery_type TEXT, currency TEXT,
        quantity INTEGER, created_at TEXT, updated_at TEXT)""")
    for listing_id, owner in ((OWNED_LISTING, OWNED_SELLER), (FOREIGN_LISTING, 200)):
        conn.execute("INSERT INTO marketplace_listings (id,seller_user_id,title,price_label,"
                     "status,approval_status,listing_type,product_type,delivery_type,currency,"
                     "quantity,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (int(listing_id), owner, "Merchant product", "$19.99", "published",
                      "approved", "physical", "physical", "shipping", "USD", 5, "now", "now"))
    conn.commit()
    conn.close()


def _sources():
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM marketplace_product_sources ORDER BY id").fetchall()]
    finally:
        conn.close()


def test_product_binding_requires_owned_canonical_product_and_exact_provider_variant(connected):
    seed_marketplace_products()
    adapter = CatalogAdapter()
    base = dict(connection_id=connected["id"], business_id="biz-a", store_id="store-a", actor_user_id="100", adapter=adapter)
    with pytest.raises(SupplierError) as failure:
        gateway.bind_product(**base, canonical_product_id=FOREIGN_LISTING, pid="1001", vid="2001")
    assert failure.value.http_status == 404 and adapter.reads == []
    with pytest.raises(SupplierError) as failure:
        gateway.bind_product(**base, canonical_product_id=OWNED_LISTING, pid="1001", vid="2002")
    assert failure.value.code == "variant_mismatch"
    result = gateway.bind_product(**base, canonical_product_id=OWNED_LISTING, pid="1001", vid="2001")
    assert result["canonical_product_id"] == OWNED_LISTING and result["vid"] == "2001"
    # The mapping landed on the canonical table, carrying both the listing it
    # belongs to and the connection scope it was established through.
    rows = _sources()
    assert len(rows) == 1
    assert rows[0]["listing_id"] == int(OWNED_LISTING)
    assert rows[0]["seller_user_id"] == OWNED_SELLER
    assert rows[0]["provider"] == "cj"
    assert rows[0]["provider_product_id"] == "1001" and rows[0]["provider_variant_id"] == "2001"
    assert rows[0]["supplier_connection_id"] == connected["id"]
    assert rows[0]["business_id"] == "biz-a" and rows[0]["store_id"] == "store-a"
    with pytest.raises(SupplierError):
        gateway.get_product_binding(connected["id"], "biz-b", "store-b", OWNED_LISTING)


def test_a_nonexistent_listing_refuses_exactly_like_someone_elses(connected):
    """No existence oracle across the reconciliation boundary.

    ``canonical_product_id`` used to be an opaque string and is now an integer
    listing id, which introduces a third failure mode — a reference that is not
    an id at all. All three must be indistinguishable, or a caller can map the
    id space by watching which refusal it gets.
    """
    seed_marketplace_products()
    base = dict(connection_id=connected["id"], business_id="biz-a", store_id="store-a",
                actor_user_id="100", adapter=CatalogAdapter(), pid="1001", vid="2001")
    seen = []
    for reference in (FOREIGN_LISTING, "999999", "market-a", "", "0", "-1"):
        with pytest.raises(SupplierError) as failure:
            gateway.bind_product(**base, canonical_product_id=reference)
        seen.append((failure.value.http_status, failure.value.code))
    assert len(set(seen)) == 1, f"refusals differ and leak existence: {seen}"
    assert seen[0][0] == 404


def test_product_transfer_during_provider_read_prevents_foreign_binding(connected):
    seed_marketplace_products()

    class TransferringAdapter(CatalogAdapter):
        def get_product(self, pid):
            conn = db.connect()
            conn.execute("UPDATE marketplace_listings SET seller_user_id=200 WHERE id=?",
                         (int(OWNED_LISTING),))
            conn.commit()
            conn.close()
            return super().get_product(pid)

    with pytest.raises(SupplierError) as failure:
        gateway.bind_product(connection_id=connected["id"], business_id="biz-a", store_id="store-a", actor_user_id="100",
            canonical_product_id=OWNED_LISTING, pid="1001", vid="2001", adapter=TransferringAdapter())
    assert failure.value.http_status == 404
    assert _sources() == []


def test_supplier_product_links_is_gone(connected):
    """The competing mapping authority must not be recreated.

    ``ensure_schema`` used to create ``supplier_product_links``. Deleting the
    writes without deleting the DDL would leave an empty table that a future
    reader could rediscover and start trusting, which is how a second authority
    comes back.
    """
    gateway.ensure_schema()
    conn = db.connect()
    try:
        with pytest.raises(Exception):
            conn.execute("SELECT 1 FROM supplier_product_links LIMIT 1").fetchone()
    finally:
        conn.close()
