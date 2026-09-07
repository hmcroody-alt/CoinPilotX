import time

import pytest

from services import db, marketplace_variants
from services.business_os.suppliers import worker, fulfillment
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_connections import database
from tests.business_os.test_cj_fulfillment import MERCHANT, ready, outbox


def test_worker_dark_without_provider_approval(ready, monkeypatch):
    monkeypatch.delenv("CJ_HOSTED_CREDENTIALS_APPROVED", raising=False)
    with pytest.raises(SupplierError):
        worker.run_once(adapter_factory=lambda _: ready[0])
    assert ready[0].created == []


def test_bounded_worker_seeds_only_owned_selected_resources(ready, monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "true")
    result = fulfillment.create_intent(**ready[2])
    now = time.time()
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now)
    assert counts["intents"] == 1 and counts["reads"] == 0
    assert len(ready[0].created) == 1
    assert outbox(result["intent_id"])["state"] == "UNKNOWN"
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now + 5)
    assert outbox(result["intent_id"])["state"] == "LINKED"
    assert len(ready[0].created) == 1
    conn = db.connect()
    jobs = [dict(r) for r in conn.execute("SELECT * FROM business_os_supplier_sync_jobs")]
    conn.close()
    assert {r["kind"] for r in jobs} >= {"health", "shops", "subscriptions", "product", "inventory"}
    assert {r["connection_id"] for r in jobs} == {ready[1]["id"]}


def test_retry_after_not_shortened_by_worker(ready, monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "true")
    def unavailable(bundle):
        raise SupplierError("RATE_LIMITED", http_status=429, retry_after=7200)
    now = time.time()
    worker.run_once(adapter_factory=unavailable, limit=1, now=now)
    conn = db.connect()
    failed = conn.execute("SELECT available_at FROM business_os_supplier_sync_jobs WHERE failures=1").fetchone()
    conn.close()
    assert failed[0] >= now + 7200


def test_worker_entrypoint_disabled_without_importing_bot(monkeypatch):
    monkeypatch.delenv("CJ_RECONCILIATION_ENABLED", raising=False)
    from supplier_worker import run_tick
    assert run_tick() == {"status": "disabled"}


def test_seeding_advances_beyond_first_page(ready):
    """Paging walks supplier mappings, and mappings now hang off real listings.

    Eight distinct provider products are expected, not seven: the ``ready``
    fixture already bound one. Each extra product needs its own *listing*, because
    a mapping with no canonical listing behind it is precisely the orphan this
    reconciliation removed — and ``_seed_jobs`` reads
    ``marketplace_product_sources``, which cannot hold one.

    The extra listings are seeded past the production id range rather than reusing
    ids 8..13, so the six real rows stay exactly as production has them.
    """
    conn = db.connect()
    cur = conn.cursor()
    for number in range(7):
        listing_id = 1000 + number
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, status, "
            "approval_status, listing_type, product_type, delivery_type, price_label, "
            "currency, quantity, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (listing_id, MERCHANT, f"Paging fixture {number}", "published", "approved",
             "physical", "physical", "shipping", "$1.00", "USD", 1, "now", "now"))
        marketplace_variants.link_source(
            cur, listing_id=listing_id, seller_user_id=MERCHANT, provider="cj",
            provider_product_id=str(30000 + number), provider_variant_id=str(40000 + number),
            supplier_connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
            source_snapshot_id="fixture")
    conn.commit()
    conn.close()
    for _ in range(5):
        worker._seed_jobs(2, time.time())
    conn = db.connect()
    rows = conn.execute("SELECT resource_id FROM business_os_supplier_sync_jobs WHERE kind='inventory'").fetchall()
    conn.close()
    assert len(rows) == 8


def test_worker_uses_refreshed_connection_status(ready, monkeypatch):
    from services.business_os.suppliers import connections
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "true")
    result = fulfillment.create_intent(**ready[2])
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET status='AUTH_EXPIRED'")
    conn.commit()
    conn.close()
    def refreshed(bundle):
        return connections.worker_adapter(bundle["connection"]["id"], "biz-a", "store-a", adapter=ready[0])
    worker.run_once(adapter_factory=refreshed, limit=1)
    assert len(ready[0].created) == 1 and outbox(result["intent_id"])["state"] == "UNKNOWN"


def test_successful_selected_read_persists_snapshot_and_sync_time(ready, monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "true")
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a", kind="inventory", resource_id="10001", now=time.time() - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    assert counts["reads"] == 1 and counts["deferred"] == 0
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM supplier_snapshots WHERE kind='inventory'").fetchone()[0] == 1
    assert conn.execute("SELECT last_sync_at FROM business_os_supplier_connections").fetchone()[0]
    conn.close()
    assert ready[0].background is True
