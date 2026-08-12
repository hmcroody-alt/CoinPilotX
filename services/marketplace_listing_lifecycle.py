"""Canonical publication and inventory policy for PulseSoc Marketplace listings.

Publication has a fourth condition alongside listing status, moderation state and
stock: the seller must have a public store name. A buyer has to know who they are
buying from, and "who" is the storefront — see
``services/marketplace_seller_identity``. Allowing a nameless seller to sell would
force every buyer surface to invent an identity, and the only name lying around
is the account holder's personal one. Better to hold the listing back and repair
the seller record (``scripts/marketplace_store_identity_audit.py``).
"""

from __future__ import annotations

from typing import Any, Mapping

from services import marketplace_seller_identity as seller_identity


DRAFT = "draft"
PENDING_REVIEW = "pending_review"
CHANGES_REQUESTED = "changes_requested"
APPROVED = "approved"
PUBLISHED = "published"
REJECTED = "rejected"
SUSPENDED = "suspended"
ARCHIVED = "archived"

# ``active`` is the only legacy public value retained. It is still gated by an
# explicit approved moderation state and approved seller, so review-ready rows
# cannot leak into buyer discovery.
PUBLIC_STATUSES = frozenset({PUBLISHED, "live", "active"})
APPROVED_STATES = frozenset({APPROVED})
STOCKLESS_TYPES = frozenset({"digital", "course", "service", "event", "booking"})
MATERIAL_FIELDS = frozenset({
    "title", "description", "short_description", "category", "subcategory",
    "price_label", "currency", "cover_image_url", "gallery_json", "video_url",
    "product_type", "listing_type", "listing_metadata_json",
})


def normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def inventory_available(listing: Mapping[str, Any], quantity: int = 1) -> bool:
    product_type = normalized(listing.get("product_type") or listing.get("listing_type"))
    if product_type in STOCKLESS_TYPES:
        return True
    raw = listing.get("quantity")
    if raw is None:
        return False
    try:
        return int(raw) >= max(1, int(quantity))
    except (TypeError, ValueError):
        return False


def seller_identity_missing(listing: Mapping[str, Any]) -> bool:
    """True only when the row proves the seller has no public store name.

    A row that was never projected with a store-name column proves nothing, so it
    is not treated as a failure here; the SQL predicate in :func:`public_sql` is
    where the invariant binds for discovery.
    """
    return (
        seller_identity.store_identity_known(listing)
        and not seller_identity.has_store_identity(listing)
    )


def is_public(listing: Mapping[str, Any]) -> bool:
    return (
        normalized(listing.get("status")) in PUBLIC_STATUSES
        and normalized(listing.get("approval_status")) in APPROVED_STATES
        and normalized(listing.get("seller_status")) == "approved"
        and not seller_identity_missing(listing)
        and inventory_available(listing)
    )


def public_denial_code(listing: Mapping[str, Any], quantity: int = 1) -> str:
    """Why this listing is not purchasable, as a stable client-facing code.

    Returns ``""`` when the listing *is* purchasable. Buyer clients branch on
    this rather than on prose, and the three outcomes are genuinely different
    next moves: a suspended seller is nobody's fault and nothing the buyer can
    retry, an unavailable listing may come back, and out-of-stock means lower
    the quantity or wait for a restock. Collapsing them into one "unavailable"
    message is what makes a marketplace feel broken.
    """
    if normalized(listing.get("seller_status")) != "approved" or seller_identity_missing(listing):
        # A seller with no store name is not presentable to a buyer, and the
        # buyer's next move is the same as for a suspended one: none. It is the
        # seller's record that needs repair, not the buyer's attempt.
        return "SELLER_UNAVAILABLE"
    if (
        normalized(listing.get("status")) not in PUBLIC_STATUSES
        or normalized(listing.get("approval_status")) not in APPROVED_STATES
    ):
        return "ITEM_UNAVAILABLE"
    if not inventory_available(listing, quantity):
        return "OUT_OF_STOCK"
    return ""


def public_sql(alias: str = "l", seller_alias: str = "ms") -> str:
    """SQL equivalent of :func:`is_public` for buyer discovery surfaces."""
    return (
        f"LOWER(COALESCE({alias}.status,'')) IN ('published','live','active') "
        f"AND LOWER(COALESCE({alias}.approval_status,''))='approved' "
        f"AND LOWER(COALESCE({seller_alias}.status,''))='approved' "
        # The store-name invariant. Every caller of this predicate already joins
        # the seller row for its status, so this costs no extra join.
        f"AND {seller_identity.store_name_sql(seller_alias)} IS NOT NULL "
        f"AND (LOWER(COALESCE({alias}.product_type,{alias}.listing_type,'')) "
        "IN ('digital','course','service','event','booking') "
        f"OR COALESCE({alias}.quantity,0)>0)"
    )


def requires_rereview(changed_fields: set[str]) -> bool:
    return bool(MATERIAL_FIELDS.intersection(changed_fields))


def seller_label(listing: Mapping[str, Any]) -> str:
    status = normalized(listing.get("status")) or DRAFT
    approval = normalized(listing.get("approval_status"))
    if status in PUBLIC_STATUSES and approval == APPROVED:
        return "Live"
    return {
        DRAFT: "Draft",
        "submitted": "Submitted",
        PENDING_REVIEW: "In review",
        "review_ready": "In review",
        CHANGES_REQUESTED: "Changes requested",
        APPROVED: "Approved",
        REJECTED: "Rejected",
        SUSPENDED: "Suspended",
        ARCHIVED: "Archived",
        "paused": "Paused",
    }.get(status, "In review" if approval in {"review_ready", "needs_review"} else status.replace("_", " ").title())
