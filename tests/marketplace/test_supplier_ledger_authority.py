"""One product authority, with supplier provenance beside it — not instead of it.

What this file is defending
---------------------------
The CJ supplier gateway was built against ``business_os_mkt_products`` and kept
its own ``supplier_product_links`` mapping. Both were reasonable choices in
isolation and together they were a split: ``business_os_mkt_products`` holds zero
rows in production, so the gateway could never reach a product a customer could
buy, and ``supplier_product_links`` was a second answer to "where did this
listing come from" that could disagree with ``marketplace_product_sources``.

The reconciliation moved the gateway onto the live ledger. What that leaves is a
shape that has to be held in place:

* ``marketplace_listings`` is the only product authority,
* ``marketplace_product_sources`` is the only supplier-mapping authority,
* a mapping cannot exist without the listing it maps,
* provider sync never writes merchant-owned retail fields,
* unknown cost is not zero, and unknown sync state is not success.

Each of those is a plausible one-line simplification away from being wrong.
``scripts/marketplace/supplier_variant_mutation_battery.py`` makes those exact
simplifications and requires this file to go red for every one; a test here that
no mutant can break is not earning its place.

On the fixture
--------------
The listings are the six real production rows (see
``tests/marketplace_production_listings``), not invented ones. Their ids start at
8, they share one owner, and their prices are TEXT. Every one of those three
details has caught something that a tidier fixture would have let through.
"""

import ast
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_supplier_schema as schema
from services import marketplace_variants as variants
from tests.marketplace_production_listings import (
    ABSENT_ID, DIGITAL_ID, FOREIGN_SELLER_ID, PHYSICAL_PUBLISHED_ID,
    PHYSICAL_REVIEW_ID, PRODUCTION_LISTINGS, PRODUCTION_SELLER_ID,
    seed_production_listings)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONNECTION = "conn-a"
OTHER_CONNECTION = "conn-b"
BUSINESS, STORE = "biz-a", "store-a"

#: Set by the fixture — the listing owned by ``FOREIGN_SELLER_ID``.
FOREIGN_LISTING_ID = None


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    # ``ensure_supplier_schema`` caches "already done" per process. Without this
    # a suite that ran earlier in the session leaves the flag set and these tests
    # silently assert against whatever tables happened to already exist.
    schema.reset_schema_cache()
    yield
    schema.reset_schema_cache()


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    global FOREIGN_LISTING_ID
    FOREIGN_LISTING_ID = seed_production_listings(cursor, extra_owner=FOREIGN_SELLER_ID)
    assert FOREIGN_LISTING_ID is not None
    result = schema.ensure_supplier_schema(cursor, force=True)
    assert result["status"] == schema.STATUS_READY, result
    yield cursor
    conn.close()


def _listings(cur):
    cur.execute("SELECT * FROM marketplace_listings ORDER BY id")
    return [dict(row) for row in cur.fetchall()]


def _link(cur, listing_id=PHYSICAL_PUBLISHED_ID, **kwargs):
    kwargs.setdefault("seller_user_id", PRODUCTION_SELLER_ID)
    kwargs.setdefault("provider", "cj")
    kwargs.setdefault("provider_product_id", "cj-100")
    kwargs.setdefault("supplier_connection_id", CONNECTION)
    kwargs.setdefault("business_id", BUSINESS)
    kwargs.setdefault("store_id", STORE)
    return variants.link_source(cur, listing_id=listing_id, **kwargs)


# ---------------------------------------------------------------------------
# §6/§17 — the six production rows are not collateral
# ---------------------------------------------------------------------------


def test_the_fixture_really_is_the_production_shape(cur):
    """Guards the fixture itself, which the rest of this file trusts.

    If somebody "tidies" the fixture to ids 1..6, every ownership and off-by-one
    test below quietly weakens without failing, because id 1 would then exist.
    """
    rows = _listings(cur)
    ids = [row["id"] for row in rows if row["seller_user_id"] == PRODUCTION_SELLER_ID]
    assert ids == [8, 9, 10, 11, 12, 13]
    assert len({row["seller_user_id"] for row in rows if row["id"] in ids}) == 1
    assert all(isinstance(row["price_label"], str) for row in rows)
    assert "price_cents" not in rows[0]


def test_importing_a_supplier_product_changes_no_listing_row(cur):
    """The whole ledger before and after, not just the row being linked.

    Compared as whole rows so that a supplier write which "helpfully" set
    ``quantity`` from provider stock, or rewrote ``price_label`` from supplier
    cost, would fail here rather than in production.
    """
    before = _listings(cur)
    _link(cur, supplier_cost_cents=250, supplier_cost_currency="USD",
          provider_variant_id="cj-100-v1", external_sku="SKU-1")
    assert _listings(cur) == before


def test_import_is_allowed_against_a_listing_still_in_review(cur):
    """Import is a draft-time act. Listing 9 is physical but not yet approved."""
    _link(cur, listing_id=PHYSICAL_REVIEW_ID, provider_product_id="cj-200")
    assert variants.source_for(cur, PHYSICAL_REVIEW_ID) is not None


def test_importing_never_publishes(cur):
    """§11 — the supplier path may create provenance, never visibility."""
    cur.execute("SELECT status, approval_status FROM marketplace_listings WHERE id=?",
                (PHYSICAL_REVIEW_ID,))
    before = dict(cur.fetchone())
    _link(cur, listing_id=PHYSICAL_REVIEW_ID, provider_product_id="cj-200",
          sync_state=schema.SYNC_SYNCED)
    cur.execute("SELECT status, approval_status FROM marketplace_listings WHERE id=?",
                (PHYSICAL_REVIEW_ID,))
    assert dict(cur.fetchone()) == before == {"status": "review_ready",
                                              "approval_status": "review_ready"}


def test_a_digital_listing_can_still_be_reached_by_the_mapping_layer(cur):
    """Refusing drop-ship for digital goods is the gateway's job, not this table's.

    Recorded as a test rather than left implicit because the obvious "safer"
    change is to reject non-physical listings here — which would also block the
    licence-key and stocked-inventory cases this column set exists to support.
    The refusal that matters lives in ``fulfillment.create_intent``, where the
    listing type is checked against the *order*.
    """
    _link(cur, listing_id=DIGITAL_ID, provider_product_id="cj-300",
          fulfillment_mode=schema.MODE_STOCKED)
    assert variants.source_for(cur, DIGITAL_ID)["fulfillment_mode"] == schema.MODE_STOCKED


# ---------------------------------------------------------------------------
# §3/§12 — a mapping cannot outlive or precede its listing, and refusals are mute
# ---------------------------------------------------------------------------


def test_a_mapping_cannot_be_created_without_a_listing(cur):
    with pytest.raises(variants.VariantRejected):
        _link(cur, listing_id=ABSENT_ID)
    cur.execute("SELECT COUNT(*) FROM marketplace_product_sources")
    assert cur.fetchone()[0] == 0


def test_absent_foreign_and_unparseable_references_refuse_identically(cur):
    """One refusal, three causes. Any split here is an existence oracle.

    ``ABSENT_ID`` is 7 — one below the first real listing — so an off-by-one in
    the lookup would show up as a *pass* on the foreign case and a leak here.
    """
    messages = set()
    for reference in (ABSENT_ID, FOREIGN_LISTING_ID, "not-an-id", "", "0", "-1"):
        with pytest.raises(variants.VariantRejected) as failure:
            # Coercion is part of the refusal path, not a step before it: an
            # unparseable reference is a third way to fail and must land on the
            # same message as the other two.
            variants.link_source(
                cur, listing_id=variants.coerce_listing_id(reference),
                seller_user_id=PRODUCTION_SELLER_ID, provider="cj",
                provider_product_id="cj-100")
        messages.add(str(failure.value))
    assert len(messages) == 1, messages


def test_a_foreign_seller_cannot_map_a_listing_it_does_not_own(cur):
    with pytest.raises(variants.VariantRejected):
        _link(cur, seller_user_id=FOREIGN_SELLER_ID)
    with pytest.raises(variants.VariantRejected):
        _link(cur, listing_id=FOREIGN_LISTING_ID)


# ---------------------------------------------------------------------------
# §15 — import identity is merchant + provider + connection + external product
# ---------------------------------------------------------------------------


def test_reimporting_the_same_product_updates_one_row(cur):
    first = _link(cur, provider_variant_id="cj-100-v1")
    second = _link(cur, provider_variant_id="cj-100-v1", supplier_cost_cents=500)
    assert first == second
    cur.execute("SELECT COUNT(*) FROM marketplace_product_sources")
    assert cur.fetchone()[0] == 1


def test_the_same_product_may_be_imported_again_through_a_second_connection(cur):
    """The uniqueness key includes the connection, and that is load-bearing.

    A merchant who reconnects CJ under a new credential gets a new
    ``supplier_connection_id``. Under the old, coarser index — merchant +
    provider + external product — importing the same catalogue item onto a second
    listing after reconnecting was refused as a duplicate, which reads to the
    merchant as "this product is already imported" for a listing they cannot find.
    """
    _link(cur, provider_variant_id="cj-100-v1")
    _link(cur, listing_id=PHYSICAL_REVIEW_ID, supplier_connection_id=OTHER_CONNECTION,
          provider_variant_id="cj-100-v1")
    cur.execute("SELECT COUNT(*) FROM marketplace_product_sources")
    assert cur.fetchone()[0] == 2


def test_one_connection_cannot_map_one_product_onto_two_listings(cur):
    _link(cur)
    with pytest.raises(sqlite3.IntegrityError):
        _link(cur, listing_id=PHYSICAL_REVIEW_ID)


def test_relinking_a_listing_to_a_different_variant_is_refused(cur):
    """§15 — variant identity is part of the binding, not a display detail."""
    _link(cur, provider_variant_id="cj-100-v1")
    with pytest.raises(variants.VariantRejected):
        _link(cur, provider_variant_id="cj-100-v2")


def test_the_retired_index_is_actually_gone(cur):
    """Adding the better index without dropping the coarser one changes nothing.

    Both would be enforced, and the coarser one is the one that refuses. This is
    only safe to drop because the table does not exist in production yet — it
    reads no row and rewrites none.
    """
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='marketplace_product_sources'")
    names = {row[0] for row in cur.fetchall()}
    assert "idx_mkt_source_conn_ref" in names
    assert not names & set(schema.RETIRED_INDEXES)


# ---------------------------------------------------------------------------
# §9/§10 — unknown is a state, not a zero
# ---------------------------------------------------------------------------


def test_an_unstated_supplier_cost_is_stored_as_unknown(cur):
    _link(cur)
    assert variants.source_for(cur, PHYSICAL_PUBLISHED_ID)["supplier_cost_cents"] is None


def test_a_zero_supplier_cost_is_a_real_cost(cur):
    _link(cur, supplier_cost_cents=0)
    assert variants.source_for(cur, PHYSICAL_PUBLISHED_ID)["supplier_cost_cents"] == 0


def test_a_resync_that_does_not_know_the_cost_does_not_erase_it(cur):
    """``None`` means "this caller is not telling you", never "set it to nothing".

    An inventory-only sync carries no price. If that nulled the column, the
    margin the merchant priced against would vanish on the next tick and the
    listing would read as unknown-cost for no reason the merchant could see.
    """
    _link(cur, supplier_cost_cents=250, supplier_cost_currency="USD")
    _link(cur, sync_state=schema.SYNC_SYNCED)
    source = variants.source_for(cur, PHYSICAL_PUBLISHED_ID)
    assert source["supplier_cost_cents"] == 250
    assert source["supplier_cost_currency"] == "USD"
    assert source["sync_state"] == schema.SYNC_SYNCED


def test_a_fresh_mapping_has_never_been_synced(cur):
    """PENDING, not SYNCED. A boolean here would merge "never asked" with "asked
    and got an answer", and the first tick of a broken connection would look
    healthy."""
    _link(cur)
    assert variants.source_for(cur, PHYSICAL_PUBLISHED_ID)["sync_state"] == schema.SYNC_PENDING


def test_error_disconnect_and_removal_are_distinct_from_each_other(cur):
    """Three different provider outcomes that a boolean would flatten into one.

    ERROR is retryable, DISCONNECTED means the credential is gone and retrying is
    pointless, REMOVED means the provider no longer sells it. Merging them either
    retries forever or gives up on a transient failure.
    """
    seen = []
    for state in (schema.SYNC_ERROR, schema.SYNC_DISCONNECTED, schema.SYNC_REMOVED):
        _link(cur, sync_state=state)
        seen.append(variants.source_for(cur, PHYSICAL_PUBLISHED_ID)["sync_state"])
    assert seen == [schema.SYNC_ERROR, schema.SYNC_DISCONNECTED, schema.SYNC_REMOVED]
    assert len(set(seen)) == 3


def test_an_unknown_sync_state_is_refused_not_coerced(cur):
    with pytest.raises(variants.VariantRejected):
        _link(cur, sync_state="PROBABLY_FINE")


def test_a_disconnected_provider_keeps_the_mapping_and_the_listing(cur):
    """Disconnecting a supplier is not a reason to forget where a product came
    from, and certainly not a reason to touch the storefront."""
    before = _listings(cur)
    _link(cur, provider_variant_id="cj-100-v1", supplier_cost_cents=250)
    _link(cur, sync_state=schema.SYNC_DISCONNECTED)
    source = variants.source_for(cur, PHYSICAL_PUBLISHED_ID)
    assert source["provider_product_id"] == "cj-100"
    assert source["provider_variant_id"] == "cj-100-v1"
    assert source["supplier_cost_cents"] == 250
    assert _listings(cur) == before


def test_a_removed_provider_product_does_not_unpublish_the_listing(cur):
    """§11 in reverse: the provider may not change visibility either.

    A merchant may have stock on a shelf. Only the merchant retires a listing.
    """
    _link(cur)
    before = _listings(cur)
    _link(cur, sync_state=schema.SYNC_REMOVED)
    assert _listings(cur) == before


# ---------------------------------------------------------------------------
# §9 — retail belongs to the merchant, provenance belongs to the provider
# ---------------------------------------------------------------------------


def test_provider_fields_and_retail_fields_live_in_different_tables(cur):
    """The separation is structural, not a convention someone has to remember.

    ``marketplace_product_sources`` has no retail column to overwrite; supplier
    cost is integer minor units and retail price is prose on the listing. If a
    price column ever appears on the source table, the two representations meet
    somewhere and one of them starts winning silently.
    """
    cur.execute("PRAGMA table_info(marketplace_product_sources)")
    columns = {row[1] for row in cur.fetchall()}
    assert "supplier_cost_cents" in columns
    assert not columns & {"price_label", "price_cents", "title", "description"}


def test_the_supplier_writer_never_touches_the_listings_table(cur):
    """Whole-row equality across a full, maximal link — every column supplied."""
    before = _listings(cur)
    _link(cur, provider_variant_id="cj-100-v1", external_sku="SKU-1",
          source_snapshot_id="snap-1", supplier_cost_cents=250,
          supplier_cost_currency="USD", inventory_source="cj-warehouse",
          inventory_reference="CN-01", sync_state=schema.SYNC_SYNCED)
    assert _listings(cur) == before


# ---------------------------------------------------------------------------
# §2/§7 — one mapping authority, and the retired ones stay retired
# ---------------------------------------------------------------------------


def test_the_supplier_schema_does_not_create_a_second_mapping_table(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "marketplace_product_sources" in tables
    assert "supplier_product_links" not in tables


def _live_strings(path):
    """Every string literal in a module except docstrings.

    SQL is a string literal, so this finds real queries. Prose is a string
    literal too, which is why docstrings are excluded rather than the whole file
    being grepped: the retired tables *should* still be named in the comments
    explaining why they were retired, and a guard that forbade that would be paid
    for by deleting the explanation.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            first = next(iter(getattr(node, "body", [])), None)
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_no_supplier_module_names_a_retired_authority():
    """A static guard, because the runtime one cannot fire on code nobody runs.

    ``business_os_mkt_products`` is kept — §7 asks for retirement, not a
    destructive migration — and an empty table left lying around is exactly the
    kind of thing a future reader rediscovers and starts trusting. The guard is
    on the *code*, not the prose: naming a retired table in a docstring is how the
    next reader finds out it is retired.
    """
    retired = ("business_os_mkt_products", "supplier_product_links")
    offences = []
    supplier_dir = os.path.join(REPO, "services", "business_os", "suppliers")
    for name in sorted(os.listdir(supplier_dir)):
        if not name.endswith(".py"):
            continue
        for lineno, text in _live_strings(os.path.join(supplier_dir, name)):
            if any(table in text for table in retired):
                offences.append(f"{name}:{lineno}: {text.strip()[:120]}")
    assert offences == [], "\n".join(offences)


def test_the_static_guard_would_actually_catch_a_relapse(tmp_path):
    """Proves the guard reads SQL, not just the absence of the word.

    A guard that skips docstrings could just as easily be skipping everything —
    it would pass on a module that queried the retired table in a way the parser
    never reached. This is the smallest module that must be caught.
    """
    relapse = tmp_path / "relapse.py"
    relapse.write_text(
        '"""A docstring mentioning business_os_mkt_products is fine."""\n'
        'def bind(conn, pid):\n'
        '    return conn.execute(\n'
        '        "SELECT 1 FROM business_os_mkt_products WHERE product_id=?", (pid,))\n',
        encoding="utf-8")
    found = [text for _, text in _live_strings(str(relapse))
             if "business_os_mkt_products" in text]
    assert len(found) == 1 and found[0].startswith("SELECT 1")


# ---------------------------------------------------------------------------
# §13 — a worker owns its own DDL
# ---------------------------------------------------------------------------


def test_a_worker_can_create_the_supplier_schema_without_booting_the_app(tmp_path):
    """The supplier worker never serves HTTP and never runs ``bot.init_db()``.

    If the canonical mapping table only came into existence as a side effect of
    the web app booting first, a worker-only deploy — or a worker that simply
    starts faster — hits "no such table" and the failure looks like data loss.
    Asserted in a subprocess, because ``bot`` may already be imported in this one
    and would make the check vacuous.
    """
    script = textwrap.dedent(
        """
        import os, sqlite3, sys
        sys.path.insert(0, sys.argv[1])
        os.environ["DATABASE_URL"] = "sqlite:///" + sys.argv[2]
        from services.business_os.suppliers import gateway
        gateway.ensure_schema()
        assert "bot" not in sys.modules, "the gateway pulled in the Flask app"
        conn = sqlite3.connect(sys.argv[2])
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "marketplace_product_sources" in names, sorted(names)
        assert "supplier_product_links" not in names, sorted(names)
        print("OK")
        """
    )
    database = str(tmp_path / "worker.sqlite")
    proc = subprocess.run([sys.executable, "-c", script, REPO, database],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# The fixture's own claims about production
# ---------------------------------------------------------------------------


def test_status_and_approval_disagree_on_a_real_row(cur):
    """Listing 10 is ``draft``/``approved`` in production.

    Recorded because it is the reason "is this visible" cannot be read off either
    column alone, and because a fixture author reconciling the two would remove
    the only row that proves it. If this ever fails, production changed — go look
    before editing the assertion.
    """
    cur.execute("SELECT status, approval_status FROM marketplace_listings WHERE id=?",
                (DIGITAL_ID,))
    assert dict(cur.fetchone()) == {"status": "draft", "approval_status": "approved"}
    assert any(row[0] == DIGITAL_ID and row[3] != row[4] for row in PRODUCTION_LISTINGS)
