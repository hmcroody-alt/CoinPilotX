"""Freight is part of the cost, and nobody is allowed to invent it. §12.

What this file is defending
---------------------------
A supplier's per-item cost is not what the merchant pays the supplier. CJ bills
freight on every order, so a 45% target margin computed against the item alone is
not a 45% margin -- and on a cheap, heavy product it is a negative one. The
merchant is shown ``HEALTHY`` anyway, because the number is arithmetically
correct about the wrong quantity. That is the failure §12 exists to stop, and it
is invisible to every test that only checks the arithmetic.

The fix has two halves, and this file is mostly about the second.

* **Arithmetic.** ``pricing`` learned a landed cost: item plus a per-unit
  shipping figure, and a ``margin_basis`` naming which of the two every margin
  was measured against. Tested here as pure functions, because that is where the
  dangerous simplifications live -- ``shipping_cents or 0`` (an unknown freight
  cost silently reads as free shipping, which is the exact fabrication §1
  forbids) and dropping ``margin_basis`` because "the caller knows".

* **Provenance.** Where the number is allowed to come from. The answer is: the
  store declared it, or it is unknown. There is deliberately no platform default
  and no request parameter, and both absences are asserted below rather than only
  documented, because both are one convenient line away from existing.

  A default *margin* is a policy choice the platform is entitled to make. A
  default *freight cost* is a claim about what a supplier charges to ship a
  product it has never quoted, to a destination nobody has named. The first is
  ``PLATFORM_DEFAULT_TARGET_MARGIN``; the second would be a §1 violation wearing
  the same clothes.

  There is also no import-time *estimate*, and there cannot be one: a CJ freight
  quote needs ``destAreaCode``, province, city and address, and at import time no
  buyer exists. The only real freight numbers this system ever holds are
  post-purchase, in ``business_os_supplier_intents.snapshot_json``.

And one end-to-end case, which is the whole point of the section: a listing that
is above water on item cost and under it once freight is counted must be refused
by the publish gate. If the allowance reached the importer but not the gate --
or the gate but not the Review screen -- the merchant is either told ``HEALTHY``
and then refused, or published at a loss. Both are asserted.

Runs alone -- see the header of ``test_dropship_import_pipeline``.
"""

import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-landed-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, gateway, import_cart, importer, pricing, revisions, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

# Same tenancy and the same fake provider as the pipeline suite; restating them
# would let the two drift.
from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OTHER_BUSINESS, OTHER_CONNECTION,
    OTHER_OWNER_ID, OTHER_STORE, OWNER_ID, STORE, _seed_connection, _seed_tenancy,
    cj_product)

# That import ran the pipeline module's header, which pointed DATABASE_URL at
# *its* temp file. db resolves the sqlite path per call, so without this every
# query below would run against a database this file never truncates.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

ITEM_COST = 820          # cj_product's "8.20", in cents
FREIGHT = 900            # a heavier-than-the-product shipping allowance
# The price the merchant types in the publish-gate cases. Chosen to sit between
# the item cost and the landed cost: 18% margin on the first, a $7.20 loss per
# unit on the second. Any price outside that window makes those tests agree with
# each other and stop measuring anything.
RETAIL = 1000


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
        _seed_connection(conn, CONNECTION, BUSINESS, STORE, OWNER_ID)
        _seed_connection(conn, OTHER_CONNECTION, OTHER_BUSINESS, OTHER_STORE,
                         OTHER_OWNER_ID)
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


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def set_policy(business=BUSINESS, store=STORE, **fields):
    conn = db.connect()
    try:
        result = store_policy.set_policy(conn, business, store, **fields)
        conn.commit()
        return result
    finally:
        conn.close()


def read_policy(business=BUSINESS, store=STORE):
    conn = db.connect()
    try:
        return store_policy.get_policy(conn, business, store)
    finally:
        conn.close()


def resolve(business=BUSINESS, store=STORE):
    conn = db.connect()
    try:
        return store_policy.resolve_shipping_allowance(conn, business, store)
    finally:
        conn.close()


def import_one(provider, pid="PID-1", *, selection=None, expect=None, **product_kwargs):
    """Import one product through the real pipeline and return it.

    ``expect`` defaults to :data:`importer.PUBLISHED`, not ``IMPORTED``, because
    that is what "Import to Store" now means: the listing finishes itself. A
    store on :data:`pricing.MANUAL_PRICE` is the exception -- there is no price to
    publish, so it lands ``NEEDS_ATTENTION`` -- and the tests below that need an
    unpublished draft to price by hand say so explicitly rather than letting any
    outcome through.
    """
    provider.add(cj_product(pid, **product_kwargs))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid,
                         selected_variant_ids=selection or [f"{pid}-V1"],
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    entry = result["results"][0]
    assert entry["outcome"] == (expect or importer.PUBLISHED), entry
    return result, entry["listing_id"]


def unpriced(provider, pid="PID-1", **kwargs):
    """A store that prices by hand, and one imported draft of theirs.

    The publish gate cases below need a listing whose retail price they choose,
    and ``MANUAL_PRICE`` is the only setting that produces one: with a rule in
    place the import prices *itself* against the landed cost, which is the
    behaviour under test everywhere else in this file and which can never be
    negative.
    """
    set_policy(pricing_rule={"type": pricing.MANUAL_PRICE}, **kwargs)
    _, listing_id = import_one(provider, pid, expect=importer.NEEDS_ATTENTION)
    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                             context=CONTEXT)
    drafts.update_draft(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
        fields={"price_cents": {str(v["variant_id"]): RETAIL
                                for v in draft["variants"]}},
        context=CONTEXT)
    return listing_id


# ---------------------------------------------------------------------------
# normalize_shipping: what counts as a freight number at all
# ---------------------------------------------------------------------------

def test_an_unknown_freight_cost_stays_unknown():
    # `shipping_cents or 0` is the one-character version of this bug, and it
    # reports every un-quoted product as shipping free.
    assert pricing.normalize_shipping(None) is None


def test_zero_freight_is_a_declaration_not_an_absence():
    # A merchant whose supplier bundles shipping into the item price is entitled
    # to say so, and must not be downgraded to UNKNOWN for saying it. Mirrors
    # `test_a_free_product_still_prices`: zero is a number.
    assert pricing.normalize_shipping(0) == 0
    assert pricing.landed_cost_cents(ITEM_COST, 0) == ITEM_COST
    assert pricing.basis(ITEM_COST, 0) == (pricing.LANDED, ITEM_COST)


def test_true_is_not_one_cent_of_freight():
    """``isinstance(True, int)`` is ``True``, which is why this is tested.

    A JSON body carrying ``"shipping_allowance_cents": true`` would otherwise be
    stored as a one-cent freight charge -- a number small enough that no margin
    badge ever changes, so nothing would ever surface it.
    """
    assert pricing.normalize_shipping(True) is None
    assert pricing.normalize_shipping(False) is None


@pytest.mark.parametrize("value", [
    -1, -100, "900", "", [], {}, float("nan"), float("inf"), float("-inf"),
    pricing.MAX_PRICE_CENTS + 1,
])
def test_an_unusable_freight_figure_is_unknown_not_zero(value):
    # Rejected the same way an unreadable cost is: by becoming unknown. Falling
    # back to zero would turn every rejection into a claim of free shipping.
    assert pricing.normalize_shipping(value) is None


def test_a_float_allowance_is_accepted_as_whole_cents():
    assert pricing.normalize_shipping(900.0) == 900


# ---------------------------------------------------------------------------
# landed_cost_cents and basis: the two halves cannot drift apart
# ---------------------------------------------------------------------------

def test_landed_cost_is_item_plus_freight():
    assert pricing.landed_cost_cents(ITEM_COST, FREIGHT) == 1720


def test_unknown_freight_gives_no_landed_cost_rather_than_the_item_cost():
    """The most tempting shortcut in the module, and the one that lies.

    Returning ``cost_cents`` here would make ``landed_cost_cents`` always
    succeed, and every caller would then believe it had a landed figure. The
    margin would be the item margin wearing the landed label.
    """
    assert pricing.landed_cost_cents(ITEM_COST, None) is None
    assert pricing.landed_cost_cents(ITEM_COST, None) != ITEM_COST


def test_unknown_item_cost_gives_no_landed_cost_either():
    assert pricing.landed_cost_cents(None, FREIGHT) is None
    # And specifically not the freight alone, which would price a product whose
    # cost could not be read as though shipping were the whole of it.
    assert pricing.landed_cost_cents(None, FREIGHT) != FREIGHT


def test_basis_names_the_number_it_returns():
    """Name and number travel as one pair so they cannot disagree.

    Two calls -- one for the label, one for the figure -- is the shape that ends
    up reporting ``LANDED`` next to an item-cost margin after somebody reorders
    the lines.
    """
    assert pricing.basis(ITEM_COST, FREIGHT) == (pricing.LANDED, 1720)
    assert pricing.basis(ITEM_COST, None) == (pricing.ITEM, ITEM_COST)
    assert pricing.basis(None, FREIGHT) == (pricing.ITEM, None)
    assert pricing.basis(ITEM_COST, True) == (pricing.ITEM, ITEM_COST)


def test_every_basis_is_declared():
    seen = {pricing.basis(ITEM_COST, FREIGHT)[0], pricing.basis(ITEM_COST, None)[0]}
    assert seen == set(pricing.MARGIN_BASES)


# ---------------------------------------------------------------------------
# quote: what a screen renders
# ---------------------------------------------------------------------------

def test_a_quote_without_freight_is_exactly_what_it_was_before():
    """Backward compatibility, measured rather than assumed.

    Every existing caller omits ``shipping_cents``. If the new parameter changed
    any number for them, §12 would have silently repriced the entire installed
    base of drafts.
    """
    quote = pricing.quote({"type": pricing.MULTIPLIER, "value": 2}, ITEM_COST)
    assert quote["retail_cents"] == 1640
    assert quote["margin_cents"] == 820
    assert quote["margin_percent"] == 50.0
    assert quote["margin_state"] == pricing.HEALTHY
    # And the new keys say, honestly, that freight is not known.
    assert quote["shipping_cents"] is None
    assert quote["landed_cost_cents"] is None
    assert quote["basis_cost_cents"] == ITEM_COST
    assert quote["margin_basis"] == pricing.ITEM


def test_a_quote_with_freight_prices_against_what_the_merchant_pays():
    quote = pricing.quote({"type": pricing.TARGET_MARGIN, "value": 45}, ITEM_COST,
                          shipping_cents=FREIGHT)
    # 1720 / (1 - 0.45)
    assert quote["proposed_retail_cents"] == 3127
    assert quote["landed_cost_cents"] == 1720
    assert quote["basis_cost_cents"] == 1720
    assert quote["margin_basis"] == pricing.LANDED
    assert quote["margin_percent"] == 45.0
    # The item cost is still reported as the item cost. It is the supplier's
    # number, and a later supplier read compares against it.
    assert quote["cost_cents"] == ITEM_COST


def test_the_same_retail_price_is_a_worse_margin_once_freight_is_counted():
    """The number §12 exists to correct, shown as a pair.

    Same product, same price, same supplier. The only difference is whether the
    freight the merchant declared is counted -- and it is the difference between
    a badge that says HEALTHY and one that says the truth.
    """
    item = pricing.quote(None, ITEM_COST, retail_cents=2000)
    landed = pricing.quote(None, ITEM_COST, retail_cents=2000, shipping_cents=FREIGHT)

    assert item["margin_state"] == pricing.HEALTHY
    assert item["margin_percent"] == 59.0
    assert landed["margin_percent"] == 14.0
    assert landed["margin_state"] == pricing.LOW_MARGIN


def test_freight_can_turn_a_positive_margin_negative():
    quote = pricing.quote(None, ITEM_COST, retail_cents=1000, shipping_cents=FREIGHT)
    assert quote["margin_cents"] == -720
    assert quote["margin_state"] == pricing.NEGATIVE_MARGIN


def test_an_unusable_freight_figure_does_not_reach_the_quote():
    quote = pricing.quote(None, ITEM_COST, retail_cents=2000, shipping_cents="900")
    assert quote["shipping_cents"] is None
    assert quote["margin_basis"] == pricing.ITEM


# ---------------------------------------------------------------------------
# Provenance: one rung, and the two that are missing
# ---------------------------------------------------------------------------

def test_an_unconfigured_store_has_no_freight_figure_not_a_free_one():
    """``None``, not ``0``.

    A store that has said nothing has not told us shipping is free; it has told
    us nothing, and on a heavy product those two produce very different margins.
    """
    policy = read_policy()
    assert policy["shipping_allowance_cents"] is None
    assert policy["shipping_allowance_source"] == store_policy.SOURCE_PLATFORM
    assert resolve() == (None, store_policy.SOURCE_PLATFORM)


def test_there_is_no_platform_default_freight_cost():
    """The asymmetry with pricing, asserted so it cannot be tidied away.

    ``PLATFORM_DEFAULT_TARGET_MARGIN`` exists because a margin is a policy
    choice the platform may make. Its shipping twin must not exist, because a
    freight number is a claim about a supplier -- and §1 forbids inventing those.
    """
    assert not [name for name in dir(store_policy)
                if name.startswith("PLATFORM_DEFAULT") and "SHIPPING" in name.upper()]


def test_resolve_shipping_allowance_takes_no_requested_value():
    """No request rung, structurally.

    ``resolve_rule`` takes a ``requested`` argument because a pricing rule is a
    *strategy* and may be named per import. An allowance is a *cost*, and the
    trust boundary is that the client does not send costs. A parameter that
    existed here -- even documented as "nobody should pass this" -- is a
    parameter a route eventually forwards.
    """
    import inspect
    accepted = set(inspect.signature(store_policy.resolve_shipping_allowance).parameters)
    assert accepted == {"conn", "business_id", "store_id"}


def test_a_store_can_declare_its_freight_allowance():
    result = set_policy(shipping_allowance_cents=FREIGHT)
    assert result["shipping_allowance_cents"] == FREIGHT
    assert result["shipping_allowance_source"] == store_policy.SOURCE_STORE
    # And it is the answer the next read gives, not just the one the write echoed.
    assert resolve() == (FREIGHT, store_policy.SOURCE_STORE)


def test_declaring_freight_leaves_the_other_settings_alone():
    set_policy(pricing_rule={"type": pricing.MULTIPLIER, "value": 3})
    set_policy(marketplace_autolist=True)
    set_policy(shipping_allowance_cents=FREIGHT)

    policy = read_policy()
    assert policy["pricing_rule"] == {"type": pricing.MULTIPLIER, "value": 3.0}
    assert policy["marketplace_autolist"] is True
    assert policy["shipping_allowance_cents"] == FREIGHT


def test_a_later_write_that_omits_freight_does_not_clear_it():
    # `None` means "leave this field alone" for every other field on this row,
    # and freight cannot be the exception -- the merchant would lose their
    # declaration by flipping an unrelated toggle.
    set_policy(shipping_allowance_cents=FREIGHT)
    set_policy(auto_publish=False)
    assert read_policy()["shipping_allowance_cents"] == FREIGHT


def test_a_store_can_take_its_freight_declaration_back():
    """Which is why ``CLEAR_ALLOWANCE`` exists.

    The cleared value *is* ``None``, and ``None`` already means "unchanged", so
    un-declaring needs a third value. It has to survive a JSON round trip, which
    is why it is a string rather than a module-level sentinel object.
    """
    set_policy(shipping_allowance_cents=FREIGHT)
    result = set_policy(shipping_allowance_cents=store_policy.CLEAR_ALLOWANCE)
    assert result["shipping_allowance_cents"] is None
    assert result["shipping_allowance_source"] == store_policy.SOURCE_PLATFORM
    assert resolve() == (None, store_policy.SOURCE_PLATFORM)


@pytest.mark.parametrize("value", [-1, "900", True, [], {}, float("nan")])
def test_an_unusable_declaration_is_refused_rather_than_stored_as_unknown(value):
    """Rejected loudly, because a merchant typed it.

    ``normalize_shipping`` answering ``None`` is the right behaviour for a figure
    read from a database or a provider. For one a merchant just submitted it is
    the wrong one: they would be shown a settings screen that silently forgot
    what they entered.
    """
    with pytest.raises(pricing.PricingRejected):
        set_policy(shipping_allowance_cents=value)
    assert read_policy()["shipping_allowance_cents"] is None


def test_a_refused_declaration_does_not_disturb_the_stored_one():
    set_policy(shipping_allowance_cents=FREIGHT)
    with pytest.raises(pricing.PricingRejected):
        set_policy(shipping_allowance_cents=-5)
    assert read_policy()["shipping_allowance_cents"] == FREIGHT


def test_write_reports_a_bad_allowance_as_a_bad_allowance():
    """A 400 naming the field, not a 503 blaming the supplier.

    ``PricingRejected`` carries no HTTP status, so untranslated it reaches the
    route's error handler as ``supplier_unavailable``.
    """
    with pytest.raises(SupplierError) as raised:
        store_policy.write(BUSINESS, STORE, OWNER_ID, shipping_allowance_cents="900",
                           context=CONTEXT)
    assert raised.value.code == "invalid_shipping_allowance"
    assert raised.value.http_status == 400


def test_one_stores_freight_is_not_anothers():
    set_policy(shipping_allowance_cents=FREIGHT)
    assert resolve(OTHER_BUSINESS, OTHER_STORE) == (None, store_policy.SOURCE_PLATFORM)


# ---------------------------------------------------------------------------
# The column has to reach a database that already exists
# ---------------------------------------------------------------------------
#
# The only part of §12 that a passing test suite cannot vouch for by itself.
# ``ensure_schema`` is ``CREATE TABLE IF NOT EXISTS``, which is a no-op on every
# database that already has the table -- which is every database that has ever
# imported a product, including production. The new column in that DDL therefore
# reaches only a *fresh* database, and every fixture in this repository builds a
# fresh one, so the whole file above would stay green while production had no
# such column and every write raised.
#
# So these three drop the column back off and re-run the real migration.


def _drop_the_column():
    """Rebuild the policy table as it looked before §12.

    SQLite has no ``DROP COLUMN`` before 3.35 and this has to be reliable across
    versions, so the table is recreated from the original DDL. That is also the
    honest reproduction: what production has is a table created by the *old*
    statement, not a new one with a column removed.
    """
    conn = db.connect()
    try:
        conn.execute(f"DROP TABLE IF EXISTS {store_policy.TABLE}")
        conn.execute(f"""CREATE TABLE {store_policy.TABLE} (
            business_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            pricing_type TEXT NOT NULL DEFAULT '{pricing.TARGET_MARGIN}',
            pricing_value REAL,
            auto_publish INTEGER NOT NULL DEFAULT 1,
            marketplace_autolist INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (business_id, store_id)
        )""")
        conn.commit()
    finally:
        conn.close()


def _columns():
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {store_policy.TABLE} LIMIT 0")
        return {d[0] for d in cur.description}
    finally:
        conn.close()


def test_ensure_schema_adds_the_column_to_a_table_that_predates_it():
    _drop_the_column()
    assert "shipping_allowance_cents" not in _columns(), "the fixture did not reproduce it"
    store_policy.ensure_schema()
    assert "shipping_allowance_cents" in _columns()


def test_an_unmigrated_table_reads_as_no_allowance_rather_than_raising():
    """Reading must survive the window before the migration runs.

    ``get_policy`` is called with a *borrowed* connection from inside the
    importer's write transaction, so it cannot run the ``ALTER`` itself: a failed
    one poisons the whole transaction on PostgreSQL, and the ``commit`` that would
    clear it would end a transaction the caller is still using. ``_row`` does
    ``SELECT *``, so the key is simply absent -- and absent is the correct answer
    for a store that has no allowance.
    """
    _drop_the_column()
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO {store_policy.TABLE} (business_id, store_id, pricing_type,"
            " pricing_value, auto_publish, marketplace_autolist, created_at, updated_at)"
            " VALUES (?,?,?,?,1,0,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')",
            (BUSINESS, STORE, pricing.TARGET_MARGIN, 45.0))
        conn.commit()
    finally:
        conn.close()

    policy = read_policy()
    assert policy["configured"] is True
    assert policy["pricing_rule"] == {"type": pricing.TARGET_MARGIN, "value": 45.0}
    # The half that matters: no exception, and no fabricated zero.
    assert policy["shipping_allowance_cents"] is None
    assert resolve() == (None, store_policy.SOURCE_PLATFORM)


def test_a_merchant_declaring_freight_on_an_unmigrated_store_succeeds():
    """The path that actually needs the column, end to end.

    ``write`` owns its connection, so it migrates before opening the transaction
    it writes in. This is the first merchant on a deployed server tapping Save --
    and without that ordering it is an unhandled database error on a settings
    screen.
    """
    _drop_the_column()
    result = store_policy.write(BUSINESS, STORE, OWNER_ID,
                                shipping_allowance_cents=FREIGHT, context=CONTEXT)
    assert result["shipping_allowance_cents"] == FREIGHT
    assert resolve() == (FREIGHT, store_policy.SOURCE_STORE)


# ---------------------------------------------------------------------------
# The importer
# ---------------------------------------------------------------------------

def test_import_accepts_no_freight_figure_from_the_caller():
    """The boundary, restated for freight specifically.

    This is not a hypothetical: the override was written onto ``import_selected``
    and ``test_import_selected_accepts_no_economic_input_from_the_caller`` failed
    on it before it could reach a price. Pinned here too, because that test reads
    as being about costs and titles and somebody will add freight without
    noticing it is one.
    """
    import inspect
    accepted = set(inspect.signature(importer.import_selected).parameters)
    for name in ("shipping_cents", "shipping_allowance_cents", "shipping",
                 "landed_cost_cents", "freight_cents"):
        assert name not in accepted


def test_an_import_prices_against_the_freight_the_store_declared(provider):
    set_policy(pricing_rule={"type": pricing.TARGET_MARGIN, "value": 45},
               shipping_allowance_cents=FREIGHT)
    result, listing_id = import_one(provider)

    assert result["shipping_allowance_cents"] == FREIGHT
    assert result["shipping_allowance_source"] == store_policy.SOURCE_STORE
    assert result["margin_basis"] == pricing.LANDED

    variant = rows("SELECT price_cents, cost_cents FROM marketplace_listing_variants"
                   " WHERE listing_id=?", (listing_id,))[0]
    assert variant["price_cents"] == 3127          # 1720 / 0.55
    # The stored cost is still the *item* cost. Storing the landed figure would
    # make the next supplier read look like a price change of exactly the
    # allowance, on every product, forever.
    assert variant["cost_cents"] == ITEM_COST


def test_an_import_with_no_declared_freight_prices_exactly_as_before(provider):
    set_policy(pricing_rule={"type": pricing.TARGET_MARGIN, "value": 45})
    result, listing_id = import_one(provider)

    assert result["shipping_allowance_cents"] is None
    assert result["margin_basis"] == pricing.ITEM
    variant = rows("SELECT price_cents FROM marketplace_listing_variants"
                   " WHERE listing_id=?", (listing_id,))[0]
    assert variant["price_cents"] == 1491          # 820 / 0.55


# ---------------------------------------------------------------------------
# The Review screen and the publish gate must agree
# ---------------------------------------------------------------------------

def test_the_review_screen_shows_the_margin_the_gate_will_judge(provider):
    """One pricing engine, §9, asserted where it is easiest to break.

    ``get_draft`` computes its own quote. If it dropped back to item cost while
    the gate used landed cost, a merchant would be shown ``HEALTHY``, tap
    Publish, and be refused for ``NEGATIVE_MARGIN`` on the same listing.
    """
    listing_id = unpriced(provider, shipping_allowance_cents=FREIGHT)

    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                             context=CONTEXT)
    assert draft["shipping_allowance_cents"] == FREIGHT
    assert draft["shipping_allowance_source"] == store_policy.SOURCE_STORE
    priced = draft["variants"][0]
    assert priced["margin_basis"] == pricing.LANDED
    assert priced["landed_cost_cents"] == 1720
    assert priced["margin_state"] == pricing.NEGATIVE_MARGIN


def test_a_listing_that_only_loses_money_once_freight_is_counted_cannot_publish(provider):
    """The end-to-end case, and the actual safety value of §12.

    $10.00 against an $8.20 item cost is an 18% margin and publishes. Against
    $8.20 plus $9.00 of freight it is a $7.20 loss on every unit sold. Without
    the allowance reaching the gate, PulseSoc publishes it and the merchant
    discovers the arithmetic from their CJ invoices.
    """
    listing_id = unpriced(provider, shipping_allowance_cents=FREIGHT)

    # Named first, because a bare SupplierError would pass just as well for a
    # missing price or a disconnected supplier -- and this listing has a price and
    # a connected supplier. `validate` is the same gate, dry.
    assert drafts.NEGATIVE_MARGIN in drafts.validate(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
        context=CONTEXT)["problems"]
    with pytest.raises(SupplierError):
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                       context=CONTEXT)
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"


def test_the_same_listing_publishes_for_a_store_that_declared_no_freight(provider):
    """The control. Without it the test above passes with the gate simply broken.

    Identical product, identical price, identical everything except the
    declaration -- so the refusal above is attributable to the freight figure and
    to nothing else.
    """
    listing_id = unpriced(provider)

    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            context=CONTEXT)
    assert result["status"] == "published"


def test_the_automatic_path_judges_freight_too(provider):
    """§18. There is no manual Publish tap on the ordinary import.

    ``autopublish`` runs the same gate inside the import transaction. A landed
    margin that only the explicit ``publish`` checked would be unreachable for
    every merchant who left auto-publish on -- which is all of them by default.
    """
    listing_id = unpriced(provider, shipping_allowance_cents=FREIGHT)

    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status='draft', published_at=NULL"
                    " WHERE id=?", (listing_id,))
        # Resolved the way the importer resolves it -- from the store, on the
        # transaction's own connection -- rather than passed in as a literal, so
        # a resolver that stopped answering would fail here too.
        shipping_cents, _ = store_policy.resolve_shipping_allowance(
            conn, BUSINESS, STORE)
        assert shipping_cents == FREIGHT
        result = drafts.autopublish(cur, listing_id, int(OWNER_ID), shipping_cents)
        conn.commit()
    finally:
        conn.close()

    assert result["published"] is False
    assert drafts.NEGATIVE_MARGIN in result["problems"]


# ---------------------------------------------------------------------------
# Repricing on a supplier cost change
# ---------------------------------------------------------------------------

def test_a_reprice_keeps_the_freight_in_the_margin():
    """§23 and §9 together.

    A supplier price change recomputes the retail price. If the reprice dropped
    back to item cost, a merchant would be repriced *out* of their freight
    allowance the first time CJ moved a cost -- a second pricing engine arrived
    at by omission rather than by decision.
    """
    variant = {"id": 1, "cost_cents": ITEM_COST, "price_cents": 3127}
    rule = {"type": pricing.TARGET_MARGIN, "value": 45}
    plan = revisions.plan_cost_revision(source={}, variant=variant, rule=rule,
                                        observed_cost_cents=1000,
                                        shipping_cents=FREIGHT)
    assert plan["action"] == revisions.REPRICED
    # 1900 / 0.55, not 1000 / 0.55.
    assert plan["price_cents"] == 3455
    assert plan["price_cents"] != 1818
    # The cost recorded is the supplier's item cost, unchanged. The allowance is
    # the merchant's declaration and does not belong in a supplier column.
    assert plan["cost_cents"] == 1000


def test_a_reprice_without_a_declared_allowance_is_unchanged():
    variant = {"id": 1, "cost_cents": ITEM_COST, "price_cents": 1491}
    rule = {"type": pricing.TARGET_MARGIN, "value": 45}
    plan = revisions.plan_cost_revision(source={}, variant=variant, rule=rule,
                                        observed_cost_cents=1000)
    assert plan["price_cents"] == 1818               # 1000 / 0.55


def test_a_reprice_compares_item_costs_not_landed_ones():
    """What §12 deliberately did *not* change.

    ``observed == stored_cost`` asks what the supplier charges for the item.
    Adding the same constant to both sides answers identically while making the
    intent unreadable, so the comparison stays on the raw figures.
    """
    variant = {"id": 1, "cost_cents": ITEM_COST, "price_cents": 3127}
    plan = revisions.plan_cost_revision(source={}, variant=variant, rule=None,
                                        observed_cost_cents=ITEM_COST,
                                        shipping_cents=FREIGHT)
    assert plan["action"] == revisions.UNCHANGED
    assert plan["price_cents"] is None
