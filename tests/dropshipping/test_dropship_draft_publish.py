"""Merchant edits stick, cost stays private, and publishing is a decision.

What this file is defending
---------------------------
The half of the pipeline that runs after a draft exists.

* **Field ownership.** The merchant owns title, description, category, media
  order, retail price and visibility. The supplier owns cost, inventory,
  provider identity and availability. ``update_draft`` records every merchant
  edit into ``overridden_fields``, and that list is what stops a later provider
  sync reverting the merchant's own words. The recording is the mechanism, not
  bookkeeping — a test that only checks the edit landed would pass with the
  recording deleted.

* **Cost is merchant-private.** It belongs in a draft the merchant is reviewing
  and nowhere a buyer can reach. A retail price edit must not disturb it, which
  is why ``_set_prices`` omits ``cost_cents`` rather than passing it through.

* **Publish is a gate, not a state change.** A draft with no price, no media, a
  negative margin, or a disconnected supplier is refused with the specific
  reasons — all of them, so the merchant is not made to discover them one
  re-submission at a time. And publishing sets ``status``; it must not
  self-approve moderation, because ``is_public()`` requires both and a product
  that approved itself skipped review.

Runs alone — see the header of ``test_dropship_import_pipeline``.
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-publish-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, gateway, import_cart, importer, pricing)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

# Reuse the pipeline suite's fixtures rather than restating them; they are the
# same tenancy and the same fake provider.
from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OTHER_BUSINESS, OTHER_CONNECTION,
    OTHER_OWNER_ID, OTHER_STORE, OWNER_ID, STORE, _seed_connection, _seed_tenancy,
    cj_product)

# That import ran the pipeline module's own header, which pointed DATABASE_URL at
# *its* temp file. db resolves the sqlite path per call, so without this every
# query below would run against a database this file never truncates.
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


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def imported(provider, pid="PID-1", selection=None, **product_kwargs):
    """Import one product and return its listing id.

    ``selection`` is the merchant's per-variant choice, exactly as
    ``SupplierProductScreen`` sends it. Left out, everything sellable is imported
    — which is what the screen defaults to, and which produces a listing with no
    single supplier variant behind it. See :func:`sellable`.
    """
    provider.add(cj_product(pid, **product_kwargs))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid, selected_variant_ids=selection,
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    entry = result["results"][0]
    assert entry["outcome"] == importer.IMPORTED, entry
    return entry["listing_id"]


def sellable(provider, pid="PID-1", vid=None, **product_kwargs):
    """Import one product as something that can actually be sold.

    A dropshipped listing sells exactly the supplier variant named by
    ``marketplace_product_sources.provider_variant_id``: ``create_intent`` routes
    every line through ``gateway.get_product_binding``, which refuses outright
    when that column is NULL. So a listing with two variants and no binding is a
    listing nothing can ship, and publication now says so
    (``SUPPLIER_VARIANT_UNBOUND``).

    Selecting one variant at import is how a merchant reaches that state through
    the UI that exists — the import screen already sends the selection — and
    ``importer`` records the binding from it. This helper is therefore the
    ordinary path, and ``imported`` is the ambiguous one.
    """
    listing_id = imported(provider, pid=pid, selection=[vid or f"{pid}-V1"],
                          **product_kwargs)
    assert rows("SELECT provider_variant_id FROM marketplace_product_sources "
                "WHERE listing_id=?", (listing_id,))[0]["provider_variant_id"] \
        == (vid or f"{pid}-V1"), "import must bind the single chosen variant"
    return listing_id


def bind(listing_id, pid, vid, provider="cj"):
    """Bind a supplier variant to an already-imported listing.

    The same call ``POST .../cj/connections/<id>/bind-product`` makes. Used by the
    multi-variant tests, which are about what publication does once the listing
    names what it sells — reaching that through the real route rather than an
    UPDATE keeps them honest about how a merchant would get there.
    """
    return gateway.bind_product(
        connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE,
        actor_user_id=OWNER_ID, canonical_product_id=listing_id, pid=pid, vid=vid,
        context=CONTEXT)


def draft_of(listing_id):
    return drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)


def price_every_variant(listing_id, amount=2000):
    draft = draft_of(listing_id)
    return drafts.update_draft(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
        fields={"price_cents": {str(v["variant_id"]): amount for v in draft["variants"]}},
        context=CONTEXT)


# ---------------------------------------------------------------------------
# Reading a draft
# ---------------------------------------------------------------------------

def test_a_draft_shows_cost_retail_and_margin_per_variant(provider):
    draft = draft_of(imported(provider))
    variant = draft["variants"][0]
    assert variant["cost_cents"] == 820
    assert variant["retail_cents"] is None      # merchant has not priced it yet
    assert variant["margin_state"] == pricing.UNKNOWN


def test_a_draft_lists_its_publication_problems_all_at_once(provider):
    # Not the first failure. A merchant fixing one problem at a time and
    # re-submitting to discover the next is the experience this avoids — so the
    # draft here is broken in two independent ways and both codes must be there.
    # The supplier dropping out while the merchant is still pricing is the
    # ordinary way two problems coexist.
    listing_id = imported(provider)
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_product_sources SET sync_state=? WHERE listing_id=?",
                     (supplier_schema.SYNC_DISCONNECTED, listing_id))
        conn.commit()
    finally:
        conn.close()
    validation = draft_of(listing_id)["validation"]
    assert validation["publishable"] is False
    assert drafts.MISSING_PRICE in validation["problems"]
    assert drafts.SUPPLIER_DISCONNECTED in validation["problems"]
    assert len(validation["problems"]) >= 2


def test_only_supplier_products_are_visible_as_drafts(provider):
    # list_drafts joins from marketplace_product_sources, so a manually authored
    # listing has no row and can never appear in a dropshipping view.
    listing_id = imported(provider)
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO marketplace_listings (seller_user_id, title, status, "
                    "created_at, updated_at, approval_status) "
                    "VALUES (?, 'Hand written listing', 'draft', '2026-01-01', "
                    "'2026-01-01', 'pending_review')", (int(OWNER_ID),))
        conn.commit()
    finally:
        conn.close()
    listed = drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    ids = [d["id"] for d in listed["items"]]
    assert ids == [listing_id]


def test_the_imported_count_is_a_total_not_the_size_of_the_page(provider):
    """`count` has to measure the table, not restate the limit it was given.

    It used to be len(rows) computed after the LIMIT, so it could only ever
    equal the page size. The hub tile asks for limit=1 deliberately -- it wants
    the number without paying to transfer the rows -- and therefore read its own
    limit back and told every merchant they had exactly "1 imported" product,
    no matter how many they had. That was visible on a real device: the store
    showed two CJ drafts while the hub beside it said "Products · 1 imported".

    Nothing asserted the field before now, which is how a number that was
    structurally incapable of being right survived a green suite.
    """
    first = imported(provider, pid="PID-1")
    second = imported(provider, pid="PID-2")
    third = imported(provider, pid="PID-3")

    one_page = drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                  context=CONTEXT, limit=1)
    assert len(one_page["items"]) == 1, "limit must still bound the rows returned"
    assert one_page["count"] == 3, "count must survive the limit, not be set by it"

    everything = drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                    context=CONTEXT, limit=50)
    assert [d["id"] for d in everything["items"]] == [third, second, first]
    assert everything["count"] == 3


def test_a_status_filter_searches_every_import_not_only_the_newest_page(provider):
    """The filter has to run in the query, beside the LIMIT it must survive.

    Applied afterwards in Python it filtered the page rather than the table, so
    a match that fell outside the newest `limit` rows was reported as absent --
    a merchant with more products than fit on one page could open a filter and
    be told, wrongly and silently, that they had nothing there.
    """
    oldest = imported(provider, pid="PID-1")
    imported(provider, pid="PID-2")
    imported(provider, pid="PID-3")
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_listings SET status='published' WHERE id=?",
                     (oldest,))
        conn.commit()
    finally:
        conn.close()

    # The one match is the OLDEST row, so it is not in a newest-first page of 1.
    found = drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION,
                               context=CONTEXT, status="published", limit=1)
    assert [d["id"] for d in found["items"]] == [oldest]
    assert found["count"] == 1

    # And the filter still excludes what does not match.
    assert drafts.list_drafts(BUSINESS, STORE, OWNER_ID, CONNECTION,
                              context=CONTEXT, status="archived")["count"] == 0


def test_another_merchant_cannot_read_the_draft(provider):
    listing_id = imported(provider)
    with pytest.raises(Exception) as exc:
        drafts.get_draft(OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID, OTHER_CONNECTION,
                         listing_id, context=CONTEXT)
    assert getattr(exc.value, "http_status", None) in (403, 404)


# ---------------------------------------------------------------------------
# Merchant edits and field ownership
# ---------------------------------------------------------------------------

def test_a_merchant_edit_is_recorded_as_an_ownership_transfer(provider):
    listing_id = imported(provider)
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"title": "Premium Cotton Tee"}, context=CONTEXT)
    assert rows("SELECT title FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["title"] == "Premium Cotton Tee"

    conn = db.connect()
    try:
        source = variants.source_for(conn.cursor(), listing_id)
    finally:
        conn.close()
    overridden = source.get("overridden_fields")
    if isinstance(overridden, str):
        overridden = json.loads(overridden or "[]")
    assert "title" in (overridden or []), \
        "the edit landed but was not recorded; a sync will revert it"


def test_a_provider_sync_cannot_overwrite_an_overridden_field(provider):
    listing_id = imported(provider)
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"title": "Premium Cotton Tee"}, context=CONTEXT)
    conn = db.connect()
    try:
        source = variants.source_for(conn.cursor(), listing_id)
    finally:
        conn.close()
    allowed = variants.sync_updates_allowed(
        source, {"title": "Supplier Renamed This", "cost_cents": 900})
    assert "title" not in allowed, "sync would revert the merchant's own words"
    # The supplier still owns cost; stripping that too would freeze economics.
    assert "cost_cents" in allowed


def test_editing_the_retail_price_leaves_supplier_cost_untouched(provider):
    listing_id = imported(provider)
    before = {v["variant_id"]: v["cost_cents"] for v in draft_of(listing_id)["variants"]}
    price_every_variant(listing_id, 2500)
    after = {v["variant_id"]: v["cost_cents"] for v in draft_of(listing_id)["variants"]}
    assert after == before
    # Pinned, so that "unchanged" cannot be satisfied by both sides being None —
    # which is exactly what a price edit that blanks cost would produce.
    assert sorted(after.values()) == [820, 860]


def test_a_priced_variant_reports_a_real_margin(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    variant = draft_of(listing_id)["variants"][0]
    assert variant["retail_cents"] == 2000
    assert variant["margin_cents"] == 1180
    assert variant["margin_state"] == pricing.HEALTHY


def test_a_price_below_cost_is_reported_as_negative_not_hidden(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 500)
    assert draft_of(listing_id)["variants"][0]["margin_state"] == pricing.NEGATIVE_MARGIN


@pytest.mark.parametrize("fields", [
    {"cost_cents": 1},
    {"supplier_cost_cents": 1},
    {"stock_quantity": 999},
    {"provider_variant_id": "spoofed"},
    {"status": "published"},
    {"approval_status": "approved"},
    {"seller_user_id": 1},
    {"quantity": 500},
])
def test_supplier_owned_and_lifecycle_fields_are_not_editable(provider, fields):
    # An allowlist, so a new column is not editable by default. The publish and
    # approval fields matter most: an editable `status` is a publish bypass.
    listing_id = imported(provider)
    with pytest.raises(SupplierError) as exc:
        drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            fields=fields, context=CONTEXT)
    assert exc.value.code == "unsupported_field"


def test_media_supplied_by_the_merchant_is_revalidated(provider):
    # The merchant is reordering a list we gave them, but the request is still a
    # client request and could carry a URL we never issued.
    listing_id = imported(provider)
    with pytest.raises(SupplierError):
        drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            fields={"media": ["https://169.254.169.254/x.jpg"]},
                            context=CONTEXT)


def test_media_can_be_reordered(provider):
    listing_id = imported(provider)
    reordered = ["https://cdn.example.com/PID-1-2.jpg", "https://cdn.example.com/PID-1.jpg"]
    result = drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                                 fields={"media": reordered}, context=CONTEXT)
    assert result["media"] == reordered
    assert result["cover_image_url"] == reordered[0]


def test_an_empty_title_is_refused_rather_than_blanking_the_listing(provider):
    listing_id = imported(provider)
    for value in ["", "   ", "<b></b>"]:
        with pytest.raises(SupplierError):
            drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                                fields={"title": value}, context=CONTEXT)


def test_a_non_supplier_listing_cannot_be_edited_through_this_path(provider):
    imported(provider)
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO marketplace_listings (seller_user_id, title, status, "
                    "created_at, updated_at, approval_status) VALUES "
                    "(?, 'Manual', 'draft', '2026-01-01', '2026-01-01', 'pending_review')",
                    (int(OWNER_ID),))
        cur.execute("SELECT id FROM marketplace_listings WHERE title='Manual'")
        manual_id = dict(cur.fetchone())["id"]
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SupplierError) as exc:
        drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, manual_id,
                            fields={"title": "x"}, context=CONTEXT)
    assert exc.value.code == "not_a_supplier_product"


# ---------------------------------------------------------------------------
# The publish gate
# ---------------------------------------------------------------------------

def test_an_unpriced_draft_cannot_be_published(provider):
    listing_id = imported(provider)
    with pytest.raises(SupplierError) as exc:
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert exc.value.http_status == 422
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"


def test_a_negative_margin_blocks_publication(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 500)
    assert drafts.NEGATIVE_MARGIN in drafts.validate(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)["problems"]
    with pytest.raises(SupplierError):
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)


def test_a_disconnected_supplier_blocks_publication(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_product_sources SET sync_state=? WHERE listing_id=?",
                     (supplier_schema.SYNC_DISCONNECTED, listing_id))
        conn.commit()
    finally:
        conn.close()
    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.SUPPLIER_DISCONNECTED in problems


def test_validate_changes_nothing(provider):
    listing_id = imported(provider)
    before = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))
    drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,)) == before


def test_a_valid_draft_publishes(provider):
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["status"] == "published"
    listing = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]
    assert listing["status"] == "published"
    assert listing["published_at"]


def test_publishing_does_not_self_approve_moderation(provider):
    # is_public() requires status AND approval. A product that approved itself
    # skipped review, and nothing downstream would notice.
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["awaiting_moderation"] is True
    listing = rows("SELECT approval_status FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]
    assert listing["approval_status"] == "pending_review"


def test_a_published_but_unapproved_listing_is_not_publicly_visible(provider):
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    listing = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]
    assert lifecycle.is_public(listing) is False


def test_published_quantity_is_units_of_the_bound_variant_not_a_count_of_variants(provider):
    # `marketplace_listings.quantity` is a unit ledger: the cart decrements it per
    # unit reserved and `lifecycle.inventory_available` answers "may this buyer
    # take N" by comparing N against it. Publish used to seed it with
    # `sum(1 for v in rows if availability(v) == AVAILABLE)` -- a count of
    # *variants* -- and return the same integer as `sellable_variants`, which is
    # what it honestly is. One value, two meanings, one line apart.
    #
    # The supplier holds 40 units of V1. The buyer's shelf must offer 40, not 1.
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    quantity = rows("SELECT quantity FROM marketplace_listings WHERE id=?",
                    (listing_id,))[0]["quantity"]
    assert quantity == 40, "the shelf must carry units, not a count of variants"
    assert result["sellable_variants"] == 1, "and the merchant's count stays a count"

    # Measured where it lands, not where it is written: the buyer surface.
    listing = dict(rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0])
    listing["listing_type"] = listing["product_type"] = "physical"
    assert lifecycle.inventory_available(listing, 40) is True
    assert lifecycle.inventory_available(listing, 41) is False


def test_an_unbound_multi_variant_listing_cannot_be_published(provider):
    # Two variants imported, nothing bound. `create_intent` resolves every line
    # through `gateway.get_product_binding`, which raises `product_binding_required`
    # when `provider_variant_id` is NULL -- so this listing is one a buyer could
    # pay for and nobody could ship. Production listing 14 is exactly this state:
    # published, moderator-approved, on sale, unbound.
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.SUPPLIER_VARIANT_UNBOUND in problems
    with pytest.raises(SupplierError):
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"


def test_binding_a_variant_is_what_makes_the_unbound_listing_publishable(provider):
    # A guard is only finished when something can satisfy it. This is that
    # something, through the route a merchant would use: `bind-product`.
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    assert drafts.SUPPLIER_VARIANT_UNBOUND in drafts.validate(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)["problems"]

    bind(listing_id, "PID-1", "PID-1-V2")

    assert drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                           context=CONTEXT) == {"publishable": True, "problems": []}
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["status"] == "published"
    # V2, the bound one, holds 12 units. V1's 40 belong to a variant this listing
    # does not sell and must not appear on the shelf.
    assert rows("SELECT quantity FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["quantity"] == 12


def test_in_stock_with_no_count_offers_exactly_one_unit(provider):
    # `variants.availability` deliberately trusts a provider that declares stock
    # without a number -- demanding a count would make every such variant
    # permanently unbuyable. The shelf still has to name a number, and any number
    # above one would be one nobody told us.
    listing_id = sellable(provider, pid="PID-3", variants_=[
        {"vid": "PID-3-V1", "variantKey": "Black-S", "variantSellPrice": "8.20",
         "stockStatus": "IN_STOCK"}])
    assert draft_of(listing_id)["variants"][0]["stock_quantity"] is None
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert rows("SELECT quantity FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["quantity"] == 1


def test_a_binding_that_names_a_variant_the_listing_no_longer_has_is_unbound(provider):
    # Drift, not absence -- but the same problem. A supplier sync that dropped
    # V1 leaves `provider_variant_id` pointing at nothing, and an order would be
    # placed for a variant CJ no longer has. "Nearly bound" must not read as bound.
    listing_id = imported(provider)
    bind(listing_id, "PID-1", "PID-1-V2")
    price_every_variant(listing_id, 2000)
    conn = db.connect()
    try:
        conn.execute(f"DELETE FROM {variants.VARIANT_TABLE} WHERE listing_id=? "
                     "AND provider_variant_id=?", (listing_id, "PID-1-V2"))
        conn.commit()
    finally:
        conn.close()

    assert drafts.SUPPLIER_VARIANT_UNBOUND in drafts.validate(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)["problems"]


def test_an_available_sibling_cannot_answer_for_an_unknown_bound_variant(provider):
    # `UNKNOWN_INVENTORY` used to ask the whole variant set, because nothing
    # identified which variant the buyer would receive and one known-available
    # variant was the best evidence available. Bound, the buyer receives V1 or
    # nothing, so V1's 40-unit sibling says nothing about whether V1 can ship.
    listing_id = imported(provider, pid="PID-4", variants_=[
        {"vid": "PID-4-V1", "variantKey": "Black-S", "variantSellPrice": "8.20"},
        {"vid": "PID-4-V2", "variantKey": "Black-M", "variantSellPrice": "8.20",
         "variantQuantity": 40},
    ])
    bind(listing_id, "PID-4", "PID-4-V1")
    price_every_variant(listing_id, 2000)

    draft = draft_of(listing_id)
    assert [v["availability"] for v in draft["variants"]] == ["UNKNOWN", "AVAILABLE"]
    assert drafts.UNKNOWN_INVENTORY in draft["validation"]["problems"]


def test_a_stocked_source_needs_no_supplier_binding():
    # The merchant holds this inventory and places no supplier order, so there is
    # nothing to bind and nothing `create_intent` would refuse. Demanding a
    # binding here would make a whole fulfillment mode unpublishable to satisfy a
    # guard that protects the other one.
    listing = {"title": "Hand-thrown mug", "category": "Home", "approval_status": "approved"}
    priced = [{"provider_variant_id": "V1", "stock_quantity": 3, "retail_cents": 2000,
               "availability": variants.AVAILABLE, "margin_state": pricing.HEALTHY}]
    stocked = {"fulfillment_mode": supplier_schema.MODE_STOCKED,
               "sync_state": supplier_schema.SYNC_SYNCED}
    assert drafts._validate(listing, priced, stocked, ["https://cdn.example.com/a.jpg"]) \
        == {"publishable": True, "problems": []}

    dropship = dict(stocked, fulfillment_mode=supplier_schema.MODE_DROPSHIP)
    assert drafts._validate(listing, priced, dropship,
                            ["https://cdn.example.com/a.jpg"])["problems"] \
        == [drafts.SUPPLIER_VARIANT_UNBOUND]


def test_an_all_unknown_inventory_draft_is_blocked(provider):
    listing_id = imported(provider, pid="PID-2", variants_=[
        {"vid": "PID-2-V1", "variantKey": "Black-S", "variantSellPrice": "8.20"}])
    price_every_variant(listing_id, 2000)
    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.UNKNOWN_INVENTORY in problems


# ---------------------------------------------------------------------------
# The price seam: merchant world -> buyer world
# ---------------------------------------------------------------------------
#
# The merchant prices `marketplace_listing_variants.price_cents`. Every buyer
# surface -- cart add, confirm-price, offers -- prices from
# `marketplace_listings.price_label` via `bot.parse_price_label_to_cents`.
# `marketplace_variants` is imported by the supplier package and by nothing else,
# so the buyer has no variant selector and no way to reach the merchant's number.
#
# Nothing joined the two. A real CJ product, imported, priced at $20.00 a
# variant, published and moderator-approved, measured as: is_public() True,
# public_denial_code() "", price_label '', and a cart that charged 0 and
# therefore refused the add with "This item is not priced for checkout." The
# suite above stops one column short of that -- it asserts status and
# published_at and never the price -- which is how it stayed green.

def test_publishing_writes_the_price_the_buyer_path_reads(provider):
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    listing = rows("SELECT price_label, currency FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]
    assert listing["price_label"], "published with no price for the buyer to see"
    assert listing["price_label"] == result["price_label"]


def test_what_publish_leaves_behind_is_a_state_moderation_will_act_on(provider):
    """The handoff, asserted from this side of it.

    ``publish`` returns ``awaiting_moderation: True``, and for a long time that
    was a promise the other authority did not keep: ``/admin/marketplace-command``
    read "already decided" off ``status``, saw ``published``, and 409'd. The
    state was terminal in both directions -- no moderator could decide it and
    nothing here moves a published listing back to ``pending_review`` -- so every
    CJ listing that reached this line needed a hand-written UPDATE to go live.

    ``tests/marketplace/test_marketplace_moderation_reachability.py`` asserts the
    route accepts that shape, but it *seeds* the shape as a literal. This test is
    the other end: it asserts the row ``publish`` really leaves satisfies the
    predicate that route is gated on. Without it the two halves agree only
    because the same two strings were typed into both files -- which is the
    failure this suite keeps finding, one component describing another rather
    than measuring it.
    """
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            context=CONTEXT)
    assert result["awaiting_moderation"] is True

    listing = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]
    assert lifecycle.awaiting_moderation(listing) is True, (
        "publish left status=%r/approval_status=%r, which the only moderation "
        "surface refuses to act on. The listing is now unreachable: not public "
        "(is_public needs approval), and not approvable."
        % (listing["status"], listing["approval_status"]))
    # And it is genuinely not yet public -- otherwise "awaiting moderation" would
    # be describing a listing buyers can already see.
    assert lifecycle.is_public(dict(listing, seller_status="approved",
                                    display_name="M&W Store")) is False


def test_publishing_writes_the_cover_the_buyer_path_reads(provider):
    """The media half of the same seam the price test above guards.

    Publication validates ``listing_metadata_json.media`` -- that is the list
    ``_media_of`` returns and the only one ``_validate`` sees. Every buyer
    surface renders the *column*: ``pulse_marketplace_listing_payload`` assembles
    its media from ``marketplace_product_media``, ``cover_image_url``,
    ``media_url`` and ``gallery_json``, and never looks at the metadata list. Two
    representations of one fact, and until publish joined them a draft could
    satisfy the media gate and still ship a card with no picture.

    The blanking below is not a hypothetical. It reproduces the shape of
    production listing 14, a real CJ import whose metadata carries five
    ``cf.cjdropshipping.com`` URLs while the column is NULL, because it was
    written before ``importer._insert_listing`` began setting the column.
    """
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)

    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_listings SET cover_image_url=NULL WHERE id=?",
                     (listing_id,))
        conn.commit()
    finally:
        conn.close()

    # The pre-fix state is genuinely reached: the draft still validates as having
    # media, so nothing on the merchant's side would report a problem.
    draft = draft_of(listing_id)
    assert draft["media"], "fixture no longer carries metadata media"
    assert draft["validation"]["publishable"] is True
    assert drafts.NO_VALID_MEDIA not in draft["validation"]["problems"]

    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    listing = rows("SELECT cover_image_url FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]
    assert listing["cover_image_url"], "published with no cover for the buyer to see"
    # Not merely non-empty: the same picture the merchant was shown as the cover.
    assert listing["cover_image_url"] == draft["cover_image_url"] == draft["media"][0]


def test_a_published_listing_leaves_the_two_media_stores_agreeing(provider):
    # The column and the metadata are two spellings of one fact, written by three
    # functions now (import, edit, publish). A test that only checked the column
    # was populated would pass if publish wrote some other listing's picture.
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    listing = rows("SELECT cover_image_url, listing_metadata_json FROM marketplace_listings "
                   "WHERE id=?", (listing_id,))[0]
    metadata_media = json.loads(listing["listing_metadata_json"])["media"]
    assert metadata_media, "fixture no longer carries metadata media"
    assert listing["cover_image_url"] == metadata_media[0]


def test_publishing_does_not_overwrite_a_cover_the_merchant_reordered(provider):
    # `update_draft` writes both stores, so a reorder moves the cover. Publish
    # must land on the merchant's current first choice, not the import's.
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    original = draft_of(listing_id)["media"]
    assert len(original) > 1, "fixture needs more than one image to reorder"

    reordered = list(reversed(original))
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"media": reordered}, context=CONTEXT)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    listing = rows("SELECT cover_image_url FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]
    assert listing["cover_image_url"] == reordered[0] != original[0]


def test_the_published_label_charges_exactly_what_the_merchant_set(provider):
    """The contract between this package and the monolith's checkout parser.

    Two independent claims, because agreeing on a *format* and agreeing on an
    *amount* are different failures. A label we render as "$2,000.00" for 2000
    cents is well-formed and charges a hundred times too much; a label the parser
    clamps is well-formed and charges too little. Only the parser's own opinion
    of our string settles either, so this test imports the real one.
    """
    import bot

    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    listing = rows("SELECT price_label, currency FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]

    charged, currency = bot.parse_price_label_to_cents(
        listing["price_label"], listing["currency"] or "USD")
    assert charged == 2000, "the buyer would be charged %s, not the 2000 set" % charged
    assert currency == "USD"

    # And the label is the monolith's own spelling, so a later change to its
    # format is a failure here rather than a silent divergence in the database.
    expected, _, _, error = bot.marketplace_normalize_price_label("20.00", "USD")
    assert not error and listing["price_label"] == expected

    # The ceiling mirrored in this package must be the ceiling the parser
    # actually enforces -- the parser *clamps* to it rather than refusing, so a
    # stale copy here would publish a price that is quietly reduced at checkout.
    assert drafts.MAX_CHECKOUT_PRICE_CENTS == bot.MAX_PRICE_LABEL_CENTS


def test_a_published_approved_import_is_purchasable_on_every_field_a_buyer_reads(provider):
    """The whole crossing, asserted once, positively.

    Every half of this is already covered above and each half was green while
    the seam was broken — that is the entire history of this file. `publish`
    wrote `status` while `price_label` stayed empty; later it wrote the price
    while `cover_image_url` stayed NULL. Both were found on production, not
    here, because no test asked the one question a buyer asks: *can I buy this
    thing, and is everything on the card real?*

    So this one does not test `publish`. It tests the row `publish` leaves
    behind, through the functions the buyer's own path calls — `is_public`,
    `public_denial_code`, `parse_price_label_to_cents`, and the serializer every
    marketplace read goes through. A future field that publication forgets to
    join fails here even if nobody thinks to write a test for that field.

    The seller state mirrors production: approved, with a store name. Moderation
    is applied as the separate authority it is — a direct write to
    `approval_status`, not anything this package can do to itself.

    What this test is *not*: evidence about which writer filled a given field.
    Measured — deleting `cover_image_url` from publish's UPDATE leaves this test
    green, because `importer._insert_listing` also writes that column and the
    normal path runs both. That is the correct division: this asserts the end
    state a buyer meets, and `test_publishing_writes_the_cover_the_buyer_path_reads`
    blanks the column first so publish is the only thing that can fill it. Read
    a failure here as "the card is wrong", not as "publish is wrong", and check
    the dedicated tests for which writer dropped it.
    """
    import bot

    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_listings SET approval_status='approved' WHERE id=?",
                     (listing_id,))
        conn.commit()
    finally:
        conn.close()

    listing = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]
    # The buyer's discovery query joins the seller for exactly these two columns;
    # `public_sql` is the binding form and this is its in-Python twin.
    listing["seller_status"] = "approved"
    listing["display_name"] = "M&W Store"

    assert lifecycle.is_public(listing) is True
    assert lifecycle.public_denial_code(listing) == ""

    charged, currency = bot.parse_price_label_to_cents(
        listing["price_label"], listing["currency"] or "USD")
    assert charged == 2000, "add-to-cart would price this at %s" % charged
    assert currency == "USD"

    payload = bot.pulse_marketplace_listing_payload(listing)
    assert payload["buyer_visible"] is True
    assert payload["inventory_state"] == "available"
    assert payload["price_label"] == listing["price_label"]
    # The card has a picture. Asserting the column alone would pass on a row the
    # serializer then drops, so the claim is made where the client reads it.
    assert payload["cover_image_url"], "the buyer's card has no image"
    assert payload["media"], "the buyer's gallery is empty"
    assert payload["cover_image_url"] == draft_of(listing_id)["media"][0]
    assert payload["seller_store_name"] == "M&W Store"

    # Supplier economics do not cross. Cost is merchant-private and this is the
    # payload every buyer surface receives.
    assert "cost_cents" not in payload
    assert "supplier_cost_cents" not in payload
    # Named outright as well as looped, because the loop reads the same constant
    # the filter does: shrinking that tuple would make the loop assert less
    # without failing. `safety_score` in particular holds the reviewer's *risk*
    # number despite its name -- 0 clean, 100 worst -- and three buyer surfaces
    # once printed it as "Safety N".
    assert "safety_score" not in payload
    assert "moderation_reason" not in payload
    for reserved in bot.MARKETPLACE_REVIEWER_ONLY_FIELDS:
        assert reserved not in payload, "%s reached the buyer" % reserved


def test_an_unpriced_variant_is_not_sold_at_another_variants_price(provider):
    # The buyer cannot choose a variant, so publishing this would have sold the
    # blank one for whatever the priced one cost. The old gate asked only whether
    # *something* was priced, and let it through.
    listing_id = imported(provider)
    draft = draft_of(listing_id)
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"price_cents": {str(draft["variants"][0]["variant_id"]): 2000}},
                        context=CONTEXT)

    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.MISSING_PRICE in problems
    with pytest.raises(SupplierError):
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)


def test_variants_at_different_prices_cannot_be_published_as_one_price(provider):
    # $20.00 and $35.00 with one listing-level price is a choice between
    # overcharging and undercharging. Neither is ours to make silently.
    listing_id = imported(provider)
    draft = draft_of(listing_id)
    first, second = (str(v["variant_id"]) for v in draft["variants"])
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"price_cents": {first: 2000, second: 3500}}, context=CONTEXT)

    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.VARIANT_PRICE_SPREAD in problems
    with pytest.raises(SupplierError):
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert rows("SELECT price_label FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["price_label"] == ""


def test_a_price_the_checkout_would_clamp_is_refused(provider):
    # `_set_prices` accepts up to pricing.MAX_PRICE_CENTS ($10,000,000) and
    # `parse_price_label_to_cents` ends in min(cents, MAX_PRICE_LABEL_CENTS).
    # Between the two limits the buyer is charged $999,999.99 for a product
    # priced far higher, and nothing anywhere says so.
    listing_id = imported(provider)
    price_every_variant(listing_id, drafts.MAX_CHECKOUT_PRICE_CENTS + 1)
    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.PRICE_ABOVE_CHECKOUT_LIMIT in problems
    with pytest.raises(SupplierError):
        drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)


def test_a_sibling_variant_at_another_price_cannot_move_the_bound_price(provider):
    # `VARIANT_PRICE_SPREAD` exists because nothing identified which variant the
    # buyer would receive, so every price in the set was one we might have to
    # honour. Bound, the question is answerable: the buyer receives V1 or nothing.
    # V2 at $35.00 is then a catalogue fact about a variant this listing does not
    # sell, and it must not take the product off sale or change what is charged.
    listing_id = imported(provider)
    bind(listing_id, "PID-1", "PID-1-V1")
    draft = draft_of(listing_id)
    first, second = (str(v["variant_id"]) for v in draft["variants"])
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"price_cents": {first: 2000, second: 3500}}, context=CONTEXT)

    assert drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                           context=CONTEXT)["problems"] == []
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["price_label"] == "$20.00"
    assert rows("SELECT quantity FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["quantity"] == 40


def test_an_unavailable_sibling_does_not_block_the_bound_variant(provider):
    # The old shape of this test: a sold-out colourway must not take the whole
    # product off sale. Still true, and now for a structural reason rather than a
    # policy one -- the sibling is not offered at all.
    listing_id = imported(provider)
    bind(listing_id, "PID-1", "PID-1-V1")
    price_every_variant(listing_id, 2000)
    second = str(draft_of(listing_id)["variants"][1]["variant_id"])
    conn = db.connect()
    try:
        conn.execute(
            f"UPDATE {variants.VARIANT_TABLE} SET status='archived' WHERE id=?", (int(second),))
        conn.commit()
    finally:
        conn.close()

    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["price_label"] == "$20.00"
    assert result["sellable_variants"] == 1
    assert rows("SELECT quantity FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["quantity"] == 40


def test_a_sold_out_product_still_carries_its_price(provider):
    # Every variant archived. The listing publishes sold out -- quantity 0 keeps
    # it out of the buyer's reach -- but it must still be priced, or it becomes a
    # priceless listing again the instant the supplier restocks, which is the
    # state this whole section exists to prevent. Without the fallback in
    # `_offered` there is no offered variant to take a price from at all, and
    # publish raises IndexError instead: a 500 on a legitimate sold-out product.
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    conn = db.connect()
    try:
        conn.execute(f"UPDATE {variants.VARIANT_TABLE} SET status='archived' "
                     "WHERE listing_id=?", (listing_id,))
        conn.commit()
    finally:
        conn.close()

    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["sellable_variants"] == 0
    assert result["price_label"] == "$20.00"
    listing = rows("SELECT quantity, price_label FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]
    assert listing["quantity"] == 0, "an archived variant must not be sold"
    assert listing["price_label"] == "$20.00"


def test_repricing_a_live_product_reaches_the_buyer(provider):
    # publish() is not the only way the price moves. A merchant raising the price
    # of a listing that is already live must not leave the cart charging the old
    # one -- the draft screen would show the new number while every buyer paid
    # the old, and the merchant would have no way to see the difference.
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert rows("SELECT price_label FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["price_label"] == "$20.00"

    price_every_variant(listing_id, 3000)
    assert rows("SELECT price_label FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["price_label"] == "$30.00"


def test_repricing_a_sibling_on_a_live_bound_product_is_not_a_spread(provider):
    # `_live_price_label` is the second price writer and it asks the same question
    # `_validate` does. Left reading the whole variant set while `_validate` reads
    # the bound one, the two disagree: the merchant corrects the price of a
    # colourway this listing does not sell and the route answers 422 for a
    # listing that is, by publication's own reckoning, perfectly chargeable.
    listing_id = imported(provider)
    bind(listing_id, "PID-1", "PID-1-V1")
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    second = str(draft_of(listing_id)["variants"][1]["variant_id"])
    drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                        fields={"price_cents": {second: 3500}}, context=CONTEXT)
    assert rows("SELECT price_label FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["price_label"] == "$20.00"


def test_repricing_a_draft_does_not_price_it_for_the_buyer(provider):
    # The mirror of the test above, and the reason it is not simply "always write
    # the label". A draft is not on sale; giving it a public price before the
    # merchant has published it would put a number on a product they are still
    # deciding about.
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    assert rows("SELECT price_label, status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0] == {"price_label": "", "status": "draft"}


def test_a_live_product_cannot_be_repriced_into_an_unchargeable_state(provider):
    listing_id = sellable(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    # Clearing the bound variant's price is the unchargeable state a bound
    # listing can still be repriced into: `parse_price_label_to_cents` reads an
    # empty label as zero, so the buyer would be charged nothing.
    first = str(draft_of(listing_id)["variants"][0]["variant_id"])
    with pytest.raises(SupplierError) as exc:
        drafts.update_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            fields={"price_cents": {first: None}},
                            context=CONTEXT)
    assert exc.value.http_status == 422
    # Refused outright rather than left on sale at a price the merchant replaced.
    assert rows("SELECT price_label FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["price_label"] == "$20.00"


def test_another_merchant_cannot_publish_this_draft(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    with pytest.raises(Exception) as exc:
        drafts.publish(OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID, OTHER_CONNECTION,
                       listing_id, context=CONTEXT)
    assert getattr(exc.value, "http_status", None) in (403, 404)
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"
