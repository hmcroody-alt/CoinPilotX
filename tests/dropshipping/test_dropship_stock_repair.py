"""The one-shot stock repair, and the four consequence levels it offers.

What this file is defending
---------------------------
``repair.py`` exists because a transient failure became permanent: a single-flight
lease collision voided inventory for 683 of 719 production variants at import
time, and the reconciler that would have fixed it on the next pass has never run.
The module is therefore a *backlog drainer*, and the things that go wrong with a
backlog drainer are not the things that go wrong with a reconciler.

* **A dry run that lies is worse than no dry run.** The whole point of planning
  before applying is that an operator can read the plan and approve it. If
  ``plan_product`` and the writer could ever disagree, approval would be
  meaningless — so the strongest test here applies a read *twice*, once through
  the planner and once through the database, and asserts the two agree
  variant-for-variant. A re-implementation of the planning rules inside
  ``repair.py`` would pass every other test in this file and fail that one.
* **A checkpoint that arrives late is not a checkpoint.** The situation a
  rollback file exists for is a run that died half way, so the file has to be on
  disk *before* the first UPDATE, not written from the return value. That is
  asserted by crashing a run on purpose and then rolling it back.
* **A repair must not invent product identity.** ``resolve_binding`` fills an
  empty ``provider_variant_id`` once stock makes the choice unambiguous. It must
  never break a genuine tie, never treat ``UNKNOWN`` as a negative, and never
  re-point a binding a merchant already made — the last of which would ship a
  buyer a different colour than the one on the page they bought from.
* **A provider outage must not delist the catalogue.** Inherited from
  ``revisions``, re-asserted here because this module is the thing that would run
  over every product at once, which is exactly when that defect would be
  catastrophic rather than cosmetic.

Why this file runs alone
------------------------
Same reason as its neighbours: it binds ``DATABASE_URL`` to its own temp file at
import, before ``services.db`` computes ``IS_POSTGRES``. One file, one process.

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_stock_repair.py
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-repair-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_NETWORK_ENABLED"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import gateway, repair, revisions  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from services.marketplace_supplier_schema import (  # noqa: E402
    STOCK_IN_STOCK, STOCK_OUT_OF_STOCK, STOCK_UNKNOWN)
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

OWNER_ID = 4001
BUSINESS, STORE, CONNECTION = "biz-a", "store-a", "conn-a"

PID = "2000000001"
PID_2 = "2000000002"
BAD_PID = "193ABEDD-9BB0-4B1F-A4B6-4EB35D87C767"
VID_A, VID_B, VID_C = "3000000001", "3000000002", "3000000003"


# ---------------------------------------------------------------------------
# Provider payloads and a stand-in adapter
# ---------------------------------------------------------------------------

def inventory_rows(*pairs, pid=PID):
    """CJ inventory rows as a bare list -- the shape ``normalize`` actually reads."""
    return [{"vid": vid, "pid": pid, "totalInventoryNum": quantity}
            for vid, quantity in pairs]


class FakeAdapter:
    """Answers ``get_inventory`` and counts the calls, so "no network" is testable."""

    def __init__(self, by_pid=None, fail=None):
        self.by_pid = by_pid or {}
        self.fail = fail or {}
        self.calls = []
        self.background = False

    def get_inventory(self, pid):
        self.calls.append(pid)
        if pid in self.fail:
            raise self.fail[pid]
        return self.by_pid.get(pid, [])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_connection(conn):
    now, later = "2026-09-07T00:00:00Z", "2099-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO business_os_supplier_connections "
        "(id, merchant_id, business_id, store_id, provider, connection_type, "
        " external_account_id, external_shop_id, status, credential_reference, "
        " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
        "VALUES (?,?,?,?,'CJ','API_KEY',?,?,'CONNECTED',?,?,?,'UNKNOWN',?,?)",
        (CONNECTION, str(OWNER_ID), BUSINESS, STORE, f"acct-{CONNECTION}",
         f"shop-{CONNECTION}", f"cred-{CONNECTION}", later, later, now, now))


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    supplier_schema.reset_schema_cache()
    if hasattr(gateway, "reset_schema_cache"):
        gateway.reset_schema_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        seed_production_listings(cur)
        cur.execute("DELETE FROM marketplace_listings")
        supplier_schema.ensure_supplier_schema(cur, force=True)
        connection_schema.ensure_schema(conn)
        _seed_connection(conn)
        conn.commit()
    finally:
        conn.close()
    gateway.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()


def make_listing(listing_id, *, pid=PID, bound=None, rows=(), title="Cotton Tee",
                 quantity=None):
    """A supplier-backed listing in the state the production backlog is in.

    ``rows`` are ``(provider_variant_id, stock_state, stock_quantity)``. The
    default production shape is every variant ``UNKNOWN`` with no count, which is
    what 683 of 719 rows look like.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, status, "
            "price_label, quantity, created_at) VALUES (?,?,?,'draft',?,?,?)",
            (listing_id, OWNER_ID, title, "", quantity, "2026-09-16T00:00:00Z"))
        for position, (vid, state, count) in enumerate(rows):
            variants.upsert_variant(
                cur, listing_id=listing_id, seller_user_id=OWNER_ID,
                options=[{"name": "Color", "value": f"c{position}"}],
                provider_variant_id=vid,
                price_cents=2400, cost_cents=900, currency="USD",
                stock_state=state, stock_quantity=count, position=position)
        variants.link_source(
            cur, listing_id=listing_id, seller_user_id=OWNER_ID, provider="cj",
            provider_product_id=pid, provider_variant_id=bound,
            supplier_connection_id=CONNECTION, business_id=BUSINESS,
            store_id=STORE, supplier_cost_cents=900)
        conn.commit()
    finally:
        conn.close()
    return listing_id


UNKNOWN_PAIR = ((VID_A, STOCK_UNKNOWN, None), (VID_B, STOCK_UNKNOWN, None))


def stored(listing_id):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM {variants.VARIANT_TABLE} WHERE listing_id=? "
            f"ORDER BY position", (listing_id,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute(f"SELECT * FROM {variants.SOURCE_TABLE} WHERE listing_id=?",
                    (listing_id,))
        source = dict(cur.fetchone())
        cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                    (listing_id,))
        listing = dict(cur.fetchone())
    finally:
        conn.close()
    return {"variants": rows, "source": source, "listing": listing}


def factory(adapter):
    return lambda *_scope: adapter


# ---------------------------------------------------------------------------
# The pure half: planning and binding
# ---------------------------------------------------------------------------

def source_row(listing_id=1, *, bound=None, pid=PID):
    return {"id": listing_id, "listing_id": listing_id, "seller_user_id": OWNER_ID,
            "provider": "cj", "provider_product_id": pid,
            "provider_variant_id": bound, "fulfillment_mode": "DROPSHIP",
            "supplier_connection_id": CONNECTION, "business_id": BUSINESS,
            "store_id": STORE}


def variant_row(variant_id, vid, state=STOCK_UNKNOWN, count=None):
    return {"id": variant_id, "listing_id": 1, "seller_user_id": OWNER_ID,
            "provider_variant_id": vid, "stock_state": state,
            "stock_quantity": count}


def test_a_variant_the_read_never_mentioned_is_not_a_change():
    """A short provider list is a short list, not a sell-out."""
    plan = repair.plan_product(
        source_row(), [variant_row(1, VID_A), variant_row(2, VID_B)],
        {VID_A: (STOCK_IN_STOCK, 40)})
    assert plan["unmentioned"] == 1
    assert [c["provider_variant_id"] for c in plan["changes"]] == [VID_A]


def test_a_reading_that_changes_nothing_is_not_reported_as_a_change():
    plan = repair.plan_product(
        source_row(), [variant_row(1, VID_A, STOCK_IN_STOCK, 40)],
        {VID_A: (STOCK_IN_STOCK, 40)})
    assert plan["changes"] == []


def test_unknown_becoming_counted_stock_is_the_repair_this_module_exists_for():
    plan = repair.plan_product(
        source_row(), [variant_row(1, VID_A)], {VID_A: (STOCK_IN_STOCK, 40)})
    change = plan["changes"][0]
    assert change["stock_state"] == {"before": STOCK_UNKNOWN, "after": STOCK_IN_STOCK}
    assert change["units"] == {"before": None, "after": 40}


def test_an_already_bound_listing_is_never_rebound():
    """``link_source`` would refuse; this refuses earlier, and says so."""
    assert repair.resolve_binding(
        source_row(bound=VID_A),
        [variant_row(1, VID_A, STOCK_IN_STOCK, 5),
         variant_row(2, VID_B, STOCK_IN_STOCK, 5)],
        {VID_A: (STOCK_IN_STOCK, 5), VID_B: (STOCK_IN_STOCK, 5)}) is None


def test_a_genuine_tie_is_still_refused_after_the_stock_lands():
    """Two orderable colours is a real question and this repair has no answer."""
    assert repair.resolve_binding(
        source_row(),
        [variant_row(1, VID_A), variant_row(2, VID_B)],
        {VID_A: (STOCK_IN_STOCK, 40), VID_B: (STOCK_IN_STOCK, 12)}) is None


def test_the_sole_survivor_is_bound_because_nothing_else_was_orderable():
    assert repair.resolve_binding(
        source_row(),
        [variant_row(1, VID_A), variant_row(2, VID_B)],
        {VID_A: (STOCK_IN_STOCK, 40), VID_B: (STOCK_OUT_OF_STOCK, 0)}) == VID_A


def test_unknown_is_not_a_negative_and_cannot_narrow_a_choice():
    """One in-stock variant beside an unreadable one is still a choice.

    Otherwise an inventory outage would decide what the merchant sells.
    """
    assert repair.resolve_binding(
        source_row(),
        [variant_row(1, VID_A), variant_row(2, VID_B)],
        {VID_A: (STOCK_IN_STOCK, 40)}) is None


def test_a_single_variant_listing_binds_because_it_is_not_a_choice():
    assert repair.resolve_binding(
        source_row(), [variant_row(1, VID_A)],
        {VID_A: (STOCK_IN_STOCK, 40)}) == VID_A


# ---------------------------------------------------------------------------
# audit: no network, no write
# ---------------------------------------------------------------------------

def test_audit_counts_the_backlog_without_reading_the_supplier():
    make_listing(11, rows=UNKNOWN_PAIR)
    make_listing(12, pid=PID_2, bound=VID_C,
                 rows=((VID_C, STOCK_IN_STOCK, 7),))
    report = repair.run(mode=repair.AUDIT)
    census = report["census"]
    assert census["sources"] == 2
    assert census["variants"] == 3
    assert census["unknown_stock"] == 2
    assert census["in_stock"] == 1
    assert census["unbound"] == 1


def test_audit_needs_no_network_flag_at_all():
    """The only mode safe to run anywhere must not be gated on reaching CJ."""
    make_listing(11, rows=UNKNOWN_PAIR)
    previous = os.environ.pop("CJ_NETWORK_ENABLED")
    try:
        assert repair.run(mode=repair.AUDIT)["census"]["sources"] == 1
    finally:
        os.environ["CJ_NETWORK_ENABLED"] = previous


def test_audit_changes_nothing():
    make_listing(11, rows=UNKNOWN_PAIR)
    before = stored(11)
    repair.run(mode=repair.AUDIT)
    assert stored(11) == before


def test_a_pid_cj_could_not_have_issued_is_counted_not_attempted():
    make_listing(11, pid=BAD_PID, rows=UNKNOWN_PAIR)
    assert repair.run(mode=repair.AUDIT)["census"]["unreachable_pid"] == 1

    adapter = FakeAdapter()
    report = repair.run(mode=repair.DRY_RUN, adapter_factory=factory(adapter))
    assert adapter.calls == []
    assert report["skipped"][0]["listing_id"] == 11


# ---------------------------------------------------------------------------
# dry-run: reads, plans, writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_reads_the_supplier_and_still_writes_nothing():
    make_listing(11, rows=UNKNOWN_PAIR)
    before = stored(11)
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 12))})

    report = repair.run(mode=repair.DRY_RUN, adapter_factory=factory(adapter))

    assert adapter.calls == [PID]
    assert report["variants_changed"] == 2
    assert stored(11) == before
    assert "after" not in report and report.get("checkpoint") is None


def test_dry_run_marks_the_tie_it_could_break_without_breaking_it():
    make_listing(11, rows=UNKNOWN_PAIR)
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 0))})

    report = repair.run(mode=repair.DRY_RUN, adapter_factory=factory(adapter))

    assert report["bindings_resolvable"] == 1
    assert stored(11)["source"]["provider_variant_id"] is None


def test_a_provider_failure_is_reported_as_a_code_and_never_as_a_body():
    make_listing(11, rows=UNKNOWN_PAIR)
    adapter = FakeAdapter(fail={PID: SupplierError("request_in_progress",
                                                   http_status=429)})
    report = repair.run(mode=repair.DRY_RUN, adapter_factory=factory(adapter))

    assert report["blocked"] == [{"listing_id": 11, "reason": "request_in_progress"}]
    assert report["planned"] == []


# ---------------------------------------------------------------------------
# The property the whole design rests on
# ---------------------------------------------------------------------------

def test_the_plan_a_dry_run_prints_is_the_change_an_apply_makes():
    """Approval is meaningless if the two can disagree, so they are compared.

    Both modes see the identical provider answer; the planner's forecast is then
    checked variant-for-variant against what the database actually holds after
    the writer ran. A ``plan_product`` that re-derived the stock rules instead of
    delegating to ``revisions.plan_stock_revision`` would pass every other test
    in this file and fail this one.
    """
    reading = inventory_rows((VID_A, 40), (VID_B, 0))
    make_listing(11, rows=UNKNOWN_PAIR)

    forecast = repair.run(mode=repair.DRY_RUN,
                          adapter_factory=factory(FakeAdapter({PID: reading})))
    predicted = {c["provider_variant_id"]: c["stock_state"]["after"]
                 for c in forecast["planned"][0]["changes"]}

    repair.run(mode=repair.APPLY,
               adapter_factory=factory(FakeAdapter({PID: reading})))
    actual = {r["provider_variant_id"]: r["stock_state"] for r in stored(11)["variants"]}

    assert predicted == {VID_A: STOCK_IN_STOCK, VID_B: STOCK_OUT_OF_STOCK}
    assert actual == predicted


# ---------------------------------------------------------------------------
# canary and apply
# ---------------------------------------------------------------------------

def test_canary_touches_only_the_products_its_limit_allows():
    make_listing(11, rows=UNKNOWN_PAIR)
    make_listing(12, pid=PID_2, rows=((VID_C, STOCK_UNKNOWN, None),))
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 12)),
                           PID_2: inventory_rows((VID_C, 9), pid=PID_2)})

    repair.run(mode=repair.CANARY, limit=1, adapter_factory=factory(adapter))

    assert adapter.calls == [PID]
    assert stored(11)["variants"][0]["stock_state"] == STOCK_IN_STOCK
    assert stored(12)["variants"][0]["stock_state"] == STOCK_UNKNOWN


def test_apply_drains_the_backlog_and_reports_both_sides_of_it():
    make_listing(11, rows=UNKNOWN_PAIR)
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 12))})

    report = repair.run(mode=repair.APPLY, adapter_factory=factory(adapter))

    assert report["before"]["unknown_stock"] == 2
    assert report["after"]["unknown_stock"] == 0
    assert report["after"]["in_stock"] == 2
    assert [r["stock_quantity"] for r in stored(11)["variants"]] == [40, 12]


def test_apply_binds_the_listing_the_landed_stock_made_unambiguous():
    """§3.3's remedy: the tie was an artefact of UNKNOWN, so it dissolves."""
    make_listing(11, rows=UNKNOWN_PAIR)
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 0))})

    report = repair.run(mode=repair.APPLY, adapter_factory=factory(adapter))

    assert stored(11)["source"]["provider_variant_id"] == VID_A
    assert report["bound"] == [{"listing_id": 11, "provider_variant_id": VID_A}]


def test_apply_leaves_a_real_tie_unbound_for_the_merchant_to_settle():
    make_listing(11, rows=UNKNOWN_PAIR)
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 12))})

    repair.run(mode=repair.APPLY, adapter_factory=factory(adapter))

    assert stored(11)["source"]["provider_variant_id"] is None


def test_a_merchants_own_binding_survives_a_repair_that_disagrees_with_it():
    """Re-pointing this would ship a buyer a colour they did not choose."""
    make_listing(11, bound=VID_B,
                 rows=((VID_A, STOCK_UNKNOWN, None), (VID_B, STOCK_UNKNOWN, None)))
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 0))})

    repair.run(mode=repair.APPLY, adapter_factory=factory(adapter))

    assert stored(11)["source"]["provider_variant_id"] == VID_B


def test_an_outage_does_not_delist_the_catalogue():
    """The inherited safety property, re-asserted where it would be worst.

    This module is the one thing that runs over every product at once, so a
    planner that treated an unreadable read as zero would empty the whole store
    in a single invocation rather than one product at a time.
    """
    make_listing(11, rows=((VID_A, STOCK_IN_STOCK, 40),))
    adapter = FakeAdapter({PID: [{"vid": VID_A, "pid": PID,
                                  "totalInventoryNum": None,
                                  "stockStatus": "unknown"}]})

    repair.run(mode=repair.APPLY, adapter_factory=factory(adapter))

    row = stored(11)["variants"][0]
    assert row["stock_state"] == STOCK_UNKNOWN
    assert row["stock_quantity"] == 40, "the last known count must survive"


def test_a_connection_that_cannot_be_resolved_blocks_rather_than_raises():
    make_listing(11, rows=UNKNOWN_PAIR)

    def explode(*_scope):
        raise SupplierError("reauth_required", http_status=409)

    report = repair.run(mode=repair.DRY_RUN, adapter_factory=explode)
    assert report["blocked"][0]["listing_id"] == 11
    assert report["planned"] == []


def test_the_repair_reads_in_the_background_so_a_buyer_keeps_the_quota(monkeypatch):
    """Asserted on the production path, with no ``adapter_factory`` to fake it.

    ``adapter.background`` is what the quota controller de-prioritises. A test
    that passed its own factory would be asserting on the test.
    """
    from services.business_os.suppliers import connections

    make_listing(11, rows=UNKNOWN_PAIR)
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 12))})
    assert adapter.background is False  # positive control: the flag starts down
    scopes = []

    def fake_worker_adapter(connection_id, business_id, store_id):
        scopes.append((connection_id, business_id, store_id))
        return adapter

    monkeypatch.setattr(connections, "worker_adapter", fake_worker_adapter)

    report = repair.run(mode=repair.DRY_RUN)
    assert scopes == [(CONNECTION, BUSINESS, STORE)]
    assert adapter.background is True
    assert adapter.calls == [PID]
    assert report["planned"][0]["listing_id"] == 11


# ---------------------------------------------------------------------------
# Checkpoint and rollback
# ---------------------------------------------------------------------------

def test_a_checkpoint_round_trips_every_field_the_repair_writes(tmp_path):
    make_listing(11, rows=UNKNOWN_PAIR, quantity=3)
    before = stored(11)
    path = str(tmp_path / "ckpt.json")
    adapter = FakeAdapter({PID: inventory_rows((VID_A, 40), (VID_B, 0))})

    repair.run(mode=repair.APPLY, adapter_factory=factory(adapter),
               checkpoint_path=path)
    assert stored(11) != before

    with open(path, encoding="utf-8") as handle:
        repair.restore(json.load(handle))

    after = stored(11)
    assert [r["stock_state"] for r in after["variants"]] == \
           [r["stock_state"] for r in before["variants"]]
    assert [r["stock_quantity"] for r in after["variants"]] == \
           [r["stock_quantity"] for r in before["variants"]]
    assert after["source"]["provider_variant_id"] is None
    assert after["listing"]["quantity"] == before["listing"]["quantity"]


def test_the_checkpoint_is_on_disk_before_the_first_write(tmp_path):
    """The situation a rollback file is for is a run that did not finish."""
    make_listing(11, rows=UNKNOWN_PAIR)
    make_listing(12, pid=PID_2, rows=((VID_C, STOCK_UNKNOWN, None),))
    path = str(tmp_path / "ckpt.json")

    # KeyboardInterrupt, not a provider error: a provider error is contained on
    # purpose so one bad product cannot abort the backlog. What a checkpoint is
    # for is the run that is killed outright, part way through.
    class DiesHalfWay(FakeAdapter):
        def get_inventory(self, pid):
            if pid == PID_2:
                raise KeyboardInterrupt("process killed")
            return super().get_inventory(pid)

    adapter = DiesHalfWay({PID: inventory_rows((VID_A, 40), (VID_B, 12))})
    with pytest.raises(KeyboardInterrupt):
        repair.run(mode=repair.APPLY, adapter_factory=factory(adapter),
                   checkpoint_path=path)

    assert os.path.exists(path), "a checkpoint written at the end is no checkpoint"
    assert stored(11)["variants"][0]["stock_state"] == STOCK_IN_STOCK

    with open(path, encoding="utf-8") as handle:
        counts = repair.restore(json.load(handle))
    assert counts["variants"] == 3
    assert stored(11)["variants"][0]["stock_state"] == STOCK_UNKNOWN


def test_restore_is_idempotent():
    make_listing(11, rows=UNKNOWN_PAIR)
    conn = db.connect()
    try:
        found = repair.targets(conn)
    finally:
        conn.close()
    saved = repair.checkpoint(found)

    repair.run(mode=repair.APPLY,
               adapter_factory=factory(FakeAdapter({PID: inventory_rows((VID_A, 40))})))
    repair.restore(saved)
    once = stored(11)
    repair.restore(saved)
    assert stored(11) == once


def test_a_dry_run_never_produces_a_checkpoint_because_it_writes_nothing():
    make_listing(11, rows=UNKNOWN_PAIR)
    report = repair.run(
        mode=repair.DRY_RUN,
        adapter_factory=factory(FakeAdapter({PID: inventory_rows((VID_A, 40))})))
    assert report.get("checkpoint") is None


# ---------------------------------------------------------------------------
# The report itself
# ---------------------------------------------------------------------------

def test_the_report_carries_no_cost_credential_or_provider_body():
    """It is written to be pasted into a mission document."""
    make_listing(11, rows=UNKNOWN_PAIR)
    report = repair.run(
        mode=repair.APPLY,
        adapter_factory=factory(FakeAdapter({PID: inventory_rows((VID_A, 40))})))
    text = repair.dumps(report)
    for forbidden in ("cost_cents", "credential", "cred-", "totalInventoryNum",
                      "supplier_cost"):
        assert forbidden not in text, forbidden
    assert repair.summarize(report)


def test_an_unknown_mode_is_refused_before_anything_is_read():
    with pytest.raises(ValueError):
        repair.run(mode="delete-everything")
