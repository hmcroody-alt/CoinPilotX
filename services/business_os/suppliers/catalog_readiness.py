"""Why every imported product is where it is, for a whole catalogue at once.

The gap this closes
-------------------
``drafts.list_drafts`` returns a page of imported products carrying ``sync_state``
and ``attention`` and no verdict at all, and ``drafts.get_draft`` returns the
verdict for exactly one product over its own database connection. So a merchant
holding 152 stuck products can learn *that* they are stuck from the list and *why*
only by opening them one at a time. Nothing here is a new rule; this is the
existing verdict asked for every product in one pass.

What it does not do
-------------------
It does not write. Not the price header, not the quantity, not the binding, not
the lifecycle. Every number it reports is derived at read time from rows that were
already there, which is what makes it safe to run against a live catalogue before
anyone has decided anything.

It also does not recommend a variant unless the recommendation involves no choice.
``importer._sole_orderable`` is asked, and its refusals are carried through rather
than broken, so a product with three colours all in stock reports ``recommended:
None`` and the merchant picks. A survey that filled that field with the cheapest
or the first row would be choosing which physical object a stranger receives, and
it would be doing it at a scale of 152 at a time, which is the one error in this
whole area that cannot be walked back after it ships.

Why the state is derived rather than stored
------------------------------------------
Each product lands in exactly one :data:`STATES` bucket, decided here from the
listing's own columns and its verdict. There is no new status column and no second
lifecycle: ``marketplace_listings.status`` and ``approval_status`` remain the only
authorities, and these buckets are a *reading* of them. That is deliberate --
a stored readiness state would be a third axis to keep in step with the two that
already disagree often enough to need ``listing_readiness`` to reconcile them.

Because the bucket is a function and not a column, the counts cannot drift from
the rows: :func:`survey` tallies the per-product answers it just returned, so a
total that failed to reconcile would have to be a product with no bucket, and
:func:`_state_of` has no path that returns nothing.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services import marketplace_listing_lifecycle as lifecycle
from services import marketplace_seller_identity as seller_identity
from services import marketplace_variants as variants
from services.business_os.marketplace import listing_readiness
from services.business_os.suppliers import (
    drafts,
    importer,
    normalize,
    policy,
    pricing,
    store_policy,
)
from services.marketplace_supplier_schema import MODE_DROPSHIP

#: A listing a buyer can pay for, whose supplier order cannot be placed. First in
#: the order below because it is the only bucket that is actively losing: the
#: money arrives, the goods cannot be ordered, and nothing on any screen says so.
#: It exists as its own state rather than as a warning on ``LIVE`` because a
#: merchant scanning a list for problems will not read a warning on a green row.
UNSHIPPABLE_LIVE = "UNSHIPPABLE_LIVE"

#: Visible, purchasable, and orderable from the supplier. The healthy end state.
LIVE = "LIVE"

#: Discoverable but not buyable -- in practice a public row whose ``price_label``
#: names no price. Distinct from ``LIVE`` because a buyer *can* reach it, so it is
#: a storefront fact and not a draft.
PUBLIC_NOT_BUYABLE = "PUBLIC_NOT_BUYABLE"

#: Hidden, and the only thing standing between it and a decision is which variant
#: ships. Separated from ``NEEDS_ATTENTION`` because the action is different in
#: kind: every other blocker is something to fix, this one is something to choose,
#: and no amount of syncing or editing will resolve it.
NEEDS_VARIANT_DECISION = "NEEDS_VARIANT_DECISION"

#: Hidden, blocked by something other than the binding.
NEEDS_ATTENTION = "NEEDS_ATTENTION"

#: Hidden with nothing blocking it. The publish gate would accept this today.
READY_TO_GO_LIVE = "READY_TO_GO_LIVE"

#: Worst first. The order is the order a merchant should read them in, and
#: :func:`_state_of` resolves overlap by it.
STATES = (UNSHIPPABLE_LIVE, LIVE, PUBLIC_NOT_BUYABLE, NEEDS_VARIANT_DECISION,
          NEEDS_ATTENTION, READY_TO_GO_LIVE)


def _is_dropship(source: Optional[dict]) -> bool:
    return str((source or {}).get("fulfillment_mode") or "").upper() == MODE_DROPSHIP


def _bound_reference(source: Optional[dict]) -> Optional[str]:
    reference = str((source or {}).get("provider_variant_id") or "").strip()
    return reference or None


def _state_of(listing: dict, verdict: dict, source: Optional[dict]) -> str:
    """Which single bucket this product is in. Total function; never returns None.

    ``is_purchasable`` and ``is_public`` are asked rather than re-derived from
    ``status``, because they are what the buyer surfaces actually run and they
    reach conditions a status column does not -- a suspended seller takes every
    one of their listings off sale without any listing row changing.
    """
    purchasable = lifecycle.is_purchasable(listing)
    if purchasable:
        if _is_dropship(source) and _bound_reference(source) is None:
            return UNSHIPPABLE_LIVE
        return LIVE
    if lifecycle.is_public(listing):
        return PUBLIC_NOT_BUYABLE
    if drafts.SUPPLIER_VARIANT_UNBOUND in (verdict.get("blockers") or ()):
        return NEEDS_VARIANT_DECISION
    if verdict.get("blockers"):
        return NEEDS_ATTENTION
    return READY_TO_GO_LIVE


def _candidate_label(variant: dict) -> str:
    """The variant named as the merchant's own data names it, and no better.

    Deliberately a join of the option *values* and not a ``Color: Black`` style
    rendering. Every option row in this catalogue carries a positional name --
    literally ``option1``, ``option2`` -- and some carry a raw supplier SKU as the
    value, so there is nothing here to map onto Color/Size/Style/Material without
    guessing which axis is which. A label that said "Color: YP23122110281" would
    be worse than one that said "YP23122110281", because the first one is a claim.
    """
    options = variant.get("options")
    values = []
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict):
                value = str(option.get("value") or "").strip()
            else:
                value = str(option or "").strip()
            if value:
                values.append(value)
    if values:
        return " / ".join(values)
    return str(variant.get("sku") or variant.get("provider_variant_id") or "").strip()


def _candidates(priced: list) -> list:
    """Every variant an order could be placed for, with the economics of choosing it.

    Filtered on ``provider_variant_id`` because a variant the supplier cannot name
    is one ``gateway.bind_product`` will refuse, and offering it as a choice would
    be offering a button that fails.
    """
    out = []
    for variant in priced:
        reference = str(variant.get("provider_variant_id") or "").strip()
        if not reference:
            continue
        out.append({
            "provider_variant_id": reference,
            "variant_id": variant.get("variant_id"),
            "label": _candidate_label(variant),
            "options": variant.get("options"),
            "sku": variant.get("sku"),
            "availability": variant.get("availability"),
            "stock_state": variant.get("stock_state"),
            "stock_quantity": variant.get("stock_quantity"),
            "units_if_chosen": drafts._sellable_units(variant),
            "cost_cents": variant.get("cost_cents"),
            "landed_cost_cents": variant.get("landed_cost_cents"),
            "retail_cents": variant.get("retail_cents"),
            "margin_cents": variant.get("margin_cents"),
            "margin_percent": variant.get("margin_percent"),
            "margin_state": variant.get("margin_state"),
        })
    return out


def _recommendation(priced: list, source: Optional[dict]) -> Optional[str]:
    """``importer._sole_orderable``'s answer, unmodified.

    Called rather than reimplemented so this cannot recommend a variant the
    importer would have declined to bind. Returns ``None`` whenever there is a
    genuine choice, and ``None`` is the useful answer -- it is what tells the
    screen to ask the merchant instead of pre-selecting a row for them.
    """
    if _bound_reference(source) is not None:
        return None
    chosen = []
    for variant in priced:
        reference = str(variant.get("provider_variant_id") or "").strip()
        if not reference:
            continue
        chosen.append({
            "external_variant_id": reference,
            "stock_state": normalize.storage_stock_state(variant.get("stock_state")),
            "stock_quantity": variant.get("stock_quantity"),
        })
    if not chosen:
        return None
    return importer._sole_orderable(chosen)


def _price_preview(priced: list, source: Optional[dict], currency) -> dict:
    """What this listing would charge, or why no single number exists yet.

    ``canonical_price_cents`` is the authority, so the preview and the publish
    gate cannot disagree about whether a price is derivable.

    The range is reported alongside, and only when the single price is not
    derivable, because that is the case where it carries information: variants
    disagreeing by a factor of eight is a different conversation from variants
    disagreeing by a cent, and ``VARIANT_PRICE_SPREAD`` on its own does not say
    which one the merchant has. Where one price *is* derivable the range would
    restate it twice.
    """
    cents, problem = drafts.canonical_price_cents(priced, source)
    preview = {
        "derivable": problem is None,
        "cents": cents,
        "label": None if cents is None else drafts._checkout_price_label(cents, currency),
        "problem": problem,
        "low_cents": None,
        "high_cents": None,
    }
    if problem is None:
        return preview
    offered = [v.get("retail_cents") for v in drafts._offered(priced, source)]
    known = [int(value) for value in offered if value is not None]
    if known:
        preview["low_cents"] = min(known)
        preview["high_cents"] = max(known)
    return preview


def _pricing_rollup(economics: list) -> dict:
    """Margin health across every variant, as a report and never as a change.

    Counted over variants rather than listings because that is the grain the
    problem has: one listing can hold 99 variants, 3 of which sell under their
    own landed cost, and a per-listing count would report that as a single
    unhappy product either way.

    Counted over *every* priced variant, not over the bindable ones each entry
    offers as candidates. Those are two different populations -- a variant with no
    ``provider_variant_id`` cannot be bound and so is not a button, but its price
    is still a price in this catalogue -- and rolling the report up over the
    narrower one would publish a denominator that silently excludes rows while
    reading like a statement about all of them.

    ``below_landed_cost`` is a strict subset of what ``NEGATIVE_MARGIN`` means and
    is counted separately anyway. A variant priced above the item cost but below
    cost-plus-freight looks profitable on a supplier invoice and is not, and that
    is the population a merchant most needs named, because it is the one they
    cannot see from the numbers in front of them.

    There is deliberately no "below target" count. ``pricing.MARGIN_STATES`` has
    no such member: a target is a property of a rule, this read runs under
    ``MANUAL_PRICE`` on purpose, and a shortfall against a target nobody applied
    would be a number this module invented. ``margin_states`` reports the
    vocabulary that actually exists.
    """
    states: dict[str, int] = {}
    below_landed = 0
    below_item = 0
    unpriced = 0
    total = 0
    offenders = []
    for row in economics:
        worst = None
        for variant in row["variants"]:
            total += 1
            state = str(variant.get("margin_state") or pricing.UNKNOWN)
            states[state] = states.get(state, 0) + 1
            retail = variant.get("retail_cents")
            landed = variant.get("landed_cost_cents")
            cost = variant.get("cost_cents")
            if retail is None:
                unpriced += 1
                continue
            if landed is not None and int(retail) < int(landed):
                below_landed += 1
                shortfall = int(landed) - int(retail)
                if worst is None or shortfall > worst["shortfall_cents"]:
                    worst = {"provider_variant_id": variant.get("provider_variant_id"),
                             "label": _candidate_label(variant),
                             "retail_cents": int(retail),
                             "landed_cost_cents": int(landed),
                             "shortfall_cents": shortfall}
            if cost is not None and int(retail) < int(cost):
                below_item += 1
        if worst is not None:
            offenders.append({"listing_id": row["listing_id"],
                              "title": row["title"], **worst})
    offenders.sort(key=lambda row: row["shortfall_cents"], reverse=True)
    return {
        "variants": total,
        "unpriced": unpriced,
        "below_item_cost": below_item,
        "below_landed_cost": below_landed,
        "margin_states": states,
        "worst_by_listing": offenders,
    }


def survey(business_id, store_id, actor_user_id, connection_id, *,
           context=None, pricing_rule=None) -> dict:
    """Every supplier-backed listing in this store, with the verdict that decides it.

    One connection for the whole pass. ``drafts.get_draft`` opens and closes its
    own, which is correct for one product and is why it cannot be the thing a
    catalogue-wide answer loops over.

    ``pricing_rule`` defaults to the same ``None`` the Review Product screen's
    route passes, so the margins and the blockers here are the ones that screen
    shows for the same product. Under the resulting ``MANUAL_PRICE`` rule no
    price is *proposed*, which is exactly right for a read: margin is computed
    from the retail price already stored and is the same under every rule, while
    ``proposed_retail_cents`` is the only field a rule changes. Passing a rule
    here would therefore change nothing a caller reads and would risk reporting a
    margin the publish gate does not judge.
    """
    policy.require_enabled()
    rule = pricing.normalize_rule(pricing_rule)
    conn = db.connect()
    try:
        _, seller_user_id = drafts._scope(conn, business_id, store_id, actor_user_id,
                                          connection_id, context=context)
        shipping_cents, shipping_source = store_policy.resolve_shipping_allowance(
            conn, business_id, store_id)
        cur = conn.cursor()
        # Joined from the supplier mapping, the way `list_drafts` is, so a
        # manually authored listing can never appear here -- it has no source row.
        # The seller columns are selected because `is_public` reads them: a query
        # that omitted them would have `_seller_is_approved` and `_seller_is_named`
        # both answer `None` and the gate treat every listing as hidden, reporting
        # a healthy catalogue as entirely stuck. Both are spelled the way the
        # modules that own them spell it -- `COALESCE(...,'missing')` so a LEFT
        # JOIN miss is projected evidence rather than an absent key, and
        # `store_name_select` because the store name is `display_name` and
        # hand-writing that column here is how it drifts from the predicate.
        cur.execute(
            "SELECT l.*, COALESCE(ms.status,'missing') AS seller_status, " +
            seller_identity.store_name_select("ms") + " "
            "FROM marketplace_product_sources s "
            "JOIN marketplace_listings l ON l.id = s.listing_id "
            "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
            "WHERE s.seller_user_id=? AND s.supplier_connection_id=? "
            "AND s.business_id=? AND s.store_id=? ORDER BY l.id ASC",
            (int(seller_user_id), connection_id, business_id, store_id))
        listings = [dict(row) for row in cur.fetchall()]

        entries = []
        economics = []
        for listing in listings:
            listing_id = int(listing["id"])
            source = variants.source_for(cur, listing_id)
            # `evaluate` is handed the *raw* variant rows, not `priced`: it builds
            # the evaluator's shape itself, from that evaluator's own helpers, so
            # that no caller can get the projection subtly wrong. Handing it
            # `priced` is exactly that mistake -- `_retail_of` reads `price_cents`,
            # which a priced dict does not carry, so every listing came back
            # MISSING_PRICE including the 43 that are live and selling.
            rows = variants.variants_for(cur, listing_id)
            priced = drafts.priced_variants(rows, rule, shipping_cents)
            verdict = listing_readiness.evaluate(
                listing, media=drafts._media_of(listing),
                supplier={"source": source, "variants": rows},
                shipping_cents=shipping_cents)
            bound = _bound_reference(source)
            economics.append({"listing_id": listing_id,
                              "title": listing.get("title"), "variants": priced})
            entries.append({
                "listing_id": listing_id,
                "title": listing.get("title"),
                "status": listing.get("status"),
                "approval_status": listing.get("approval_status"),
                "state": _state_of(listing, verdict, source),
                "public": lifecycle.is_public(listing),
                "purchasable": lifecycle.is_purchasable(listing),
                "publishable": verdict["publishable"],
                "blockers": verdict["blockers"],
                "warnings": verdict["warnings"],
                "summary": verdict["summary"],
                "fixes": verdict["fixes"],
                "notes": verdict["notes"],
                "price": _price_preview(priced, source, listing.get("currency")),
                "binding": {
                    "bound": bound is not None,
                    "provider_variant_id": bound,
                    "fulfillment_mode": (source or {}).get("fulfillment_mode"),
                    "provider_product_id": (source or {}).get("provider_product_id"),
                    "variants": len(priced),
                    "recommended": _recommendation(priced, source),
                    "candidates": _candidates(priced),
                },
            })
    finally:
        conn.close()

    # Tallied from the answers just returned, not counted in a second pass over
    # the database. The reconciliation below is therefore a statement about this
    # payload rather than about two reads that may have seen different rows.
    partition = {state: 0 for state in STATES}
    for entry in entries:
        partition[entry["state"]] += 1
    return {
        "total": len(entries),
        "partition": partition,
        "listings": entries,
        "pricing": _pricing_rollup(economics),
        "shipping_allowance_cents": shipping_cents,
        "shipping_allowance_source": shipping_source,
        "pricing_rule": rule,
    }


def summarize(report: dict) -> str:
    """The survey as a human reads it. No cost basis, no credential, no provider body."""
    lines = [f"total supplier-backed listings: {report['total']}"]
    for state in STATES:
        lines.append(f"  {state:<24} {report['partition'][state]}")
    priced = report["pricing"]
    lines += [
        "pricing (report only, nothing changed):",
        f"  variants                 {priced['variants']}",
        f"  unpriced                 {priced['unpriced']}",
        f"  below item cost          {priced['below_item_cost']}",
        f"  below landed cost        {priced['below_landed_cost']}",
    ]
    for state, count in sorted(priced["margin_states"].items()):
        lines.append(f"  margin {state:<18} {count}")
    return "\n".join(lines)
