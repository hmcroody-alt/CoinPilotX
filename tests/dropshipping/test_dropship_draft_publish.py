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


def imported(provider, pid="PID-1", **product_kwargs):
    """Import one product and return its listing id."""
    provider.add(cj_product(pid, **product_kwargs))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid, context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    entry = result["results"][0]
    assert entry["outcome"] == importer.IMPORTED, entry
    return entry["listing_id"]


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
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["status"] == "published"
    listing = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]
    assert listing["status"] == "published"
    assert listing["published_at"]


def test_publishing_does_not_self_approve_moderation(provider):
    # is_public() requires status AND approval. A product that approved itself
    # skipped review, and nothing downstream would notice.
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    assert result["awaiting_moderation"] is True
    listing = rows("SELECT approval_status FROM marketplace_listings WHERE id=?",
                   (listing_id,))[0]
    assert listing["approval_status"] == "pending_review"


def test_a_published_but_unapproved_listing_is_not_publicly_visible(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    listing = rows("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))[0]
    assert lifecycle.is_public(listing) is False


def test_published_quantity_counts_confirmed_variants_not_supplier_stock(provider):
    # The supplier says 40 and 12. That is a warehouse we do not control; the
    # listing's quantity is a count of variants we can positively confirm.
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    quantity = rows("SELECT quantity FROM marketplace_listings WHERE id=?",
                    (listing_id,))[0]["quantity"]
    assert quantity == result["sellable_variants"] == 2
    assert quantity != 52


def test_an_all_unknown_inventory_draft_is_blocked(provider):
    listing_id = imported(provider, pid="PID-2", variants_=[
        {"vid": "PID-2-V1", "variantKey": "Black-S", "variantSellPrice": "8.20"}])
    price_every_variant(listing_id, 2000)
    problems = drafts.validate(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT)["problems"]
    assert drafts.UNKNOWN_INVENTORY in problems


def test_another_merchant_cannot_publish_this_draft(provider):
    listing_id = imported(provider)
    price_every_variant(listing_id, 2000)
    with pytest.raises(Exception) as exc:
        drafts.publish(OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID, OTHER_CONNECTION,
                       listing_id, context=CONTEXT)
    assert getattr(exc.value, "http_status", None) in (403, 404)
    assert rows("SELECT status FROM marketplace_listings WHERE id=?",
                (listing_id,))[0]["status"] == "draft"
