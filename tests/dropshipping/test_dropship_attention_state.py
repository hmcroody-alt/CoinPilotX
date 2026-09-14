"""A live listing that went wrong overnight must say so where the merchant looks. §26/§31.

What this file is defending
---------------------------
``test_dropship_reprice_audit.py`` made an overnight reprice *recordable*. This
file is about the other half of the same failure, and it is the worse half.

Before this, a supplier could quadruple their cost at 3am, ``revisions`` would
correctly conclude ``SELLING_BELOW_COST``, and then throw that conclusion away:
it was de-duplicated into a per-tick list, stripped of any listing identity,
handed to ``worker`` — which does not read it — and dropped. The listing's
``sync_state`` stayed ``SYNCED``, because the sync had in fact worked perfectly.

So the merchant's "Sync & issues" screen, which keys entirely on ``sync_state``,
showed no issue. Not silence: a false all-clear, on the one screen whose entire
job is to answer "what is currently wrong between this store and a supplier".
A screen that affirmatively says nothing is wrong is worse than one that says
nothing at all, because the merchant stops checking the product.

The properties here:

* **The conclusion is durable.** It survives the tick that reached it, on the row
  every merchant surface already reads.
* **It is not ``sync_state``.** SYNCED + ``SELLING_BELOW_COST`` is a real and
  common state. Folding either into the other makes "the supplier is unreachable"
  and "the supplier doubled their price" the same fact.
* **It clears.** A reason that outlives its cause trains the merchant to ignore
  the flag, which costs them the next real one.
* **A read only clears what it can see.** A ``product`` read knows nothing about a
  warehouse, so it must not erase a sell-out that is still true — and the reverse.
* **The tile and the detail screen agree.** Both read the same column through the
  same parser, for the same reason they both derive the cover image one way.
* **§27.** This is a merchant-scoped payload and the reasons are the merchant's
  own vocabulary. No provider blob, no credential, no supplier-side identifier
  arrives with them.

Why this file runs alone
------------------------
``DATABASE_URL`` is bound to its own temp file at import, before ``services.db``
computes ``IS_POSTGRES``.

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_attention_state.py
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-attention-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, gateway, import_cart, importer, revisions, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

# The applier's own end-to-end harness, reused rather than re-typed. Everything
# borrowed resolves the database through `db.connect()` at call time, so it
# follows `DATABASE_URL` to this file's temp path.
from tests.dropshipping.test_dropship_revision_apply import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, OWNER_ID, PID, STORE, VID, FakeProvider,
    _seed_connection, _seed_tenancy, apply_read, bound_variant, cj_inventory,
    cj_product, counted_inventory, import_one, listing_row, publish, rows,
    set_store_policy, source_row)

# That import ran the applier suite's header, which pointed DATABASE_URL at *its*
# temp file. Restored, or every query below runs against a database this file
# never truncates.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


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
    store_policy.ensure_schema()
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

def live_listing(provider, **policy):
    if policy:
        set_store_policy(**policy)
    listing_id = import_one(provider)
    publish(listing_id)
    return listing_id


def cost(value="16.40"):
    return cj_product(cost=value, second_cost=value)


def sold_out():
    return counted_inventory(0)


def apply_at(kind, payload, when):
    """``apply_read`` with an explicit clock, which its harness does not expose."""
    return revisions.apply_supplier_read(
        connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE,
        kind=kind, resource_id=PID, payload=payload, now=when)


def own_the_price(listing_id):
    """The merchant claims ``price_label``, exactly as the seller reprice route does."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        variants.mark_overridden(cur, listing_id=listing_id,
                                 seller_user_id=int(bound_variant(listing_id)["seller_user_id"]),
                                 fields=["price_label"])
        conn.commit()
    finally:
        conn.close()


def stored(listing_id):
    """The reasons on the source row, read the way every caller reads them."""
    return revisions.stored_attention(source_row(listing_id))


def raw(listing_id):
    return source_row(listing_id)["attention_json"]


def tile(listing_id):
    """The row `DropshippingSyncScreen` renders, straight off the list route."""
    listed = drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    matches = [row for row in listed["items"] if int(row["id"]) == int(listing_id)]
    assert matches, f"listing {listing_id} absent from the imported list"
    return matches[0]


def detail(listing_id):
    return drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            context=CONTEXT)


def raise_the_price(listing_id, cents):
    """The merchant sets their own retail price, the one thing that fixes a collapse.

    Written straight to ``price_cents`` because that is the field
    ``plan_cost_revision`` measures the margin against, and the seller reprice
    route's own path through pricing is tested elsewhere. What matters here is
    that the price moved without the *supplier* moving, which is precisely the
    case that touches no variant on the next tick.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE {variants.VARIANT_TABLE} SET price_cents=? WHERE listing_id=?",
                    (int(cents), listing_id))
        conn.commit()
    finally:
        conn.close()


def collapse(provider, listing_id):
    """Drive the listing into SELLING_BELOW_COST and return the reasons stored."""
    own_the_price(listing_id)
    apply_read("product", cost("400.00"))
    return stored(listing_id)


# ---------------------------------------------------------------------------
# 1. The false all-clear
# ---------------------------------------------------------------------------

def test_a_margin_collapse_is_recorded_on_the_row_the_merchant_reads(provider):
    """The defect this file exists for, stated as the property that fixes it."""
    listing_id = live_listing(provider)

    assert collapse(provider, listing_id) == [revisions.SELLING_BELOW_COST]


def test_a_collapsed_listing_still_reports_a_healthy_sync_state(provider):
    """Not a bug being tolerated -- the reason the reasons need their own column.

    The read succeeded, the provider answered, the data is current. ``SYNCED`` is
    the truthful answer to the question ``sync_state`` asks. Writing STALE or ERROR
    here to make the product look wrong would corrupt the one column that tells an
    operator whether the supplier is reachable.
    """
    listing_id = live_listing(provider)

    collapse(provider, listing_id)

    assert source_row(listing_id)["sync_state"] == supplier_schema.SYNC_SYNCED
    assert source_row(listing_id)["last_sync_error"] is None
    assert stored(listing_id) == [revisions.SELLING_BELOW_COST], \
        "the pair is the point: a perfectly synced listing that is losing money"


def test_the_sync_screens_own_row_carries_the_reason(provider):
    """`list_drafts` is what `listImportedProducts` serves; the reason must survive it."""
    listing_id = live_listing(provider)

    collapse(provider, listing_id)

    assert tile(listing_id)["attention"] == [revisions.SELLING_BELOW_COST]


def test_the_tile_and_the_detail_screen_do_not_disagree(provider):
    listing_id = live_listing(provider)

    collapse(provider, listing_id)

    assert tile(listing_id)["attention"] == detail(listing_id)["supplier"]["attention"]


def test_a_healthy_listing_claims_nothing(provider):
    """An empty list, not a missing key. The screens branch on emptiness."""
    listing_id = live_listing(provider)

    apply_read("product", cost("9.10"))

    assert stored(listing_id) == []
    assert tile(listing_id)["attention"] == []
    assert detail(listing_id)["supplier"]["attention"] == []


# ---------------------------------------------------------------------------
# 2. It clears
# ---------------------------------------------------------------------------

def test_a_reason_disappears_when_the_supplier_price_comes_back_down(provider):
    """A flag that outlives its cause is a flag the merchant learns to ignore."""
    listing_id = live_listing(provider)
    collapse(provider, listing_id)
    assert stored(listing_id) == [revisions.SELLING_BELOW_COST]

    apply_read("product", cost("9.10"))

    assert stored(listing_id) == []
    assert tile(listing_id)["attention"] == []


def test_the_read_that_resolves_a_problem_is_usually_the_read_that_writes_nothing(provider):
    """Clearing cannot be conditional on a variant having been written.

    `plan_cost_revision` returns both cents as ``None`` for an unreadable cost, so
    the tick that raises COST_UNAVAILABLE touches no variant -- and so does the
    tick where the cost is simply unchanged. If the attention write rode on
    ``result["variants"]`` the reason could neither be raised nor cleared by the
    two ticks that matter most.
    """
    listing_id = live_listing(provider)
    collapse(provider, listing_id)

    # The same cost again: UNCHANGED, no variant written, margin still negative.
    apply_read("product", cost("400.00"))
    assert stored(listing_id) == [revisions.SELLING_BELOW_COST], \
        "an unchanged tick must restate the problem, not drop it"


def test_the_merchants_own_fix_is_seen_by_a_tick_that_writes_nothing(provider):
    """The other half of the same rule, and the one a merchant actually lives.

    A merchant who reads SELLING_BELOW_COST and raises their price has fixed it.
    The supplier has not moved, so the next reconciliation returns UNCHANGED and
    writes no variant at all -- and if the attention write were gated on
    ``result["variants"]`` the flag would stay up forever, over a listing that is
    now perfectly profitable. A warning that never comes down is a warning nobody
    reads the next time.
    """
    listing_id = live_listing(provider)
    assert collapse(provider, listing_id) == [revisions.SELLING_BELOW_COST]

    raise_the_price(listing_id, 90000)

    # Same supplier cost as the collapse: UNCHANGED, zero variants written.
    apply_read("product", cost("400.00"))

    assert stored(listing_id) == [], "the merchant fixed it and the row still says otherwise"
    assert tile(listing_id)["attention"] == []


def test_a_cleared_reason_does_not_survive_in_the_raw_column(provider):
    """Cleared means the stored text is an empty list, not a stale one hidden by the parser."""
    listing_id = live_listing(provider)
    collapse(provider, listing_id)

    apply_read("product", cost("9.10"))

    assert json.loads(raw(listing_id)) == []


# ---------------------------------------------------------------------------
# 3. A read only speaks for what it can see
# ---------------------------------------------------------------------------

def test_a_product_read_does_not_erase_a_sell_out_that_is_still_true(provider):
    """The whole reason the reasons are partitioned by read kind.

    A supplier who sold out on Monday and held their price all week would have the
    sell-out wiped by the very next product read, on a 900-3600s cadence, and the
    merchant would never see it. A product read knows costs. It knows nothing
    whatever about a warehouse.
    """
    listing_id = live_listing(provider)
    apply_read("inventory", sold_out())
    assert stored(listing_id) == [revisions.SUPPLIER_OUT_OF_STOCK]

    apply_read("product", cost("9.10"))

    assert stored(listing_id) == [revisions.SUPPLIER_OUT_OF_STOCK]


def test_an_inventory_read_does_not_erase_a_margin_collapse(provider):
    """The same rule in the other direction, which is the half easier to forget."""
    listing_id = live_listing(provider)
    collapse(provider, listing_id)

    apply_read("inventory", counted_inventory(7))

    assert stored(listing_id) == [revisions.SELLING_BELOW_COST]


def test_both_families_can_be_true_at_once_and_are_ordered_stably(provider):
    """Two problems is a normal state, and the stored order must not depend on tick order."""
    listing_id = live_listing(provider)
    collapse(provider, listing_id)
    apply_read("inventory", sold_out())

    assert stored(listing_id) == [revisions.SELLING_BELOW_COST,
                                  revisions.SUPPLIER_OUT_OF_STOCK]
    assert stored(listing_id) == [reason for reason in revisions.ATTENTION_REASONS
                                  if reason in stored(listing_id)], \
        "declaration order, not the order the planner happened to append in"


def test_an_inventory_read_clears_only_the_stock_half(provider):
    listing_id = live_listing(provider)
    collapse(provider, listing_id)
    apply_read("inventory", sold_out())

    apply_read("inventory", counted_inventory(12))

    assert stored(listing_id) == [revisions.SELLING_BELOW_COST]


# ---------------------------------------------------------------------------
# 4. The partition itself
# ---------------------------------------------------------------------------

def test_every_reason_belongs_to_exactly_one_read():
    """The assertion that stops a seventh reason being raised and never cleared."""
    assert set(revisions.COST_REASONS) | set(revisions.STOCK_REASONS) == \
        set(revisions.ATTENTION_REASONS)
    assert not set(revisions.COST_REASONS) & set(revisions.STOCK_REASONS)


def test_merge_replaces_its_own_family_wholesale_and_leaves_the_other_alone():
    stored_now = [revisions.SELLING_BELOW_COST, revisions.SUPPLIER_OUT_OF_STOCK]

    assert revisions.merge_attention(stored_now, "product", []) == \
        [revisions.SUPPLIER_OUT_OF_STOCK]
    assert revisions.merge_attention(stored_now, "inventory", []) == \
        [revisions.SELLING_BELOW_COST]


def test_the_order_is_the_declared_one_and_not_the_alphabet():
    """Sorting would look identical on most pairs, which is what makes it a trap.

    ``ATTENTION_REASONS`` is declared worst-first, so a merchant scanning a
    product reads the thing costing them money before the thing merely unknown.
    ``sorted()`` produces the same list for several pairs and a different one
    here: MARGIN_LOST precedes COST_UNAVAILABLE by declaration and follows it by
    alphabet. Pinning a pair that disagrees is the only way this file can tell
    the two implementations apart.
    """
    merged = revisions.merge_attention(
        [], "product", [revisions.COST_UNAVAILABLE, revisions.MARGIN_LOST])
    assert merged == [revisions.MARGIN_LOST, revisions.COST_UNAVAILABLE]
    assert merged != sorted(merged), \
        "this pair no longer distinguishes declaration order from sorting"


def test_merge_ignores_a_reason_the_read_has_no_standing_to_raise():
    """A cost read claiming a sell-out would be a fact nothing can ever clear."""
    assert revisions.merge_attention([], "product", [revisions.SUPPLIER_OUT_OF_STOCK]) == []


def test_merge_deduplicates_a_reason_raised_by_several_variants():
    """Three siblings below cost is one problem with the listing, not three."""
    raised = [revisions.SELLING_BELOW_COST] * 3
    assert revisions.merge_attention([], "product", raised) == \
        [revisions.SELLING_BELOW_COST]


def test_an_unknown_read_kind_changes_nothing():
    stored_now = [revisions.SELLING_BELOW_COST]
    assert revisions.merge_attention(stored_now, "tracking", [revisions.MARGIN_LOST]) == \
        stored_now


# ---------------------------------------------------------------------------
# 5. Reading a column that predates the column
# ---------------------------------------------------------------------------

def test_a_row_imported_before_this_column_existed_reads_as_no_problems():
    assert revisions.stored_attention({"attention_json": None}) == []
    assert revisions.stored_attention({}) == []
    assert revisions.stored_attention(None) == []


def test_a_malformed_blob_does_not_take_down_the_reprice():
    """The price was perfectly readable. Raising here would strand it over a log field."""
    assert revisions.stored_attention({"attention_json": "{not json"}) == []
    assert revisions.stored_attention({"attention_json": '"SELLING_BELOW_COST"'}) == []
    assert revisions.stored_attention({"attention_json": "[]"}) == []


def test_a_reason_this_version_does_not_recognise_is_dropped_rather_than_shown():
    """A downgrade must not put an untranslatable code in front of a merchant."""
    blob = json.dumps(["SELLING_BELOW_COST", "REASON_FROM_THE_FUTURE"])
    assert revisions.stored_attention({"attention_json": blob}) == \
        [revisions.SELLING_BELOW_COST]


def test_a_legacy_row_gains_its_reasons_on_the_next_read(provider):
    """No backfill: the reconciler that runs every 900-3600s is the backfill."""
    listing_id = live_listing(provider)
    conn = db.connect()
    try:
        conn.execute(f"UPDATE {variants.SOURCE_TABLE} SET attention_json=NULL "
                     f"WHERE listing_id=?", (listing_id,))
        conn.commit()
    finally:
        conn.close()

    collapse(provider, listing_id)

    assert stored(listing_id) == [revisions.SELLING_BELOW_COST]


# ---------------------------------------------------------------------------
# 6. Restraint
# ---------------------------------------------------------------------------

def test_a_tick_that_concluded_nothing_new_does_not_touch_the_row(provider):
    """Ninety-six writes a day that restate the same list would make every source
    row look permanently freshly-modified to anything sorting or diffing on
    ``updated_at``."""
    listing_id = live_listing(provider)
    apply_read("product", cost("9.10"))
    before = source_row(listing_id)["updated_at"]

    apply_at("product", cost("9.10"), 9_999_999)

    assert source_row(listing_id)["updated_at"] == before


def test_a_tick_that_concluded_something_new_does_touch_it(provider):
    """The other half of the same guard, or "unchanged" would mean "never written"."""
    listing_id = live_listing(provider)
    apply_read("product", cost("9.10"))
    before = source_row(listing_id)["updated_at"]

    own_the_price(listing_id)
    apply_at("product", cost("400.00"), 9_999_999)

    assert source_row(listing_id)["updated_at"] != before


# ---------------------------------------------------------------------------
# 7. §27 -- what rides along
# ---------------------------------------------------------------------------

def test_the_reasons_are_the_merchants_vocabulary_and_nothing_else(provider):
    """Whatever is stored must be drawn from the declared set, so no provider text,
    identifier or error string can reach a merchant screen through this column."""
    listing_id = live_listing(provider)
    collapse(provider, listing_id)
    apply_read("inventory", sold_out())

    for reason in json.loads(raw(listing_id)):
        assert reason in revisions.ATTENTION_REASONS


def test_the_tile_gained_a_reason_list_and_not_the_raw_column(provider):
    """`attention_json` is storage. Serving it would hand every client a string it
    has to parse, and a second parser is a second opinion about a malformed blob."""
    listing_id = live_listing(provider)
    collapse(provider, listing_id)

    row = tile(listing_id)
    assert "attention_json" not in row
    assert isinstance(row["attention"], list)
