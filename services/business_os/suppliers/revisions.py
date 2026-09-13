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

## The merchant's price is the merchant's

A repricing that overwrites a hand-typed price is not a correction, and §44 says
a store on manual pricing does not get automatic anything. So a cost move
reprices when — and only when — the store has a rule that is not
``MANUAL_PRICE`` *and* the merchant has not claimed ``price`` in
``overridden_fields``. Otherwise the cost is recorded, the price is left alone,
and the resulting margin is reported. Silence would be the failure: the whole
point of noticing is that somebody has to be told.

## This module decides. It does not yet write.

Both functions here are pure planners and **nothing calls them yet**, which is
stated here rather than left for a reader to discover: a merchant's live listing
is still not repriced and still not taken off sale when their supplier changes
it. §23 and §24 are not closed by this file.

What is missing is the applier, and it is missing because it needs decisions this
layer does not: which variant's units ``marketplace_listings.quantity`` tracks
(``drafts.publish`` uses the *bound* variant, not a sum across the set), and
where an ``attention`` reason is recorded so the merchant actually sees it —
``marketplace_product_sources`` has ``last_sync_error``, but a listing selling
below cost is not a sync error and filing it as one would make a real read
failure indistinguishable from a healthy read of bad news.

Writing that applier against guessed answers is how the reconciler would end up
delisting catalogues, so the decision layer is landed first, with the rules it
encodes proved by mutation, and the wiring follows.
"""

from __future__ import annotations

from services import marketplace_variants as variants
from services.marketplace_supplier_schema import (
    STOCK_IN_STOCK,
    STOCK_OUT_OF_STOCK,
    STOCK_UNKNOWN,
    SYNC_SYNCED,
    SYNC_STALE,
)
from . import pricing

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


def plan_cost_revision(*, source, variant, rule, observed_cost_cents) -> dict:
    """Decide what one variant's cost and price become after a supplier read.

    Pure. Returns ``cost_cents`` / ``price_cents`` where ``None`` means "leave
    the column as it is" — never "write NULL".

    ``rule`` is the store's resolved pricing rule, not the one recorded at import
    time. A merchant who changes their margin policy and then has a supplier move
    their cost should get the *current* policy applied; reusing the import-time
    rule would make the policy screen a write-only setting for every product
    already in the store.
    """
    stored_cost = _int_or_none((source or {}).get("supplier_cost_cents"))
    retail = _int_or_none((variant or {}).get("price_cents"))
    observed = _int_or_none(observed_cost_cents)

    if observed is None or observed < 0:
        # An unreadable or nonsense cost is not a cost. Writing it would replace
        # a number the merchant can act on with one nobody asserted.
        return {
            "action": COST_UNREADABLE,
            "cost_cents": None,
            "price_cents": None,
            "margin_state": pricing.margin_state(retail, stored_cost),
            "attention": COST_UNAVAILABLE if stored_cost is None else None,
        }

    if stored_cost is not None and observed == stored_cost:
        return {
            "action": UNCHANGED,
            "cost_cents": None,
            "price_cents": None,
            "margin_state": pricing.margin_state(retail, stored_cost),
            "attention": None,
        }

    # `sync_updates_allowed` is the authority on who owns `price`, rather than a
    # second membership test written here. Two implementations of one ownership
    # rule is how the merchant's edit survives in one code path and dies in the
    # other.
    may_reprice = "price_cents" in variants.sync_updates_allowed(
        source, {"price_cents": True})
    kind = (rule or {}).get("type", pricing.MANUAL_PRICE)
    proposed = pricing.apply_rule(rule, observed) if may_reprice else None

    if kind == pricing.MANUAL_PRICE or not may_reprice:
        # Cost recorded, price untouched. The margin is now whatever it is, and
        # saying so is the only service this branch can render.
        state = pricing.margin_state(retail, observed)
        return {
            "action": COST_RECORDED,
            "cost_cents": observed,
            "price_cents": None,
            "margin_state": state,
            "attention": _cost_attention(state),
        }

    if proposed is None or proposed <= 0:
        # §8/§11: never write a free or absent price onto a live listing. The
        # old price stands and the merchant is told we could not hold their rule.
        return {
            "action": COST_RECORDED,
            "cost_cents": observed,
            "price_cents": None,
            "margin_state": pricing.margin_state(retail, observed),
            "attention": REPRICE_IMPOSSIBLE,
        }

    state = pricing.margin_state(proposed, observed)
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
    """
    state = str(observed_state or "").strip().upper()
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
            "units": 0,
            "sync_state": SYNC_SYNCED,
            "attention": SUPPLIER_OUT_OF_STOCK,
        }

    if state == STOCK_IN_STOCK:
        return {
            "stock_state": STOCK_IN_STOCK,
            "stock_quantity": quantity,
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
        "units": None,
        "sync_state": SYNC_STALE,
        "attention": STOCK_UNREADABLE,
    }
