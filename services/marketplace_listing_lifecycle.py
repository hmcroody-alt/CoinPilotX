"""Canonical publication and inventory policy for PulseSoc Marketplace listings."""

from __future__ import annotations

from typing import Any, Mapping


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


def is_public(listing: Mapping[str, Any]) -> bool:
    return (
        normalized(listing.get("status")) in PUBLIC_STATUSES
        and normalized(listing.get("approval_status")) in APPROVED_STATES
        and normalized(listing.get("seller_status")) == "approved"
        and inventory_available(listing)
    )


def public_sql(alias: str = "l", seller_alias: str = "ms") -> str:
    """SQL equivalent of :func:`is_public` for buyer discovery surfaces."""
    return (
        f"LOWER(COALESCE({alias}.status,'')) IN ('published','live','active') "
        f"AND LOWER(COALESCE({alias}.approval_status,''))='approved' "
        f"AND LOWER(COALESCE({seller_alias}.status,''))='approved' "
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
