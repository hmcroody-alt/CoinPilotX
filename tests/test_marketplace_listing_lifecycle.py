"""Focused publication and inventory policy tests for the legacy Marketplace."""

from services import marketplace_listing_lifecycle as lifecycle


def listing(**overrides):
    value = {
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "product_type": "physical",
        "quantity": 2,
    }
    value.update(overrides)
    return value


def test_only_approved_published_inventory_is_public():
    assert lifecycle.is_public(listing()) is True
    assert lifecycle.is_public(listing(status="draft")) is False
    assert lifecycle.is_public(listing(status="pending_review")) is False
    assert lifecycle.is_public(listing(status="review_ready")) is False
    assert lifecycle.is_public(listing(approval_status="pending_review")) is False
    assert lifecycle.is_public(listing(seller_status="suspended")) is False
    assert lifecycle.is_public(listing(quantity=0)) is False


def test_stockless_inventory_and_requested_quantity():
    assert lifecycle.is_public(listing(product_type="digital", quantity=0)) is True
    assert lifecycle.inventory_available(listing(quantity=2), 2) is True
    assert lifecycle.inventory_available(listing(quantity=2), 3) is False


def test_material_edits_require_review_but_quantity_does_not():
    assert lifecycle.requires_rereview({"title"}) is True
    assert lifecycle.requires_rereview({"price_label"}) is True
    assert lifecycle.requires_rereview({"quantity"}) is False


def test_truthful_seller_labels():
    assert lifecycle.seller_label(listing()) == "Live"
    assert lifecycle.seller_label(listing(status="pending_review", approval_status="pending_review")) == "In review"
    assert lifecycle.seller_label(listing(status="changes_requested")) == "Changes requested"
