import time

import pytest

from services import db, marketplace_variants
from services.business_os.suppliers import worker, fulfillment
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_connections import database
from tests.business_os.test_cj_fulfillment import MERCHANT, ready, outbox


def test_worker_dark_until_the_deployment_enables_the_network(ready, monkeypatch):
    """A background loop is the worst place for a missing gate.

    The worker runs unattended, so it calls `require_network()` for the same
    reason the request path does. This asserts the default -- nothing
    configured -- stops it before it makes a single call.
    """
    with pytest.raises(SupplierError):
        worker.run_once(adapter_factory=lambda _: ready[0])
    assert ready[0].created == []


def test_bounded_worker_seeds_only_owned_selected_resources(ready, monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
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
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a", kind="inventory", resource_id="10001", now=time.time() - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    assert counts["reads"] == 1 and counts["deferred"] == 0
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM supplier_snapshots WHERE kind='inventory'").fetchone()[0] == 1
    assert conn.execute("SELECT last_sync_at FROM business_os_supplier_connections").fetchone()[0]
    conn.close()
    assert ready[0].background is True


# ---------------------------------------------------------------------------
# Is anything draining the outbox?
#
# `run_once` is the only caller of `fulfillment.claim`/`dispatch`. Its only
# entry point is `supplier_worker.py`, which is not in the Procfile -- so in
# this deployment nothing drains, every intent stays at READY forever, and the
# merchant reads "Queued to send to your supplier" permanently.
#
# The tests above could not have caught that, and neither could any test, for a
# reason worth naming: `run_once` returned its counts to the caller and
# persisted nothing about itself. "A drain ran" was not a fact in the database,
# so no read path could assert on it and no copy could be checked against it.
# These tests exist because the tick is now recorded.
# ---------------------------------------------------------------------------


def test_a_deployment_that_has_never_drained_says_so(ready):
    """The state this repo is actually in, asserted rather than assumed.

    No `run_once` call anywhere above this line in the fixture, which is exactly
    production's situation: the worker is not in the Procfile. Before the drain
    latch existed this was indistinguishable from a healthy queue.
    """
    status = fulfillment.drain_status()
    assert status["state"] == "NO_DRAIN_HAS_EVER_RUN"
    # Not 0, and not `now`. A default would make "never" look like "just now",
    # which is the whole failure.
    assert status["started_at"] is None and status["completed_at"] is None


def test_a_queued_order_is_not_called_queued_to_send_when_nothing_sends(ready):
    """The defect, end to end, on the payload the merchant's screen reads.

    A paid order with a live intent sits at READY. READY renders as "Queued to
    send to your supplier". This asserts the same payload also carries the fact
    that contradicts it, because a merchant acting on the row without that fact
    waits on a dispatch that cannot happen.
    """
    fulfillment.create_intent(**ready[2])
    result = fulfillment.list_obligations(ready[1]["id"], "biz-a", "store-a", MERCHANT)
    assert [row["state"] for row in result["obligations"]] == ["READY"]
    assert result["drain"]["state"] == "NO_DRAIN_HAS_EVER_RUN"


def test_a_completed_tick_is_what_makes_the_queue_moving(ready, monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    now = time.time()
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now)
    status = fulfillment.drain_status(now=now)
    assert status["state"] == "DRAINING"
    assert status["started_at"] == now and status["completed_at"] == now


def test_a_worker_that_dies_every_tick_is_not_reported_as_healthy(ready, monkeypatch):
    """The reason two timestamps are kept instead of one.

    `_seed_jobs` runs after the latch and before any item is handled, so a
    failure there aborts the tick without completing it. That deployment has a
    worker -- it is deployed, it is running, it is broken -- and reporting it as
    `NO_DRAIN_HAS_EVER_RUN` would send the owner to look for a missing process
    that is in fact present, while reporting `DRAINING` would hide an incident.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    def explode(*args, **kwargs):
        raise RuntimeError("seeding failed")
    monkeypatch.setattr(worker, "_seed_jobs", explode)
    now = time.time()
    with pytest.raises(RuntimeError):
        worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now)
    status = fulfillment.drain_status(now=now)
    assert status["state"] == "TICKING_BUT_NOT_COMPLETING"
    assert status["started_at"] == now and status["completed_at"] is None


def test_a_drain_that_stopped_completing_stops_being_called_draining(ready, monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    now = time.time()
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now)
    fresh = fulfillment.drain_status(now=now + fulfillment.DRAIN_STALL_SECONDS)
    assert fresh["state"] == "DRAINING", "the boundary itself is not yet a stall"
    stale = fulfillment.drain_status(now=now + fulfillment.DRAIN_STALL_SECONDS + 1)
    assert stale["state"] == "DRAIN_STALLED"


def test_a_looping_crash_cannot_keep_a_stalled_drain_looking_fresh(ready, monkeypatch):
    """Staleness is measured on the completion, not on the start.

    A worker crashing inside every tick refreshes `started_at` forever. If the
    stall window were measured against that, the most alarming failure mode --
    a live process draining nothing, indefinitely -- would be the one that never
    raised a notice.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    now = time.time()
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now)
    def explode(*args, **kwargs):
        raise RuntimeError("seeding failed")
    monkeypatch.setattr(worker, "_seed_jobs", explode)
    much_later = now + fulfillment.DRAIN_STALL_SECONDS * 10
    with pytest.raises(RuntimeError):
        worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=much_later)
    status = fulfillment.drain_status(now=much_later)
    assert status["started_at"] == much_later, "the crash did refresh the start"
    assert status["state"] == "DRAIN_STALLED"


def test_the_stall_window_is_two_of_the_workers_own_slowest_ticks():
    """An absolute pin, for the reason `test_cj_fulfillment` learned the hard way.

    Every test above is written *relative* to `DRAIN_STALL_SECONDS`, so widening
    it leaves all of them green -- a test written relative to a constant cannot
    detect a change to that constant. This one states the number, and states
    where the number comes from: `supplier_worker.main` clamps its sleep to at
    most 3600s, so two missed ticks at that ceiling is 7200s, and anything
    shorter would cry wolf at a legally-configured slow deployment.
    """
    assert fulfillment.DRAIN_STALL_SECONDS == 7200
    import supplier_worker
    source = open(supplier_worker.__file__, encoding="utf-8").read()
    assert "min(args.interval, 3600)" in source, (
        "the worker's sleep clamp changed, so the stall window is no longer two "
        "of its slowest ticks -- re-derive DRAIN_STALL_SECONDS from the new one")


def test_the_drain_latch_carries_no_credential_or_provider_data(ready, monkeypatch):
    """It is reported on a merchant payload, so §27 applies to it too.

    Timestamps and one word from a closed set. The latch has no connection,
    business, store or provider column at all -- a per-connection latch would
    have needed one, which is a second reason the row is a single global one.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=time.time())
    conn = db.connect()
    rows = [dict(r) for r in conn.execute("SELECT * FROM business_os_supplier_drain_ticks")]
    conn.close()
    assert len(rows) == 1, "the latch is one row for the deployment, not one per connection"
    assert set(rows[0]) == {"scope", "started_at", "completed_at"}
    status = fulfillment.drain_status()
    assert status["state"] in fulfillment.DRAIN_STATES
