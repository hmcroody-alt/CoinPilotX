"""Import Cart items become listings in the merchant's store. Ids in, facts out.

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

"Import to Store" finishes the job
---------------------------------
This module used to stop at a draft on principle, and the principle was wrong for
the button the merchant actually taps. "Import to Store" is a request for a
listing that sells, and what it produced was a product with no price, no stock
figure and a MISSING_PRICE badge -- so the merchant's next action was always to
open an editor and type a number the store could have supplied. An import that
reliably needs a second step is an import that did not happen.

So the pipeline now runs to the end: authoritative read, normalize, select
variants, price from the store's own policy (:mod:`store_policy`), write the
listing and its variants, bind the supplier mapping, then hand the listing to
:func:`drafts.autopublish`, which runs the publish gate and reads the row back.
An ordinary product comes out ``PUBLISHED``. One that genuinely cannot be sold
safely comes out ``NEEDS_ATTENTION`` carrying the specific codes that stopped it.

What did *not* change is who decides. :func:`_create_draft_listing` still writes
``status='draft'`` as a SQL literal, and this module still contains no publish
rule of its own: every question about whether a buyer may see something is
answered by :mod:`drafts`, in the same function the merchant's explicit Publish
button calls. The insert cannot publish, the gate can, and the gate is one
implementation shared by both entry points (§32). A refusal is therefore never
"the importer disagreed with the publisher" -- there is only one publisher.

The trust boundary above is unaffected by any of it. Auto-publishing widens what
the server *does* with supplier facts; it does not widen what the client may
assert. There is still no parameter here through which a phone can name a price,
a stock level, or a published state -- ``pricing_rule`` names a *strategy* whose
inputs are all server-read, and §33's "the client must not construct a ready
listing" is enforced by there being nothing to construct with.

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
from services.business_os.suppliers import (connections, drafts, gateway,
                                            import_cart, normalize, policy,
                                            pricing, store_policy)
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
#: Imported, completed, validated and now live in the merchant's store. The
#: normal outcome of "Import to Store" for an ordinary product, and the one this
#: module previously had no way to report because it always stopped at a draft.
PUBLISHED = "PUBLISHED"
#: Imported and left as a draft because publishing it would not have been safe.
#: Carries ``problems`` -- :mod:`drafts`' own validation codes, unmodified -- so
#: the merchant is told the actual reason rather than "needs attention".
#:
#: Distinct from the refusals above, and the distinction is the merchant's:
#: ``NO_MEDIA`` and friends mean *nothing was created*, while this means the
#: product is in their store and is one specific fix away from selling.
NEEDS_ATTENTION = "NEEDS_ATTENTION"

OUTCOMES = (IMPORTED, ALREADY_EXISTS, PROVIDER_UNAVAILABLE, INVALID_PRODUCT,
            NO_VARIANTS, NO_MEDIA, RESTRICTED, NEEDS_REVIEW, PUBLISHED,
            NEEDS_ATTENTION)

#: Outcomes after which the cart row is cleared. ``ALREADY_EXISTS`` clears too:
#: the merchant's intent — "this product should be in my store" — is satisfied,
#: and leaving the row would make the cart un-emptiable by repeated tapping.
#:
#: ``NEEDS_ATTENTION`` clears for the same reason and it is worth being explicit
#: about why, because it reads like a failure: the listing *was* created and is in
#: the store. Keeping the cart row would leave the merchant holding two copies of
#: one intent — a product on their shelf and a cart item for it — and tapping
#: Import again would answer ``ALREADY_EXISTS`` forever without ever clearing.
#: The fix for a needs-attention product is in the editor, not in the cart.
CLEARS_CART = frozenset({IMPORTED, ALREADY_EXISTS, PUBLISHED, NEEDS_ATTENTION})

#: Outcomes that mean a listing now exists in the merchant's store. The honest
#: denominator for "imported", which ``IMPORTED`` alone stopped being the moment
#: the same run could also answer ``PUBLISHED``.
CREATED_LISTING = frozenset({IMPORTED, PUBLISHED, NEEDS_ATTENTION})

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


def _create_draft_listing(cur, seller_user_id, product, *, marketplace_autolist=False):
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

    ``marketplace_autolist`` records the store's Marketplace-distribution setting
    *on the product*, at the moment the merchant imported it. Publishing to their
    store already does not broadcast anything -- ``approval_status`` stays
    ``pending_review`` and every buyer surface gates on it -- so this is not a
    second visibility switch. It is the merchant's answer to "and may this one go
    into the wider PulseSoc Marketplace when it clears review", captured per
    listing rather than read from the store's current setting later. A merchant who
    imports fifty products with distribution off and then turns it on has said
    something about their *next* imports; silently back-dating that to fifty
    products they chose to keep to their own store would be the single broadcast
    this split exists to prevent.

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
         json.dumps({"source": "dropship", "media": media,
                     "marketplace_autolist": bool(marketplace_autolist)},
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


def _sole_orderable(chosen):
    """The supplier variant this listing can only be, or ``None`` if there is a choice.

    ``marketplace_product_sources.provider_variant_id`` is the variant an order
    will actually be placed for -- ``fulfillment.create_intent`` can order no other
    -- so an unbound dropship listing is one nothing can ship, and
    :data:`drafts.SUPPLIER_VARIANT_UNBOUND` correctly refuses to publish it.

    This function answers that guard in the only two cases where answering it is
    not choosing on the merchant's behalf:

    * **One variant was chosen.** Already the old rule. Not a choice; the only
      thing the listing can be.
    * **One chosen variant is not confirmed unavailable.** The siblings are
      known-negative -- ``availability`` returned ``UNAVAILABLE``, meaning the
      provider said out of stock or the variant is archived -- and an order for one
      of them would be refused at the supplier anyway. Binding the survivor
      substitutes nothing, because nothing else was orderable to begin with.

    What it deliberately does **not** do is break a genuine tie. Three colours all
    in stock is a real question about which one a buyer receives, this import has
    no answer to it, and §1 forbids inventing product identity. Those land
    ``NEEDS_ATTENTION`` with ``SUPPLIER_VARIANT_UNBOUND`` and the merchant picks.

    ``UNKNOWN`` is not a negative and is not treated as one. A variant the provider
    declined to report on might be perfectly orderable, so one ``IN_STOCK`` variant
    beside two unreadable ones is still a choice -- narrowing it here would let an
    inventory outage decide what a merchant sells. Same asymmetry
    :func:`_authoritative` keeps for the shelf, applied to the binding.

    Availability is asked of :func:`marketplace_variants.availability` rather than
    reimplemented from ``stock_state``, so this cannot drift from the reading
    ``drafts`` will apply to the same rows one transaction later.
    """
    if len(chosen) == 1:
        return chosen[0].get("external_variant_id")
    shippable = [v for v in chosen if variants.availability({
        "status": "active",
        "stock_state": normalize.storage_stock_state(v.get("stock_state")),
        "stock_quantity": v.get("stock_quantity"),
    }) != variants.UNAVAILABLE]
    if len(shippable) == 1:
        return shippable[0].get("external_variant_id")
    return None


def _import_one(conn, *, seller_user_id, business_id, store_id,
                actor_user_id, connection_id, provider, external_product_id,
                selection, rule, auto_publish, marketplace_autolist,
                context, adapter):
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

    listing_id = _create_draft_listing(cur, seller_user_id, product,
                                       marketplace_autolist=marketplace_autolist)
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
        # for interpretation -- see `_sole_orderable` for what "no room" means and
        # for the ties it refuses to break. With several orderable variants there
        # genuinely is a question, this import has no answer to it, and inventing
        # one would ship a buyer whichever variant we guessed. `link_source`
        # refuses to re-point an existing binding, so this cannot silently
        # override a merchant's later explicit choice either.
        provider_variant_id=_sole_orderable(chosen),
        # The low end of the range, and ``None`` when no variant had a readable
        # cost. Never 0 — see ``normalize.cost_range``.
        supplier_cost_cents=low,
        supplier_cost_currency=product.get("currency"),
        inventory_source=provider,
        inventory_reference=external_product_id,
        sync_state=supplier_schema.SYNC_SYNCED,
    )

    payload = {
        "listing_id": listing_id,
        "snapshot_id": snapshot_id,
        "variant_count": len(chosen),
        "cost_low_cents": low,
        "cost_high_cents": high,
    }

    if not auto_publish:
        # The store asked for drafts. A merchant who turns auto-publish off has
        # said they want to look at each product first, and finishing the listing
        # anyway would be this module overruling a setting they went and changed.
        return IMPORTED, {**payload, "status": "DRAFT", "published": False}

    # Everything above wrote facts. This asks the one authority on buyer
    # visibility whether those facts add up to something sellable, inside the
    # same transaction, so there is no window in which a half-finished listing
    # is visible to another reader and no outcome that is committed before it is
    # known. `autopublish` does the read-back (§35) and returns codes, not prose.
    finish = drafts.autopublish(cur, listing_id, seller_user_id)
    if not finish["published"]:
        return NEEDS_ATTENTION, {
            **payload,
            "status": "DRAFT",
            "published": False,
            # `drafts`' own codes, passed through unmodified. Translating them
            # here would give the merchant a second, less precise vocabulary for
            # the same refusal, and the editor deep-link that fixes each one is
            # keyed on the code.
            "problems": finish["problems"],
        }
    return PUBLISHED, {
        **payload,
        "status": "PUBLISHED",
        "published": True,
        "price_label": finish.get("price_label"),
        "quantity": finish.get("quantity"),
        "sellable_variants": finish.get("sellable_variants"),
        # Published to the merchant's store is not the same as discoverable
        # across PulseSoc. Reported so the success screen can say "live in your
        # store" without implying a marketplace placement moderation has not
        # granted yet.
        "awaiting_moderation": finish.get("awaiting_moderation", True),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_selected(business_id, store_id, actor_user_id, connection_id, *,
                    item_ids=None, pricing_rule=None, context=None, adapter=None):
    """Import cart items into the merchant's store. Per-item outcomes, never all-or-nothing.

    ``item_ids`` names rows in the merchant's own Import Cart. Absent, the whole
    cart is imported. Nothing else about the products is accepted from the
    caller — see the module docstring.

    ``pricing_rule`` is optional and remains an *override*. Its absence used to
    mean :data:`pricing.MANUAL_PRICE`, which proposes no price at all, which is why
    every import landed unpriced. It now means "the store has not been asked" and
    resolution falls to :func:`store_policy.resolve_rule`. The Import Cart's rule
    picker is unaffected and still wins.
    """
    policy.require_enabled()
    import_cart.ensure_schema()
    gateway.ensure_schema()

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
        # Resolved once, for the whole batch, on the connection that just proved
        # the caller owns this store. Per item would be the same answer plus N
        # reads, and would let a policy edited mid-batch price the first half of
        # one import differently from the second.
        store = store_policy.get_policy(conn, business_id, store_id)
        rule, pricing_source = store_policy.resolve_rule(
            conn, business_id, store_id, pricing_rule)
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
                rule=rule, auto_publish=store["auto_publish"],
                marketplace_autolist=store["marketplace_autolist"],
                context=context, adapter=adapter)
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
        # Every item that produced a listing, not only the ones that stopped at a
        # draft. Reporting `counts[IMPORTED]` here after auto-publish arrived would
        # have told a merchant who published twenty products that none imported.
        "imported": sum(counts[o] for o in CREATED_LISTING),
        "counts": {k: v for k, v in counts.items() if v},
        # True only when every listing this run created is live. §30's "be honest"
        # applies to the summary as much as to the rows: a batch of twenty with one
        # needs-attention is not a published batch, and `any()` here would let one
        # success speak for nineteen drafts.
        "published": counts[PUBLISHED] > 0 and counts[PUBLISHED] == sum(
            counts[o] for o in CREATED_LISTING),
        "published_count": counts[PUBLISHED],
        "needs_attention": counts[NEEDS_ATTENTION],
        "pricing_rule": rule,
        # Which of §8's three tiers answered. The merchant's import result can say
        # *why* their products are priced the way they are, and a test can tell
        # "the store chose 45%" from "nobody chose and the platform did".
        "pricing_source": pricing_source,
        "auto_publish": store["auto_publish"],
        "marketplace_autolist": store["marketplace_autolist"],
    }
