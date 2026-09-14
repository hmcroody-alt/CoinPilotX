"""One tap imports, prices, validates and publishes — or says exactly why not.

What this file is defending
---------------------------
The philosophy change. "Import to Store" used to mean "create a draft", and the
merchant's next action was always to open an editor and type a price the store
could have supplied. Now the pipeline runs to the end, and the only products that
stop are the ones that genuinely cannot be sold safely.

Both halves of that sentence need defending, and they pull in opposite
directions, which is why they are in one file:

* **Publishing must actually happen.** An ordinary product comes out
  ``PUBLISHED``, priced against its real supplier cost, with a ``price_label`` the
  buyer's checkout can parse, a unit count from the supplier's warehouse, a cover
  image, and a supplier binding an order can be placed against. A test that only
  asserted the outcome string would pass with every one of those missing.
* **Publishing must not happen when it would be a lie.** No price, no readable
  cost, no orderable variant, a supplier that dropped out, a restricted product:
  each lands ``NEEDS_ATTENTION`` carrying the specific code, and nothing reaches a
  buyer. §1 — never fabricate — is the constraint that makes the first half hard.

Where the boundary sits, and why it is not arbitrary
---------------------------------------------------
A dropshipped listing sells exactly one supplier variant: the buyer's checkout
charges one listing-level price and shows no variant picker, and
``fulfillment.create_intent`` can place an order for no variant but the bound one.
So "which variant does this listing sell" has to have an answer before anything
can publish.

``importer._sole_orderable`` answers it in the two cases where answering is not
choosing on the merchant's behalf — one variant chosen, or one variant not
confirmed-unavailable — and refuses in the case where it is. Three colours all in
stock is a real question about what a buyer receives, and inventing an answer
would ship somebody the wrong thing. Those import and wait for one tap.

That is a genuine limitation, not a bug being papered over: buyer-side variant
selection does not exist anywhere in the checkout, and building it is a separate
feature spanning the buyer payload, the cart and the order→supplier binding.

The mutation battery
--------------------
The last section pins the ten mutations §43 names. Each is a one-line change to
the implementation that a reasonable reviewer might wave through, and each is
mapped by name to the test that fails when it is made. They are written as
assertions rather than run through a mutation harness on purpose — the mapping is
the useful artefact, and it is checkable by reading.

Runs alone — see the header of ``test_dropship_import_pipeline``.
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-autopublish-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, fulfillment, gateway, import_cart, importer, pricing, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OTHER_BUSINESS, OTHER_CONNECTION,
    OTHER_OWNER_ID, OTHER_STORE, OWNER_ID, STORE, _seed_connection, _seed_tenancy,
    cj_product)

# That import ran the pipeline module's header, which pointed DATABASE_URL at its
# own temp file. Point it back before anything here opens a connection.
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
        _seed_connection(conn, CONNECTION, BUSINESS, STORE, OWNER_ID)
        _seed_connection(conn, OTHER_CONNECTION, OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID)
        conn.commit()
    finally:
        conn.close()
    import_cart.ensure_schema()
    gateway.ensure_schema()
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

def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def listing():
    found = rows("SELECT * FROM marketplace_listings ORDER BY id DESC LIMIT 1")
    assert found, "no listing was created"
    return found[0]


def source(listing_id):
    return rows("SELECT * FROM marketplace_product_sources WHERE listing_id=?",
                (listing_id,))[0]


#: One variant, in stock, with a readable cost. The shape of an ordinary CJ
#: product for the purposes of this pipeline: exactly one thing a buyer can
#: receive, so there is nothing for the merchant to decide.
SIMPLE = [{"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
           "variantQuantity": 40, "variantSku": "PID-1-SKU-1"}]


def add_to_cart(pid, *, selected=None, business=BUSINESS, store=STORE,
                connection=CONNECTION, actor=OWNER_ID):
    return import_cart.add_item(business, store, actor, connection,
                                external_product_id=pid,
                                selected_variant_ids=selected, context=CONTEXT)


def run_import(*, rule=None, business=BUSINESS, store=STORE,
               connection=CONNECTION, actor=OWNER_ID):
    return importer.import_selected(business, store, actor, connection,
                                    pricing_rule=rule, context=CONTEXT)


def import_one(provider_, pid="PID-1", *, variants_=SIMPLE, selected=None, rule=None,
               **product_kwargs):
    """Add one product to the cart, import it, return its result row."""
    provider_.add(cj_product(pid, variants_=variants_, **product_kwargs))
    add_to_cart(pid, selected=selected)
    return run_import(rule=rule)["results"][0]


def set_policy(**fields):
    conn = db.connect()
    try:
        result = store_policy.set_policy(conn, BUSINESS, STORE, **fields)
        conn.commit()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §38 — a real simple product goes all the way
# ---------------------------------------------------------------------------

def test_one_tap_imports_prices_publishes_and_verifies(provider):
    """The mission, as one test. Every field a buyer needs, measured.

    Asserted field by field rather than through the outcome string, because
    ``PUBLISHED`` is exactly the claim that used to be unverifiable: an
    ``UPDATE ... WHERE seller_user_id=?`` against the wrong identity updates zero
    rows, raises nothing, and would have reported this outcome anyway.
    """
    entry = import_one(provider)
    assert entry["outcome"] == importer.PUBLISHED, entry
    assert entry["published"] is True
    # No `problems` key at all on a success. An empty list would read on the
    # success screen as "we checked and found nothing", which is the same thing,
    # but it would also let a caller `.length`-check its way into rendering an
    # empty problems section under a published product.
    assert "problems" not in entry

    row = listing()
    assert row["status"] == "published"
    # Published to the merchant's store is not approved for the marketplace.
    # Publishing must not self-approve: `is_public()` requires both, and a product
    # that approved itself skipped review.
    assert row["approval_status"] == "pending_review"
    assert entry["awaiting_moderation"] is True

    # Priced, from the supplier's real cost, by the platform default rule.
    assert rows("SELECT cost_cents, price_cents FROM marketplace_listing_variants"
                " WHERE listing_id=?", (row["id"],))[0] == {
        "cost_cents": 820, "price_cents": 1491}
    # And the price the buyer's checkout will actually parse, on the listing.
    assert row["price_label"] == "$14.91"
    assert entry["price_label"] == "$14.91"

    # The supplier's unit count, not a count of variants.
    assert row["quantity"] == 40
    assert entry["quantity"] == 40

    # A cover the buyer grid can render, in the column buyer surfaces read.
    assert row["cover_image_url"] == "https://cdn.example.com/PID-1.jpg"

    # And a binding, so an order for this listing can be placed with the supplier.
    assert source(row["id"])["provider_variant_id"] == "PID-1-V1"


def test_the_batch_summary_says_published(provider):
    provider.add(cj_product("PID-1", variants_=SIMPLE))
    add_to_cart("PID-1")
    result = run_import()

    assert result["published"] is True
    assert result["published_count"] == 1
    assert result["needs_attention"] == 0
    assert result["imported"] == 1
    assert result["counts"] == {importer.PUBLISHED: 1}
    assert result["pricing_source"] == store_policy.SOURCE_PLATFORM


def test_the_cart_is_emptied_by_a_published_import(provider):
    import_one(provider)
    assert import_cart.get_cart(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                context=CONTEXT)["items"] == []


def test_a_store_that_turned_auto_publish_off_still_gets_a_draft(provider):
    """§44's spirit: a setting the merchant changed is a decision.

    Finishing the listing anyway because the code can would be this module
    overruling something they went and turned off.
    """
    set_policy(auto_publish=False)
    entry = import_one(provider)
    assert entry["outcome"] == importer.IMPORTED
    assert entry["published"] is False
    assert listing()["status"] == "draft"
    # Priced all the same. Auto-publish and auto-pricing are different settings,
    # and a merchant who wants to review each product still wants a price proposed.
    assert rows("SELECT price_cents FROM marketplace_listing_variants")[0][
        "price_cents"] == 1491


# ---------------------------------------------------------------------------
# §19/§20 — the store, not the whole marketplace
# ---------------------------------------------------------------------------

def test_marketplace_distribution_is_off_unless_the_store_turns_it_on(provider):
    """One import tap must not broadcast a sourced product network-wide.

    Store publication and Marketplace distribution are related and separate. The
    default is off, and the flag is recorded on the product at import time rather
    than read from the store later — see ``_create_draft_listing``.
    """
    entry = import_one(provider)
    assert entry["outcome"] == importer.PUBLISHED
    metadata = json.loads(listing()["listing_metadata_json"])
    assert metadata["marketplace_autolist"] is False


def test_a_store_that_opted_into_distribution_says_so_on_the_product(provider):
    set_policy(marketplace_autolist=True)
    entry = import_one(provider)
    assert entry["outcome"] == importer.PUBLISHED
    assert json.loads(listing()["listing_metadata_json"])["marketplace_autolist"] is True


def test_turning_distribution_on_later_does_not_back_date_earlier_imports(provider):
    """The single broadcast the split exists to prevent.

    A merchant who imported fifty products with distribution off has said something
    about those fifty. Reading the store's *current* setting at distribution time
    would let one toggle publish all of them to the whole marketplace at once.
    """
    import_one(provider, "PID-1")
    set_policy(marketplace_autolist=True)
    import_one(provider, "PID-2")

    by_title = {r["id"]: json.loads(r["listing_metadata_json"])["marketplace_autolist"]
                for r in rows("SELECT id, listing_metadata_json FROM marketplace_listings"
                              " ORDER BY id")}
    assert sorted(by_title.values()) == [False, True]


# ---------------------------------------------------------------------------
# §39 — a real multi-variant product
# ---------------------------------------------------------------------------

TWO_IN_STOCK = [
    {"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
     "variantQuantity": 40, "variantSku": "PID-1-SKU-1"},
    {"vid": "PID-1-V2", "variantKey": "Black-M", "variantSellPrice": "8.60",
     "variantQuantity": 12, "variantSku": "PID-1-SKU-2"},
]


def test_two_orderable_variants_is_a_question_this_import_will_not_answer(provider):
    """§1. Guessing which variant a buyer receives is fabricating product identity.

    Both are in stock, so both could ship, and nothing here knows which one the
    buyer wants. The listing is created with both variants' real facts on it and
    waits for one tap.
    """
    entry = import_one(provider, variants_=TWO_IN_STOCK)
    assert entry["outcome"] == importer.NEEDS_ATTENTION
    assert drafts.SUPPLIER_VARIANT_UNBOUND in entry["problems"]
    assert listing()["status"] == "draft"
    assert source(listing()["id"])["provider_variant_id"] is None
    # Both variants are there, with their real costs and their provider identity —
    # the merchant is choosing between facts, not being asked to retype them.
    assert [v["cost_cents"] for v in rows(
        "SELECT cost_cents FROM marketplace_listing_variants ORDER BY position")] \
        == [820, 860]


def test_choosing_one_variant_in_the_cart_publishes(provider):
    """The merchant's own selection is an answer, and the ordinary path to one."""
    entry = import_one(provider, variants_=TWO_IN_STOCK, selected=["PID-1-V2"])
    assert entry["outcome"] == importer.PUBLISHED
    assert source(listing()["id"])["provider_variant_id"] == "PID-1-V2"
    # 860 at 45%, and the *chosen* variant's price — not the cheaper sibling's.
    assert listing()["price_label"] == "$15.64"


def test_one_orderable_variant_among_sold_out_siblings_publishes(provider):
    """Not a choice: the siblings are confirmed unavailable.

    An order for a sold-out variant would be refused at the supplier, so binding
    the survivor substitutes nothing for anything. This is the case that keeps
    needs-attention the exception rather than the norm for real CJ apparel, where
    most sizes are routinely out of stock.
    """
    entry = import_one(provider, variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
         "variantQuantity": 0, "variantSku": "PID-1-SKU-1"},
        {"vid": "PID-1-V2", "variantKey": "Black-M", "variantSellPrice": "8.60",
         "variantQuantity": 12, "variantSku": "PID-1-SKU-2"},
        {"vid": "PID-1-V3", "variantKey": "Black-L", "variantSellPrice": "8.90",
         "variantQuantity": 0, "variantSku": "PID-1-SKU-3"},
    ])
    assert entry["outcome"] == importer.PUBLISHED, entry
    assert source(listing()["id"])["provider_variant_id"] == "PID-1-V2"
    assert listing()["price_label"] == "$15.64"
    assert listing()["quantity"] == 12


def test_an_unreadable_variant_beside_an_in_stock_one_is_still_a_choice(provider):
    """UNKNOWN is not a negative, and an inventory outage must not pick for anyone.

    The mirror of the test above, and the reason ``_sole_orderable`` narrows on
    ``!= UNAVAILABLE`` rather than on ``== AVAILABLE``. A variant the provider
    declined to report on might be perfectly orderable; treating silence as
    "sold out" would let a bad afternoon at CJ decide what a merchant sells.
    """
    entry = import_one(provider, variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
         "variantQuantity": 40, "variantSku": "PID-1-SKU-1"},
        # No quantity at all: normalize cannot read a stock state for this one.
        {"vid": "PID-1-V2", "variantKey": "Black-M", "variantSellPrice": "8.60",
         "variantSku": "PID-1-SKU-2"},
    ])
    assert entry["outcome"] == importer.NEEDS_ATTENTION
    assert drafts.SUPPLIER_VARIANT_UNBOUND in entry["problems"]


# ---------------------------------------------------------------------------
# §40 — a product with a forced blocker
# ---------------------------------------------------------------------------

def test_a_variant_with_no_readable_cost_is_not_published_at_any_price(provider):
    """§11 fails closed on unknown cost. The alternative is selling at a loss.

    ``pricing.apply_rule`` returns ``None`` rather than pricing from zero, and the
    gate then refuses. Both halves are asserted: a rule that priced an unknown cost
    as 0 would produce a listing at ``$0.00``, which §11 forbids outright.
    """
    entry = import_one(provider, variants_=[
        {"vid": "PID-1-V1", "variantKey": "Black-S", "variantQuantity": 5,
         "variantSku": "PID-1-SKU-1"}])
    assert entry["outcome"] == importer.NEEDS_ATTENTION
    assert drafts.MISSING_PRICE in entry["problems"]

    row = listing()
    assert row["status"] == "draft"
    assert row["price_label"] == ""
    assert row["price_label"] not in {"$0.00", "Free"}
    assert rows("SELECT price_cents FROM marketplace_listing_variants")[0][
        "price_cents"] is None


def test_a_product_with_no_usable_media_creates_nothing(provider):
    provider.add(cj_product("PID-1", variants_=SIMPLE, media=False))
    add_to_cart("PID-1")
    result = run_import()
    assert result["results"][0]["outcome"] == importer.NO_MEDIA
    assert rows("SELECT id FROM marketplace_listings") == []
    assert result["published"] is False


def test_a_product_with_no_variants_creates_nothing(provider):
    provider.add(cj_product("PID-1", variants_=[]))
    add_to_cart("PID-1")
    result = run_import()
    assert result["results"][0]["outcome"] == importer.NO_VARIANTS
    assert rows("SELECT id FROM marketplace_listings") == []


def test_a_restricted_product_is_held_for_review_and_never_published(provider):
    provider.add(cj_product("PID-1", variants_=SIMPLE,
                            title="Full Spectrum CBD Tincture"))
    add_to_cart("PID-1")
    result = run_import()
    assert result["results"][0]["outcome"] == importer.NEEDS_REVIEW
    assert rows("SELECT id FROM marketplace_listings") == []
    assert result["published"] is False


def test_a_supplier_that_dropped_the_product_does_not_publish(provider):
    """§25. The gate is asked *after* the supplier mapping is written, on purpose.

    Reached by marking the source ``REMOVED`` and re-running the gate through
    ``autopublish`` — the state a sync worker writes when CJ stops listing a
    product. Nothing may be sellable through a supplier that cannot ship it.
    """
    import_one(provider)
    listing_id = listing()["id"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status='draft', price_label='',"
                    " published_at=NULL WHERE id=?", (listing_id,))
        cur.execute("UPDATE marketplace_product_sources SET sync_state=?"
                    " WHERE listing_id=?", (supplier_schema.SYNC_REMOVED, listing_id))
        result = drafts.autopublish(cur, listing_id, int(OWNER_ID))
        conn.commit()
    finally:
        conn.close()

    assert result["published"] is False
    assert drafts.PROVIDER_PRODUCT_UNAVAILABLE in result["problems"]
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"


# ---------------------------------------------------------------------------
# §41 — re-import
# ---------------------------------------------------------------------------

def test_re_importing_a_published_product_neither_duplicates_nor_republishes(provider):
    first = import_one(provider)
    assert first["outcome"] == importer.PUBLISHED

    add_to_cart("PID-1")
    second = run_import()["results"][0]
    assert second["outcome"] == importer.ALREADY_EXISTS
    assert second["listing_id"] == first["listing_id"]
    assert len(rows("SELECT id FROM marketplace_listings")) == 1
    assert len(rows("SELECT listing_id FROM marketplace_product_sources")) == 1
    assert len(rows("SELECT id FROM marketplace_listing_variants")) == 1


def test_a_double_tap_in_one_batch_creates_one_listing(provider):
    """§34. Two cart rows for one product is what a double-tapped Import looks like.

    ``import_cart.add_item`` collapses them to one row, and the supplier-mapping
    read collapses whatever survives that. One listing either way.
    """
    provider.add(cj_product("PID-1", variants_=SIMPLE))
    add_to_cart("PID-1")
    add_to_cart("PID-1")
    result = run_import()
    assert len(rows("SELECT id FROM marketplace_listings")) == 1
    assert result["published_count"] + result["counts"].get(
        importer.ALREADY_EXISTS, 0) == len(result["results"])


def test_a_merchant_edit_survives_a_re_import_of_a_live_product(provider):
    import_one(provider)
    listing_id = listing()["id"]
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"title": "My Own Title"}, context=CONTEXT)
    add_to_cart("PID-1")
    run_import()
    row = listing()
    assert row["title"] == "My Own Title"
    assert row["status"] == "published"


# ---------------------------------------------------------------------------
# §42 — a mixed bulk batch, reported honestly
# ---------------------------------------------------------------------------

def test_a_mixed_batch_reports_each_product_for_what_it_is(provider):
    """§30. "17 published, 2 need attention, 1 could not be imported", not "done".

    Four products, four different fates, one request. The summary has to be
    decomposable back into them — a batch reported as published because something
    in it published is the dishonest version of this screen.
    """
    provider.add(cj_product("PID-1", variants_=SIMPLE))                 # publishes
    provider.add(cj_product("PID-2", variants_=TWO_IN_STOCK))           # needs a choice
    provider.add(cj_product("PID-3", variants_=SIMPLE, media=False))    # no listing
    provider.add(cj_product("PID-4", variants_=SIMPLE))
    provider.fail["PID-4"] = "provider_unavailable"                      # no listing
    for pid in ("PID-1", "PID-2", "PID-3", "PID-4"):
        add_to_cart(pid)

    result = run_import()
    outcomes = {r["external_product_id"]: r["outcome"] for r in result["results"]}
    assert outcomes == {
        "PID-1": importer.PUBLISHED,
        "PID-2": importer.NEEDS_ATTENTION,
        "PID-3": importer.NO_MEDIA,
        "PID-4": importer.PROVIDER_UNAVAILABLE,
    }
    assert result["published_count"] == 1
    assert result["needs_attention"] == 1
    assert result["imported"] == 2          # two listings exist
    assert result["requested"] == 4
    # One success does not make the batch a success.
    assert result["published"] is False
    assert len(rows("SELECT id FROM marketplace_listings")) == 2


def test_a_batch_uses_one_pipeline_and_one_store_policy(provider):
    """§32. Bulk is not a second import engine, and §29 — bulk honours the policy.

    Measured by the prices: every product in the batch is priced by the same store
    rule, and the result names that rule once for the whole batch.
    """
    set_policy(pricing_rule={"type": pricing.MULTIPLIER, "value": 2})
    for pid in ("PID-1", "PID-2", "PID-3"):
        provider.add(cj_product(pid, variants_=[dict(SIMPLE[0], vid=f"{pid}-V1")]))
        add_to_cart(pid)

    result = run_import()
    assert result["published_count"] == 3
    assert result["pricing_source"] == store_policy.SOURCE_STORE
    assert {r["price_label"] for r in rows("SELECT price_label FROM marketplace_listings")} \
        == {"$16.40"}


def test_one_item_that_cannot_finish_does_not_roll_back_its_neighbours(provider):
    provider.add(cj_product("PID-1", variants_=SIMPLE))
    provider.add(cj_product("PID-2", variants_=[
        {"vid": "PID-2-V1", "variantKey": "Black-S", "variantQuantity": 5}]))
    add_to_cart("PID-1")
    add_to_cart("PID-2")
    run_import()

    states = {r["price_label"]: r["status"] for r in
              rows("SELECT price_label, status FROM marketplace_listings")}
    assert states == {"$14.91": "published", "": "draft"}


# ---------------------------------------------------------------------------
# §43 — the mutation battery
#
# Each test below is the one that fails when the named one-line change is made.
# Written out by name because the mapping is the artefact: a battery whose
# mutations are not traceable to tests cannot be audited, only re-run.
# ---------------------------------------------------------------------------

def test_mutation_a_missing_price_cannot_publish(provider):
    """Mutation: delete the MISSING_PRICE branch from ``drafts._validate``.

    Reached without touching the gate: a store on MANUAL_PRICE prices nothing, so
    the branch is the only thing standing between an unpriced import and a buyer.
    """
    set_policy(pricing_rule={"type": pricing.MANUAL_PRICE})
    entry = import_one(provider)
    assert entry["outcome"] == importer.NEEDS_ATTENTION
    assert drafts.MISSING_PRICE in entry["problems"]
    assert listing()["status"] == "draft"
    assert listing()["price_label"] == ""


def test_mutation_b_an_unknown_cost_is_never_treated_as_zero(provider):
    """Mutation: ``cost = variant.get("cost_cents") or 0`` in ``_write_variants``.

    The single most dangerous line in the pipeline, because it is the one a
    reasonable person writes to make the type checker happy. Every rule would then
    price from zero and every unknown-cost product would publish at or near free.
    """
    entry = import_one(provider, rule={"type": pricing.COST_PLUS_PERCENT, "value": 50},
                       variants_=[{"vid": "PID-1-V1", "variantKey": "Black-S",
                                   "variantQuantity": 5, "variantSku": "PID-1-SKU-1"}])
    assert entry["outcome"] == importer.NEEDS_ATTENTION
    variant = rows("SELECT cost_cents, price_cents FROM marketplace_listing_variants")[0]
    assert variant["cost_cents"] is None
    assert variant["price_cents"] is None, "an unknown cost was priced from zero"
    assert variant["price_cents"] != 0
    assert listing()["price_label"] == ""


def test_mutation_c_a_listing_with_no_variants_cannot_publish(provider):
    """Mutation: drop the NO_VARIANTS_SELECTED branch, or the NO_VARIANTS refusal.

    Asserted at both layers, because they fail differently: the importer refuses to
    create the listing at all, and the gate refuses to publish one whose variants
    were deleted afterwards — which is the state a variant cleanup would produce.
    """
    provider.add(cj_product("PID-2", variants_=[]))
    add_to_cart("PID-2")
    assert run_import()["results"][0]["outcome"] == importer.NO_VARIANTS

    import_one(provider, "PID-1")
    listing_id = listing()["id"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listing_variants WHERE listing_id=?",
                    (listing_id,))
        cur.execute("UPDATE marketplace_listings SET status='draft' WHERE id=?",
                    (listing_id,))
        result = drafts.autopublish(cur, listing_id, int(OWNER_ID))
        conn.commit()
    finally:
        conn.close()
    assert result["published"] is False
    assert drafts.NO_VARIANTS_SELECTED in result["problems"]


def test_mutation_d_a_restricted_product_cannot_publish(provider):
    """Mutation: remove a term from ``REVIEW_TERMS``, or the RESTRICTED_PRODUCT branch.

    Both directions: the importer refuses the product outright, and a listing whose
    moderation verdict is ``rejected`` cannot be published by the gate either.
    """
    provider.add(cj_product("PID-2", variants_=SIMPLE, title="Nicotine Vape Pods"))
    add_to_cart("PID-2")
    assert run_import()["results"][0]["outcome"] == importer.NEEDS_REVIEW

    import_one(provider, "PID-1")
    listing_id = listing()["id"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status='draft',"
                    " approval_status='rejected' WHERE id=?", (listing_id,))
        result = drafts.autopublish(cur, listing_id, int(OWNER_ID))
        conn.commit()
    finally:
        conn.close()
    assert result["published"] is False
    assert drafts.RESTRICTED_PRODUCT in result["problems"]


def test_mutation_e_the_listing_lands_on_the_importing_store_only(provider):
    """Mutation: resolve the seller from the first store instead of the scoped one.

    §14 — never a first-Store fallback. Two tenants import the same catalogue
    product; each listing must carry its own owner, and neither store may gain a
    product it did not import.
    """
    provider.add(cj_product("PID-1", variants_=SIMPLE))
    add_to_cart("PID-1")
    run_import()
    add_to_cart("PID-1", business=OTHER_BUSINESS, store=OTHER_STORE,
                connection=OTHER_CONNECTION, actor=OTHER_OWNER_ID)
    run_import(business=OTHER_BUSINESS, store=OTHER_STORE,
               connection=OTHER_CONNECTION, actor=OTHER_OWNER_ID)

    owners = sorted(int(r["seller_user_id"]) for r in
                    rows("SELECT seller_user_id FROM marketplace_listings"))
    assert owners == sorted([int(OWNER_ID), int(OTHER_OWNER_ID)])
    for row in rows("SELECT id, seller_user_id FROM marketplace_listings"):
        assert int(source(row["id"])["seller_user_id"]) == int(row["seller_user_id"])
    # And each store's own scope really is what it published.
    scoped = rows("SELECT business_id, store_id FROM marketplace_product_sources"
                  " ORDER BY business_id")
    assert [(r["business_id"], r["store_id"]) for r in scoped] == [
        (BUSINESS, STORE), (OTHER_BUSINESS, OTHER_STORE)]


def test_mutation_f_no_duplicate_listing_for_one_supplier_product(provider):
    """Mutation: drop the ``_existing_listing`` read, or widen its WHERE clause.

    Three imports of one product. Two listings would mean two prices for one thing
    and a buyer able to purchase the stale one.
    """
    import_one(provider)
    for _ in range(2):
        add_to_cart("PID-1")
        assert run_import()["results"][0]["outcome"] == importer.ALREADY_EXISTS
    assert len(rows("SELECT id FROM marketplace_listings")) == 1


def test_mutation_g_no_provider_identifier_reaches_a_buyer_field(provider):
    """Mutation: seed the title, description or price prose from provider data.

    §13 — CJ identifiers are never exposed to buyers. The variant id and the
    supplier SKU live in ``marketplace_listing_variants`` and
    ``marketplace_product_sources``, which are merchant-private, and must appear in
    none of the columns a buyer surface renders as text.
    """
    import_one(provider)
    row = listing()
    buyer_text = " ".join(str(row.get(column) or "") for column in
                          ("title", "description", "category", "price_label"))
    for secret in ("PID-1-V1", "PID-1-SKU-1", "cjdropshipping", "conn-a"):
        assert secret not in buyer_text, f"{secret} reached a buyer-visible field"
    # The identifiers are recorded — privately. Asserting their absence above would
    # otherwise pass just as well with the supplier mapping never written.
    assert source(row["id"])["provider_variant_id"] == "PID-1-V1"
    assert rows("SELECT sku FROM marketplace_listing_variants")[0]["sku"] == "PID-1-SKU-1"


def test_mutation_h_the_client_cannot_ask_for_a_published_listing(provider):
    """Mutation: accept a ``status``/``published`` field from the request. §33.

    Measured against the signature rather than by sending one, because a rejected
    parameter and an unread parameter look identical from outside. ``import_selected``
    takes cart ids and a pricing *strategy*; there is nothing to construct a ready
    listing with.
    """
    import inspect
    accepted = set(inspect.signature(importer.import_selected).parameters)
    assert accepted == {"business_id", "store_id", "actor_user_id", "connection_id",
                        "item_ids", "pricing_rule", "context", "adapter"}
    for forbidden in ("status", "published", "price_cents", "price_label", "quantity",
                      "cost_cents", "approval_status", "seller_user_id"):
        assert forbidden not in accepted

    # And the same for the cart, which is the only other thing the phone writes.
    cart_accepted = set(inspect.signature(import_cart.add_item).parameters)
    for forbidden in ("cost_cents", "price_cents", "status", "published"):
        assert forbidden not in cart_accepted


def test_mutation_i_removing_the_read_back_is_caught(provider):
    """Mutation: return ``{"published": True}`` without calling ``verify_published``.

    The read-back is the difference between a measurement and an assumption, and
    it cannot be tested by a happy path — a publish that works looks the same with
    or without it. So this makes the write fail to stick: the gate passes, the row
    does not say ``published``, and ``autopublish`` must report that rather than its
    own UPDATE.

    The mechanism is a listing id the seller does not own. ``_publish_core``'s
    ``WHERE id=? AND seller_user_id=?`` matches nothing, updates zero rows, and
    raises nothing at all — which is exactly the silent failure §35 exists for.
    """
    import_one(provider)
    listing_id = listing()["id"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status='draft', price_label='',"
                    " published_at=NULL WHERE id=?", (listing_id,))
        original = drafts._publish_core

        def publish_to_nobody(cursor, lid, seller_user_id, row, shipping_cents=None):
            # The gate runs for real against the real listing; only the write is
            # aimed at an identity that owns nothing. The shipping allowance is
            # forwarded untouched: this mutation is about the read-back, and a
            # stub that dropped it would silently test item-cost margins instead.
            return original(cursor, lid, 999999, row, shipping_cents)

        drafts._publish_core = publish_to_nobody
        try:
            result = drafts.autopublish(cur, listing_id, int(OWNER_ID))
        finally:
            drafts._publish_core = original
        conn.commit()
    finally:
        conn.close()

    assert result["published"] is False, "a publish that did not land reported success"
    assert drafts.PUBLISH_NOT_PERSISTED in result["problems"]
    # And the committed row is a draft, not a listing claiming to be published
    # while the merchant is told it is not.
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"


def test_mutation_j_an_unavailable_supplier_cannot_publish(provider):
    """Mutation: drop the ``sync_state`` branches from ``drafts._validate``.

    A disconnected supplier and a removed product are both "nobody can ship this",
    and both must stop publication. Asserted for each state separately because they
    are two branches and deleting one is the plausible edit.
    """
    for state, code in ((supplier_schema.SYNC_DISCONNECTED, drafts.SUPPLIER_DISCONNECTED),
                        (supplier_schema.SYNC_REMOVED, drafts.PROVIDER_PRODUCT_UNAVAILABLE)):
        conn = db.connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM marketplace_listings")
            cur.execute("DELETE FROM marketplace_product_sources")
            cur.execute("DELETE FROM marketplace_listing_variants")
            conn.commit()
        finally:
            conn.close()
        provider.products.clear()

        import_one(provider)
        listing_id = listing()["id"]
        conn = db.connect()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE marketplace_listings SET status='draft', price_label='',"
                        " published_at=NULL WHERE id=?", (listing_id,))
            cur.execute("UPDATE marketplace_product_sources SET sync_state=?"
                        " WHERE listing_id=?", (state, listing_id))
            result = drafts.autopublish(cur, listing_id, int(OWNER_ID))
            conn.commit()
        finally:
            conn.close()

        assert result["published"] is False, state
        assert code in result["problems"], (state, result["problems"])
        assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                    (listing_id,))[0]["status"] == "draft"


# ---------------------------------------------------------------------------
# §37 — no supplier spend
# ---------------------------------------------------------------------------

def test_importing_places_no_supplier_order(provider):
    """Real CJ supplier spend is $0, and the pipeline is why, not the sandbox flag.

    Publishing a listing is a statement about a storefront. Buying anything from CJ
    happens when a *buyer* pays, through ``fulfillment.create_intent``, and no
    amount of importing may reach it. Measured on the provider seam: the only
    operations this pipeline performs are reads.
    """
    provider.add(cj_product("PID-1", variants_=SIMPLE))
    add_to_cart("PID-1")
    assert run_import()["results"][0]["outcome"] == importer.PUBLISHED

    operations = {call[0] for call in provider.calls}
    assert operations <= {"product", "variants", "inventory"}, operations

    # And nothing was written on the order side either. The two tables are named
    # rather than discovered: a `LIKE '%supplier_order%'` sweep matches neither of
    # them, so it would loop over nothing and assert nothing, which is worse than
    # no assertion because it reads like one.
    #
    # They are created here rather than assumed present. `fulfillment.ensure_schema`
    # is called by `create_intent`, not by this file's fixture, so on an import-only
    # database the tables do not exist -- and "no such table" is an error, not a
    # failure, and would still be raised if the importer had placed an order. Making
    # them exist first is what gives the emptiness below something to be false about.
    fulfillment.ensure_schema()
    for table in ("business_os_supplier_intents", "business_os_supplier_outbox"):
        assert rows(f"SELECT * FROM {table}") == [], f"{table} was written at import"
