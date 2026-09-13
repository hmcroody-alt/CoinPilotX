import time

import pytest

from services import db, marketplace_variants
from services.business_os.suppliers import revisions, worker, fulfillment
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_connections import database
from tests.business_os.test_cj_fulfillment import (
    MERCHANT, OWNED_LISTING, PID, VID, ready, outbox)


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
# `run_once` is the only caller of `fulfillment.claim`/`dispatch`. Its only entry
# point is `supplier_worker.py`, which now has a Procfile entry -- but the entry
# only starts a process. `run_tick` returns `disabled` without
# `CJ_RECONCILIATION_ENABLED`, and `run_once` is additionally behind
# `policy.require_network()`; both are unset in production. So in this deployment
# nothing drains, every intent stays at READY forever, and the merchant reads
# "Queued to send to your supplier" permanently.
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
    production's situation: the worker has a Procfile entry but is gated off by
    `CJ_RECONCILIATION_ENABLED` and `CJ_NETWORK_ENABLED`, and the latch is only
    written past both. Before the drain latch existed this was indistinguishable
    from a healthy queue.
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


# ---------------------------------------------------------------------------
# Does a completed read reach the listing it describes? §23/§24
#
# `_seed_jobs` has always scheduled a `product` and an `inventory` job for every
# imported listing, and `_finish` has always stored the answer in
# `supplier_snapshots`. Nothing read that table. A supplier could double their
# cost or sell out and the listing kept its import-time price and unit count for
# as long as it existed, while the worker recorded the new numbers every cadence.
#
# `revisions.apply_supplier_read` is the consumer that was missing. Its own
# behaviour is proved against a real database in
# `tests/dropshipping/test_dropship_revision_apply.py` -- from a provider outage
# that must not delist a catalogue to a held reservation that must survive a
# restock. None of those tests can prove the worker calls it: `_apply` is one
# line in `run_once`, and deleting that line leaves every one of them green while
# §23 and §24 quietly stop existing in production.
# ---------------------------------------------------------------------------


def bind_variant(*, stock_quantity, cost_cents=200, price_cents=900):
    """The variant row a supplier read is allowed to move.

    The `ready` fixture binds a *source* and nothing else -- `bind_product`
    writes `marketplace_product_sources`, which is provenance, not inventory --
    so its listing has a supplier but no variants, and a revision would have
    nothing to write to. Production gets these rows from `importer`; what is
    load-bearing here is only that one exists carrying the provider variant id
    the scheduled read will name, because that id is the join the worker's
    wiring has to survive.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        variant_id = marketplace_variants.upsert_variant(
            cur, listing_id=int(OWNED_LISTING), seller_user_id=MERCHANT,
            options=[{"name": "Size", "value": "S"}], sku="FIXTURE-SKU",
            provider_variant_id=VID, price_cents=price_cents, cost_cents=cost_cents,
            currency="USD", stock_state="IN_STOCK", stock_quantity=stock_quantity)
        conn.commit()
        return variant_id
    finally:
        conn.close()


def variant_row(variant_id):
    conn = db.connect()
    try:
        return dict(conn.execute(
            f"SELECT * FROM {marketplace_variants.VARIANT_TABLE} WHERE id=?",
            (int(variant_id),)).fetchone())
    finally:
        conn.close()


def listing_row():
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM marketplace_listings WHERE id=?",
                                 (int(OWNED_LISTING),)).fetchone())
    finally:
        conn.close()


def sync_job(kind, resource_id):
    conn = db.connect()
    try:
        return dict(conn.execute(
            "SELECT * FROM business_os_supplier_sync_jobs WHERE kind=? AND resource_id=?",
            (kind, resource_id)).fetchone())
    finally:
        conn.close()


def test_a_completed_inventory_read_moves_the_stock_it_describes(ready, monkeypatch):
    """The wiring, end to end: scheduled job -> provider -> the seller's row.

    The count the fixture adapter reports is 25 against a stored 8, so both
    halves of §24 are visible: the variant mirrors the supplier's number, and
    `marketplace_listings.quantity` moves by the *difference*. That column is a
    reservation ledger -- the cart decrements it per unit held and credits it back
    on release -- so assigning 25 over it would silently release every
    outstanding reservation. Asserted as `before + 17` rather than `== 25` so
    that a future absolute assignment fails here instead of overselling a buyer.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    variant_id = bind_variant(stock_quantity=8)
    before = listing_row()["quantity"]
    ready[0].stock = 25
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="inventory", resource_id=PID, now=time.time() - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    assert counts["reads"] == 1 and counts["deferred"] == 0
    assert counts["revisions"] == 1 and counts["revision_failures"] == 0
    assert variant_row(variant_id)["stock_quantity"] == 25
    assert listing_row()["quantity"] == before + 17


def test_a_supplier_that_sold_out_takes_the_variant_off_sale_through_the_worker(ready, monkeypatch):
    """Zero is a claim, and the worker must be able to carry it.

    Distinct from the test above because the interesting direction is the one a
    merchant never asks for. A sell-out arriving as `stock_quantity=0` is what
    stops the listing selling units the supplier cannot ship; a sell-out that
    never left `supplier_snapshots` is an order that gets taken and refunded.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    variant_id = bind_variant(stock_quantity=8)
    ready[0].stock = 0
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="inventory", resource_id=PID, now=time.time() - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    assert counts["revisions"] == 1
    row = variant_row(variant_id)
    assert row["stock_quantity"] == 0 and row["stock_state"] == "OUT_OF_STOCK"


def test_an_unreadable_inventory_read_leaves_the_count_alone(ready, monkeypatch):
    """A provider that answered without a number must change nothing.

    The adapter reports `verified != 1`, which is CJ's "we hold this but have not
    confirmed it" -- the fixture's own definition of UNKNOWN. Routed through the
    worker rather than the applier directly, because this is the failure mode a
    scheduler is most likely to introduce: a tick that treats every completed
    read as authoritative turns one provider hiccup into a delisted catalogue.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    variant_id = bind_variant(stock_quantity=8)
    before = listing_row()["quantity"]
    ready[0].verified = 0
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="inventory", resource_id=PID, now=time.time() - 1)
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    row = variant_row(variant_id)
    assert row["stock_quantity"] == 8, "an unreadable count is not a count of zero"
    assert listing_row()["quantity"] == before


def test_a_completed_product_read_reprices_the_variant_and_the_buyers_label(ready, monkeypatch):
    """§23, through the worker, including the pairing that charges the buyer.

    The fixture's product costs $2.00 against a stored $1.00, so the read is a
    doubling. What this asserts beyond "the number moved" is that
    `marketplace_listings.price_label` moved with it: the merchant's records and
    the buyer's card are read from different columns, and a repricing that
    updates only `price_cents` charges the old price indefinitely. The retail
    figure itself is the pricing engine's business and is pinned in
    `test_dropship_revisions.py`; what is pinned here is that the two columns
    agree after a worker tick.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    variant_id = bind_variant(stock_quantity=8, cost_cents=100, price_cents=182)
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="product", resource_id=PID, now=time.time() - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    assert counts["reads"] == 1 and counts["revisions"] == 1
    row = variant_row(variant_id)
    assert row["cost_cents"] == 200, "the supplier's new cost reached the variant"
    assert row["price_cents"] > 182, "a cost rise did not leave the retail price behind"
    label = listing_row()["price_label"]
    assert label == f"${row['price_cents'] / 100:.2f}", (
        f"the buyer is still charged {label} while the variant says "
        f"{row['price_cents']}")


def test_a_read_the_merchant_owns_the_price_of_is_not_repriced_by_the_worker(ready, monkeypatch):
    """§44, asserted on the path that would actually violate it.

    A merchant who set their own price has claimed it. The supplier read still
    arrives, still succeeds, and still records the new cost -- what it must not
    do is move the price. This is the only §44 guarantee that a background loop
    can break without anybody watching, which is why it is asserted here and not
    only against the planner.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    variant_id = bind_variant(stock_quantity=8, cost_cents=100, price_cents=182)
    conn = db.connect()
    cur = conn.cursor()
    marketplace_variants.mark_overridden(
        cur, listing_id=int(OWNED_LISTING), seller_user_id=MERCHANT,
        fields=["price_label"])
    conn.commit()
    conn.close()
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="product", resource_id=PID, now=time.time() - 1)
    worker.run_once(adapter_factory=lambda _: ready[0], limit=1)
    row = variant_row(variant_id)
    assert row["cost_cents"] == 200, "the cost is a fact and is still recorded"
    assert row["price_cents"] == 182, "the merchant's price survived the supplier read"


def test_a_failed_revision_does_not_cost_the_job_its_read(ready, monkeypatch):
    """Why `_apply` runs after `_finish` and swallows.

    The read succeeded. Scheduling a retry because the *write* failed would send
    the worker back to CJ -- spending quota on a fresh read of numbers it already
    has -- to fix something that was never a read problem. So the job keeps its
    success: failures stays 0, the snapshot is stored, and the next attempt comes
    at the normal cadence rather than at a backoff.

    The failure is still counted. `revision_failures` is the only evidence that
    the reconciler is broken, and a swallow with no counter is how a subsystem
    goes dark for a month.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    bind_variant(stock_quantity=8)

    def explode(**kwargs):
        raise RuntimeError("listing write failed")

    monkeypatch.setattr(revisions, "apply_supplier_read", explode)
    now = time.time()
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="inventory", resource_id=PID, now=now - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=now)
    assert counts["reads"] == 1 and counts["deferred"] == 0
    assert counts["revisions"] == 0 and counts["revision_failures"] == 1
    job = sync_job("inventory", PID)
    assert job["failures"] == 0 and job["last_verified_at"] == now
    assert job["available_at"] == now + worker.CADENCE["inventory"], (
        "a write failure must not put the job into read backoff")
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM supplier_snapshots WHERE kind='inventory'"
                        ).fetchone()[0] == 1
    conn.close()


def test_a_failed_revision_does_not_abort_the_tick(ready, monkeypatch):
    """A reconciler that can kill a tick takes fulfilment down with it.

    `run_once` drains the supplier outbox *and* reads. They share a tick because
    they share a lease and a network gate, not because they are related -- so a
    raise out of the listings consumer must not stop a paid order from
    dispatching. Asserted by the latch, which is only written when the tick runs
    to its bound.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")

    def explode(**kwargs):
        raise RuntimeError("listing write failed")

    monkeypatch.setattr(revisions, "apply_supplier_read", explode)
    bind_variant(stock_quantity=8)
    result = fulfillment.create_intent(**ready[2])
    now = time.time()
    worker.schedule(connection_id=ready[1]["id"], business_id="biz-a", store_id="store-a",
                    kind="inventory", resource_id=PID, now=now - 1)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=2, now=now)
    assert counts["intents"] == 1 and counts["revision_failures"] == 1
    assert outbox(result["intent_id"])["state"] == "UNKNOWN", "the order still went out"
    assert fulfillment.drain_status(now=now)["completed_at"] == now


def test_a_fulfilment_read_is_never_offered_to_the_reconciler(ready, monkeypatch):
    """Only `product` and `inventory` describe a listing.

    `health`, `shops`, `subscriptions`, `order` and `tracking` are answers about
    the connection or about one parcel. Handing any of them to a function whose
    whole job is to write prices and stock counts would mean asking `normalize`
    to interpret a payload it has never seen -- and `_readings` returns `{}` for
    anything it cannot read, so the mistake would be silent rather than loud.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    seen = []
    monkeypatch.setattr(revisions, "apply_supplier_read",
                        lambda **kwargs: seen.append(kwargs["kind"]) or {"variants": 0})
    now = time.time()
    for kind in ("health", "shops", "subscriptions"):
        worker.schedule(connection_id=ready[1]["id"], business_id="biz-a",
                        store_id="store-a", kind=kind, now=now - 10)
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=3, now=now)
    assert counts["reads"] == 3 and counts["deferred"] == 0
    assert seen == [], f"a connection-level read reached the listings consumer: {seen}"
    assert counts["revisions"] == 0 and counts["revision_failures"] == 0


def test_every_tick_reports_what_it_revised(ready, monkeypatch):
    """The counts are the operator's only view of this subsystem.

    `run_once`'s return value is what `supplier_worker` logs. Two keys, always
    present even on a tick that revised nothing, because a key that only appears
    when work happened cannot be graphed and cannot be alerted on -- and the
    failure this whole file guards against looks exactly like a permanent zero.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    counts = worker.run_once(adapter_factory=lambda _: ready[0], limit=1, now=time.time())
    assert counts["revisions"] == 0 and counts["revision_failures"] == 0
