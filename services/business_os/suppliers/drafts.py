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
from decimal import Decimal

from services import db, marketplace_variants as variants
from services import marketplace_listing_lifecycle as lifecycle
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
#: Offered variants carry more than one retail price. The buyer's checkout
#: charges one listing-level price and has no variant selector, so publishing
#: this would pick one of the merchant's prices and charge it for all of them.
VARIANT_PRICE_SPREAD = "VARIANT_PRICE_SPREAD"
#: Priced above what the checkout's own label format can carry.
PRICE_ABOVE_CHECKOUT_LIMIT = "PRICE_ABOVE_CHECKOUT_LIMIT"
#: No supplier variant is bound, so nothing can be ordered for this listing.
#:
#: ``marketplace_product_sources.provider_variant_id`` names the one variant an
#: order is placed for. ``gateway.get_product_binding`` raises
#: ``product_binding_required`` when it is NULL, and ``fulfillment.create_intent``
#: goes through that function for every line -- so an unbound listing is one no
#: supplier order can ever be created for. Publishing it produces a product a
#: buyer can pay for and nobody can ship, which is the state §5's sellability
#: contract exists to prevent. Measured on production listing 14: published,
#: moderator-approved, on sale, and unbound.
SUPPLIER_VARIANT_UNBOUND = "SUPPLIER_VARIANT_UNBOUND"

#: The checkout's ceiling, mirrored from ``bot.MAX_PRICE_LABEL_CENTS``.
#:
#: It is *lower* than :data:`pricing.MAX_PRICE_CENTS`, which is what makes it
#: matter here: ``_set_prices`` accepts a variant price up to ten million
#: dollars, and ``bot.parse_price_label_to_cents`` ends in
#: ``min(cents, MAX_PRICE_LABEL_CENTS)``. A price between the two limits would
#: therefore be *clamped* on the way to the buyer rather than refused — the
#: merchant sets $5,000,000 and the card is charged $999,999.99. Refusing to
#: publish is the only reading of that gap that does not quietly move money.
#: ``test_dropship_draft_publish`` pins this against the monolith's own constant.
MAX_CHECKOUT_PRICE_CENTS = 99_999_999

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
            # Repricing a *live* product has to reach the buyer, not only the
            # draft screen. `publish` is the other writer of `price_label`; if it
            # were the only one, a merchant raising the price of a published
            # listing would see the new number everywhere they look while the
            # cart went on charging the old one until they happened to republish.
            if lifecycle.normalized(listing.get("status")) in lifecycle.PUBLIC_STATUSES:
                updates["price_label"] = _live_price_label(cur, listing_id, listing)

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


def _live_price_label(cur, listing_id, listing):
    """The label a *published* listing should now carry, or a refusal.

    Re-reads the variants after the write rather than working from the request
    body, because the merchant may have repriced only some of them and it is the
    resulting whole that has to be chargeable.

    A live listing that would be left in a state the checkout cannot represent is
    refused outright instead of being quietly taken off sale or left at its old
    price. Both alternatives are worse: one hides a merchant's mistake behind a
    dead product page, the other keeps charging a price they have replaced.
    """
    rows = variants.variants_for(cur, listing_id)
    priced = [{"provider_variant_id": v.get("provider_variant_id"),
               "retail_cents": _retail_of(v),
               "availability": variants.availability(v)} for v in rows]
    offered = _offered(priced, variants.source_for(cur, listing_id))
    if not offered or any(v["retail_cents"] is None for v in offered):
        raise SupplierError("publication_blocked", http_status=422)
    distinct = {v["retail_cents"] for v in offered}
    if len(distinct) > 1 or max(distinct) > MAX_CHECKOUT_PRICE_CENTS:
        raise SupplierError("publication_blocked", http_status=422)
    return _checkout_price_label(offered[0]["retail_cents"], listing.get("currency"))


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

def _sold_variant(priced, source):
    """The one supplier variant this listing sells, or ``None`` if unbound.

    A dropshipped listing does not sell "its variants". It sells exactly the
    variant named by ``marketplace_product_sources.provider_variant_id``, because
    that is the only one an order can be placed for: ``create_intent`` resolves
    every line through ``gateway.get_product_binding`` and then demands the
    line's ``vid`` equal the bound one. The other rows are catalogue -- what the
    supplier offers -- not stock this listing can sell.

    Returns ``None`` both when nothing is bound and when the bound id names a
    variant this listing does not have, which is drift rather than absence and is
    reported as the same problem: there is no variant we can prove will ship.
    """
    bound = str((source or {}).get("provider_variant_id") or "").strip()
    if not bound:
        return None
    for variant in priced:
        if str(variant.get("provider_variant_id") or "").strip() == bound:
            return variant
    return None


def _sellable_units(variant):
    """How many *units* of the sold variant are on offer.

    ``marketplace_listings.quantity`` is a unit ledger. The cart decrements it
    per unit reserved (``marketplace_cart_routes``) and credits it back on
    release, and ``lifecycle.inventory_available`` answers "may this buyer take
    N" by comparing N against it.

    Publishing used to seed it with ``sum(1 for v in rows if availability(v) ==
    AVAILABLE)`` -- a count of *variants* -- and return that same integer as
    ``sellable_variants``, which is what it honestly is; the mobile draft screen
    renders it as "2 variants are on sale". One value, two meanings, one line
    apart. Production listing 14 is the measurement: one variant, 132 units in
    the supplier's warehouse, ``quantity = 1``. A three-colour product with 52
    units behind it offered two.

    ``None`` means available with no count, and becomes one unit at a time.
    ``variants.availability`` deliberately trusts a provider that declares stock
    without a number, so refusing to sell it would contradict that -- and any
    number above 1 would be one nobody told us.
    """
    if variant.get("availability") != variants.AVAILABLE:
        return 0
    quantity = variant.get("stock_quantity")
    if quantity is None:
        return 1
    try:
        return max(0, int(quantity))
    except (TypeError, ValueError):
        return 1


def _offered(priced, source=None):
    """The variants a buyer could actually end up receiving.

    When the listing is bound, that is the sold variant and nothing else -- see
    :func:`_sold_variant`. Asking the whole variant set what price to charge only
    ever made sense while no single variant was identifiable, and it is why
    :data:`VARIANT_PRICE_SPREAD` had to exist.

    Unbound, the old reading stands and is still right for a ``STOCKED`` source,
    which the merchant fulfils themselves and which therefore needs no binding:
    an ``UNAVAILABLE`` variant is not on sale, so its price is not a promise to
    anybody and must not block the rest of the product. The fallback is the part
    worth keeping: when *nothing* is available the listing still publishes, sold
    out, and it still needs a price written on it — otherwise it becomes a
    priceless listing again the moment the supplier restocks, which is the exact
    state this whole section exists to prevent.
    """
    sold = _sold_variant(priced, source)
    if sold is not None:
        return [sold]
    return [v for v in priced
            if v.get("availability") != variants.UNAVAILABLE] or list(priced)


def _checkout_price_label(cents, currency):
    """Render the agreed retail price as the label the buyer's checkout parses.

    This is the seam the pipeline was missing. The merchant prices variants in
    ``marketplace_listing_variants.price_cents``; every buyer surface — the cart,
    ``confirm-price``, offers — reads ``marketplace_listings.price_label`` and
    runs it through ``bot.parse_price_label_to_cents``. Nothing joined the two,
    so a published dropship product reached the buyer with an empty label, which
    parses to zero, and add-to-cart answered "This item is not priced for
    checkout" for a product the merchant had priced.

    The format mirrors ``bot.marketplace_normalize_price_label`` rather than
    calling it: no module in this package imports the monolith, and doing it here
    would run ``bot``'s import-time schema work inside a publish. The duplication
    is safe only because a test asserts this function agrees with that one
    character for character, and that the label parses back to exactly the cents
    passed in — the number written here is the number a stranger's card is
    charged, so equality is the only acceptable evidence.
    """
    currency = (str(currency or "USD").strip() or "USD").upper()
    # Decimal, not float, for the same reason the monolith uses it: money divided
    # by 100 in binary floating point is a rounding argument waiting to happen.
    amount = "{:,.2f}".format(Decimal(int(cents)) / 100)
    return ("$" + amount) if currency == "USD" else (currency + " " + amount)


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

    # The buyer's checkout charges one listing-level price for the whole listing
    # and offers no variant selector at all -- `marketplace_variants` is imported
    # by this package and nowhere else. So the question publication has to answer
    # is not "is anything priced" but "is there exactly one price we could honour
    # for whichever variant this buyer ends up with".
    #
    # This used to be `all(... is None)`, which asked only whether the merchant
    # had priced *something*. A product with one variant at $20 and another left
    # blank published happily, and the blank one was then sold at $20.
    sold = _sold_variant(priced, source)
    offered = _offered(priced, source)
    if offered and any(v.get("retail_cents") is None for v in offered):
        problems.append(MISSING_PRICE)
    elif offered:
        distinct = {v["retail_cents"] for v in offered}
        if len(distinct) > 1:
            problems.append(VARIANT_PRICE_SPREAD)
        elif max(distinct) > MAX_CHECKOUT_PRICE_CENTS:
            problems.append(PRICE_ABOVE_CHECKOUT_LIMIT)
    if any(v.get("margin_state") == pricing.NEGATIVE_MARGIN for v in priced):
        problems.append(NEGATIVE_MARGIN)
    # Indeterminate stock is asked of the variant that will actually ship. While
    # nothing was bound this had to be asked of the whole set, and "one known
    # variant is enough" was the right reading of a question we could not aim.
    # Bound, it is answerable exactly: the buyer receives that variant or nothing,
    # so its state is the product's state and a sibling's cannot stand in for it.
    inventory_pool = [sold] if sold is not None else priced
    if inventory_pool and all(v.get("availability") == variants.UNKNOWN
                              for v in inventory_pool):
        problems.append(UNKNOWN_INVENTORY)

    # Nothing can be ordered for an unbound dropship listing. This is a refusal
    # to publish a product that a buyer could pay for and nobody could ship --
    # see `SUPPLIER_VARIANT_UNBOUND`. It is only a fair thing to demand because
    # `importer` now binds at import when the merchant's selection names one
    # variant, so the ordinary path satisfies it without the merchant doing
    # anything. `STOCKED` sources are exempt: the merchant holds that inventory
    # and places no supplier order, so there is nothing to bind.
    if str(source.get("fulfillment_mode") or "").upper() == supplier_schema.MODE_DROPSHIP \
            and priced and sold is None:
        problems.append(SUPPLIER_VARIANT_UNBOUND)

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

    ``price_label`` is written here because this is where a product crosses from
    the merchant's world into the buyer's. The two halves keep price in different
    places — the merchant prices variants, the buyer's cart reads the listing's
    label — and until this line existed nothing joined them, so a fully priced,
    published, moderator-approved dropship product was publicly listed and then
    refused at add-to-cart with "This item is not priced for checkout."

    ``quantity`` is the number of *units* of the bound supplier variant that are
    on offer, because ``marketplace_listing_lifecycle.inventory_available`` gates
    purchasability on it for physical products and the cart decrements it per unit
    reserved. It used to be a count of *variants* — see :func:`_sellable_units`
    for the measurement, and note that the same integer is still returned as
    ``sellable_variants``, where a count of variants is what it honestly means.
    Confirmed-available only: an indeterminate variant contributes nothing, so a
    supplier outage lowers the number to zero rather than inventing stock.

    ``cover_image_url`` is written here for the same reason and by the same
    argument as ``price_label``. The two halves keep media in different places:
    the merchant's side stores an ordered list in ``listing_metadata_json.media``
    and that is what ``_validate`` reads, while every buyer surface renders the
    *column* — ``pulse_marketplace_listing_payload`` builds its media list from
    ``marketplace_product_media``, ``cover_image_url``, ``media_url`` and
    ``gallery_json``, and consults the metadata list for nothing. So a draft with
    five photos validated as having media and published a card with none, which
    is the black placeholder ``NO_VALID_MEDIA`` exists to prevent, arrived at
    through a *passing* validation.

    Both writers on the merchant side already set the column and the metadata
    together (``importer._insert_listing`` and ``update_draft``), so a draft
    imported by current code is not affected. What this line fixes is every row
    written before they did — measured on production listing 14, a real CJ
    upholstered bed whose metadata carries five ``cf.cjdropshipping.com`` URLs
    with ``cover_image_url`` NULL, and which the real evaluator calls
    ``publishable=True``. Repairing it at the crossing point costs the merchant
    no action for a bug that was never theirs, and means no dropship listing can
    become buyer-visible with a cover the buyer cannot see.
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
            "provider_variant_id": v.get("provider_variant_id"),
            "stock_quantity": v.get("stock_quantity"),
            "retail_cents": _retail_of(v),
            "availability": variants.availability(v),
            "margin_state": pricing.margin_state(_retail_of(v), v.get("cost_cents")),
        } for v in rows]
        media = _media_of(listing)
        verdict = _validate(listing, priced, source, media)
        if not verdict["publishable"]:
            raise SupplierError("publication_blocked", http_status=422)

        # Two numbers, deliberately kept apart. `sellable` counts *variants* and
        # is what the merchant's draft screen renders as "N variants are on sale";
        # `units` is the buyer's stock ledger. They were one integer until now,
        # which is why a product with 132 units in the warehouse offered one.
        sellable = sum(1 for v in rows if variants.availability(v) == variants.AVAILABLE)
        offered = _offered(priced, source)
        units = _sellable_units(offered[0])
        # `_validate` has just established that every offered variant carries the
        # same price, so there is exactly one number here and it is the merchant's
        # own -- nothing is being chosen on their behalf.
        label = _checkout_price_label(offered[0]["retail_cents"],
                                      listing.get("currency"))
        # `_validate` has just established `media` is non-empty. `media[0]` is the
        # cover by this package's own definition -- `get_draft` reports exactly
        # this expression as `cover_image_url` -- so nothing is being chosen on
        # the merchant's behalf here either.
        cover = media[0]
        cur.execute(
            "UPDATE marketplace_listings SET status='published', quantity=?, "
            "price_label=?, cover_image_url=?, published_at=?, updated_at=? "
            "WHERE id=? AND seller_user_id=?",
            (units, label, cover, _iso(), _iso(), listing_id, int(seller_user_id)))
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
        "price_label": label,
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
