"""CJ Dropshipping as a ``SupplierLogisticsProvider``.

This is the only module in the delivery domain that knows what a ``pid`` is.

Why it does not go through ``gateway.read("shipping", ...)``
-----------------------------------------------------------
That function is the right way for a *merchant* to price a *real order*, and
``fulfillment.quote_for_order`` uses it correctly. Reusing it for a product page
would be wrong three times over:

1. **It writes a row per call.** ``gateway.read`` persists a ``supplier_snapshots``
   record for every ``shipping`` read, because ``create_intent`` cross-checks the
   request fields of a stored quote before funding an order. Those rows are order
   evidence. A product page served to anonymous visitors would mint them at the
   rate of public traffic, and an estimate is not evidence of anything.
2. **It authorizes per call.** It takes an ``actor_user_id`` and re-runs the
   merchant scope check on the way in and the way out. A visitor reading a product
   page is not an actor in the seller's merchant scope, and inventing one to
   satisfy the signature would be inventing an authorization.
3. **Its cache answers a different question.** Thirty seconds is a herd guard for a
   merchant refreshing a dashboard. The forty-product feed in §18 needs hours, keyed
   on a corridor rather than on an exact params dict.

So this provider takes an already-resolved adapter and calls
``estimate_shipping`` directly. Credential resolution and "which connection serves
this listing" stay outside the delivery domain, where they were already solved.

What it refuses to invent
-------------------------
CJ needs five facts before it will price freight: an origin country, a weight, a
SKU, the shipping properties of the goods, and the variant id. Each one absent is
a refusal here, never a default, and the reasoning is ``fulfillment.py``'s: a
substituted ``"ORDINARY"`` property "would quote the wrong service for exactly the
goods where it matters most", and a substituted weight prices a parcel whose
contents we do not know. A guessed input does not produce a guessed answer — it
produces a real freight quote for an imaginary parcel, which is indistinguishable
from a good one until it is wrong.

Those refusals raise :class:`~services.delivery.breaker.NotProviderEvidence`,
because a variant we failed to describe is not a CJ outage. See that class.

What it deliberately does not send
----------------------------------
No street address, no city, no province, no recipient, and no postal code — even
when the caller has one. The cached answer must be a function of its cache key,
and that key holds a country and at most a postal *prefix*. Sending a full postal
code would compute a remote-area surcharge for one buyer and then serve it to
every other buyer sharing their prefix. The finer read belongs at checkout, where
``quote_for_order`` has a real address and stores the answer against one order.

The cost is accepted knowingly: callers who pass a postal code get a key more
specific than the request behind it, so several keys hold identical values. A
lower hit rate is the cheaper mistake.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.delivery import breaker

SUPPLIER_NAME = "cj"

#: Facts a freight request cannot be formed without. The reason travels to the
#: caller so an operator reading a log learns which column to go and fill in,
#: rather than "no delivery estimate available".
REASON_UNKNOWN_VARIANT = "variant_unknown"
REASON_NO_ORIGIN = "variant_origin_unknown"
REASON_NO_WEIGHT = "variant_weight_unknown"
REASON_NO_SKU = "variant_sku_unknown"
REASON_NO_PROPERTIES = "variant_logistics_properties_unknown"
REASON_NO_VID = "variant_supplier_id_unknown"

#: CJ's own ceiling on ``skuQuantity``; asking beyond it is a rejected request
#: rather than an expensive one.
MAX_QUANTITY = 10_000

#: CJ's ceiling on how many SKU rows one freight trial may carry.
MAX_SKU_ROWS = 20


class CJLogisticsProvider:
    """Freight routes for one CJ variant to one country.

    ``facts`` maps an opaque ``variant_ref`` to the physical description CJ needs.
    It is injected rather than read here so that this module performs no database
    access and no second supplier call: resolving a stocked origin costs a CJ
    inventory read, and doing that per quote would double the request count this
    domain exists to keep down. Origin changes with stock, not with the buyer, so
    it belongs to a longer-lived lookup than a freight quote.
    """

    name = SUPPLIER_NAME

    def __init__(self, *, adapter: Any, facts: Callable[[str], Optional[Dict[str, Any]]]):
        if adapter is None:
            raise ValueError("a CJ logistics provider needs an adapter")
        if not callable(facts):
            raise ValueError("a CJ logistics provider needs a variant facts resolver")
        self._adapter = adapter
        self._facts = facts

    def quote_routes(self, *, variant_ref: str, destination: Dict[str, Any],
                     quantity: int = 1) -> Sequence[Dict[str, Any]]:
        country = _country(destination)
        units = _units(quantity)
        described = _described(self._facts(variant_ref), variant_ref)

        line = {
            "srcAreaCode": described["origin"],
            "destAreaCode": country,
            # Per-unit weight times units. A single unit's weight sent for a
            # five-unit order prices the wrong parcel, and CJ answers it without
            # complaint because the request is well formed.
            "weight": float(described["weight_grams"] * units),
            "productProp": described["properties"],
            "skuList": [described["sku"]],
            "freightTrialSkuList": [{
                "vid": described["vid"],
                "sku": described["sku"],
                "skuQuantity": units,
            }],
        }
        mode = (destination or {}).get("shipping_mode")
        if isinstance(mode, str) and mode.strip():
            line["shippingMode"] = mode.strip()

        answer = self._adapter.estimate_shipping({"reqDTOS": [line]})
        quotes = (answer or {}).get("quotes")
        if not isinstance(quotes, list):
            # Not an empty route list. An empty list is CJ saying "nowhere from
            # here", which is a cacheable negative; a malformed body is us not
            # knowing what CJ said, and must reach the breaker as a failure.
            raise ValueError("CJ freight answer carried no quote list")
        # Returned exactly as the adapter normalized them. The router's contract
        # is already this shape, and re-mapping the fields here is how one of them
        # silently stops arriving.
        return quotes


def _country(destination: Any) -> str:
    country = (destination or {}).get("country") if isinstance(destination, dict) else None
    if not isinstance(country, str) or len(country.strip()) != 2 or not country.strip().isalpha():
        raise breaker.NotProviderEvidence(
            "destination_unresolved",
            "a freight quote needs an ISO-3166 alpha-2 destination country",
        )
    return country.strip().upper()


def _units(quantity: Any) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise breaker.NotProviderEvidence(
            "invalid_quantity", "quantity must be a whole number of units, at least one")
    if quantity > MAX_QUANTITY:
        raise breaker.NotProviderEvidence(
            "invalid_quantity", f"quantity {quantity} is beyond what the supplier will price")
    return quantity


def _described(facts: Any, variant_ref: Any) -> Dict[str, Any]:
    """Validate the five facts, naming whichever one is missing."""
    if not isinstance(facts, dict):
        raise breaker.NotProviderEvidence(
            REASON_UNKNOWN_VARIANT, f"no supplier variant is bound to {variant_ref!r}")

    vid = facts.get("vid")
    if not isinstance(vid, str) or not vid.strip():
        raise breaker.NotProviderEvidence(REASON_NO_VID, "the variant has no supplier id")

    origin = facts.get("origin")
    if not isinstance(origin, str) or len(origin.strip()) != 2 or not origin.strip().isalpha():
        # Not "CN". Quoting from a warehouse that holds none of the stock prices a
        # shipment that will not happen, which is `_stocked_origin`'s point.
        raise breaker.NotProviderEvidence(
            REASON_NO_ORIGIN, "no stocked warehouse country is known for the variant")

    sku = facts.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        raise breaker.NotProviderEvidence(REASON_NO_SKU, "the variant has no supplier SKU")

    grams = facts.get("weight_grams")
    if (isinstance(grams, bool) or not isinstance(grams, (int, float))
            or not math.isfinite(grams) or grams <= 0):
        raise breaker.NotProviderEvidence(
            REASON_NO_WEIGHT, "the variant states no usable shipping weight")

    properties = facts.get("properties")
    if not isinstance(properties, (list, tuple)):
        properties = None
    clean: List[str] = [p.strip() for p in (properties or [])
                        if isinstance(p, str) and p.strip()]
    if not clean:
        # CJ routes batteries, liquids and magnets differently. "ORDINARY" is a
        # plausible value and the wrong one exactly where it matters.
        raise breaker.NotProviderEvidence(
            REASON_NO_PROPERTIES, "the variant declares no shipping properties")

    return {
        "vid": vid.strip(),
        "origin": origin.strip().upper(),
        "sku": sku.strip(),
        "weight_grams": grams,
        "properties": clean,
    }
