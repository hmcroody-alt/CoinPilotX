"""Import Cart items become DRAFT listings. The client supplies ids, nothing else.

The trust boundary
------------------
This is the file the whole mission's security story rests on, and it has exactly
one rule: **the client names products, the server states facts.**

A merchant's phone sends ``item_ids``. That is all it sends. It does not send a
supplier cost, an inventory count, a title, a variant list, or a shipping quote,
and there is no parameter here through which it could. Every economic fact
written to the database on this path comes from :func:`_authoritative`, which
calls the supplier gateway and re-reads the provider.

Why that matters concretely: supplier cost is what margin is computed from, and
margin is what a merchant prices against. A client-settable cost is a client-
settable margin, and any customer with a proxy becomes able to make a merchant's
storefront claim a profit that does not exist. The Import Cart's ``cached_json``
is *display* state for exactly this reason — it is never read here.

Import never publishes
----------------------
Everything created lands as ``status='draft'`` with ``approval_status``
``'pending_review'``. There is no argument to this module that can produce a
public listing, and :func:`_create_draft_listing` hard-codes both columns rather
than accepting them. Publication is a separate, explicit merchant action in
``publication.py`` with its own validation.

Partial success is the honest shape
-----------------------------------
Ten items where the seventh product was deleted at the provider is nine
successes and one ``PROVIDER_UNAVAILABLE`` — not a failed batch, and not nine
successes with a silent gap. Each item gets its own outcome and its own database
transaction, so one failure cannot roll back its neighbours.
"""

from __future__ import annotations

import json
import time

from services import db, marketplace_variants as variants
from services import marketplace_supplier_schema as supplier_schema
from services.business_os.suppliers import (connections, gateway, import_cart,
                                            normalize, policy, pricing)
from services.business_os.suppliers.errors import SupplierError

#: Per-item outcomes. A bulk import returns one of these per requested item.
IMPORTED = "IMPORTED"
ALREADY_EXISTS = "ALREADY_EXISTS"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
INVALID_PRODUCT = "INVALID_PRODUCT"
NO_VARIANTS = "NO_VARIANTS"
NO_MEDIA = "NO_MEDIA"
RESTRICTED = "RESTRICTED"
NEEDS_REVIEW = "NEEDS_REVIEW"

OUTCOMES = (IMPORTED, ALREADY_EXISTS, PROVIDER_UNAVAILABLE, INVALID_PRODUCT,
            NO_VARIANTS, NO_MEDIA, RESTRICTED, NEEDS_REVIEW)

#: Outcomes after which the cart row is cleared. ``ALREADY_EXISTS`` clears too:
#: the merchant's intent — "this product should be in my store" — is satisfied,
#: and leaving the row would make the cart un-emptiable by repeated tapping.
CLEARS_CART = frozenset({IMPORTED, ALREADY_EXISTS})

#: Terms that force a draft to NEEDS_REVIEW instead of importing clean. This is
#: a coarse first pass, not a compliance system: it exists so that the obvious
#: categories cannot reach a storefront without a human looking, and it is
#: deliberately biased toward false positives, which cost a merchant one tap.
REVIEW_TERMS = (
    "cbd", "vape", "e-cigarette", "nicotine", "tobacco", "kratom",
    "weapon", "firearm", "ammunition", "taser", "pepper spray",
    "prescription", "pharmaceutical", "supplement", "steroid",
    "replica", "counterfeit", "knockoff", "brand copy",
)

#: One import may create at most this many listings. Bounds provider quota use
#: and the transaction count of a single request.
MAX_BATCH = 25


class _ItemFailure(Exception):
    """Internal: one item failed with a known outcome. Never leaves this module."""

    def __init__(self, outcome, detail=None):
        super().__init__(outcome)
        self.outcome = outcome
        self.detail = detail


# ---------------------------------------------------------------------------
# Authoritative provider state
# ---------------------------------------------------------------------------

def _authoritative(business_id, store_id, actor_user_id, connection_id, provider,
                   external_product_id, *, context=None, adapter=None):
    """Re-read the provider and normalize. The only source of supplier facts.

    Three reads, degrading independently:

    * **product** — required. Its failure is the item's failure, because there
      is nothing to import without it. It also mints the snapshot that becomes
      the draft's provenance record.
    * **variants** — attempted only when the product payload carried none, since
      CJ returns them inline on some endpoint versions and not others.
    * **inventory** — best-effort. A failed inventory read leaves each variant
      at whatever the catalogue said and does *not* mark anything out of stock.
      This is the asymmetry that matters: an inventory outage must not empty a
      merchant's shelf, because an empty shelf looks like a normal bad day and
      nobody pages anyone about it.
    """
    try:
        product_read = gateway.read(
            "product", business_id=business_id, store_id=store_id,
            actor_user_id=actor_user_id, connection_id=connection_id,
            params={"pid": external_product_id}, context=context, adapter=adapter)
    except SupplierError as exc:
        raise _ItemFailure(PROVIDER_UNAVAILABLE, getattr(exc, "code", None)) from None

    try:
        product = normalize.product(provider, product_read.get("data"))
    except normalize.NormalizationError:
        raise _ItemFailure(INVALID_PRODUCT, "unreadable_provider_payload") from None

    if not product.get("variants"):
        try:
            variant_read = gateway.read(
                "variants", business_id=business_id, store_id=store_id,
                actor_user_id=actor_user_id, connection_id=connection_id,
                params={"pid": external_product_id}, context=context, adapter=adapter)
            product["variants"] = normalize.variants(provider, variant_read.get("data"))
        except (SupplierError, normalize.NormalizationError):
            # Leave the empty list. Validation below reports NO_VARIANTS, which
            # is a truthful description of what we know.
            pass

    try:
        inventory_read = gateway.read(
            "inventory", business_id=business_id, store_id=store_id,
            actor_user_id=actor_user_id, connection_id=connection_id,
            params={"pid": external_product_id}, context=context, adapter=adapter)
        readings = normalize.inventory(provider, inventory_read.get("data"))
        product["variants"] = normalize.apply_inventory(product["variants"], readings)
    except (SupplierError, normalize.NormalizationError):
        pass

    return product, product_read.get("snapshot_id")


def _validate(product, selection):
    """Reject what cannot become a listing; select the variants to import.

    Returns the chosen variants. Raises ``_ItemFailure`` with the specific
    reason — never a generic failure — because "Something went wrong" gives a
    merchant nothing to act on, and every branch here has a precise cause the
    merchant can either fix or accept.
    """
    if not product.get("external_product_id") or not product.get("title"):
        raise _ItemFailure(INVALID_PRODUCT, "missing_identity")
    if not product.get("media"):
        # After media_list() rejected every URL. A listing with no image is a
        # black card in the marketplace grid; better to refuse than to ship one.
        raise _ItemFailure(NO_MEDIA)

    available = product.get("variants") or []
    if not available:
        raise _ItemFailure(NO_VARIANTS)
    if selection:
        wanted = set(selection)
        chosen = [v for v in available if v.get("external_variant_id") in wanted]
        if not chosen:
            # The merchant ticked variants that the provider no longer lists.
            # Importing all of them instead would silently substitute a
            # different product configuration for the one they chose.
            raise _ItemFailure(NO_VARIANTS, "selected_variants_unavailable")
    else:
        chosen = available

    haystack = " ".join(filter(None, [
        product.get("title"), product.get("category"), product.get("brand"),
        (product.get("description") or "")[:2000],
    ])).lower()
    if any(term in haystack for term in REVIEW_TERMS):
        raise _ItemFailure(NEEDS_REVIEW, "restricted_category_terms")
    return chosen


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _existing_listing(cur, seller_user_id, provider, connection_id, external_product_id):
    """The listing this supplier product already imported to, if any.

    Reads ``marketplace_product_sources`` — the single supplier-mapping
    authority — on the same tuple its UNIQUE index covers. This is the
    duplicate-prevention read, and it is the reason a double-tapped Import
    button reopens one draft rather than creating two listings.
    """
    cur.execute(
        "SELECT listing_id FROM marketplace_product_sources "
        "WHERE seller_user_id=? AND provider=? AND supplier_connection_id=? "
        "AND provider_product_id=? LIMIT 1",
        (int(seller_user_id), provider, connection_id, external_product_id))
    row = cur.fetchone()
    if row is None:
        return None
    try:
        return int(row["listing_id"])
    except (KeyError, IndexError, TypeError):
        return int(row[0])


def _create_draft_listing(cur, seller_user_id, product):
    """Insert one DRAFT listing. Status and approval are not parameters.

    Both are literals in the SQL rather than arguments with draft defaults. A
    default is a value a caller can override, and "import must never publish" is
    not a default — it is the invariant. Writing it as a literal means the only
    way to make this function publish something is to edit this line, which is a
    diff a reviewer will see.

    ``price_label`` is left empty on purpose. It is the listing's public price
    prose and the merchant has not set a price yet; seeding it with the supplier
    cost would print the merchant's own cost on their storefront.

    ``quantity`` is left NULL for the same reason, which this function used to
    get right for the price and wrong for the stock one argument later. It was a
    literal ``0``, and ``0`` is not "unknown" — it is a *count*, the merchant's
    own assertion that they have none. Nobody made that assertion: an import has
    not counted anything, and on a dropship listing the merchant never will,
    because the units sit in the supplier's warehouse and arrive via
    ``drafts.publish`` (``_sellable_units``) at publish time.

    The cost of the lie was not theoretical. All seven physical drafts in
    production carried ``quantity = 0``, so ``listing_readiness`` reported
    OUT_OF_STOCK over UNKNOWN_INVENTORY and the seller's store row read
    "Out of stock — hidden / Restock" — an instruction to reorder from a
    supplier, for products that had simply never been counted. NULL is what the
    column is for (it is nullable, the checkout decider refuses it, and every
    ``quantity>=?`` decrement guard fails closed against it), and it is the
    difference between telling a merchant "you are sold out" and "nobody has
    counted this yet".

    ``cover_image_url`` is written from the same list that goes into the
    metadata, rather than left for a reader to derive. `_validate` already
    refuses a product with no media — "better to refuse than to ship a black
    card" — but that guard only held for readers that derive the cover from
    ``listing_metadata_json`` the way ``get_draft`` does. Every reader of the
    *column* (the products list, the merchant's store list, the buyer grid) got
    NULL, so the black card the guard exists to prevent shipped anyway on every
    import. Deriving both from one local list is what keeps the column and the
    metadata from disagreeing later.
    """
    now = _iso()
    media = [m for m in (product.get("media") or []) if isinstance(m, str)]
    cur.execute(
        "INSERT INTO marketplace_listings "
        "(seller_user_id, title, description, category, price_label, status, "
        " created_at, updated_at, approval_status, currency, quantity, "
        " delivery_type, product_type, listing_type, cover_image_url, "
        " listing_metadata_json) "
        "VALUES (?,?,?,?,?,'draft',?,?,'pending_review',?,?,'physical','physical','',?,?)",
        (int(seller_user_id), product.get("title"), product.get("description"),
         product.get("category"), "", now, now, product.get("currency") or "USD", None,
         media[0] if media else None,
         json.dumps({"source": "dropship", "media": media},
                    separators=(",", ":"))))
    cur.execute(
        "SELECT id FROM marketplace_listings WHERE seller_user_id=? AND status='draft' "
        "ORDER BY id DESC LIMIT 1", (int(seller_user_id),))
    row = cur.fetchone()
    if row is None:
        raise _ItemFailure(INVALID_PRODUCT, "listing_not_created")
    try:
        return int(row["id"])
    except (KeyError, IndexError, TypeError):
        return int(row[0])


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_variants(cur, listing_id, seller_user_id, chosen, rule):
    """Create the listing's variants from authoritative provider facts.

    ``price_cents`` comes from the pricing rule and is ``None`` under manual
    pricing or unknown cost — a variant with no price defers to the listing, and
    the publish gate refuses to publish a listing whose variants have no price.
    That chain is what stops an unpriced import from reaching a buyer.

    ``stock_state`` goes through ``storage_stock_state``, which maps every
    indeterminate state onto ``UNKNOWN``. It must not map onto ``OUT_OF_STOCK``:
    downstream readers treat that as a confirmed negative.
    """
    written = []
    for position, variant in enumerate(chosen):
        cost = variant.get("cost_cents")
        cur_variant_id = variants.upsert_variant(
            cur,
            listing_id=listing_id,
            seller_user_id=seller_user_id,
            options=variant.get("options"),
            sku=variant.get("external_sku"),
            provider_variant_id=variant.get("external_variant_id"),
            price_cents=pricing.apply_rule(rule, cost),
            cost_cents=cost,
            currency=variant.get("currency"),
            stock_state=normalize.storage_stock_state(variant.get("stock_state")),
            stock_quantity=variant.get("stock_quantity"),
            position=position,
        )
        written.append(cur_variant_id)
    return written


def _import_one(conn, *, seller_user_id, business_id, store_id,
                actor_user_id, connection_id, provider, external_product_id,
                selection, rule, context, adapter):
    """One cart item, one transaction. Returns (outcome, payload).

    No `merchant_id`. It used to take one and never read it: `merchant_id` is
    `business_os_business.owner_user_id` and `seller_user_id` is `int()` of the
    same value, resolved once by the caller so an unparseable identity refuses
    with `merchant_identity_unresolved` before any import begins.

    Benign as it stood, and removed anyway, because carrying two spellings of
    one identity into a function is how the next edit reads the one that cannot
    work -- the same hazard `list_obligations` names about the two spellings of
    "the SKU". Found by grepping for parameters a body never loads, which is the
    mechanical tell for the nineteenth corollary: `dispatch` took a clock it
    ignored, and that made a real rule unexecutable. This was the same shape
    with nothing behind it, which is the answer the tell is supposed to be able
    to give.
    """
    product, snapshot_id = _authoritative(
        business_id, store_id, actor_user_id, connection_id, provider,
        external_product_id, context=context, adapter=adapter)
    chosen = _validate(product, selection)

    cur = conn.cursor()
    existing = _existing_listing(cur, seller_user_id, provider, connection_id,
                                 external_product_id)
    if existing is not None:
        # Re-open, do not re-create. The merchant gets back the draft or listing
        # they already have, with its merchant edits intact.
        return ALREADY_EXISTS, {"listing_id": existing}

    listing_id = _create_draft_listing(cur, seller_user_id, product)
    _write_variants(cur, listing_id, seller_user_id, chosen, rule)

    low, high = normalize.cost_range(chosen)
    variants.link_source(
        cur,
        listing_id=listing_id,
        seller_user_id=seller_user_id,
        provider=provider,
        provider_product_id=external_product_id,
        fulfillment_mode=supplier_schema.MODE_DROPSHIP,
        supplier_connection_id=connection_id,
        business_id=business_id,
        store_id=store_id,
        external_sku=product.get("external_sku"),
        source_snapshot_id=snapshot_id,
        # The supplier variant an order for this listing will actually be placed
        # for. `fulfillment.create_intent` can only order the variant named here
        # (`gateway.get_product_binding` refuses outright when it is NULL), so a
        # listing without one is a listing nothing can ship.
        #
        # Recorded here, and only when the merchant's selection leaves no room
        # for interpretation. One chosen variant is not a choice we are making on
        # their behalf -- it is the only thing this listing can be. With several
        # chosen there genuinely is a question, this import has no answer to it,
        # and inventing one would ship a buyer whichever variant we guessed.
        # `link_source` refuses to re-point an existing binding, so this cannot
        # silently override a merchant's later explicit choice either.
        provider_variant_id=(chosen[0].get("external_variant_id")
                             if len(chosen) == 1 else None),
        # The low end of the range, and ``None`` when no variant had a readable
        # cost. Never 0 — see ``normalize.cost_range``.
        supplier_cost_cents=low,
        supplier_cost_currency=product.get("currency"),
        inventory_source=provider,
        inventory_reference=external_product_id,
        sync_state=supplier_schema.SYNC_SYNCED,
    )
    return IMPORTED, {
        "listing_id": listing_id,
        "snapshot_id": snapshot_id,
        "variant_count": len(chosen),
        "cost_low_cents": low,
        "cost_high_cents": high,
        "status": "DRAFT",
        "published": False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_selected(business_id, store_id, actor_user_id, connection_id, *,
                    item_ids=None, pricing_rule=None, context=None, adapter=None):
    """Import cart items as DRAFT listings. Per-item outcomes, never all-or-nothing.

    ``item_ids`` names rows in the merchant's own Import Cart. Absent, the whole
    cart is imported. Nothing else about the products is accepted from the
    caller — see the module docstring.
    """
    policy.require_enabled()
    import_cart.ensure_schema()
    gateway.ensure_schema()
    rule = pricing.normalize_rule(pricing_rule)

    if item_ids is not None:
        if not isinstance(item_ids, (list, tuple)):
            raise SupplierError("invalid_input", http_status=400)
        if len(item_ids) > MAX_BATCH:
            raise SupplierError("batch_too_large", http_status=400)
        item_ids = [str(i) for i in item_ids if isinstance(i, str) and i.strip()]

    conn = db.connect()
    try:
        merchant_id = connections._authorize(conn, business_id, store_id, actor_user_id,
                                             context=context, write=True)
        connections._row(conn, connection_id, business_id, store_id, merchant_id)
        rows = import_cart.items_for_import(conn, merchant_id, business_id, store_id,
                                            connection_id, item_ids)
    finally:
        conn.close()

    if not rows:
        raise SupplierError("import_cart_empty", http_status=409)
    rows = rows[:MAX_BATCH]

    try:
        seller_user_id = int(str(merchant_id).strip())
    except (TypeError, ValueError):
        # merchant_id is business_os_business.owner_user_id and is the same
        # identity as marketplace_listings.seller_user_id. If it will not parse,
        # this store cannot own a listing and no amount of retrying changes it.
        raise SupplierError("merchant_identity_unresolved", http_status=409) from None

    results, imported_item_ids = [], []
    for row in rows:
        item_id = row["item_id"]
        provider = str(row["provider"] or "cj").strip().lower()
        external_product_id = row["external_product_id"]
        try:
            selection = json.loads(row["selected_variant_ids_json"] or "[]")
        except (ValueError, TypeError):
            selection = []

        # A fresh connection per item. One item's rollback must not discard the
        # listing its predecessor already created — that is what "honest partial
        # success" costs, and sharing a transaction would silently undo it.
        conn = db.connect()
        try:
            outcome, payload = _import_one(
                conn, seller_user_id=seller_user_id,
                business_id=business_id, store_id=store_id, actor_user_id=actor_user_id,
                connection_id=connection_id, provider=provider,
                external_product_id=external_product_id, selection=selection,
                rule=rule, context=context, adapter=adapter)
            conn.commit()
        except _ItemFailure as failure:
            conn.rollback()
            outcome, payload = failure.outcome, ({"detail": failure.detail}
                                                 if failure.detail else {})
        except SupplierError as exc:
            conn.rollback()
            outcome, payload = PROVIDER_UNAVAILABLE, {"detail": getattr(exc, "code", None)}
        except variants.VariantRejected:
            conn.rollback()
            outcome, payload = INVALID_PRODUCT, {"detail": "variant_rejected"}
        finally:
            conn.close()

        if outcome in CLEARS_CART:
            imported_item_ids.append(item_id)
        results.append({
            "item_id": item_id,
            "external_product_id": external_product_id,
            "provider": provider,
            "outcome": outcome,
            **payload,
        })

    if imported_item_ids:
        conn = db.connect()
        try:
            import_cart.drop_items(conn, merchant_id, imported_item_ids)
            conn.commit()
        finally:
            conn.close()

    counts = {outcome: sum(1 for r in results if r["outcome"] == outcome)
              for outcome in OUTCOMES}
    return {
        "results": results,
        "requested": len(rows),
        "imported": counts[IMPORTED],
        "counts": {k: v for k, v in counts.items() if v},
        "published": False,
        "pricing_rule": rule,
    }
