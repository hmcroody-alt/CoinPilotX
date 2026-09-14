"""What a published listing does when its supplier changes the deal underneath it.

## The gap this closes

``worker.py`` already reads ``product`` and ``inventory`` for every imported
listing on a 900–3600s cadence and writes the result to ``supplier_snapshots``.
Nothing read those rows. The observation was durable, bounded, quota-controlled
— and inert. A supplier could double their cost or sell out entirely and the
listing kept the price and the unit count it was imported with, forever.

Both halves of that are worse than a stale number:

* **Cost up, price unchanged** is the merchant selling at a margin they did not
  agree to, and possibly below cost. They find out from their own accounts.
* **Sold out, units unchanged** is checkout continuing to take orders that
  ``fulfillment`` cannot place, which converts a supplier's stock problem into
  the merchant's refund problem.

``marketplace_variants.sync_updates_allowed`` was written for this consumer and
had none: its own docstring names the choice this module has to make ("never
refreshing, so supplier price and stock go stale" versus "always overwriting, so
the merchant's edited title and margin are destroyed"). The field-ownership row
exists so that neither is necessary.

## Unknown is not a change

Every function here distinguishes "the supplier told us something new" from "we
could not read the supplier". The second is *routine* — rate limits, outages, a
provider that omits a field — and it must never be written as a value.

This is the same rule ``pricing`` states for cost and ``importer`` learned the
hard way for ``quantity``, and the direction of travel here makes it sharper.
``lifecycle.inventory_available`` returns ``False`` for a NULL quantity, so
blanking a buyer-facing quantity on an unreadable stock read would take every
listing in a merchant's catalogue off sale during a sync outage, then report it
as "out of stock". A failed read has to leave the last known count exactly where
it is and say so somewhere the *merchant* sees, not somewhere the buyer does.

So the planners below return ``None`` for "leave this column alone", which is
deliberately distinct from ``0``. A caller that treats those as the same thing
reintroduces the outage-delists-everything defect.

``None`` carries that meaning everywhere except one place, and rather than leave
a reader to work out which, ``plan_stock_revision`` says so with a separate
``write_quantity`` flag. A supplier that declares stock without a count has
*withdrawn* a number we may still be storing, and holding onto it would report a
figure nobody currently claims — that is a ``None`` worth writing. An unreadable
read is not. The two are indistinguishable by value, so the flag states it.

## The merchant's price is the merchant's

A repricing that overwrites a hand-typed price is not a correction, and §44 says
a store on manual pricing does not get automatic anything. So a cost move
reprices when — and only when — the store has a rule that is not
``MANUAL_PRICE`` *and* the merchant has not claimed ``price`` in
``overridden_fields``. Otherwise the cost is recorded, the price is left alone,
and the resulting margin is reported. Silence would be the failure: the whole
point of noticing is that somebody has to be told.

## Deciding and writing are separate halves

The planners are pure and hold every rule. The applier below them holds none: it
resolves rows, calls a planner, and executes the UPDATE the planner described.
That split is why the dangerous rules can be proved exhaustively without a
fixture, and it is also the shape a reader needs — checking whether an outage can
delist a catalogue means reading one planner and two UPDATE statements, not a
reconciliation loop.

Two answers the applier needed, and which the decision layer could not supply,
were settled by reading the code that already owns them rather than by guessing:

* ``marketplace_listings.quantity`` tracks the **bound** variant, because
  ``drafts._sold_variant`` establishes that a dropship listing sells exactly the
  variant named in ``provider_variant_id`` and an order cannot be placed for any
  other. It is also a *ledger* the cart decrements per reservation, so it is
  moved by a delta here and never assigned — see :func:`_move_ledger`.
* An ``attention`` reason needs no new storage. ``drafts._validate`` derives
  ``NEGATIVE_MARGIN`` and ``UNKNOWN_INVENTORY`` live from the variant rows this
  module writes, so writing the state *is* recording the attention.
  ``last_sync_error`` is deliberately left alone: a listing selling below cost is
  a healthy read of bad news, and filing it as a sync error would make it
  indistinguishable from a connection that is actually broken.

## Somebody has to be able to ask why

A reprice here changes ``marketplace_listings.price_label`` — what a stranger's
card is charged — with no merchant awake and no tap anywhere. Afterwards the
column holds one number and has always held one number, so without a record the
honest answer to "why is this priced at $21.50" is that nobody can tell.

So a cost read that moves the buyer's price, or that raises a reason the merchant
needs to see, writes one row to the same append-only trail :mod:`audit` files
imports in. Same table, same timeline, deliberately a different subject type:
"why was this price *chosen*" and "why did it *change*" are two questions, and a
merchant looking for the second should not have to page through the first.

Two things about the shape of that write:

* **It rides this transaction.** The row is inserted on the same connection as
  the ``UPDATE`` it describes, before the commit. A row that survived a rolled-back
  reprice would assert a change that never happened; a commit without the row
  would be a silent change to a stranger's bill.
* **Restraint is the design.** ``worker`` re-reads every imported product every
  900–3600 seconds. A row per read is ninety-six rows per listing per day, and a
  trail that long has nothing findable in it. A supplier who did not move their
  price has not done anything worth telling anyone about.

Stock is deliberately not audited yet. A count flickers on a cadence a price does
not, and the rows worth keeping there are buyer-visible threshold crossings — in
stock to out of stock — rather than every reading. Filing those under the reprice
verb to get them landed would make the price history unreadable.

## What is still not wired

``worker.run_once`` calls :func:`apply_supplier_read` after a successful
``product`` or ``inventory`` read, so §23 and §24 now reach the listing, and the
trail above means the reason a price moved is durable rather than inferred. What
still does not exist is any *notification*: the record is something a merchant has
to go and look at, so they still learn their margin collapsed by opening the
product rather than by being told. §27's needs-attention surface covers imports,
not revisions, and pointing revisions at it would reuse a vocabulary that means
"this never went live" for listings that are live now.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from services import db
from services import marketplace_listing_lifecycle as lifecycle
from services import marketplace_variants as variants
from services.marketplace_supplier_schema import (
    MODE_STOCKED,
    STOCK_IN_STOCK,
    STOCK_OUT_OF_STOCK,
    STOCK_UNKNOWN,
    SYNC_SYNCED,
    SYNC_STALE,
)
from . import audit, drafts, normalize, pricing, store_policy

#: Mirrored from ``drafts`` rather than restated, so the ceiling the planner
#: refuses to cross and the ceiling publication refuses to cross are one number.
MAX_CHECKOUT_PRICE_CENTS = drafts.MAX_CHECKOUT_PRICE_CENTS

#: The supplier moved the cost and we repriced to hold the merchant's rule.
REPRICED = "REPRICED"
#: The cost moved and the price is the merchant's to change, so we did not.
COST_RECORDED = "COST_RECORDED"
#: Nothing the supplier said differs from what we already stored.
UNCHANGED = "UNCHANGED"
#: We could not read a cost. The stored one stands.
COST_UNREADABLE = "COST_UNREADABLE"

#: Reasons a revision is worth a merchant's attention. These are *not* the
#: import-time publish problems: those describe a listing that never went live,
#: and these describe one that is live now and has changed under the merchant.
#: Sharing a vocabulary would make "we could not publish this" and "this is
#: selling at a loss" indistinguishable to every reader downstream.
MARGIN_LOST = "MARGIN_LOST"
SELLING_BELOW_COST = "SELLING_BELOW_COST"
COST_UNAVAILABLE = "COST_UNAVAILABLE"
SUPPLIER_OUT_OF_STOCK = "SUPPLIER_OUT_OF_STOCK"
STOCK_UNREADABLE = "STOCK_UNREADABLE"
REPRICE_IMPOSSIBLE = "REPRICE_IMPOSSIBLE"

ATTENTION_REASONS = (MARGIN_LOST, SELLING_BELOW_COST, COST_UNAVAILABLE,
                     SUPPLIER_OUT_OF_STOCK, STOCK_UNREADABLE, REPRICE_IMPOSSIBLE)


def _row_time(now) -> str:
    """``marketplace_listing_variants`` / ``marketplace_product_sources`` format.

    Two formats, not one, and the split is not an oversight. ``variants._now``
    writes these two tables without an offset suffix and its docstring explains
    why — they sort against reservations and every other marketplace timestamp —
    while ``drafts._iso`` writes ``marketplace_listings`` with a trailing ``Z``.
    A writer that picked one format for both would leave a column holding two
    spellings of the same instant, which compares and orders wrongly for exactly
    the rows this module touched.
    """
    return datetime.fromtimestamp(_seconds(now), timezone.utc) \
        .replace(tzinfo=None).isoformat(timespec="seconds")


def _listing_time(now) -> str:
    """``marketplace_listings`` format. See :func:`_row_time` for why it differs."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_seconds(now)))


def _seconds(now) -> float:
    return time.time() if now is None else float(now)


def _int_or_none(value):
    """``int`` when the value is a real number, else ``None``.

    ``bool`` is rejected because ``True`` is an ``int`` in Python and a provider
    payload carrying ``"quantity": true`` is malformed, not a count of one.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stored_cost(source, variant):
    """The cost we believe we are paying for one variant, before this read.

    Extracted so the planner's comparison and the audit trail's ``before`` figure
    are one rule rather than two copies of it. The fallback order is explained at
    length in :func:`plan_cost_revision`; what matters here is that a trail row
    saying "the cost went from X to Y" has to use the same X the reprice decision
    used, or it describes a change that did not happen.
    """
    stored = _int_or_none((variant or {}).get("cost_cents"))
    if stored is None:
        stored = _int_or_none((source or {}).get("supplier_cost_cents"))
    return stored


def plan_cost_revision(*, source, variant, rule, observed_cost_cents,
                       shipping_cents=None) -> dict:
    """Decide what one variant's cost and price become after a supplier read.

    Pure. Returns ``cost_cents`` / ``price_cents`` where ``None`` means "leave
    the column as it is" — never "write NULL".

    ``rule`` is the store's resolved pricing rule, not the one recorded at import
    time. A merchant who changes their margin policy and then has a supplier move
    their cost should get the *current* policy applied; reusing the import-time
    rule would make the policy screen a write-only setting for every product
    already in the store.

    The cost being compared against is the *variant's* — ``cost_cents`` on
    ``marketplace_listing_variants`` — because that is the per-variant number the
    supplier moves and the one every margin here is computed from. The source
    row's ``supplier_cost_cents`` is a listing-level headline written at import,
    and it is only the fallback: on a multi-variant listing it holds one
    variant's figure, so comparing every variant against it would report a
    "change" for each sibling whose cost merely differs from the bound one, on
    every single tick.

    ``shipping_cents`` is the store's declared per-unit shipping allowance and it
    is threaded here for the same reason ``rule`` is: the importer prices against
    ``pricing.basis``, and a reprice that dropped back to the item cost would
    undo the allowance on the first supplier cost change. That is the "two
    pricing engines" failure §9 names, arrived at by omission rather than by
    someone writing a second formula.

    Note what it does *not* change: the cost comparison. ``observed == stored_cost``
    is a question about what the supplier charges for the item, and adding a
    constant to both sides of it would answer identically while making the
    intent unreadable. Only the margin and the proposed price move onto the
    landed basis.
    """
    stored_cost = _stored_cost(source, variant)
    retail = _int_or_none((variant or {}).get("price_cents"))
    observed = _int_or_none(observed_cost_cents)
    # Resolved once each, so no branch below can measure its margin against a
    # different basis than the one beside it. `pricing.basis` answers ITEM with
    # the cost unchanged whenever there is no usable allowance, which is what
    # every one of these calls did before.
    _, stored_basis = pricing.basis(stored_cost, shipping_cents)
    _, observed_basis = pricing.basis(observed, shipping_cents)

    if observed is None or observed < 0:
        # An unreadable or nonsense cost is not a cost. Writing it would replace
        # a number the merchant can act on with one nobody asserted.
        return {
            "action": COST_UNREADABLE,
            "cost_cents": None,
            "price_cents": None,
            "margin_state": pricing.margin_state(retail, stored_basis),
            "attention": COST_UNAVAILABLE if stored_cost is None else None,
        }

    if stored_cost is not None and observed == stored_cost:
        return {
            "action": UNCHANGED,
            "cost_cents": None,
            "price_cents": None,
            "margin_state": pricing.margin_state(retail, stored_basis),
            "attention": None,
        }

    # `sync_updates_allowed` is the authority on who owns `price`, rather than a
    # second membership test written here. Two implementations of one ownership
    # rule is how the merchant's edit survives in one code path and dies in the
    # other.
    #
    # The key is `price_label`, which is the name every writer actually records.
    # `sync_updates_allowed` is an exact string match against
    # `overridden_fields`, and both paths that hand price to the merchant store
    # that spelling: `drafts.edit_draft` appends "price_label" when it is given
    # `price_cents`, and the seller reprice route passes `fields=["price_label"]`.
    # Asking about "price_cents" — the column this function writes — therefore
    # matched nothing, and every merchant who had ever set their own price got it
    # overwritten by the next supplier cost change. The name to ask about is the
    # one the ownership list is written with, not the one the UPDATE uses.
    may_reprice = "price_label" in variants.sync_updates_allowed(
        source, {"price_label": True})
    kind = (rule or {}).get("type", pricing.MANUAL_PRICE)
    proposed = pricing.apply_rule(rule, observed_basis) if may_reprice else None

    if kind == pricing.MANUAL_PRICE or not may_reprice:
        # Cost recorded, price untouched. The margin is now whatever it is, and
        # saying so is the only service this branch can render.
        state = pricing.margin_state(retail, observed_basis)
        return {
            "action": COST_RECORDED,
            "cost_cents": observed,
            "price_cents": None,
            "margin_state": state,
            "attention": _cost_attention(state),
        }

    if proposed is None or proposed <= 0 or proposed > MAX_CHECKOUT_PRICE_CENTS:
        # §8/§11: never write a free or absent price onto a live listing. The
        # old price stands and the merchant is told we could not hold their rule.
        #
        # The ceiling is the same refusal at the other end. `_set_prices` accepts
        # a variant price up to ten million dollars and the buyer's checkout
        # clamps at `MAX_CHECKOUT_PRICE_CENTS`, so a rule that computes into that
        # gap would have the merchant's records say one number and the card
        # charged another. Refusing here means the applier never has to discover
        # it after the write, when the old price is already gone.
        return {
            "action": COST_RECORDED,
            "cost_cents": observed,
            "price_cents": None,
            "margin_state": pricing.margin_state(retail, observed_basis),
            "attention": REPRICE_IMPOSSIBLE,
        }

    state = pricing.margin_state(proposed, observed_basis)
    return {
        "action": REPRICED,
        "cost_cents": observed,
        "price_cents": proposed,
        "margin_state": state,
        # A rule-held price can still be a bad one: TARGET_MARGIN 5 is a healthy
        # application of an unhealthy policy, so the state is reported on its own
        # terms rather than assumed fine because we chose the number.
        "attention": _cost_attention(state),
    }


def _cost_attention(margin_state):
    if margin_state == pricing.NEGATIVE_MARGIN:
        return SELLING_BELOW_COST
    if margin_state == pricing.CRITICAL_MARGIN:
        return MARGIN_LOST
    if margin_state == pricing.UNKNOWN:
        return COST_UNAVAILABLE
    return None


def plan_stock_revision(*, observed_state, observed_quantity) -> dict:
    """Decide what one variant's stock becomes, and whether units may be touched.

    Pure. ``units`` is ``None`` whenever the buyer-facing count must not move,
    which is the case this function mainly exists to get right.

    Mirrors ``drafts._sellable_units`` for the in-stock arithmetic rather than
    restating it: in stock with no number is one unit at a time, because
    ``variants.availability`` trusts a provider that declares availability
    without a count and refusing to sell it would contradict that.

    ``stock_quantity: None`` is ambiguous on its own and ``write_quantity``
    disambiguates it. In stock without a number, the supplier is actively no
    longer asserting a count, so a previously stored one must be cleared —
    keeping it would report a number nobody currently claims. On an unreadable
    read nothing was asserted at all, so the last known count stays. Both cases
    carry ``None``; only the flag separates "the count is gone" from "we did not
    learn the count", and an applier that inferred it from the value alone would
    erase a real count every time the provider timed out.

    The observed state is collapsed through ``normalize.storage_stock_state``
    rather than compared directly, because the vocabulary handed to this function
    is wider than the three states storage keeps. ``normalize.stock_state``
    returns ``LOW_STOCK`` for anything under five units, and matching on
    ``IN_STOCK`` alone sent that to the unreadable branch below: a supplier
    honestly reporting its last three units was recorded as a failed read, the
    count stayed on whatever stale number preceded it, and the source sat
    ``STALE`` for as long as the product remained nearly sold out. Exactly
    inverted — low stock is the most urgent *readable* state there is — and the
    helper already owns the rule that it collapses upward to IN_STOCK.
    """
    state = normalize.storage_stock_state(str(observed_state or "").strip().upper())
    quantity = _int_or_none(observed_quantity)

    if state == STOCK_OUT_OF_STOCK or (state == STOCK_IN_STOCK and quantity is not None
                                       and quantity <= 0):
        # Zero units stops checkout (`lifecycle.inventory_available` compares
        # against it) without unpublishing: the product page, its reviews and its
        # place in the merchant's store survive a supplier's stock-out, which is
        # temporary, and come back when the stock does.
        return {
            "stock_state": STOCK_OUT_OF_STOCK,
            "stock_quantity": 0,
            "write_quantity": True,
            "units": 0,
            "sync_state": SYNC_SYNCED,
            "attention": SUPPLIER_OUT_OF_STOCK,
        }

    if state == STOCK_IN_STOCK:
        return {
            "stock_state": STOCK_IN_STOCK,
            "stock_quantity": quantity,
            "write_quantity": True,
            "units": 1 if quantity is None else quantity,
            "sync_state": SYNC_SYNCED,
            "attention": None,
        }

    # Everything else — a literal UNKNOWN, an empty read, or a state this code
    # has never heard of. `units: None` is the whole point: a failed stock read
    # leaves the count alone. Writing 0 here would delist a merchant's entire
    # catalogue during a provider outage and label it "out of stock", and
    # writing NULL would do the same thing via `inventory_available`.
    return {
        "stock_state": STOCK_UNKNOWN,
        "stock_quantity": None,
        "write_quantity": False,
        "units": None,
        "sync_state": SYNC_STALE,
        "attention": STOCK_UNREADABLE,
    }


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
#
# The planners above answer "what should this become". Everything below answers
# "and write it", and it is deliberately thin: no rule is decided here, so a
# reader checking whether the outage-delists-everything defect is present only
# has to read `plan_stock_revision` and this file's two UPDATE statements.
#
# Nothing goes through `variants.upsert_variant`. That helper writes every
# column it is given and treats an omitted one as NULL, so an inventory sync
# routed through it would blank `cost_cents` and `price_cents` and hand every
# product a fabricated 100% margin -- the identical hazard `drafts._set_prices`
# names. Each statement below lists exactly the columns its half owns, which is
# what makes the field-ownership split something the SQL enforces rather than
# something a caller is trusted to respect.


def _stored_units(variant) -> int | None:
    """Units the stored row currently offers, or ``None`` if it never said.

    This is the "before" half of the ledger arithmetic below, and it reads the
    variant the same way :func:`drafts._sellable_units` reads the one being
    published, with one deliberate difference: a variant whose state is
    ``UNKNOWN`` but which still carries a count is worth that count.

    That difference is the whole reason this is not just a call to
    ``_sellable_units``. ``availability`` answers ``UNKNOWN`` for such a row and
    ``_sellable_units`` therefore answers ``0`` -- correct when deciding what to
    publish, and wrong here, because a preserved count is exactly what the
    previous tick's unreadable read left behind. Treating it as zero would make
    the next successful read look like a restock from nothing and credit the
    merchant's ledger with units it already had.
    """
    state = str(variant.get("stock_state") or STOCK_UNKNOWN).strip().upper()
    if state == STOCK_OUT_OF_STOCK:
        return 0
    quantity = _int_or_none(variant.get("stock_quantity"))
    if quantity is not None:
        return max(0, quantity)
    return 1 if state == STOCK_IN_STOCK else None


def _move_ledger(cur, *, listing_id, seller_user_id, before, after, now) -> None:
    """Move ``marketplace_listings.quantity`` by what the supplier changed.

    A relative move, not an assignment, and that is the load-bearing choice.
    ``quantity`` is not a copy of the supplier's stock level: the cart decrements
    it per unit reserved (``marketplace_cart_routes``, ``quantity=quantity-?``)
    and credits it back on release. Overwriting it with the supplier's count
    would silently release every outstanding reservation -- two buyers holding
    four units between them would find those units back on sale, and the second
    checkout would take an order the first one already owns.

    ``before is None`` is the one case with no delta to apply: the row has never
    carried a count, which means it has never been publishable (``_validate``
    stops on ``UNKNOWN_INVENTORY``), which means nothing can be reserved against
    it. Assignment is safe there and is the only thing available.

    The floor is a second statement rather than ``MAX``/``GREATEST``, which are
    spelled differently on SQLite and PostgreSQL. Applied unconditionally so it
    also repairs a row some other writer drove negative.
    """
    if after is None:
        return
    if before is None:
        cur.execute(
            "UPDATE marketplace_listings SET quantity=?, updated_at=? "
            "WHERE id=? AND seller_user_id=?",
            (max(0, int(after)), _listing_time(now), int(listing_id), int(seller_user_id)))
    elif after != before:
        cur.execute(
            "UPDATE marketplace_listings SET quantity=COALESCE(quantity,0)+?, updated_at=? "
            "WHERE id=? AND seller_user_id=?",
            (int(after) - int(before), _listing_time(now), int(listing_id), int(seller_user_id)))
    else:
        return
    cur.execute(
        "UPDATE marketplace_listings SET quantity=0 WHERE id=? AND quantity<0",
        (int(listing_id),))


def _apply_stock(cur, *, source, rows, readings, now) -> dict:
    """Write one listing's stock from a normalized inventory read.

    A variant the read did not mention is skipped entirely, per the rule
    ``normalize.apply_inventory`` already states: providers routinely return only
    the warehouses that have rows, so omission is a short list, not a sell-out.
    """
    listing_id = int(source["listing_id"])
    seller_user_id = int(source["seller_user_id"])
    bound = drafts._sold_variant(rows, source)
    attention, touched, sync_state = [], 0, SYNC_SYNCED

    for row in rows:
        reference = str(row.get("provider_variant_id") or "").strip()
        reading = readings.get(reference) if reference else None
        if reading is None:
            continue
        observed_state, observed_quantity = reading
        plan = plan_stock_revision(observed_state=observed_state,
                                   observed_quantity=observed_quantity)
        if plan["write_quantity"]:
            cur.execute(
                f"UPDATE {variants.VARIANT_TABLE} SET stock_state=?, stock_quantity=?, "
                f"stock_synced_at=?, updated_at=? WHERE id=? AND seller_user_id=?",
                (plan["stock_state"], plan["stock_quantity"], _row_time(now),
                 _row_time(now), int(row["id"]), seller_user_id))
        else:
            # `stock_quantity` is absent from this column list on purpose, and
            # that absence is the defect this module exists to avoid. The state
            # moves to UNKNOWN -- which the merchant's draft screen reads and no
            # buyer surface does -- and the count the supplier could not confirm
            # stays exactly where the last successful read left it.
            #
            # `stock_synced_at` is absent for the same reason: it answers "when
            # did we last learn this variant's stock", and a read that taught us
            # nothing must not advance it.
            cur.execute(
                f"UPDATE {variants.VARIANT_TABLE} SET stock_state=?, updated_at=? "
                f"WHERE id=? AND seller_user_id=?",
                (plan["stock_state"], _row_time(now), int(row["id"]), seller_user_id))
        touched += 1
        if plan["attention"]:
            attention.append(plan["attention"])
        if plan["sync_state"] != SYNC_SYNCED:
            sync_state = plan["sync_state"]
        if bound is not None and int(row["id"]) == int(bound["id"]):
            # Only the bound variant moves the ledger. `marketplace_listings.
            # quantity` counts units of the one variant an order can be placed
            # for -- `drafts._sold_variant` explains why there is exactly one --
            # so summing the siblings would offer a buyer stock of a colour they
            # cannot choose and nobody will ship.
            _move_ledger(cur, listing_id=listing_id, seller_user_id=seller_user_id,
                         before=_stored_units(row), after=plan["units"], now=now)

    return {"variants": touched, "attention": attention, "sync_state": sync_state}


def _apply_cost(cur, *, source, listing, rows, costs, rule, shipping_cents, economics,
                now) -> dict:
    """Write one listing's costs and rule-held prices from a product read.

    Returns the usual counts plus an ``audit`` entry: either ``None`` when nothing
    happened that a merchant would want a record of, or the ``before``/``after``
    pair and action for one trail row. It *computes* that row and does not write
    it — the write belongs to :func:`_apply_to_listing`, which owns the transaction
    the price change commits in, and this function does not know the
    ``business_id`` the trail is keyed to.
    """
    listing_id = int(source["listing_id"])
    seller_user_id = int(source["seller_user_id"])
    bound = drafts._sold_variant(rows, source)
    attention, touched, repriced_bound = [], 0, None
    # The bound variant's figures, because that is the variant a buyer can
    # actually order -- `drafts._sold_variant` is the authority on why there is
    # exactly one -- and therefore the only one whose cost movement changes what a
    # card is charged. A sibling's cost moving is recorded on the variant row and
    # is not a story about this listing's price.
    bound_cost_before = None if bound is None else _stored_cost(source, bound)
    bound_cost_after = bound_cost_before
    bound_state = None
    # Captured before any write, because the row the caller read is the only place
    # the old label still exists once the UPDATE below runs.
    label_before = listing.get("price_label")
    label_after = label_before

    for row in rows:
        reference = str(row.get("provider_variant_id") or "").strip()
        if not reference or reference not in costs:
            continue
        plan = plan_cost_revision(source=source, variant=row, rule=rule,
                                  observed_cost_cents=costs[reference],
                                  shipping_cents=shipping_cents)
        assignments, args = [], []
        if plan["cost_cents"] is not None:
            assignments.append("cost_cents=?")
            args.append(plan["cost_cents"])
        if plan["price_cents"] is not None:
            assignments.append("price_cents=?")
            args.append(plan["price_cents"])
        if assignments:
            cur.execute(
                f"UPDATE {variants.VARIANT_TABLE} SET {', '.join(assignments)}, updated_at=? "
                f"WHERE id=? AND seller_user_id=?",
                (*args, _row_time(now), int(row["id"]), seller_user_id))
            touched += 1
        if plan["attention"]:
            attention.append(plan["attention"])
        if bound is not None and int(row["id"]) == int(bound["id"]):
            bound_state = plan["margin_state"]
            if plan["cost_cents"] is not None:
                bound_cost_after = plan["cost_cents"]
                # The source row's cost is the listing-level headline the Review
                # screen shows, and it tracks the bound variant because that is
                # the variant the merchant is actually billed for.
                cur.execute(
                    f"UPDATE {variants.SOURCE_TABLE} SET supplier_cost_cents=?, updated_at=? "
                    f"WHERE listing_id=? AND seller_user_id=?",
                    (plan["cost_cents"], _row_time(now), listing_id, seller_user_id))
            if plan["price_cents"] is not None:
                repriced_bound = plan["price_cents"]

    if repriced_bound is not None and \
            lifecycle.normalized(listing.get("status")) in lifecycle.PUBLIC_STATUSES:
        # Repricing a variant without this line is worse than not repricing at
        # all. `marketplace_listings.price_label` is the number a stranger's card
        # is charged -- every buyer surface parses it and none of them read
        # `marketplace_listing_variants` -- so a reprice that stopped at the
        # variant would leave the merchant's records saying one price while the
        # cart went on taking the old one.
        #
        # Called directly rather than through `drafts._live_price_label` because
        # that function's three refusals are already answered here: a bound
        # listing offers exactly one variant, so there is no price spread;
        # `plan_cost_revision` refuses above `MAX_CHECKOUT_PRICE_CENTS`; and the
        # price is positive by the same branch. `_publish_core` calls the same
        # helper directly on the same reasoning.
        label_after = drafts._checkout_price_label(repriced_bound, listing.get("currency"))
        cur.execute(
            "UPDATE marketplace_listings SET price_label=?, updated_at=? "
            "WHERE id=? AND seller_user_id=?",
            (label_after, _listing_time(now), listing_id, seller_user_id))

    return {
        "variants": touched,
        "attention": attention,
        "sync_state": SYNC_SYNCED,
        "audit": _cost_audit(
            source=source, economics=economics,
            label_before=label_before, label_after=label_after,
            cost_before=bound_cost_before, cost_after=bound_cost_after,
            margin_state=bound_state, attention=attention, variants_written=touched),
    }


def _cost_audit(*, source, economics, label_before, label_after, cost_before,
                cost_after, margin_state, attention, variants_written):
    """The trail row a cost read earned, or ``None`` if it earned none.

    Two things earn one: the number a buyer's card is charged moved, or something
    about the margin now needs a human. Nothing else does, and the restraint is the
    design rather than an optimisation. ``worker`` re-reads every imported product
    every 900–3600 seconds; a row per read would put ninety-six rows per listing
    per day into the same timeline a merchant opens to find out what happened to
    their store, and a trail that long is a trail with nothing findable in it. A
    supplier who has not changed their price has not done anything worth telling
    the merchant about.

    ``UNCHANGED`` ticks cannot reach this in the first place -- ``plan_cost_revision``
    returns both cents as ``None`` when the observed cost matches the stored one --
    but the comparison below is on the *label*, not on whether a write happened,
    because "did the buyer's charge move" is the question the row answers. A cost
    that moved a cent and rounded to the same price is not a price change.

    ``price_label`` appears on both sides even when it did not move: an attention
    row whose payload said nothing about the price would leave a reader unable to
    tell whether the margin collapsed because the cost rose or because the price
    fell.
    """
    moved = label_after is not None and label_after != label_before
    if not moved and not attention:
        return None
    identity = {
        "listing_id": int(source["listing_id"]),
        "provider": str(source.get("provider") or "").strip().lower(),
        "external_product_id": source.get("provider_product_id"),
    }
    return {
        "action": audit.REPRICE_APPLIED if moved else audit.REPRICE_ATTENTION,
        "before": {**identity,
                   "price_label": label_before,
                   "supplier_cost_cents": cost_before},
        "after": {**identity, **(economics or {}),
                  "price_label": label_after if moved else label_before,
                  "supplier_cost_cents": cost_after,
                  "margin_state": margin_state,
                  "attention": list(attention),
                  "variants_written": variants_written},
    }


def _readings(kind, provider, payload) -> dict:
    """The supplier's numbers, keyed by provider variant id, or ``{}``.

    ``{}`` is returned for anything unrecognisable — an unsupported provider, a
    payload shape ``normalize`` refuses — and it is the right answer rather than
    a raise: an empty mapping applies nothing, which is what "we could not read
    the supplier" has to mean everywhere in this module. Raising would push the
    same non-event into the worker's error path, where it would burn the job's
    retry budget and eventually surface to the merchant as a broken connection.
    """
    if not normalize.supported(provider):
        return {}
    try:
        if kind == "inventory":
            return normalize.inventory(provider, payload) or {}
        product = normalize.product(provider, payload) or {}
        return {str(v.get("external_variant_id")): v.get("cost_cents")
                for v in product.get("variants") or ()
                if v.get("external_variant_id") is not None}
    except Exception:
        return {}


def _apply_to_listing(binding, *, kind, readings, rule, shipping_cents, business_id,
                      economics, now) -> dict:
    """Everything one listing's revision writes, in one transaction.

    A connection per listing, not per tick. One listing whose write fails must
    not roll back the others' — a supplier read covers whichever listings happen
    to be bound to the same product id, and they have nothing to do with each
    other beyond that coincidence.

    The trail row goes in on this connection, before this commit. That is the whole
    reason the audit is written here and not by the caller: the row says a buyer's
    price moved, and the statement that moved it is in this transaction. Committed
    separately it could survive a rollback and assert a change that never happened,
    or be lost while the change stood.
    """
    listing_id = int(binding["listing_id"])
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1", (listing_id,))
        row = cur.fetchone()
        if row is None:
            return {"variants": 0, "attention": [], "skipped": "listing_missing"}
        listing = dict(row)
        # Re-read through `source_for` rather than using the binding as-is: the
        # binding comes from a bulk SELECT and carries `overridden_fields_json`
        # as raw text, and `sync_updates_allowed` reads the parsed list. Handing
        # it the unparsed row would make every merchant-owned field look
        # unclaimed, which is precisely the "merchant's edit dies on sync" defect
        # the ownership row exists to prevent.
        source = variants.source_for(cur, listing_id)
        if source is None:
            return {"variants": 0, "attention": [], "skipped": "source_missing"}
        rows = variants.variants_for(cur, listing_id)
        if kind == "inventory":
            result = _apply_stock(cur, source=source, rows=rows, readings=readings, now=now)
        else:
            result = _apply_cost(cur, source=source, listing=listing, rows=rows,
                                 costs=readings, rule=rule,
                                 shipping_cents=shipping_cents,
                                 economics=economics, now=now)
        if result.get("audit"):
            audit.record_reprice(conn, business_id=business_id, listing_id=listing_id,
                                 action=result["audit"]["action"],
                                 before=result["audit"]["before"],
                                 after=result["audit"]["after"])
        if result["variants"]:
            cur.execute(
                f"UPDATE {variants.SOURCE_TABLE} SET sync_state=?, last_synced_at=?, "
                f"updated_at=? WHERE listing_id=? AND seller_user_id=?",
                (result["sync_state"], _row_time(now), _row_time(now), listing_id,
                 int(source["seller_user_id"])))
        conn.commit()
        return result
    finally:
        conn.close()


def apply_supplier_read(*, connection_id, business_id, store_id, kind, resource_id,
                        payload, now=None) -> dict:
    """Apply one completed ``product`` or ``inventory`` read to its listings. §23/§24.

    This is the consumer ``supplier_snapshots`` never had. The worker was already
    reading every imported product on a 900–3600s cadence and storing the result;
    until this function ran, a supplier could double their cost or sell out and
    the listing kept its import-time price and unit count forever.

    Scoped to the connection tuple, not to the resource id the read names, for
    the reason ``supplier_bindings_for_connection`` gives: the answer to "which
    listings does this touch" must be bounded by what this connection actually
    bound.

    ``STOCKED`` sources are skipped. That inventory is the merchant's own — they
    hold it, they count it, they ship it — and a supplier read describes stock
    in somebody else's warehouse. Writing it over their count would be the same
    class of error as fabricating one.

    Returns counts only. No provider payload, cost or credential is returned,
    because the worker logs what this hands back.
    """
    out = {"listings": 0, "variants": 0, "skipped": 0, "attention": []}
    if kind not in {"product", "inventory"}:
        return out
    now = _seconds(now)

    conn = db.connect()
    try:
        bindings = variants.supplier_bindings_for_connection(
            conn.cursor(), supplier_connection_id=connection_id,
            business_id=business_id, store_id=store_id,
            provider_product_id=resource_id)
        rule = None
        shipping_cents = None
        economics = None
        if kind == "product":
            # Resolved once for the whole read, on the same connection, exactly
            # as `importer` resolves it once for a whole batch: per listing would
            # be the same answer plus N reads, and would let a policy edited
            # mid-tick reprice half of one product's listings differently.
            rule, pricing_source = store_policy.resolve_rule(conn, business_id, store_id)
            # Resolved beside the rule, never separately. The importer priced
            # against `pricing.basis(cost, allowance)`; a reprice that resolved
            # the rule but not the allowance would hold the merchant's margin
            # policy against the wrong cost on the first supplier price move.
            shipping_cents, shipping_source = store_policy.resolve_shipping_allowance(
                conn, business_id, store_id)
            # Both sources were being discarded, and the trail is why they are not
            # any more. §8 gives three tiers and a merchant asking why an overnight
            # reprice landed where it did needs to know which one answered: "your
            # own policy" and "PulseSoc's 45% default" are different answers to the
            # same question, and the rule alone cannot tell them apart.
            economics = {
                "pricing_rule": rule,
                "pricing_source": pricing_source,
                "shipping_allowance_cents": shipping_cents,
                "shipping_allowance_source": shipping_source,
                "margin_basis": pricing.LANDED if shipping_cents is not None
                else pricing.ITEM,
            }
    finally:
        conn.close()

    if economics is not None and bindings:
        # Same reason the importer does this: the trail's table belongs to the
        # store subsystem, whose DDL is reached through a route pack registered in
        # an `except Exception` block. A worker process may never have touched it.
        audit.ensure_schema()

    for binding in bindings:
        provider = str(binding.get("provider") or "").strip().lower()
        readings = _readings(kind, provider, payload)
        if not readings or str(binding.get("fulfillment_mode") or "").strip().upper() == MODE_STOCKED:
            out["skipped"] += 1
            continue
        result = _apply_to_listing(binding, kind=kind, readings=readings, rule=rule,
                                   shipping_cents=shipping_cents,
                                   business_id=business_id, economics=economics,
                                   now=now)
        if result.get("skipped"):
            out["skipped"] += 1
            continue
        out["listings"] += 1
        out["variants"] += result["variants"]
        for reason in result["attention"]:
            if reason not in out["attention"]:
                out["attention"].append(reason)
    return out
