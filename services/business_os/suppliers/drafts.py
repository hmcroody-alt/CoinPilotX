"""The merchant's half: review an imported draft, edit it, price it, publish it.

Field ownership is the whole design
-----------------------------------
Two parties write to an imported product and they never write the same columns:

* **The merchant owns the storefront.** ``title``, ``description``, ``category``,
  media order, retail price, visibility. These are on ``marketplace_listings``
  and ``marketplace_listing_variants.price_cents``.
* **The supplier owns upstream facts.** Cost, inventory, provider identity,
  availability. These are on ``marketplace_product_sources`` and
  ``marketplace_listing_variants.cost_cents``.

:func:`update_draft` records every merchant edit through
``marketplace_variants.mark_overridden``, which is what a later provider sync
reads to decide it may not touch that field. Without that call an import that
syncs overnight silently reverts the merchant's own title to the supplier's
keyword-stuffed one, and the merchant discovers it from a customer.

Publishing is explicit and validated
------------------------------------
:func:`publish` is the only function in the dropshipping pipeline that changes
``status`` away from ``draft``, and it refuses with a specific, listed reason
rather than a generic failure. It also does *not* set ``approval_status``:
moderation is a separate authority, and a publish path that approved its own
listing would be a merchant self-certifying past review.
"""

from __future__ import annotations

import json
import time

from services import db, marketplace_variants as variants
from services import marketplace_supplier_schema as supplier_schema
from services.business_os.suppliers import connections, normalize, policy, pricing
from services.business_os.suppliers.errors import SupplierError

# Publication validation codes. Every one names a specific thing the merchant
# can act on; there is deliberately no generic catch-all in this list.
MISSING_TITLE = "MISSING_TITLE"
MISSING_CATEGORY = "MISSING_CATEGORY"
NO_VALID_MEDIA = "NO_VALID_MEDIA"
NO_VARIANTS_SELECTED = "NO_VARIANTS_SELECTED"
MISSING_PRICE = "MISSING_PRICE"
UNKNOWN_INVENTORY = "UNKNOWN_INVENTORY"
NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
SUPPLIER_DISCONNECTED = "SUPPLIER_DISCONNECTED"
PROVIDER_PRODUCT_UNAVAILABLE = "PROVIDER_PRODUCT_UNAVAILABLE"
RESTRICTED_PRODUCT = "RESTRICTED_PRODUCT"

#: Merchant-editable storefront fields. Anything not in this set cannot be
#: written through this module — an allowlist, so that adding a column to
#: ``marketplace_listings`` never silently becomes merchant-writable.
EDITABLE = frozenset({"title", "description", "category", "media", "price_cents", "currency"})

MAX_TITLE = 160
MAX_DESCRIPTION = 8000


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _scope(conn, business_id, store_id, actor_user_id, connection_id, *, context=None, write=False):
    merchant_id = connections._authorize(conn, business_id, store_id, actor_user_id,
                                         context=context, write=write)
    connections._row(conn, connection_id, business_id, store_id, merchant_id)
    try:
        return merchant_id, int(str(merchant_id).strip())
    except (TypeError, ValueError):
        raise SupplierError("merchant_identity_unresolved", http_status=409) from None


def _owned_listing(cur, listing_id, seller_user_id):
    """The listing row, if this seller owns it. Identical 404 for every miss.

    An absent listing and someone else's listing return the same error on
    purpose: distinguishing them turns this endpoint into an oracle for which
    listing ids exist.
    """
    try:
        listing_id = variants.coerce_listing_id(listing_id)
    except variants.VariantRejected:
        raise SupplierError("not_found", http_status=404) from None
    cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1", (listing_id,))
    row = cur.fetchone()
    if row is None:
        raise SupplierError("not_found", http_status=404)
    row = dict(row)
    if row.get("seller_user_id") is None or int(row["seller_user_id"]) != int(seller_user_id):
        raise SupplierError("not_found", http_status=404)
    return listing_id, row


def _media_of(listing):
    try:
        meta = json.loads(listing.get("listing_metadata_json") or "{}")
    except (ValueError, TypeError):
        return []
    media = meta.get("media") if isinstance(meta, dict) else None
    return [m for m in (media or []) if isinstance(m, str)]


def _retail_of(variant):
    return variant.get("price_cents")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_draft(business_id, store_id, actor_user_id, connection_id, listing_id, *,
              context=None, pricing_rule=None):
    """Everything the Review Product screen needs, with truthful economics.

    Supplier cost appears here because this is a merchant-authenticated,
    merchant-scoped route. It must never appear on a buyer route; the separation
    is enforced by which module a route calls, not by a flag on the payload.
    """
    policy.require_enabled()
    rule = pricing.normalize_rule(pricing_rule)
    conn = db.connect()
    try:
        _, seller_user_id = _scope(conn, business_id, store_id, actor_user_id,
                                   connection_id, context=context)
        cur = conn.cursor()
        listing_id, listing = _owned_listing(cur, listing_id, seller_user_id)
        source = variants.source_for(cur, listing_id)
        if source is None:
            # Not a dropshipped product. This module has nothing to say about a
            # manually authored listing and must not pretend otherwise.
            raise SupplierError("not_a_supplier_product", http_status=404)
        rows = variants.variants_for(cur, listing_id)
    finally:
        conn.close()

    priced = []
    for variant in rows:
        economics = pricing.quote(rule, variant.get("cost_cents"), _retail_of(variant))
        priced.append({
            "variant_id": variant.get("id"),
            "options": variant.get("options"),
            "sku": variant.get("sku"),
            "provider_variant_id": variant.get("provider_variant_id"),
            "stock_state": variant.get("stock_state"),
            "stock_quantity": variant.get("stock_quantity"),
            "availability": variants.availability(variant),
            "currency": variant.get("currency"),
            **economics,
        })

    media = _media_of(listing)
    return {
        "listing_id": listing_id,
        "status": listing.get("status"),
        "approval_status": listing.get("approval_status"),
        "published": str(listing.get("status") or "").lower() == "published",
        "title": listing.get("title"),
        "description": listing.get("description"),
        "category": listing.get("category"),
        "currency": listing.get("currency"),
        "media": media,
        "cover_image_url": media[0] if media else None,
        "variants": priced,
        "supplier": {
            "provider": source.get("provider"),
            "fulfillment_mode": source.get("fulfillment_mode"),
            "sync_state": source.get("sync_state"),
            "last_synced_at": source.get("last_synced_at"),
            "supplier_cost_cents": source.get("supplier_cost_cents"),
            "supplier_cost_currency": source.get("supplier_cost_currency"),
            "external_sku": source.get("external_sku"),
            "merchant_owned_fields": source.get("overridden_fields") or [],
        },
        "pricing_rule": rule,
        "validation": _validate(listing, priced, source, media),
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def update_draft(business_id, store_id, actor_user_id, connection_id, listing_id, *,
                 fields, context=None):
    """Apply merchant storefront edits and record the field ownership transfer.

    Every field written here is added to the source row's ``overridden_fields``.
    That list is what ``marketplace_variants.sync_updates_allowed`` consults to
    strip merchant-owned keys out of a provider sync payload, so the recording
    is not bookkeeping — it is the mechanism.
    """
    policy.require_enabled()
    if not isinstance(fields, dict) or not fields:
        raise SupplierError("invalid_input", http_status=400)
    unknown = set(fields) - EDITABLE
    if unknown:
        raise SupplierError("unsupported_field", http_status=400)

    updates, touched = {}, []
    if "title" in fields:
        title = normalize.clean_text(fields["title"], MAX_TITLE)
        if not title:
            raise SupplierError("invalid_title", http_status=400)
        updates["title"] = title
        touched.append("title")
    if "description" in fields:
        updates["description"] = normalize.clean_text(fields["description"], MAX_DESCRIPTION)
        touched.append("description")
    if "category" in fields:
        category = normalize.clean_text(fields["category"], 120)
        if not category:
            raise SupplierError("invalid_category", http_status=400)
        updates["category"] = category
        touched.append("category")
    if "currency" in fields:
        code = normalize.currency(fields["currency"])
        if code is None:
            raise SupplierError("invalid_currency", http_status=400)
        updates["currency"] = code
        touched.append("currency")

    media = None
    if "media" in fields:
        raw = fields["media"]
        if not isinstance(raw, (list, tuple)):
            raise SupplierError("invalid_media", http_status=400)
        # Re-validated, not trusted. The merchant is reordering a list we gave
        # them, but the request is still a client request and could carry a URL
        # we never issued.
        media = normalize.media_list(raw)
        if not media:
            raise SupplierError("invalid_media", http_status=400)
        touched.append("media")

    conn = db.connect()
    try:
        _, seller_user_id = _scope(conn, business_id, store_id, actor_user_id,
                                   connection_id, context=context, write=True)
        cur = conn.cursor()
        listing_id, listing = _owned_listing(cur, listing_id, seller_user_id)
        if variants.source_for(cur, listing_id) is None:
            raise SupplierError("not_a_supplier_product", http_status=404)

        if media is not None:
            try:
                meta = json.loads(listing.get("listing_metadata_json") or "{}")
            except (ValueError, TypeError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            meta["media"] = media
            updates["listing_metadata_json"] = json.dumps(meta, separators=(",", ":"))
            updates["cover_image_url"] = media[0]

        if "price_cents" in fields:
            _set_prices(cur, listing_id, seller_user_id, fields["price_cents"])
            touched.append("price_label")

        if updates:
            updates["updated_at"] = _iso()
            assignments = ", ".join(f"{column}=?" for column in updates)
            cur.execute(
                f"UPDATE marketplace_listings SET {assignments} WHERE id=? AND seller_user_id=?",
                (*updates.values(), listing_id, int(seller_user_id)))

        if touched:
            variants.mark_overridden(cur, listing_id=listing_id,
                                     seller_user_id=seller_user_id, fields=touched)
        conn.commit()
    finally:
        conn.close()
    return get_draft(business_id, store_id, actor_user_id, connection_id, listing_id,
                     context=context)


def _set_prices(cur, listing_id, seller_user_id, payload):
    """Set retail price per variant, and only that column.

    A targeted UPDATE rather than ``upsert_variant``: that helper writes every
    column it is given and treats an omitted one as NULL, so calling it here
    would silently blank the supplier's ``cost_cents`` on a merchant price edit
    and hand every product a fabricated 100% margin. The field-ownership split is
    expressed as the column list of this statement — cost, stock and provider
    identity are not in it, so this path cannot reach them.
    """
    if not isinstance(payload, dict) or not payload:
        raise SupplierError("invalid_price", http_status=400)
    existing = {str(v.get("id")): v for v in variants.variants_for(cur, listing_id)}
    now = _iso()
    for key, value in payload.items():
        variant = existing.get(str(key))
        if variant is None:
            raise SupplierError("not_found", http_status=404)
        if value is not None and (type(value) is not int or not 0 <= value <= pricing.MAX_PRICE_CENTS):
            raise SupplierError("invalid_price", http_status=400)
        cur.execute(
            f"UPDATE {variants.VARIANT_TABLE} SET price_cents=?, updated_at=? "
            f"WHERE id=? AND listing_id=? AND seller_user_id=?",
            (value, now, int(variant["id"]), int(listing_id), int(seller_user_id)))


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------

def _validate(listing, priced, source, media):
    """Every reason this draft cannot be published, as specific codes.

    Returns a list, not the first failure. A merchant fixing one problem at a
    time and re-submitting to discover the next one is the experience this
    avoids.
    """
    problems = []
    if not (listing.get("title") or "").strip():
        problems.append(MISSING_TITLE)
    if not (listing.get("category") or "").strip():
        problems.append(MISSING_CATEGORY)
    if not media:
        problems.append(NO_VALID_MEDIA)
    if not priced:
        problems.append(NO_VARIANTS_SELECTED)

    if priced and all(v.get("retail_cents") is None for v in priced):
        problems.append(MISSING_PRICE)
    if any(v.get("margin_state") == pricing.NEGATIVE_MARGIN for v in priced):
        problems.append(NEGATIVE_MARGIN)
    # Every variant indeterminate means we cannot say the product is buyable.
    # One known-available variant is enough — the others are simply not offered.
    if priced and all(v.get("availability") == variants.UNKNOWN for v in priced):
        problems.append(UNKNOWN_INVENTORY)

    sync = str(source.get("sync_state") or "").upper()
    if sync == supplier_schema.SYNC_DISCONNECTED:
        problems.append(SUPPLIER_DISCONNECTED)
    elif sync == supplier_schema.SYNC_REMOVED:
        problems.append(PROVIDER_PRODUCT_UNAVAILABLE)

    approval = str(listing.get("approval_status") or "").lower()
    if approval in {"rejected", "suspended"}:
        problems.append(RESTRICTED_PRODUCT)
    return {"publishable": not problems, "problems": problems}


def publish(business_id, store_id, actor_user_id, connection_id, listing_id, *, context=None):
    """Move a validated draft to ``published``. Moderation state is untouched.

    ``quantity`` is set from the count of variants we can positively confirm are
    available, because ``marketplace_listing_lifecycle.inventory_available``
    gates purchasability on it for physical products. Confirmed-available only:
    an indeterminate variant does not contribute, so a supplier outage lowers the
    number toward zero rather than inventing stock.

    This is a sellable-count policy, not a copy of the supplier's warehouse
    quantity — the two are different numbers and conflating them is how a store
    oversells a warehouse it does not control.
    """
    policy.require_enabled()
    conn = db.connect()
    try:
        _, seller_user_id = _scope(conn, business_id, store_id, actor_user_id,
                                   connection_id, context=context, write=True)
        cur = conn.cursor()
        listing_id, listing = _owned_listing(cur, listing_id, seller_user_id)
        source = variants.source_for(cur, listing_id)
        if source is None:
            raise SupplierError("not_a_supplier_product", http_status=404)
        rows = variants.variants_for(cur, listing_id)
        priced = [{
            "retail_cents": _retail_of(v),
            "availability": variants.availability(v),
            "margin_state": pricing.margin_state(_retail_of(v), v.get("cost_cents")),
        } for v in rows]
        media = _media_of(listing)
        verdict = _validate(listing, priced, source, media)
        if not verdict["publishable"]:
            raise SupplierError("publication_blocked", http_status=422)

        sellable = sum(1 for v in rows if variants.availability(v) == variants.AVAILABLE)
        cur.execute(
            "UPDATE marketplace_listings SET status='published', quantity=?, "
            "published_at=?, updated_at=? WHERE id=? AND seller_user_id=?",
            (sellable, _iso(), _iso(), listing_id, int(seller_user_id)))
        conn.commit()
    finally:
        conn.close()
    return {
        "listing_id": listing_id,
        "status": "published",
        # Published is not the same as publicly discoverable. Moderation still
        # has to approve, and saying otherwise here would have the merchant
        # looking for their product in a marketplace that is correctly hiding it.
        "awaiting_moderation": True,
        "sellable_variants": sellable,
    }


def validate(business_id, store_id, actor_user_id, connection_id, listing_id, *, context=None):
    """Dry-run the publish gate without changing anything."""
    return get_draft(business_id, store_id, actor_user_id, connection_id, listing_id,
                     context=context)["validation"]


def list_drafts(business_id, store_id, actor_user_id, connection_id, *,
                context=None, status=None, limit=50):
    """Imported products for this connection, newest first.

    Joins from the supplier mapping rather than scanning listings, so a
    merchant's manually authored products can never appear in a dropshipping
    view — a manual listing has no ``marketplace_product_sources`` row.
    """
    policy.require_enabled()
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50
    wanted = str(status or "").strip().lower()
    conn = db.connect()
    try:
        _, seller_user_id = _scope(conn, business_id, store_id, actor_user_id,
                                   connection_id, context=context)
        # The status filter belongs in the query, beside the LIMIT it has to
        # survive. Filtering the page afterwards in Python searched only the
        # newest `limit` imports and reported every older match as absent, so a
        # merchant with more products than one page could open "Drafts" and be
        # told they had none.
        source = ("FROM marketplace_product_sources s "
                  "JOIN marketplace_listings l ON l.id = s.listing_id "
                  "WHERE s.seller_user_id=? AND s.supplier_connection_id=? "
                  "AND s.business_id=? AND s.store_id=?")
        params = [int(seller_user_id), connection_id, business_id, store_id]
        if wanted:
            source += " AND LOWER(l.status)=?"
            params.append(wanted)
        cur = conn.cursor()
        cur.execute(
            "SELECT l.id, l.title, l.status, l.approval_status, l.currency, "
            "l.cover_image_url, l.updated_at, s.provider, s.sync_state, "
            "s.supplier_cost_cents, s.provider_product_id " + source +
            " ORDER BY l.id DESC LIMIT ?", tuple(params) + (limit,))
        rows = [dict(row) for row in cur.fetchall()]
        # `count` is how many match, not how many were just returned. It used to
        # be len(rows) -- computed after the LIMIT -- which made it a restatement
        # of the page size rather than a measurement of anything. The hub tile
        # asks for limit=1 on purpose, wanting the number without paying for the
        # rows, so it read back its own limit and told every merchant with a
        # supplier connection that they had exactly "1 imported" product.
        cur.execute("SELECT COUNT(*) " + source, tuple(params))
        fetched = cur.fetchone()
        total = int((fetched[0] if fetched else 0) or 0)
    finally:
        conn.close()
    return {"items": rows, "count": total}
