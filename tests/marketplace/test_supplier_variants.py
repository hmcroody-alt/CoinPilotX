"""Variants and supplier provenance — the invariants that must not be simplified away.

What this file is defending
---------------------------
``COMMERCE_DROPSHIPPING_FOUNDATION_MAP.md`` found that the live product row is
flat and that its inventory column cannot say "unknown": ``quantity IS NULL``
reads as out-of-stock, so a failed supplier sync is indistinguishable from a
sell-out. ``services/marketplace_variants.py`` adds the missing unit and, more
importantly, keeps three distinctions that every cheaper implementation loses:

* unknown stock is not out-of-stock,
* unknown cost is not zero cost,
* "not yours" and "not there" are the same refusal.

Each of those is one line away from being wrong in a way no obvious test
notices. ``scripts/marketplace/supplier_variant_mutation_battery.py`` proves that
by making each simplification and requiring this file to fail. A test here that
no mutant can break is not carrying its weight and should be deleted or
strengthened.

On the fixture
--------------
Every test builds the tables from ``ensure_supplier_schema`` rather than from
hand-written DDL, so the schema under test is the one production would get. The
autouse cache reset is not optional: ``ensure_supplier_schema`` keeps a
process-global "already done" flag, so a file that ran earlier in the session
would otherwise short-circuit the ensure and let these tests pass against
whatever tables happened to already exist.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_supplier_schema as schema
from services import marketplace_variants as variants

SELLER = 1001
OTHER_SELLER = 2002


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema.reset_schema_cache()
    yield
    schema.reset_schema_cache()


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE marketplace_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_user_id INTEGER,
            title TEXT,
            price_label TEXT,
            status TEXT,
            approval_status TEXT,
            quantity INTEGER
        )
    """)
    cursor.execute(
        "INSERT INTO marketplace_listings (id, seller_user_id, title, price_label, status, approval_status) "
        "VALUES (10, ?, 'Tee', '$20.00', 'draft', 'draft')", (SELLER,))
    cursor.execute(
        "INSERT INTO marketplace_listings (id, seller_user_id, title, price_label, status, approval_status) "
        "VALUES (20, ?, 'Other tee', '$30.00', 'draft', 'draft')", (OTHER_SELLER,))
    result = schema.ensure_supplier_schema(cursor, force=True)
    assert result["status"] == schema.STATUS_READY, result
    yield cursor
    conn.close()


def _variant(cur, **kwargs):
    kwargs.setdefault("listing_id", 10)
    kwargs.setdefault("seller_user_id", SELLER)
    return variants.upsert_variant(cur, **kwargs)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_ensure_is_idempotent_and_reports_ready(cur):
    """Calling ensure twice must not error and must still report ready.

    The second call goes through the process cache; ``force`` bypasses it, which
    is the path a worker would take after a suspected schema change.
    """
    assert schema.ensure_supplier_schema(cur)["status"] == schema.STATUS_READY
    again = schema.ensure_supplier_schema(cur, force=True)
    assert again["status"] == schema.STATUS_READY
    assert again["missing"] == []


def test_required_columns_are_actually_present(cur):
    """Guards against a DDL edit that drops a column the required list still names.

    Without this, ``REQUIRED_*`` could name a column the CREATE never makes and
    the ensure would report ``missing`` forever — or worse, the list could be
    trimmed to match a broken DDL and nothing would notice.
    """
    from services import db as db_module

    variant_cols = db_module.get_table_columns(cur, schema.VARIANT_TABLE)
    source_cols = db_module.get_table_columns(cur, schema.SOURCE_TABLE)
    assert set(schema.REQUIRED_VARIANT_COLUMNS) <= variant_cols
    assert set(schema.REQUIRED_SOURCE_COLUMNS) <= source_cols


def test_the_required_lists_name_the_columns_that_matter():
    """A subset assertion alone is vacuous — an empty required list satisfies it.

    ``REQUIRED_*`` is what stands between a caller and a table that exists but
    cannot answer the question asked of it. Trimming the list is the cheap way to
    make ``ensure`` report ready, so the specific columns are named here: without
    ``cost_cents`` there is no supplier economics, without ``stock_state`` there
    is no way to say "unknown", and without ``fulfillment_mode`` there is no
    answer to who ships the order.
    """
    assert "cost_cents" in schema.REQUIRED_VARIANT_COLUMNS
    assert "stock_state" in schema.REQUIRED_VARIANT_COLUMNS
    assert "seller_user_id" in schema.REQUIRED_VARIANT_COLUMNS
    assert "provider" in schema.REQUIRED_SOURCE_COLUMNS
    assert "fulfillment_mode" in schema.REQUIRED_SOURCE_COLUMNS


class _RefusesToAddColumn:
    """A cursor that cannot ``ALTER TABLE ... ADD COLUMN <name>``.

    Stands in for the production shape this branch exists for: a database role
    with SELECT/INSERT but not ALTER, or a column addition that loses a race and
    then keeps losing. Everything else passes through untouched.
    """

    def __init__(self, inner, column):
        self._inner = inner
        self._column = column

    def execute(self, sql, params=None):
        text = str(sql)
        if "ADD COLUMN" in text.upper() and self._column in text:
            raise RuntimeError("permission denied for relation")
        return self._inner.execute(sql, params or ())

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_table_missing_a_required_column_is_not_reported_ready():
    """``missing`` must not degrade into ``ready``.

    This is the branch that stops a caller proceeding against a table that
    exists but cannot answer the question asked of it. Reporting ready here is
    strictly worse than reporting an error: the import would run, write rows
    without supplier economics, and look like it worked. The suite has to
    exercise the failing path, because the happy path passes either way.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    inner = conn.cursor()
    # Pre-create the table without cost_cents so the ensure has to ALTER for it.
    inner.execute(f"""
        CREATE TABLE {schema.VARIANT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            seller_user_id INTEGER NOT NULL,
            variant_key TEXT NOT NULL
        )
    """)
    result = schema.ensure_supplier_schema(
        _RefusesToAddColumn(inner, "cost_cents"), force=True)
    assert result["status"] == schema.STATUS_MISSING, result
    assert "cost_cents" in result["missing"]
    assert result["status"] != schema.STATUS_READY
    conn.close()


def test_force_actually_rebuilds_rather_than_trusting_the_cache(cur):
    """``force`` has to bypass the process-global flag, not just claim to.

    A worker calls ``force`` precisely when it suspects the tables changed
    underneath it. If the flag short-circuits anyway, the call is a no-op that
    reports success — the most expensive shape of failure, because the caller
    then proceeds against a schema that is not there.
    """
    assert schema.ensure_supplier_schema(cur)["status"] == schema.STATUS_READY
    cur.execute(f"DROP TABLE {schema.VARIANT_TABLE}")
    assert schema.ensure_supplier_schema(cur, force=True)["status"] == schema.STATUS_READY
    # Raises if force was ignored and the table was never recreated.
    cur.execute(f"SELECT COUNT(*) FROM {schema.VARIANT_TABLE}")


# ---------------------------------------------------------------------------
# The variant key
# ---------------------------------------------------------------------------

def test_variant_key_is_order_independent():
    """Providers do not promise option ordering.

    A key that depended on it would create a duplicate variant on every sync
    instead of updating the existing one — the bug would look like a catalogue
    that doubles in size weekly.
    """
    a = variants.variant_key([{"name": "Size", "value": "M"}, {"name": "Color", "value": "Red"}])
    b = variants.variant_key([{"name": "Color", "value": "Red"}, {"name": "Size", "value": "M"}])
    assert a == b


def test_variant_key_is_case_insensitive_for_grouping():
    a = variants.variant_key([{"name": "Size", "value": "M"}])
    b = variants.variant_key([{"name": "size", "value": "m"}])
    assert a == b


def test_variant_key_distinguishes_different_values():
    a = variants.variant_key([{"name": "Size", "value": "M"}])
    b = variants.variant_key([{"name": "Size", "value": "L"}])
    assert a != b


def test_no_options_keys_to_a_real_token():
    """A single-configuration product must still have a key the unique index can hold."""
    assert variants.variant_key([]) == "-"
    assert variants.variant_key([]) != ""


def test_duplicate_option_name_is_refused():
    """Two values for one dimension is two variants, not one.

    Accepting it would make the key depend on which duplicate the normaliser
    happened to keep, and the loser would silently overwrite a sibling.
    """
    with pytest.raises(variants.VariantRejected):
        variants.normalize_options([
            {"name": "Size", "value": "M"},
            {"name": "size", "value": "L"},
        ])


def test_option_without_value_is_refused():
    with pytest.raises(variants.VariantRejected):
        variants.normalize_options([{"name": "Size", "value": ""}])


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

def test_cross_owner_write_is_refused(cur):
    with pytest.raises(variants.VariantRejected):
        variants.upsert_variant(cur, listing_id=20, seller_user_id=SELLER,
                                options=[{"name": "Size", "value": "M"}])


def test_cross_owner_and_missing_listing_refuse_identically(cur):
    """No existence oracle.

    If these two messages differed, any caller could enumerate which listing ids
    exist by writing to each one and reading the error — while owning nothing.
    """
    with pytest.raises(variants.VariantRejected) as foreign:
        variants.upsert_variant(cur, listing_id=20, seller_user_id=SELLER, options=[])
    with pytest.raises(variants.VariantRejected) as absent:
        variants.upsert_variant(cur, listing_id=999999, seller_user_id=SELLER, options=[])
    assert str(foreign.value) == str(absent.value)


def test_archive_does_not_cross_owners(cur):
    variant_id = _variant(cur, options=[{"name": "Size", "value": "M"}])
    assert variants.archive_variant(cur, variant_id=variant_id,
                                    seller_user_id=OTHER_SELLER) is False
    rows = variants.variants_for(cur, 10)
    assert rows[0]["status"] == "active"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_reimport_updates_rather_than_duplicates(cur):
    first = _variant(cur, options=[{"name": "Size", "value": "M"}], cost_cents=500)
    second = _variant(cur, options=[{"name": "size", "value": "m"}], cost_cents=650)
    assert first == second
    rows = variants.variants_for(cur, 10)
    assert len(rows) == 1
    assert rows[0]["cost_cents"] == 650


def test_distinct_options_create_distinct_variants(cur):
    _variant(cur, options=[{"name": "Size", "value": "M"}])
    _variant(cur, options=[{"name": "Size", "value": "L"}])
    assert len(variants.variants_for(cur, 10)) == 2


def test_options_are_stored_with_original_case(cur):
    """The key folds case for grouping; the merchant must still see what was sent."""
    _variant(cur, options=[{"name": "Size", "value": "M"}])
    row = variants.variants_for(cur, 10)[0]
    assert row["options"] == [{"name": "Size", "value": "M"}]


# ---------------------------------------------------------------------------
# Unknown is not zero — stock
# ---------------------------------------------------------------------------

def test_unknown_stock_is_not_unavailable():
    """The central invariant. A failed sync must not read as a sell-out."""
    assert variants.availability({"stock_state": variants.STOCK_UNKNOWN}) == variants.UNKNOWN
    assert variants.availability({"stock_state": variants.STOCK_UNKNOWN}) != variants.UNAVAILABLE


def test_missing_stock_state_defaults_to_unknown():
    """A row written before this column existed must not read as purchasable."""
    assert variants.availability({}) == variants.UNKNOWN


def test_unrecognised_stock_state_is_unknown_not_available():
    """A future fourth state must not be silently read as buyable by older code."""
    assert variants.availability({"stock_state": "BACKORDER"}) == variants.UNKNOWN


def test_out_of_stock_is_unavailable():
    assert variants.availability(
        {"stock_state": variants.STOCK_OUT_OF_STOCK}) == variants.UNAVAILABLE


def test_in_stock_without_a_count_is_available():
    """Providers often report availability with no number; demanding one would
    make every such variant permanently unbuyable."""
    assert variants.availability(
        {"stock_state": variants.STOCK_IN_STOCK}) == variants.AVAILABLE


def test_in_stock_with_zero_count_is_unavailable():
    assert variants.availability(
        {"stock_state": variants.STOCK_IN_STOCK, "stock_quantity": 0}) == variants.UNAVAILABLE


def test_archived_variant_is_unavailable_even_when_in_stock():
    assert variants.availability({
        "status": "archived",
        "stock_state": variants.STOCK_IN_STOCK,
        "stock_quantity": 5,
    }) == variants.UNAVAILABLE


def test_listing_availability_prefers_available():
    assert variants.listing_availability([
        {"stock_state": variants.STOCK_OUT_OF_STOCK},
        {"stock_state": variants.STOCK_IN_STOCK, "stock_quantity": 3},
    ]) == variants.AVAILABLE


def test_all_unknown_aggregates_to_unknown_not_unavailable():
    """The clause a "simplification" deletes.

    Reporting UNAVAILABLE here turns a supplier outage into a storefront that
    looks legitimately sold out — the failure nobody investigates.
    """
    assert variants.listing_availability([
        {"stock_state": variants.STOCK_UNKNOWN},
        {"stock_state": variants.STOCK_UNKNOWN},
    ]) == variants.UNKNOWN


def test_one_unknown_among_out_of_stock_is_unknown():
    assert variants.listing_availability([
        {"stock_state": variants.STOCK_OUT_OF_STOCK},
        {"stock_state": variants.STOCK_UNKNOWN},
    ]) == variants.UNKNOWN


def test_all_out_of_stock_is_unavailable():
    assert variants.listing_availability([
        {"stock_state": variants.STOCK_OUT_OF_STOCK},
        {"stock_state": variants.STOCK_OUT_OF_STOCK},
    ]) == variants.UNAVAILABLE


def test_no_variants_is_unknown_not_unavailable():
    """Every listing in production today has no variants.

    Returning UNAVAILABLE would let this function assert something false about
    all of them the moment anything started consulting it.
    """
    assert variants.listing_availability([]) == variants.UNKNOWN


# ---------------------------------------------------------------------------
# Unknown is not zero — cost
# ---------------------------------------------------------------------------

def test_unknown_cost_yields_no_margin():
    """``cost or 0`` would report a full-price margin on unknown economics."""
    assert variants.margin_cents({"cost_cents": None}, 2000) is None


def test_zero_cost_is_a_real_margin():
    """The other half: a genuine zero must not be mistaken for unknown."""
    assert variants.margin_cents({"cost_cents": 0}, 2000) == 2000


def test_margin_is_computed_when_both_sides_known():
    assert variants.margin_cents({"cost_cents": 500}, 2000) == 1500


def test_negative_margin_is_reported_not_clamped():
    """Selling below cost is the one case worth alerting on; clamping hides it."""
    assert variants.margin_cents({"cost_cents": 2500}, 2000) == -500


def test_unknown_retail_yields_no_margin():
    assert variants.margin_cents({"cost_cents": 500}, None) is None


def test_null_cost_survives_the_write(cur):
    """The distinction has to reach the database, not just the reader."""
    _variant(cur, options=[{"name": "Size", "value": "M"}], cost_cents=None)
    assert variants.variants_for(cur, 10)[0]["cost_cents"] is None


def test_negative_money_is_refused(cur):
    with pytest.raises(variants.VariantRejected):
        _variant(cur, options=[{"name": "Size", "value": "M"}], cost_cents=-1)
    with pytest.raises(variants.VariantRejected):
        _variant(cur, options=[{"name": "Size", "value": "L"}], price_cents=-1)


def test_non_numeric_money_is_refused(cur):
    with pytest.raises(variants.VariantRejected):
        _variant(cur, options=[{"name": "Size", "value": "M"}], cost_cents="free")


def test_negative_stock_is_refused(cur):
    with pytest.raises(variants.VariantRejected):
        _variant(cur, options=[{"name": "Size", "value": "M"}], stock_quantity=-3)


def test_an_unrecognised_incoming_stock_state_is_stored_as_unknown(cur):
    """The reader's caution has to be matched by the writer's.

    ``availability`` refuses to guess about a state it does not recognise, but
    that only helps if the unrecognised value never reaches the row in the first
    place. A supplier sending ``PREORDER`` must land as UNKNOWN — "we do not
    know" is true — and not as anything a later reader could treat as buyable.
    """
    _variant(cur, options=[{"name": "Size", "value": "M"}], stock_state="PREORDER")
    row = variants.variants_for(cur, 10)[0]
    assert row["stock_state"] == variants.STOCK_UNKNOWN
    assert variants.availability(row) == variants.UNKNOWN


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

def test_variant_cap_is_enforced(cur, monkeypatch):
    monkeypatch.setattr(variants, "MAX_VARIANTS_PER_LISTING", 3)
    for i in range(3):
        _variant(cur, options=[{"name": "Size", "value": f"S{i}"}])
    with pytest.raises(variants.VariantRejected):
        _variant(cur, options=[{"name": "Size", "value": "S99"}])


def test_updating_an_existing_variant_at_the_cap_still_works(cur, monkeypatch):
    """A listing already at the ceiling must remain correctable and restockable."""
    monkeypatch.setattr(variants, "MAX_VARIANTS_PER_LISTING", 2)
    _variant(cur, options=[{"name": "Size", "value": "M"}])
    _variant(cur, options=[{"name": "Size", "value": "L"}])
    _variant(cur, options=[{"name": "Size", "value": "M"}], cost_cents=999)
    assert variants.variants_for(cur, 10)[0]["cost_cents"] == 999


def test_too_many_options_is_refused():
    with pytest.raises(variants.VariantRejected):
        variants.normalize_options(
            [{"name": f"n{i}", "value": "v"} for i in range(variants.MAX_OPTIONS_PER_VARIANT + 1)])


# ---------------------------------------------------------------------------
# Supplier source
# ---------------------------------------------------------------------------

def test_link_source_is_idempotent(cur):
    first = variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                                 provider="cj", provider_product_id="CJ-1")
    second = variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                                  provider="cj", provider_product_id="CJ-1")
    assert first == second


def test_relinking_to_a_different_supplier_product_is_refused(cur):
    """A listing whose supplier changed underneath it would keep selling a page
    describing the old product."""
    variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                         provider="cj", provider_product_id="CJ-1")
    with pytest.raises(variants.VariantRejected):
        variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                             provider="cj", provider_product_id="CJ-2")


def test_unknown_provider_is_refused(cur):
    with pytest.raises(variants.VariantRejected):
        variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                             provider="aliexpress", provider_product_id="X-1")


def test_an_empty_provider_product_id_is_refused(cur):
    """A source row that names a provider but no product is worse than no row.

    ``source_for`` would report the listing as CJ-sourced, and a later sync would
    have a provider to call and nothing to ask it about. The listing would look
    integrated and be unfulfillable, which is harder to notice than an obviously
    missing link.
    """
    with pytest.raises(variants.VariantRejected):
        variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                             provider="cj", provider_product_id="")
    with pytest.raises(variants.VariantRejected):
        variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                             provider="cj", provider_product_id="   ")
    assert variants.source_for(cur, 10) is None


def test_unknown_fulfillment_mode_is_refused(cur):
    """Unlike a stock state, a bad mode must not degrade to a default.

    An unrecognised stock state can honestly become "unknown". There is no
    honest default for how an order is fulfilled: silently writing DROPSHIP
    would commit the merchant to calling a supplier for goods that may be sitting
    in their own garage, and the row would look deliberate afterwards.
    """
    with pytest.raises(variants.VariantRejected):
        variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                             provider="cj", provider_product_id="CJ-1",
                             fulfillment_mode="TELEPORT")


def test_provider_is_independent_of_fulfillment_mode(cur):
    """A hand-authored product can be drop-shipped and an imported one stocked.

    One column for both would make "who fulfils this order" unanswerable.
    """
    variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                         provider="manual", provider_product_id="M-1",
                         fulfillment_mode=schema.MODE_DROPSHIP)
    assert variants.source_for(cur, 10)["fulfillment_mode"] == schema.MODE_DROPSHIP

    variants.link_source(cur, listing_id=20, seller_user_id=OTHER_SELLER,
                         provider="cj", provider_product_id="CJ-9",
                         fulfillment_mode=schema.MODE_STOCKED)
    assert variants.source_for(cur, 20)["fulfillment_mode"] == schema.MODE_STOCKED


def test_two_sellers_may_import_the_same_supplier_product(cur):
    """Normal, and must not collide."""
    variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                         provider="cj", provider_product_id="CJ-SHARED")
    variants.link_source(cur, listing_id=20, seller_user_id=OTHER_SELLER,
                         provider="cj", provider_product_id="CJ-SHARED")
    assert variants.source_for(cur, 10)["seller_user_id"] == SELLER
    assert variants.source_for(cur, 20)["seller_user_id"] == OTHER_SELLER


def test_listing_without_a_source_reads_as_none(cur):
    assert variants.source_for(cur, 10) is None


def test_link_source_refuses_cross_owner(cur):
    with pytest.raises(variants.VariantRejected):
        variants.link_source(cur, listing_id=20, seller_user_id=SELLER,
                             provider="cj", provider_product_id="CJ-1")


# ---------------------------------------------------------------------------
# Field-level ownership
# ---------------------------------------------------------------------------

def test_overrides_are_additive_and_idempotent(cur):
    variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                         provider="cj", provider_product_id="CJ-1")
    variants.mark_overridden(cur, listing_id=10, seller_user_id=SELLER, fields=["title"])
    variants.mark_overridden(cur, listing_id=10, seller_user_id=SELLER, fields=["title"])
    merged = variants.mark_overridden(cur, listing_id=10, seller_user_id=SELLER,
                                      fields=["description"])
    assert merged == ["title", "description"]


def test_sync_cannot_overwrite_a_merchant_owned_field(cur):
    """Without this the choice is: never refresh (stale supplier data) or always
    overwrite (destroyed merchant edits). Neither is acceptable."""
    variants.link_source(cur, listing_id=10, seller_user_id=SELLER,
                         provider="cj", provider_product_id="CJ-1")
    variants.mark_overridden(cur, listing_id=10, seller_user_id=SELLER, fields=["title"])
    source = variants.source_for(cur, 10)
    allowed = variants.sync_updates_allowed(
        source, {"title": "supplier title", "cost_cents": 700})
    assert "title" not in allowed
    assert allowed["cost_cents"] == 700


def test_sync_against_a_merchant_authored_product_updates_nothing(cur):
    """No source means no supplier to accept updates from.

    Reading "no source" as "no restrictions" would let a stray sync overwrite a
    hand-made product.
    """
    assert variants.sync_updates_allowed(None, {"title": "x"}) == {}


def test_marking_overrides_without_a_source_is_refused(cur):
    with pytest.raises(variants.VariantRejected):
        variants.mark_overridden(cur, listing_id=10, seller_user_id=SELLER, fields=["title"])


def test_mark_overridden_refuses_cross_owner(cur):
    variants.link_source(cur, listing_id=20, seller_user_id=OTHER_SELLER,
                         provider="cj", provider_product_id="CJ-1")
    with pytest.raises(variants.VariantRejected):
        variants.mark_overridden(cur, listing_id=20, seller_user_id=SELLER, fields=["title"])


# ---------------------------------------------------------------------------
# Scope — this change must not have touched the live path
# ---------------------------------------------------------------------------

def test_variants_are_not_a_money_authority_yet(cur):
    """price_cents is nullable and NULL means the listing's price_label governs.

    Stated as a test so that a later change making variants authoritative has to
    delete this deliberately rather than drift into it while
    ``marketplace_listings`` still stores retail money as prose.
    """
    _variant(cur, options=[{"name": "Size", "value": "M"}])
    assert variants.variants_for(cur, 10)[0]["price_cents"] is None
